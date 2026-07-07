"""Bounded source-read policy enforcement."""

from __future__ import annotations

import fnmatch
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from codalith.config import load_config
from codalith.corpus.uri_resolver import ResolvedURI
from codalith.errors import SourcePolicyError


@dataclass(frozen=True, slots=True)
class SensitivePattern:
    pattern: str
    required_scope: str


@dataclass(frozen=True, slots=True)
class SourcePolicy:
    default_max_lines: int = 200
    hard_max_lines: int = 500
    max_source_reads_per_10min: int = 100
    max_total_lines_per_10min: int = 10000
    max_distinct_paths_per_10min: int = 50
    max_adjacent_reads_per_path_per_10min: int = 8
    deny_patterns: tuple[str, ...] = ()
    sensitive_patterns: tuple[SensitivePattern, ...] = ()

    @classmethod
    def from_file(cls, path: str) -> SourcePolicy:
        raw = load_config(path)
        limits = raw.get("limits", {})
        return cls(
            default_max_lines=int(limits.get("default_max_lines", 200)),
            hard_max_lines=int(limits.get("hard_max_lines", 500)),
            max_source_reads_per_10min=int(limits.get("max_source_reads_per_10min", 100)),
            max_total_lines_per_10min=int(limits.get("max_total_lines_per_10min", 10000)),
            max_distinct_paths_per_10min=int(limits.get("max_distinct_paths_per_10min", 50)),
            max_adjacent_reads_per_path_per_10min=int(
                limits.get("max_adjacent_reads_per_path_per_10min", 8)
            ),
            deny_patterns=tuple(str(item) for item in raw.get("deny_patterns", [])),
            sensitive_patterns=tuple(
                SensitivePattern(
                    pattern=str(item["pattern"]),
                    required_scope=str(item["required_scope"]),
                )
                for item in raw.get("sensitive_patterns", [])
            ),
        )

    def check(self, resolved: ResolvedURI, user_scopes: Iterable[str]) -> None:
        scopes = set(user_scopes)
        if "source:read" not in scopes:
            raise SourcePolicyError("Missing required scope: source:read")
        line_count = resolved.line_count
        if line_count is None:
            raise SourcePolicyError("Source reads must specify a bounded line range")
        if line_count < 1:
            raise SourcePolicyError(f"Source reads must cover at least one line: {line_count}")
        # default_max_lines only sizes the fallback window when a caller omits an
        # explicit range; hard_max_lines is the single enforcement cap here.
        if line_count > self.hard_max_lines:
            raise SourcePolicyError(
                f"Line range exceeds hard max of {self.hard_max_lines}: {line_count}"
            )
        rel = PurePosixPath(resolved.relative_path).as_posix()
        for pattern in self.deny_patterns:
            if _match(pattern, rel):
                raise SourcePolicyError(f"Source path denied by policy: {rel}")
        for sensitive in self.sensitive_patterns:
            if _match(sensitive.pattern, rel) and sensitive.required_scope not in scopes:
                raise SourcePolicyError(
                    f"Missing required scope for sensitive path: {sensitive.required_scope}"
                )


class SourceReadRateLimiter:
    def __init__(
        self,
        policy: SourcePolicy,
        *,
        window_seconds: float = 600.0,
        time_func: Callable[[], float] = time.monotonic,
    ) -> None:
        self.policy = policy
        self.window_seconds = window_seconds
        self.time_func = time_func
        self._events: list[tuple[float, int, str | None, int | None, int | None]] = []

    def record_read(
        self,
        *,
        line_count: int,
        path: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> None:
        if line_count < 1:
            raise SourcePolicyError(f"Source reads must cover at least one line: {line_count}")
        now = float(self.time_func())
        cutoff = now - self.window_seconds
        self._events = [
            (timestamp, lines, event_path, event_start, event_end)
            for timestamp, lines, event_path, event_start, event_end in self._events
            if timestamp >= cutoff
        ]
        read_count = len(self._events)
        total_lines = sum(lines for _, lines, _, _, _ in self._events)
        if read_count + 1 > self.policy.max_source_reads_per_10min:
            raise SourcePolicyError(
                f"Source read rate limit exceeded: {read_count + 1} > "
                f"{self.policy.max_source_reads_per_10min} per 10 minutes"
            )
        if total_lines + line_count > self.policy.max_total_lines_per_10min:
            raise SourcePolicyError(
                f"Source read line budget exceeded: {total_lines + line_count} > "
                f"{self.policy.max_total_lines_per_10min} per 10 minutes"
            )
        if path is not None:
            paths = {event_path for _, _, event_path, _, _ in self._events if event_path}
            if path not in paths and len(paths) + 1 > self.policy.max_distinct_paths_per_10min:
                raise SourcePolicyError(
                    "Potential bulk export detected: distinct source path budget exceeded"
                )
            same_path_reads = [
                (event_start, event_end)
                for _, _, event_path, event_start, event_end in self._events
                if event_path == path
            ]
            touches_existing = any(
                _ranges_touch_or_overlap(event_start, event_end, start_line, end_line)
                for event_start, event_end in same_path_reads
            )
            adjacent_reads = sum(
                1
                for index, (event_start, event_end) in enumerate(same_path_reads)
                if any(
                    index != other_index
                    and _ranges_touch_or_overlap(event_start, event_end, other_start, other_end)
                    for other_index, (other_start, other_end) in enumerate(same_path_reads)
                )
            )
            if touches_existing and adjacent_reads + 1 > self.policy.max_adjacent_reads_per_path_per_10min:
                raise SourcePolicyError(
                    "Potential bulk export detected: repeated adjacent reads for one source path"
                )
        self._events.append((now, line_count, path, start_line, end_line))


def _match(pattern: str, path: str) -> bool:
    parent_pattern = pattern[:-3] if pattern.endswith("/**") else pattern
    return fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(path, parent_pattern)


def _ranges_touch_or_overlap(
    left_start: int | None,
    left_end: int | None,
    right_start: int | None,
    right_end: int | None,
) -> bool:
    if left_start is None or left_end is None or right_start is None or right_end is None:
        return False
    return right_start <= left_end + 1 and left_start <= right_end + 1
