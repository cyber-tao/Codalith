"""Bounded in-memory telemetry used by the local operations dashboard."""

from __future__ import annotations

import math
import os
import resource
import sys
import threading
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from codalith import __version__
from codalith.query.models import StatusResponse

WindowKey = Literal["15m", "1h", "6h", "24h", "7d"]


@dataclass(frozen=True, slots=True)
class WindowDefinition:
    key: WindowKey
    label: str
    seconds: int
    bucket_seconds: int


WINDOWS: dict[str, WindowDefinition] = {
    "15m": WindowDefinition("15m", "Last 15 minutes", 15 * 60, 30),
    "1h": WindowDefinition("1h", "Last hour", 60 * 60, 60),
    "6h": WindowDefinition("6h", "Last 6 hours", 6 * 60 * 60, 5 * 60),
    "24h": WindowDefinition("24h", "Last 24 hours", 24 * 60 * 60, 30 * 60),
    "7d": WindowDefinition("7d", "Last 7 days", 7 * 24 * 60 * 60, 4 * 60 * 60),
}


@dataclass(frozen=True, slots=True)
class QueryEvent:
    event_id: str
    timestamp: datetime
    tool: str
    target: str
    query: str
    duration_ms: float
    status: Literal["success", "error"]
    degraded: bool
    result_count: int
    error_code: str | None
    error_message: str | None


@dataclass(frozen=True, slots=True)
class LogEvent:
    event_id: str
    timestamp: datetime
    level: Literal["INFO", "WARN", "ERROR"]
    source: str
    message: str
    target: str
    duration_ms: float
    details: dict[str, object]


@dataclass(frozen=True, slots=True)
class ResourceSample:
    timestamp: datetime
    cpu_percent: float
    peak_memory_mb: float


class ProcessSampler:
    """Sample process CPU and portable peak RSS without another runtime dependency."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._wall = time.perf_counter()
        self._cpu = time.process_time()

    def sample(self, timestamp: datetime | None = None) -> ResourceSample:
        with self._lock:
            now_wall = time.perf_counter()
            now_cpu = time.process_time()
            elapsed = max(now_wall - self._wall, 1e-9)
            cpu_percent = min(100.0, max(0.0, (now_cpu - self._cpu) / elapsed * 100.0))
            self._wall = now_wall
            self._cpu = now_cpu
        return ResourceSample(
            timestamp=timestamp or datetime.now(UTC),
            cpu_percent=round(cpu_percent, 2),
            peak_memory_mb=round(_peak_memory_mb(), 2),
        )


class TelemetryStore:
    """Thread-safe bounded query, resource, and structured-log history."""

    def __init__(
        self,
        *,
        max_events: int = 10_000,
        max_logs: int = 2_000,
        max_resource_samples: int = 2_500,
    ) -> None:
        self.started_at = datetime.now(UTC)
        self._events: deque[QueryEvent] = deque(maxlen=max_events)
        self._logs: deque[LogEvent] = deque(maxlen=max_logs)
        self._resources: deque[ResourceSample] = deque(maxlen=max_resource_samples)
        self._sampler = ProcessSampler()
        self._lock = threading.RLock()
        self._resources.append(self._sampler.sample(self.started_at))

    def record_call(
        self,
        *,
        tool: str,
        arguments: dict[str, Any],
        target: str,
        started_at: datetime,
        duration_ms: float,
        result: dict[str, Any] | None,
        error: Exception | None,
    ) -> None:
        status: Literal["success", "error"] = "error" if error is not None else "success"
        degraded = bool(result and result.get("degraded"))
        error_code = _error_code(error) if error is not None else None
        error_message = _safe_error_message(error) if error is not None else None
        event = QueryEvent(
            event_id=str(uuid4()),
            timestamp=started_at,
            tool=tool,
            target=target,
            query=_query_preview(arguments),
            duration_ms=round(max(duration_ms, 0.0), 2),
            status=status,
            degraded=degraded,
            result_count=_result_count(result),
            error_code=error_code,
            error_message=error_message,
        )
        sample = self._sampler.sample()
        level: Literal["INFO", "WARN", "ERROR"]
        if error is not None:
            level = "ERROR"
        elif degraded:
            level = "WARN"
        else:
            level = "INFO"
        message = _log_message(event)
        details: dict[str, object] = {
            "request_id": event.event_id,
            "target": target,
            "query": event.query,
            "result_count": event.result_count,
            "status": event.status,
        }
        if error_code is not None:
            details["error_code"] = error_code
        if error_message is not None:
            details["error_message"] = error_message
        log = LogEvent(
            event_id=event.event_id,
            timestamp=sample.timestamp,
            level=level,
            source=tool,
            message=message,
            target=target,
            duration_ms=event.duration_ms,
            details=details,
        )
        with self._lock:
            self._events.append(event)
            self._logs.append(log)
            self._resources.append(sample)

    def snapshot(
        self,
        *,
        window_key: str,
        target: str,
        status: StatusResponse,
        targets: list[dict[str, str]],
    ) -> dict[str, object]:
        try:
            window = WINDOWS[window_key]
        except KeyError as exc:
            raise ValueError(f"Unsupported dashboard range: {window_key}") from exc

        now = datetime.now(UTC)
        current_start = now - timedelta(seconds=window.seconds)
        previous_start = current_start - timedelta(seconds=window.seconds)
        sample = self._sampler.sample(now)
        with self._lock:
            self._resources.append(sample)
            events = tuple(self._events)
            logs = tuple(self._logs)
            resources = tuple(self._resources)

        current_events = [
            item
            for item in events
            if item.target == target and item.timestamp >= current_start
        ]
        previous_events = [
            item
            for item in events
            if item.target == target and previous_start <= item.timestamp < current_start
        ]
        current_summary = _summary(current_events, resources)
        previous_summary = _summary(previous_events, resources)
        recent_queries = sorted(current_events, key=lambda item: item.timestamp, reverse=True)[:25]
        matching_logs = sorted(
            (
                item
                for item in logs
                if item.target == target and item.timestamp >= current_start
            ),
            key=lambda item: item.timestamp,
            reverse=True,
        )
        recent_logs = matching_logs[:100]
        recent_ids = {item.event_id for item in recent_logs}
        diagnostic_logs = [
            item
            for item in matching_logs
            if item.level != "INFO" and item.event_id not in recent_ids
        ][:20]
        recent_logs = sorted(
            (*recent_logs, *diagnostic_logs),
            key=lambda item: item.timestamp,
            reverse=True,
        )
        uptime_seconds = max(0, int((now - self.started_at).total_seconds()))

        return {
            "generated_at": now.isoformat(),
            "window": {
                "key": window.key,
                "label": window.label,
                "seconds": window.seconds,
                "bucket_seconds": window.bucket_seconds,
            },
            "service": {
                "version": __version__,
                "started_at": self.started_at.isoformat(),
                "uptime_seconds": uptime_seconds,
                "ready": status.ready,
                "target": status.target,
            },
            "targets": targets,
            "summary": current_summary,
            "comparison": _comparison(current_summary, previous_summary),
            "series": _series(
                current_events,
                resources,
                start=current_start,
                end=now,
                bucket_seconds=window.bucket_seconds,
            ),
            "tools": _tool_usage(current_events),
            "corpora": [item.model_dump(mode="json") for item in status.corpora],
            "recent_queries": [_query_payload(item) for item in recent_queries],
            "logs": [_log_payload(item) for item in recent_logs],
            "alerts": _alerts(status, current_summary),
            "retention": {
                "events_stored": len(events),
                "event_capacity": self._events.maxlen or 0,
                "logs_stored": len(logs),
                "log_capacity": self._logs.maxlen or 0,
            },
        }


def _summary(
    events: list[QueryEvent],
    resources: tuple[ResourceSample, ...],
) -> dict[str, float | int]:
    latencies = [item.duration_ms for item in events]
    errors = sum(item.status == "error" for item in events)
    peak_memory = max((item.peak_memory_mb for item in resources), default=0.0)
    return {
        "total_queries": len(events),
        "p50_latency_ms": round(_percentile(latencies, 0.50), 2),
        "p95_latency_ms": round(_percentile(latencies, 0.95), 2),
        "success_rate": round((len(events) - errors) / len(events) * 100, 2)
        if events
        else 100.0,
        "peak_memory_mb": round(peak_memory, 2),
        "error_count": errors,
        "degraded_count": sum(item.degraded for item in events),
    }


def _comparison(
    current: dict[str, float | int],
    previous: dict[str, float | int],
) -> dict[str, float | None]:
    return {
        "total_queries_percent": _percent_change(
            float(current["total_queries"]), float(previous["total_queries"])
        ),
        "p95_latency_percent": _percent_change(
            float(current["p95_latency_ms"]), float(previous["p95_latency_ms"])
        ),
        "success_rate_points": round(
            float(current["success_rate"]) - float(previous["success_rate"]), 2
        )
        if previous["total_queries"]
        else None,
        "peak_memory_percent": _percent_change(
            float(current["peak_memory_mb"]), float(previous["peak_memory_mb"])
        ),
    }


def _series(
    events: list[QueryEvent],
    resources: tuple[ResourceSample, ...],
    *,
    start: datetime,
    end: datetime,
    bucket_seconds: int,
) -> list[dict[str, object]]:
    first_bucket = math.floor(start.timestamp() / bucket_seconds) * bucket_seconds
    last_bucket = math.floor(end.timestamp() / bucket_seconds) * bucket_seconds
    event_buckets: dict[int, list[QueryEvent]] = defaultdict(list)
    resource_buckets: dict[int, ResourceSample] = {}
    for event in events:
        bucket = math.floor(event.timestamp.timestamp() / bucket_seconds) * bucket_seconds
        event_buckets[bucket].append(event)
    for sample in resources:
        if sample.timestamp < start:
            continue
        bucket = math.floor(sample.timestamp.timestamp() / bucket_seconds) * bucket_seconds
        if bucket <= last_bucket:
            resource_buckets[bucket] = sample

    output: list[dict[str, object]] = []
    previous_resource: ResourceSample | None = None
    for bucket in range(first_bucket, last_bucket + 1, bucket_seconds):
        bucket_events = event_buckets.get(bucket, [])
        latencies = [item.duration_ms for item in bucket_events]
        previous_resource = resource_buckets.get(bucket, previous_resource)
        output.append(
            {
                "timestamp": datetime.fromtimestamp(bucket, UTC).isoformat(),
                "calls": len(bucket_events),
                "calls_per_minute": round(len(bucket_events) * 60 / bucket_seconds, 2),
                "errors": sum(item.status == "error" for item in bucket_events),
                "p50_latency_ms": round(_percentile(latencies, 0.50), 2)
                if latencies
                else None,
                "p95_latency_ms": round(_percentile(latencies, 0.95), 2)
                if latencies
                else None,
                "cpu_percent": previous_resource.cpu_percent if previous_resource else None,
                "peak_memory_mb": previous_resource.peak_memory_mb
                if previous_resource
                else None,
            }
        )
    return output


def _tool_usage(events: list[QueryEvent]) -> list[dict[str, object]]:
    counts = Counter(item.tool for item in events)
    total = len(events)
    rows: list[dict[str, object]] = []
    for tool, count in counts.most_common():
        tool_events = [item for item in events if item.tool == tool]
        latencies = [item.duration_ms for item in tool_events]
        errors = sum(item.status == "error" for item in tool_events)
        rows.append(
            {
                "name": tool,
                "calls": count,
                "share": round(count / total * 100, 2) if total else 0.0,
                "p95_latency_ms": round(_percentile(latencies, 0.95), 2),
                "error_rate": round(errors / count * 100, 2),
            }
        )
    return rows


def _alerts(
    status: StatusResponse,
    summary: dict[str, float | int],
) -> list[dict[str, object]]:
    alerts: list[dict[str, object]] = []
    if not status.ready:
        unavailable = [item.corpus_id for item in status.corpora if item.state != "ready"]
        alerts.append(
            {
                "id": "corpus-readiness",
                "severity": "error",
                "title": "Corpus readiness degraded",
                "message": f"Check {', '.join(unavailable)} before relying on semantic retrieval.",
            }
        )
    if float(summary["p95_latency_ms"]) >= 2_000:
        alerts.append(
            {
                "id": "p95-latency",
                "severity": "warning",
                "title": "High P95 latency",
                "message": f"P95 is {float(summary['p95_latency_ms']):,.0f} ms in this window.",
            }
        )
    if int(summary["error_count"]) > 0:
        alerts.append(
            {
                "id": "query-errors",
                "severity": "error" if float(summary["success_rate"]) < 95 else "warning",
                "title": "Query errors detected",
                "message": (
                    f"{int(summary['error_count'])} failed calls; success rate is "
                    f"{float(summary['success_rate']):.2f}%."
                ),
            }
        )
    return alerts


def _query_payload(event: QueryEvent) -> dict[str, object]:
    return {
        "id": event.event_id,
        "timestamp": event.timestamp.isoformat(),
        "tool": event.tool,
        "target": event.target,
        "query": event.query,
        "duration_ms": event.duration_ms,
        "status": event.status,
        "degraded": event.degraded,
        "result_count": event.result_count,
        "error_code": event.error_code,
    }


def _log_payload(event: LogEvent) -> dict[str, object]:
    return {
        "id": event.event_id,
        "timestamp": event.timestamp.isoformat(),
        "level": event.level,
        "source": event.source,
        "message": event.message,
        "target": event.target,
        "duration_ms": event.duration_ms,
        "details": event.details,
    }


def _query_preview(arguments: dict[str, Any]) -> str:
    for key in ("query", "uri", "root_uri"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return _truncate(value.strip(), 180)
    from_corpus = arguments.get("from_corpus")
    to_corpus = arguments.get("to_corpus")
    if isinstance(from_corpus, str) and isinstance(to_corpus, str):
        return _truncate(f"Compare {from_corpus} to {to_corpus}", 180)
    target = arguments.get("target")
    if isinstance(target, str) and target.strip():
        return f"Status for {target.strip()}"
    return "Service status"


def _result_count(result: dict[str, Any] | None) -> int:
    if result is None:
        return 0
    for key in ("hits", "sources", "definitions", "changes", "corpora"):
        value = result.get(key)
        if isinstance(value, list):
            return len(value)
    nodes = result.get("nodes")
    edges = result.get("edges")
    if isinstance(nodes, list) and isinstance(edges, list):
        return len(nodes) + len(edges)
    return 1


def _log_message(event: QueryEvent) -> str:
    if event.status == "error":
        return f"{event.tool} failed: {event.error_message or 'unknown error'}"
    qualifier = " with degraded retrieval" if event.degraded else ""
    return f"{event.tool} completed{qualifier} with {event.result_count} results"


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[rank]


def _percent_change(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return round((current - previous) / previous * 100, 2)


def _error_code(error: Exception) -> str:
    name = type(error).__name__.removesuffix("Error")
    output: list[str] = []
    for index, character in enumerate(name):
        if character.isupper() and index:
            output.append("_")
        output.append(character.lower())
    return "".join(output) or "internal"


def _safe_error_message(error: Exception) -> str:
    return _truncate(str(error).replace("\n", " ").strip() or "Internal tool error", 240)


def _truncate(value: str, length: int) -> str:
    if len(value) <= length:
        return value
    return f"{value[: length - 1].rstrip()}…"


def _peak_memory_mb() -> float:
    raw = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if sys.platform == "darwin":
        return raw / 1024 / 1024
    return raw / 1024


def default_target(arguments: dict[str, Any], fallback: str) -> str:
    """Resolve the target label without invoking or scanning the corpus."""

    value = arguments.get("target")
    if isinstance(value, str) and value.strip():
        return value.strip()
    from_corpus = arguments.get("from_corpus")
    if isinstance(from_corpus, str) and from_corpus.strip():
        return from_corpus.strip()
    return fallback


def monotonic_duration_ms(started: float) -> float:
    return max(0.0, (time.perf_counter() - started) * 1_000)


def process_identity() -> dict[str, object]:
    return {"pid": os.getpid(), "python": sys.version.split()[0]}
