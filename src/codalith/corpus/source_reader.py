"""Neutral source file reads for configured corpora."""

from __future__ import annotations

from pathlib import Path

from codalith.corpus.registry import Corpus, CorpusRegistry
from codalith.errors import SourceReadError


class SourceReader:
    """Read source files from source roots, falling back to indexed roots.

    Retrieval/indexing can use a reduced indexed root. Source reads should use
    the canonical source root first so evidence URIs still resolve when the
    indexed root only contains a searchable subset.
    """

    def __init__(self, registry: CorpusRegistry) -> None:
        self.registry = registry

    def read_source(
        self,
        corpus_id: str,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        lines = self.read_lines(corpus_id, path)
        if start_line is None and end_line is None:
            return "\n".join(lines)
        start = max(1, start_line or 1)
        end = min(len(lines), end_line or len(lines))
        if end < start:
            return ""
        return "\n".join(lines[start - 1 : end])

    def read_lines(self, corpus_id: str, path: str) -> list[str]:
        resolved = self.resolve_path(corpus_id, path)
        return resolved.read_text(encoding="utf-8", errors="replace").splitlines()

    def resolve_path(self, corpus_id: str, path: str) -> Path:
        corpus = self.registry.get_corpus(corpus_id)
        relative = _clean_relative_path(path)
        roots = _candidate_roots(corpus)
        for root in roots:
            candidate = (root / relative).resolve()
            _ensure_inside(root, candidate, relative)
            if candidate.is_file():
                return candidate
        raise SourceReadError(f"Source file does not exist: {path}")


def _candidate_roots(corpus: Corpus) -> list[Path]:
    roots: list[Path] = []
    for root in (corpus.source_root, corpus.indexed_root):
        resolved = root.resolve()
        if resolved not in roots:
            roots.append(resolved)
    return roots


def _clean_relative_path(path: str) -> Path:
    normalized = path.replace("\\", "/").lstrip("/")
    parts = Path(normalized).parts
    if not normalized or any(part == ".." for part in parts):
        raise SourceReadError(f"Invalid source path: {path}")
    return Path(normalized)


def _ensure_inside(root: Path, candidate: Path, relative: Path) -> None:
    if root not in candidate.parents and candidate != root:
        raise SourceReadError(f"Path escapes corpus root: {relative.as_posix()}")
