"""Literal text search constrained to one structural generation."""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import tempfile
import threading
from bisect import bisect_left
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from codalith.corpus.registry import Corpus
from codalith.errors import RetrievalError
from codalith.indexing.structure.store import StructureIndex

_LINES_PER_FILE = 3
_TIMEOUT_SECONDS = 30.0
_MAX_ERROR_BYTES = 8_192
_MAX_SNIPPET_CHARACTERS = 2_000


@dataclass(frozen=True, slots=True)
class TextHit:
    path: str
    line: int
    text: str


class TextMatchCollector:
    def __init__(self, query: str, limit: int) -> None:
        self.query = query
        self.limit = limit
        self._keys: list[tuple[bool, int, str]] = []
        self._files: list[list[TextHit]] = []

    def add(self, path: str, lines: list[tuple[int, str]]) -> None:
        if not lines or self.limit <= 0:
            return
        selected = lines[:_LINES_PER_FILE]
        key = (not any(self.query in text for _, text in selected), len(path), path)
        position = bisect_left(self._keys, key)
        if position >= self.limit:
            return
        self._keys.insert(position, key)
        self._files.insert(
            position,
            [TextHit(path, number, bounded_snippet(text)) for number, text in selected],
        )
        if len(self._keys) > self.limit:
            self._keys.pop()
            self._files.pop()

    def hits(self) -> list[TextHit]:
        hits: list[TextHit] = []
        for lines in self._files:
            hits.extend(lines[: self.limit - len(hits)])
            if len(hits) == self.limit:
                break
        return hits

    def complete_in_path_order(self) -> bool:
        return sum(
            len(lines) for key, lines in zip(self._keys, self._files, strict=True) if not key[0]
        ) >= self.limit


def first_matching_lines(text: str, query: str) -> list[tuple[int, str]]:
    needle = query.casefold()
    matches: list[tuple[int, str]] = []
    for number, line in enumerate(text.split("\n"), 1):
        if needle in line.casefold():
            matches.append((number, line.rstrip("\r")))
            if len(matches) == _LINES_PER_FILE:
                break
    return matches


def bounded_snippet(value: str) -> str:
    normalized = value.strip()
    if len(normalized) <= _MAX_SNIPPET_CHARACTERS:
        return normalized
    return normalized[: _MAX_SNIPPET_CHARACTERS - 1] + "…"


def search_text(
    corpus: Corpus,
    index: StructureIndex,
    query: str,
    *,
    limit: int,
    max_file_bytes: int,
) -> list[TextHit]:
    """Find case-insensitive literal occurrences of ``query`` in indexed files only."""

    if not query or limit <= 0:
        return []
    if query.isascii() and "\n" not in query and "\r" not in query and shutil.which("rg"):
        matches = _ripgrep_matches(corpus, index, query, max_file_bytes=max_file_bytes)
    else:
        matches = _python_matches(corpus, index, query, max_file_bytes=max_file_bytes)
    collector = TextMatchCollector(query, limit)
    for path, lines in matches:
        collector.add(path, lines)
    return collector.hits()


def _ripgrep_matches(
    corpus: Corpus,
    index: StructureIndex,
    query: str,
    *,
    max_file_bytes: int,
) -> Iterator[tuple[str, list[tuple[int, str]]]]:
    command = [
        "rg",
        "--json",
        "--line-number",
        "--no-config",
        "--no-ignore",
        "--hidden",
        "--text",
        "--ignore-case",
        "--glob-case-insensitive",
        "--threads",
        "1",
        "--max-filesize",
        str(max_file_bytes),
    ]
    for extension in corpus.include_extensions:
        command.extend(("--glob", f"*{extension}"))
    for pattern in corpus.exclude_globs:
        command.extend(("--glob", f"!{pattern}"))
    # Full casefold can expand Unicode characters that ripgrep's simple folding cannot match.
    command.extend(("--regexp", re.escape(query), "--regexp", r"[^\x00-\x7F]", "--", "."))
    needle = query.casefold()
    with tempfile.TemporaryFile() as errors:
        try:
            process = subprocess.Popen(
                command, cwd=corpus.source_root, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=errors,
            )
        except OSError as exc:
            raise RetrievalError(f"Cannot start ripgrep text search: {exc}") from exc
        assert process.stdout is not None
        timed_out = threading.Event()

        def terminate() -> None:
            if process.poll() is None:
                timed_out.set()
                try:
                    process.kill()
                except OSError:
                    pass

        timer = threading.Timer(_TIMEOUT_SECONDS, terminate)
        timer.daemon = True
        timer.start()
        current_path: str | None = None
        current_indexed = False
        lines: list[tuple[int, str]] = []
        try:
            for raw in process.stdout:
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if event.get("type") != "match":
                    continue
                data = event.get("data")
                if not isinstance(data, dict) or not isinstance(data.get("line_number"), int):
                    continue
                printed_path = _event_text(data.get("path"))
                text = _event_text(data.get("lines"))
                if printed_path is None or text is None:
                    continue
                path = PurePath(printed_path).as_posix()
                if path != current_path:
                    if current_path is not None and lines:
                        yield current_path, lines
                    current_path = path
                    current_indexed = index.get_file(path) is not None
                    lines = []
                if (
                    current_indexed and len(lines) < _LINES_PER_FILE
                    and needle in text.casefold()
                ):
                    lines.append((data["line_number"], text.rstrip("\r\n")))
            process.wait()
            if timed_out.is_set():
                raise RetrievalError(f"Text search timed out for {corpus.corpus_id}")
            if process.returncode not in {0, 1}:
                errors.seek(0)
                message = errors.read(_MAX_ERROR_BYTES).decode("utf-8", errors="replace").strip()
                raise RetrievalError(message or "ripgrep text search failed")
            if current_path is not None and lines:
                yield current_path, lines
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()


def _python_matches(
    corpus: Corpus,
    index: StructureIndex,
    query: str,
    *,
    max_file_bytes: int,
) -> Iterator[tuple[str, list[tuple[int, str]]]]:
    root = corpus.source_root.resolve()
    for record in index.list_files():
        path = (root / record.path).resolve()
        if root not in path.parents:
            continue
        try:
            if path.stat().st_size > max_file_bytes:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if len(data) > max_file_bytes:
            continue
        lines = first_matching_lines(data.decode("utf-8", errors="replace"), query)
        if lines:
            yield record.path, lines


def _event_text(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    text = payload.get("text")
    if isinstance(text, str):
        return text
    encoded = payload.get("bytes")
    if isinstance(encoded, str):
        return base64.b64decode(encoded).decode("utf-8", errors="replace")
    return None
