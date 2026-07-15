"""Bounded source reads tied to an active index generation."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from codalith.corpus.registry import Corpus, CorpusRegistry
from codalith.corpus.source_policy import SourcePolicy
from codalith.corpus.uris import parse_uri, source_uri
from codalith.errors import SourceReadError, URIResolutionError


class IndexedFile(Protocol):
    path: str
    sha256: str
    size_bytes: int


class FileCatalog(Protocol):
    def get_file(self, path: str) -> IndexedFile | None: ...


@dataclass(frozen=True, slots=True)
class SourceSlice:
    corpus_id: str
    revision: str
    uri: str
    path: str
    start_line: int
    end_line: int
    total_lines: int
    text: str
    sha256: str
    indexed_sha256: str
    stale: bool
    truncated: bool
    decode_replacements: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class SourceReader:
    def __init__(
        self,
        registry: CorpusRegistry,
        policy: SourcePolicy,
        catalog_factory: Callable[[Corpus], FileCatalog],
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.catalog_factory = catalog_factory

    def read_uri(self, uri: str) -> SourceSlice:
        parsed = parse_uri(uri)
        if parsed.kind != "source":
            raise URIResolutionError(f"Not a source URI: {uri}")
        return self.read(
            parsed.corpus_id,
            parsed.value,
            start_line=parsed.start_line or 1,
            end_line=parsed.end_line,
        )

    def read(
        self,
        corpus_id: str,
        path: str,
        *,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> SourceSlice:
        corpus = self.registry.get_corpus(corpus_id)
        canonical = self.policy.normalize_path(path)
        explicit_end = end_line is not None
        start, requested_end = self.policy.validate_range(start_line, end_line)
        catalog = self.catalog_factory(corpus)
        indexed = catalog.get_file(canonical)
        if indexed is None:
            raise SourceReadError(f"Path is not present in the active index: {canonical}")
        source_path = _safe_source_path(corpus.source_root, canonical)
        try:
            size = source_path.stat().st_size
        except OSError as exc:
            raise SourceReadError(f"Cannot stat source file {canonical}: {exc}") from exc
        if size > self.policy.max_file_bytes:
            raise SourceReadError(
                f"Source file exceeds {self.policy.max_file_bytes} byte limit: {canonical}"
            )
        digest = hashlib.sha256()
        selected: list[str] = []
        replacements = 0
        total_lines = 0
        try:
            with source_path.open("rb") as handle:
                for line_number, raw_line in enumerate(handle, start=1):
                    total_lines = line_number
                    digest.update(raw_line)
                    if start <= line_number <= requested_end:
                        decoded = raw_line.decode("utf-8", errors="replace")
                        replacements += decoded.count("\ufffd")
                        if decoded.endswith("\n"):
                            decoded = decoded[:-1]
                            if decoded.endswith("\r"):
                                decoded = decoded[:-1]
                        selected.append(decoded)
                if size == 0:
                    total_lines = 1
                elif _ends_with_newline(source_path):
                    total_lines += 1
        except OSError as exc:
            raise SourceReadError(f"Cannot read source file {canonical}: {exc}") from exc
        if start > total_lines:
            raise SourceReadError(
                f"start_line {start} exceeds {total_lines} lines in {canonical}"
            )
        actual_end = min(requested_end, total_lines)
        current_hash = digest.hexdigest()
        return SourceSlice(
            corpus_id=corpus.corpus_id,
            revision=corpus.revision,
            uri=source_uri(
                corpus.corpus_id,
                canonical,
                start_line=start,
                end_line=actual_end,
            ),
            path=canonical,
            start_line=start,
            end_line=actual_end,
            total_lines=total_lines,
            text="".join(selected) if len(selected) == 1 else "\n".join(selected),
            sha256=current_hash,
            indexed_sha256=indexed.sha256,
            stale=current_hash != indexed.sha256 or size != indexed.size_bytes,
            truncated=not explicit_end and requested_end < total_lines,
            decode_replacements=replacements,
        )


def _safe_source_path(root: Path, relative: str) -> Path:
    resolved_root = root.resolve()
    candidate = (resolved_root / Path(*relative.split("/"))).resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise SourceReadError(f"Source path escapes corpus root: {relative}")
    if not candidate.is_file():
        raise SourceReadError(f"Source file does not exist: {relative}")
    return candidate


def _ends_with_newline(path: Path) -> bool:
    if path.stat().st_size == 0:
        return False
    with path.open("rb") as handle:
        handle.seek(-1, 2)
        return handle.read(1) == b"\n"
