"""Shared data types used across chunking, storage, and retrieval."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Chunk:
    """A unit of indexed code: a function, class, or method, or a line window."""

    text: str
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive
    language: str
    symbol: str | None = None  # qualified name, e.g. "ClassName.method"
    kind: str = "window"  # "function" | "class" | "method" | "window"


@dataclass(slots=True)
class SearchHit:
    """A retrieval result, hydrated from the store."""

    chunk_id: int
    path: str
    symbol: str | None
    kind: str
    language: str
    start_line: int
    end_line: int
    text: str
    score: float  # fused (RRF) score, a relative ranking signal
    similarity: float  # raw cosine similarity in [0, 1]


@dataclass(frozen=True, slots=True)
class IndexStats:
    total_files: int
    total_chunks: int
