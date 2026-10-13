from __future__ import annotations

from pathlib import Path

import pytest

from codalith.semantic.chunking import chunk_file
from codalith.semantic.chunking.base import MAX_CHUNK_BYTES, limit_chunk_bytes
from codalith.semantic.config import Config
from codalith.semantic.types import Chunk


@pytest.mark.parametrize(
    "text",
    [
        "ordinary code\nwith two lines",
        "a" * MAX_CHUNK_BYTES,
        "é" * (MAX_CHUNK_BYTES // 2),
        "🙂" * (MAX_CHUNK_BYTES // 4),
    ],
    ids=["ordinary", "ascii_boundary", "two_byte_boundary", "four_byte_boundary"],
)
def test_chunks_at_or_below_the_byte_limit_keep_their_original_behavior(text: str) -> None:
    chunk = Chunk(text, 10, 10 + text.count("\n"), "cpp", "FExample::Run", "method")
    result = limit_chunk_bytes(chunk)
    assert result == [chunk]
    assert result[0] is chunk


def test_multiline_chunks_split_only_between_complete_lines_and_keep_metadata() -> None:
    text = "\n".join(character * 3999 for character in "abc")
    chunk = Chunk(text, 10, 12, "cpp", "FExample::Run", "method")
    result = limit_chunk_bytes(chunk)
    assert [(part.start_line, part.end_line) for part in result] == [(10, 11), (12, 12)]
    assert [len(part.text.encode("utf-8")) for part in result] == [8000, 3999]
    assert "".join(part.text for part in result) == text
    assert all(
        (part.language, part.symbol, part.kind) == ("cpp", "FExample::Run", "method")
        for part in result
    )
    assert (chunk.text, chunk.start_line, chunk.end_line) == (text, 10, 12)


@pytest.mark.parametrize(
    "text",
    [
        "a" * (MAX_CHUNK_BYTES + 1),
        "é" * (MAX_CHUNK_BYTES // 2 + 1),
        "🙂" * (MAX_CHUNK_BYTES // 4 + 1),
        "a" * (MAX_CHUNK_BYTES - 1) + "🙂汉é",
        "汉🙂é" * 3000,
    ],
    ids=["ascii", "two_byte", "four_byte", "mixed_boundary", "mixed_repeated"],
)
def test_long_single_lines_split_at_utf8_codepoint_boundaries_without_losing_text(
    text: str,
) -> None:
    result = limit_chunk_bytes(Chunk(text, 42, 42, "cpp", "Data", "class"))
    assert len(result) > 1
    assert "".join(part.text for part in result) == text
    assert all(0 < len(part.text.encode("utf-8")) <= MAX_CHUNK_BYTES for part in result)
    assert all((part.start_line, part.end_line) == (42, 42) for part in result)
    assert all((part.language, part.symbol, part.kind) == ("cpp", "Data", "class") for part in result)
    assert all(part.text.encode("utf-8").decode("utf-8") == part.text for part in result)


def test_long_lines_surrounded_by_other_lines_keep_their_source_coordinates() -> None:
    text = "prefix\n" + "🙂" * 4001 + "\nsuffix"
    result = limit_chunk_bytes(Chunk(text, 7, 9, "cpp", "Data", "window"))
    assert result[0].text == "prefix\n"
    assert (result[0].start_line, result[0].end_line) == (7, 7)
    assert result[-1].text == "suffix"
    assert (result[-1].start_line, result[-1].end_line) == (9, 9)
    assert all((part.start_line, part.end_line) == (8, 8) for part in result[1:-1])
    assert "".join(part.text for part in result) == text
    assert all(len(part.text.encode("utf-8")) <= MAX_CHUNK_BYTES for part in result)


def test_a_line_at_the_limit_reserves_space_for_its_trailing_lf() -> None:
    text = "x" * MAX_CHUNK_BYTES + "\nz"
    result = limit_chunk_bytes(Chunk(text, 3, 4, "text"))
    assert [len(part.text.encode("utf-8")) for part in result] == [7999, 2, 1]
    assert result[1].text == "x\n"
    assert [(part.start_line, part.end_line) for part in result] == [(3, 3), (3, 3), (4, 4)]
    assert "".join(part.text for part in result) == text
    assert all(part.text.strip() for part in result)


def test_whitespace_crlf_and_non_lf_controls_are_preserved_exactly() -> None:
    text = "\n" + "a" * 8001 + "\r\n\n" + "b" * 8001 + "\u2028\v\f\r\n"
    original = Chunk(text, 20, 25, "cpp", "Data", "window")
    result = limit_chunk_bytes(original)
    assert "".join(part.text for part in result) == text
    assert all(len(part.text.encode("utf-8")) <= MAX_CHUNK_BYTES for part in result)
    assert all(20 <= part.start_line <= part.end_line <= 25 for part in result)
    control_parts = [part for part in result if "\u2028" in part.text]
    assert len(control_parts) == 1
    assert (control_parts[0].start_line, control_parts[0].end_line) == (23, 23)


@pytest.mark.parametrize("language", ["text", "python"])
def test_chunk_file_applies_the_byte_limit_after_each_language_strategy(
    tmp_path: Path, language: str
) -> None:
    text = "def example():\n    value = '" + "é" * 4500 + "'\n    return value"
    result = chunk_file(text, language, Config(store_dir=tmp_path / "store"))
    assert len(result) > 1
    assert "".join(part.text for part in result) == text
    assert all(len(part.text.encode("utf-8")) <= MAX_CHUNK_BYTES for part in result)
    assert all(1 <= part.start_line <= part.end_line <= 3 for part in result)
    if language == "python":
        assert all((part.symbol, part.kind) == ("example", "function") for part in result)
