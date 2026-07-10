"""Deterministic local fallback retrieval over fixed line windows.

The index keeps an inverted posting list over line windows of the scanned
corpus files so Docker tests and policy validation do not depend on model
downloads or external services.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from codalith.coderag.types import RetrievalHit, language_for_path, module_from_path
from codalith.corpus.registry import Corpus
from codalith.corpus.uris import source_uri
from codalith.errors import CodeRAGAdapterError
from codalith.text import camel_words, tokenize

# Local fallback windows: 80-line spans starting every 70 lines (10-line overlap),
# mirroring the chunk sizing the native indexer uses for source files.
_WINDOW_STEP = 70
_WINDOW_SPAN = 80

# VCS/store internals only; corpus-specific ignores come from Corpus.index_ignore_dirs.
_BUILTIN_IGNORE_DIRS = {
    ".git",
    ".coderag",
}
# Generic plain-text formats; corpus-specific suffixes come from Corpus.index_suffixes.
_BUILTIN_TEXT_SUFFIXES = {
    ".h",
    ".hpp",
    ".inl",
    ".cpp",
    ".c",
    ".cs",
    ".py",
    ".pyi",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".toml",
    ".yaml",
    ".yml",
    ".ini",
    ".json",
    ".md",
    ".txt",
}

_RAW_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(slots=True)
class IndexedFile:
    path: str
    lines: list[str]


@dataclass(slots=True)
class _Window:
    file_index: int
    start_line: int
    end_line: int


@dataclass(slots=True)
class LocalIndex:
    """Inverted index over fixed line windows of the scanned corpus files."""

    files: list[IndexedFile]
    windows: list[_Window]
    # token -> [(window_index, term_frequency), ...] in window order.
    postings: dict[str, list[tuple[int, int]]]


def scan_corpus(
    corpus: Corpus,
    root: Path,
    subpath: str | None = None,
    *,
    max_files: int | None = None,
    max_bytes: int | None = None,
) -> list[IndexedFile]:
    resolved_root = root.resolve()
    if not resolved_root.exists():
        return []
    scan_root = (resolved_root / subpath).resolve() if subpath else resolved_root
    if resolved_root not in scan_root.parents and scan_root != resolved_root:
        raise CodeRAGAdapterError(f"Path escapes corpus root: {subpath}")
    ignore_dirs = _BUILTIN_IGNORE_DIRS | set(corpus.index_ignore_dirs)
    suffixes = _BUILTIN_TEXT_SUFFIXES | set(corpus.index_suffixes)
    files: list[IndexedFile] = []
    total_bytes = 0
    paths = [scan_root] if scan_root.is_file() else _iter_text_paths(scan_root, ignore_dirs)
    for full_path in paths:
        if not full_path.is_file() or not _matches_suffix(full_path, suffixes):
            continue
        if any(part in ignore_dirs for part in full_path.parts):
            continue
        try:
            file_bytes = full_path.stat().st_size
        except OSError:
            continue
        if max_files is not None and len(files) >= max_files:
            raise CodeRAGAdapterError(
                f"Local fallback exceeds file limit of {max_files} for {corpus.corpus_id}"
            )
        if max_bytes is not None and total_bytes + file_bytes > max_bytes:
            raise CodeRAGAdapterError(
                f"Local fallback exceeds byte limit of {max_bytes} for {corpus.corpus_id}"
            )
        try:
            text = full_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        relative = full_path.relative_to(resolved_root).as_posix()
        files.append(IndexedFile(path=relative, lines=text.splitlines()))
        total_bytes += file_bytes
    return files


def build_local_index(files: list[IndexedFile]) -> LocalIndex:
    windows: list[_Window] = []
    postings: dict[str, list[tuple[int, int]]] = {}
    for file_index, item in enumerate(files):
        path_terms = list(_index_terms(item.path))
        for start in range(1, len(item.lines) + 1, _WINDOW_STEP):
            end = min(len(item.lines), start + _WINDOW_SPAN - 1)
            window_index = len(windows)
            windows.append(_Window(file_index=file_index, start_line=start, end_line=end))
            counts: dict[str, int] = {}
            text = "\n".join(item.lines[start - 1 : end])
            for term in [*path_terms, *_index_terms(text)]:
                counts[term] = counts.get(term, 0) + 1
            for term, frequency in counts.items():
                postings.setdefault(term, []).append((window_index, frequency))
    return LocalIndex(files=files, windows=windows, postings=postings)


def search_local_index(
    index: LocalIndex,
    corpus: Corpus,
    query: str,
    top_k: int,
    path_prefix: str | None = None,
) -> list[RetrievalHit]:
    # Single-character tokens would match almost every window.
    tokens = tokenize(query, min_length=2)
    lowered_paths = [item.path.lower() for item in index.files]

    totals: dict[int, float] = {}
    for token in tokens:
        postings = index.postings.get(token)
        if not postings:
            continue
        for window_index, frequency in postings:
            window = index.windows[window_index]
            file = index.files[window.file_index]
            if path_prefix and not file.path.startswith(path_prefix):
                continue
            weight = 2.0 if token in lowered_paths[window.file_index] else 1.0
            totals[window_index] = totals.get(window_index, 0.0) + frequency * weight

    ranked = sorted(totals.items(), key=lambda item: (-item[1], item[0]))[:top_k]
    hits: list[RetrievalHit] = []
    for window_index, score in ranked:
        window = index.windows[window_index]
        file = index.files[window.file_index]
        snippet = "\n".join(file.lines[window.start_line - 1 : window.end_line])
        hits.append(
            RetrievalHit(
                source="coderag-local",
                corpus_id=corpus.corpus_id,
                uri=source_uri(corpus.corpus_id, file.path, window.start_line, window.end_line),
                path=file.path,
                start_line=window.start_line,
                end_line=window.end_line,
                title=f"{file.path}:{window.start_line}-{window.end_line}",
                snippet=snippet,
                score=score,
                kind="window",
                language=language_for_path(file.path),
                module=module_from_path(file.path, corpus.module_roots),
                reason="Local deterministic retrieval hit.",
                metadata={"local_score": score},
            )
        )
    return hits


def _iter_text_paths(scan_root: Path, ignore_dirs: set[str]) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(scan_root):
        dirnames[:] = [dirname for dirname in dirnames if dirname not in ignore_dirs]
        for filename in filenames:
            yield Path(dirpath) / filename


def _matches_suffix(path: Path, suffixes: set[str]) -> bool:
    lowered = path.name.lower()
    return any(lowered.endswith(suffix.lower()) for suffix in suffixes)


def _index_terms(raw_text: str) -> Iterator[str]:
    """Indexable terms of raw source text.

    Each identifier is indexed as its lowercase whole form plus its snake_case
    segments and CamelCase words, so a query token like "cached" still matches
    "CachedValue" without substring scans.
    """
    for raw in _RAW_TOKEN_RE.findall(raw_text):
        terms = {raw.lower()}
        for segment in raw.split("_"):
            if segment:
                terms.add(segment.lower())
                terms.update(word.lower() for word in camel_words(segment))
        for term in terms:
            if len(term) >= 2:
                yield term
