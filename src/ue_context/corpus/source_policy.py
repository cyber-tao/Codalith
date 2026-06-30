"""Bounded source-read policy enforcement."""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from ue_context.config import load_config
from ue_context.corpus.uri_resolver import ResolvedURI
from ue_context.errors import SourcePolicyError


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
        if line_count > self.hard_max_lines:
            raise SourcePolicyError(
                f"Line range exceeds hard max of {self.hard_max_lines}: {line_count}"
            )
        if line_count > self.default_max_lines:
            raise SourcePolicyError(
                f"Line range exceeds default max of {self.default_max_lines}: {line_count}"
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "limits": {
                "default_max_lines": self.default_max_lines,
                "hard_max_lines": self.hard_max_lines,
                "max_source_reads_per_10min": self.max_source_reads_per_10min,
                "max_total_lines_per_10min": self.max_total_lines_per_10min,
            },
            "deny_patterns": list(self.deny_patterns),
            "sensitive_patterns": [
                {"pattern": item.pattern, "required_scope": item.required_scope}
                for item in self.sensitive_patterns
            ],
        }


def _match(pattern: str, path: str) -> bool:
    parent_pattern = pattern[:-3] if pattern.endswith("/**") else pattern
    return fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(path, parent_pattern)
