"""Source-access policy shared by indexing and reads."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from codalith.config import load_toml
from codalith.corpus.globs import matches_path
from codalith.errors import ConfigurationError, SourcePolicyError


@dataclass(frozen=True, slots=True)
class SourcePolicy:
    default_max_lines: int
    hard_max_lines: int
    max_file_bytes: int
    deny_globs: tuple[str, ...]

    @classmethod
    def from_file(cls, path: str | Path) -> SourcePolicy:
        config_path, payload = load_toml(path)
        default_max = _positive_int(payload.get("default_max_lines"), "default_max_lines")
        hard_max = _positive_int(payload.get("hard_max_lines"), "hard_max_lines")
        max_file_bytes = _positive_int(payload.get("max_file_bytes"), "max_file_bytes")
        if default_max > hard_max:
            raise ConfigurationError(
                f"{config_path} default_max_lines cannot exceed hard_max_lines"
            )
        raw_globs = payload.get("deny_globs")
        if not isinstance(raw_globs, list) or not raw_globs:
            raise ConfigurationError(f"{config_path} deny_globs must be a non-empty array")
        globs: list[str] = []
        for raw in raw_globs:
            if not isinstance(raw, str) or not raw.strip():
                raise ConfigurationError(f"{config_path} deny_globs contains an invalid item")
            normalized = raw.replace("\\", "/").strip().lstrip("/")
            if normalized in {"", ".", ".."}:
                raise ConfigurationError(f"{config_path} contains an unsafe deny glob")
            globs.append(normalized)
        return cls(default_max, hard_max, max_file_bytes, tuple(globs))

    def normalize_path(self, path: str) -> str:
        normalized = path.replace("\\", "/").strip()
        if not normalized or normalized.startswith("/"):
            raise SourcePolicyError("Source path must be a non-empty relative path")
        candidate = PurePosixPath(normalized)
        if any(part in {"", ".", ".."} for part in candidate.parts):
            raise SourcePolicyError(f"Unsafe source path: {path}")
        canonical = candidate.as_posix()
        if self.is_denied(canonical):
            raise SourcePolicyError(f"Source policy denies path: {canonical}")
        return canonical

    def is_denied(self, path: str) -> bool:
        canonical = path.replace("\\", "/").lstrip("/")
        for pattern in self.deny_globs:
            if matches_path(canonical, pattern):
                return True
        return False

    def validate_range(self, start_line: int, end_line: int | None) -> tuple[int, int]:
        if start_line < 1:
            raise SourcePolicyError("start_line must be at least 1")
        effective_end = end_line or (start_line + self.default_max_lines - 1)
        if effective_end < start_line:
            raise SourcePolicyError("end_line cannot be before start_line")
        if effective_end - start_line + 1 > self.hard_max_lines:
            raise SourcePolicyError(
                f"Requested range exceeds hard limit of {self.hard_max_lines} lines"
            )
        return start_line, effective_end


def _positive_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigurationError(f"{name} must be a positive integer")
    return value
