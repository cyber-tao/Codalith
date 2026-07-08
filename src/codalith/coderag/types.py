"""Retrieval result types shared across the retrieval, compiler, and gateway layers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    source: str
    corpus_id: str
    uri: str
    path: str
    start_line: int
    end_line: int
    title: str
    snippet: str
    score: float
    kind: str = "window"
    language: str = "text"
    symbol: str | None = None
    module: str | None = None
    reason: str = "CodeRAG retrieval hit."
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def language_for_path(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return {
        ".h": "cpp",
        ".hpp": "cpp",
        ".inl": "cpp",
        ".cpp": "cpp",
        ".c": "c",
        ".cs": "csharp",
        ".py": "python",
        ".md": "markdown",
        ".json": "json",
    }.get(suffix, "text")


def module_from_path(path: str, module_roots: tuple[str, ...]) -> str | None:
    """Module name hinted by the path segment following a configured module root."""
    if not module_roots:
        return None
    parts = path.split("/")
    for root in module_roots:
        if root in parts:
            index = parts.index(root)
            if index + 1 < len(parts):
                return parts[index + 1]
    return None
