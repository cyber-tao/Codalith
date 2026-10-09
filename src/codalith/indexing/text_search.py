"""Literal text search constrained to one structural generation."""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from codalith.corpus.registry import Corpus
from codalith.errors import RetrievalError
from codalith.indexing.structure.store import StructureIndex

_LINES_PER_FILE = 3
_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class TextHit:
    path: str
    line: int
    text: str


def search_text(
    corpus: Corpus,
    index: StructureIndex,
    query: str,
    *,
    limit: int,
    max_file_bytes: int,
) -> list[TextHit]:
    """Find case-insensitive literal occurrences of ``query`` in indexed files only."""

    if not query:
        return []
    if shutil.which("rg") is not None:
        matches = _ripgrep_matches(corpus, index, query, max_file_bytes=max_file_bytes)
    else:
        matches = _python_matches(corpus, index, query, max_file_bytes=max_file_bytes)
    ranked = sorted(
        matches.items(),
        key=lambda item: (
            not any(query in text for _, text in item[1]),
            len(item[0]),
            item[0],
        ),
    )
    hits: list[TextHit] = []
    for path, lines in ranked:
        for line, text in lines:
            hits.append(TextHit(path, line, text))
            if len(hits) >= limit:
                return hits
    return hits


def _ripgrep_matches(
    corpus: Corpus,
    index: StructureIndex,
    query: str,
    *,
    max_file_bytes: int,
) -> dict[str, list[tuple[int, str]]]:
    command = [
        "rg",
        "--json",
        "--line-number",
        "--no-config",
        "--no-ignore",
        "--hidden",
        "--fixed-strings",
        "--ignore-case",
        "--glob-case-insensitive",
        "--max-count",
        str(_LINES_PER_FILE),
        "--max-filesize",
        str(max_file_bytes),
    ]
    for extension in corpus.include_extensions:
        command.extend(("--glob", f"*{extension}"))
    for pattern in corpus.exclude_globs:
        command.extend(("--glob", f"!{pattern}"))
    command.extend(("--regexp", query, "--", "."))
    try:
        completed = subprocess.run(  # noqa: S603 - fixed executable and argv-only input
            command,
            cwd=corpus.source_root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RetrievalError(f"Text search timed out for {corpus.corpus_id}") from exc
    except OSError as exc:
        raise RetrievalError(f"Cannot start ripgrep text search: {exc}") from exc
    if completed.returncode not in {0, 1}:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RetrievalError(message or "ripgrep text search failed")
    matches: dict[str, list[tuple[int, str]]] = {}
    membership: dict[str, bool] = {}
    for raw in completed.stdout.splitlines():
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
        if path not in membership:
            membership[path] = index.get_file(path) is not None
        if membership[path]:
            matches.setdefault(path, []).append((data["line_number"], text.rstrip("\r\n")))
    return matches


def _python_matches(
    corpus: Corpus,
    index: StructureIndex,
    query: str,
    *,
    max_file_bytes: int,
) -> dict[str, list[tuple[int, str]]]:
    needle = query.casefold()
    root = corpus.source_root.resolve()
    matches: dict[str, list[tuple[int, str]]] = {}
    for record in index.list_files():
        path = (root / record.path).resolve()
        if root not in path.parents:
            continue
        try:
            if path.stat().st_size > max_file_bytes:
                continue
            text = path.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            continue
        lines: list[tuple[int, str]] = []
        for number, line in enumerate(text.split("\n"), start=1):
            if needle in line.casefold():
                lines.append((number, line.rstrip("\r")))
                if len(lines) == _LINES_PER_FILE:
                    break
        if lines:
            matches[record.path] = lines
    return matches


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
