"""Run independent datasets through an actual MCP Streamable HTTP endpoint."""

from __future__ import annotations

import json
import math
import statistics
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from codalith.benchmarks.models import BenchmarkCase, BenchmarkReport, BenchmarkRow


def load_dataset(path: str | Path) -> list[BenchmarkCase]:
    dataset_path = Path(path)
    cases: list[BenchmarkCase] = []
    seen: set[str] = set()
    try:
        lines = dataset_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"Cannot read benchmark dataset {dataset_path}: {exc}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            case = BenchmarkCase.model_validate_json(line)
        except Exception as exc:
            raise ValueError(
                f"Invalid benchmark row {dataset_path}:{line_number}: {exc}"
            ) from exc
        if case.id in seen:
            raise ValueError(f"Duplicate benchmark id: {case.id}")
        if case.negative and (case.expected_files or case.expected_symbols):
            raise ValueError(f"Negative benchmark {case.id} cannot declare expected results")
        if not case.negative and not (case.expected_files or case.expected_symbols):
            raise ValueError(f"Benchmark {case.id} has no ground truth")
        seen.add(case.id)
        cases.append(case)
    if not cases:
        raise ValueError(f"Benchmark dataset is empty: {dataset_path}")
    return cases


def run_mcp_benchmark(
    *,
    endpoint: str,
    dataset_path: str | Path,
    label: str,
    require_ready: bool = True,
) -> BenchmarkReport:
    cases = load_dataset(dataset_path)
    return anyio.run(_run_mcp_benchmark, endpoint, cases, label, require_ready)


async def _run_mcp_benchmark(
    endpoint: str,
    cases: list[BenchmarkCase],
    label: str,
    require_ready: bool,
) -> BenchmarkReport:
    rows: list[BenchmarkRow] = []
    async with streamable_http_client(endpoint) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            if require_ready:
                status = await _call(session, "codalith_status", {})
                if not status.get("ready"):
                    raise RuntimeError(f"Codalith endpoint is not ready: {status}")
            for case in cases:
                started = time.perf_counter()
                try:
                    payload = await _call(
                        session,
                        "codalith_search",
                        {
                            "query": case.query,
                            "target": case.target,
                            "strategy": case.strategy,
                            "limit": 10,
                        },
                    )
                    latency = (time.perf_counter() - started) * 1000
                    hits = payload.get("hits", [])
                    if not isinstance(hits, list):
                        raise RuntimeError("codalith_search returned invalid hits")
                    citation_valid = await _validate_citations(session, hits[:5])
                    rows.append(_score_case(case, payload, hits, latency, citation_valid))
                except Exception as exc:
                    rows.append(
                        BenchmarkRow(
                            id=case.id,
                            latency_ms=(time.perf_counter() - started) * 1000,
                            file_recall_at_5=None,
                            reciprocal_rank=None,
                            ndcg_at_10=None,
                            symbol_recall_at_5=None,
                            citation_valid=False,
                            degraded=True,
                            negative_passed=False if case.negative else None,
                            returned_files=[],
                            returned_symbols=[],
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    )
    return _aggregate(label, endpoint, rows)


async def _call(
    session: ClientSession,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    cleaned = {key: value for key, value in arguments.items() if value is not None}
    result = await session.call_tool(name, cleaned)
    if result.isError:
        text = result.content[0].text if result.content else "tool error"  # type: ignore[union-attr]
        raise RuntimeError(text)
    payload = result.structuredContent
    if not isinstance(payload, dict):
        raise RuntimeError(f"{name} returned no structured content")
    return payload


async def _validate_citations(session: ClientSession, hits: list[object]) -> bool:
    for raw in hits:
        if not isinstance(raw, dict) or not isinstance(raw.get("uri"), str):
            return False
        payload = await _call(session, "codalith_read", {"uri": raw["uri"]})
        if payload.get("uri") != raw["uri"] or payload.get("stale"):
            return False
        if not isinstance(payload.get("sha256"), str):
            return False
    return True


def _score_case(
    case: BenchmarkCase,
    payload: dict[str, Any],
    hits: list[object],
    latency_ms: float,
    citation_valid: bool,
) -> BenchmarkRow:
    files = _unique_ranked([
        raw["path"]
        for raw in hits
        if isinstance(raw, dict) and isinstance(raw.get("path"), str)
    ])
    symbols = _unique_ranked([
        raw["symbol"]
        for raw in hits
        if isinstance(raw, dict) and isinstance(raw.get("symbol"), str)
    ])
    if case.negative:
        negative_passed = not hits
        file_recall = reciprocal_rank = ndcg = symbol_recall = None
    else:
        negative_passed = None
        file_recall = _recall(files[:5], case.expected_files)
        reciprocal_rank = _reciprocal_rank(files, case.expected_files)
        ndcg = _ndcg(files[:10], case.expected_files)
        symbol_recall = _symbol_recall(symbols[:5], case.expected_symbols)
    return BenchmarkRow(
        id=case.id,
        latency_ms=latency_ms,
        file_recall_at_5=file_recall,
        reciprocal_rank=reciprocal_rank,
        ndcg_at_10=ndcg,
        symbol_recall_at_5=symbol_recall,
        citation_valid=citation_valid,
        degraded=bool(payload.get("degraded")),
        negative_passed=negative_passed,
        returned_files=files,
        returned_symbols=symbols,
    )


def _aggregate(label: str, endpoint: str, rows: list[BenchmarkRow]) -> BenchmarkReport:
    latencies = [row.latency_ms for row in rows]
    return BenchmarkReport(
        label=label,
        endpoint=endpoint,
        count=len(rows),
        file_recall_at_5=_average(row.file_recall_at_5 for row in rows),
        symbol_recall_at_5=_average(row.symbol_recall_at_5 for row in rows),
        mrr=_average(row.reciprocal_rank for row in rows),
        ndcg_at_10=_average(row.ndcg_at_10 for row in rows),
        citation_valid_rate=sum(row.citation_valid for row in rows) / len(rows),
        degraded_rate=sum(row.degraded for row in rows) / len(rows),
        negative_pass_rate=_average(
            1.0 if row.negative_passed else 0.0
            for row in rows
            if row.negative_passed is not None
        ),
        latency_p50_ms=statistics.median(latencies),
        latency_p95_ms=_percentile(latencies, 0.95),
        errors=sum(row.error is not None for row in rows),
        rows=rows,
    )


def acceptance_failures(
    report: BenchmarkReport,
    *,
    min_file_recall: float = 0.85,
    min_symbol_recall: float = 0.80,
    min_mrr: float = 0.65,
    min_ndcg: float = 0.75,
    max_p95_ms: float = 2_000,
) -> list[str]:
    failures: list[str] = []
    checks = (
        ("file_recall@5", report.file_recall_at_5, min_file_recall),
        ("symbol_recall@5", report.symbol_recall_at_5, min_symbol_recall),
        ("MRR", report.mrr, min_mrr),
        ("nDCG@10", report.ndcg_at_10, min_ndcg),
    )
    for label, value, minimum in checks:
        if value is None:
            failures.append(f"{label} is unavailable")
        elif value < minimum:
            failures.append(f"{label} {value:.3f} < {minimum:.3f}")
    if report.citation_valid_rate != 1.0:
        failures.append(f"citation_valid_rate {report.citation_valid_rate:.3f} != 1.000")
    if report.degraded_rate != 0.0:
        failures.append(f"degraded_rate {report.degraded_rate:.3f} != 0.000")
    if report.negative_pass_rate is not None and report.negative_pass_rate != 1.0:
        failures.append(f"negative_pass_rate {report.negative_pass_rate:.3f} != 1.000")
    if report.latency_p95_ms > max_p95_ms:
        failures.append(f"latency_p95_ms {report.latency_p95_ms:.1f} > {max_p95_ms:.1f}")
    if report.errors:
        failures.append(f"errors {report.errors} != 0")
    return failures


def write_report(report: BenchmarkReport, path: str | Path) -> None:
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _recall(ranked: list[str], relevant: list[str]) -> float | None:
    if not relevant:
        return None
    return len(set(ranked) & set(relevant)) / len(set(relevant))


def _reciprocal_rank(ranked: list[str], relevant: list[str]) -> float | None:
    if not relevant:
        return None
    wanted = set(relevant)
    for rank, item in enumerate(ranked, start=1):
        if item in wanted:
            return 1.0 / rank
    return 0.0


def _ndcg(ranked: list[str], relevant: list[str]) -> float | None:
    if not relevant:
        return None
    wanted = set(relevant)
    dcg = sum(1.0 / math.log2(rank + 1) for rank, item in enumerate(ranked, 1) if item in wanted)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(len(wanted), 10) + 1))
    return dcg / ideal if ideal else 0.0


def _symbol_recall(ranked: list[str], relevant: list[str]) -> float | None:
    if not relevant:
        return None
    normalized = {item.casefold() for item in ranked}
    found = 0
    for expected in set(relevant):
        folded = expected.casefold()
        if any(item == folded or item.endswith(f"::{folded}") or item.endswith(f".{folded}") for item in normalized):
            found += 1
    return found / len(set(relevant))


def _average(values: Iterable[float | None]) -> float | None:
    filtered = [value for value in values if value is not None]
    return sum(filtered) / len(filtered) if filtered else None


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


def _unique_ranked(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
