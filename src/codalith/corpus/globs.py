"""Canonical relative-path glob matching shared by source selection policies."""

from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import PurePosixPath

SOURCE_SELECTION_VERSION = 2


def matches_path(path: str, pattern: str) -> bool:
    """Match canonical paths case-insensitively across host filesystems."""

    canonical = path.casefold()
    normalized_pattern = pattern.casefold()
    if fnmatchcase(canonical, normalized_pattern):
        return True
    if normalized_pattern.startswith("**/") and fnmatchcase(
        canonical, normalized_pattern[3:]
    ):
        return True
    if "/" not in normalized_pattern:
        return any(
            fnmatchcase(part, normalized_pattern)
            for part in PurePosixPath(canonical).parts
        )
    return False
