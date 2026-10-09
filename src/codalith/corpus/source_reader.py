"""Bounded source reads tied to one index generation."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Protocol

from codalith.corpus.registry import Corpus
from codalith.corpus.source_policy import SourcePolicy
from codalith.corpus.uris import source_uri
from codalith.errors import SourceReadError


class IndexedFile(Protocol):
    @property
    def path(self) -> str: ...

    @property
    def sha256(self) -> str: ...

    @property
    def size_bytes(self) -> int: ...


class FileCatalog(Protocol):
    @property
    def generation_id(self) -> str: ...

    def get_file(self, path: str) -> IndexedFile | None: ...


@dataclass(frozen=True, slots=True)
class SourceSlice:
    corpus_id: str
    revision: str
    generation_id: str
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

    def ending_at(self, end_line: int) -> SourceSlice:
        """Shorten the slice in memory; hashes still describe the whole file."""

        if not self.start_line <= end_line <= self.end_line:
            raise ValueError("end_line must fall inside the slice")
        if end_line == self.end_line:
            return self
        text = "\n".join(self.text.split("\n")[: end_line - self.start_line + 1])
        return replace(
            self,
            uri=source_uri(
                self.corpus_id,
                self.path,
                start_line=self.start_line,
                end_line=end_line,
            ),
            end_line=end_line,
            text=text,
            truncated=True,
            decode_replacements=text.count("\ufffd"),
        )


class SourceReader:
    def __init__(self, policy: SourcePolicy) -> None:
        self.policy = policy

    def read(
        self,
        corpus: Corpus,
        catalog: FileCatalog,
        path: str,
        *,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> SourceSlice:
        canonical = self.policy.normalize_path(path)
        explicit_end = end_line is not None
        start, requested_end = self.policy.validate_range(start_line, end_line)
        indexed = catalog.get_file(canonical)
        if indexed is None:
            raise SourceReadError(
                f"Path is not present in generation {catalog.generation_id}: {canonical}"
            )
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
        last_line = b""
        try:
            with source_path.open("rb") as handle:
                for line_number, raw_line in enumerate(handle, start=1):
                    total_lines = line_number
                    last_line = raw_line
                    digest.update(raw_line)
                    if start <= line_number <= requested_end:
                        decoded = raw_line.decode("utf-8", errors="replace")
                        replacements += decoded.count("\ufffd")
                        if decoded.endswith("\n"):
                            decoded = decoded[:-1]
                            if decoded.endswith("\r"):
                                decoded = decoded[:-1]
                        selected.append(decoded)
        except OSError as exc:
            raise SourceReadError(f"Cannot read source file {canonical}: {exc}") from exc
        if total_lines == 0 or last_line.endswith(b"\n"):
            total_lines += 1
        if start > total_lines:
            raise SourceReadError(
                f"start_line {start} exceeds {total_lines} lines in {canonical}"
            )
        actual_end = min(requested_end, total_lines)
        current_hash = digest.hexdigest()
        return SourceSlice(
            corpus_id=corpus.corpus_id,
            revision=corpus.revision,
            generation_id=catalog.generation_id,
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
            text="\n".join(selected),
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
