from __future__ import annotations

import hashlib
import stat
from pathlib import Path

import pytest

from codalith.semantic.chunking import chunk_file
from codalith.semantic.config import Config
from codalith.semantic.engine import SemanticEngine
from codalith.semantic.indexer import has_indexable_text
from codalith.semantic.search import (
    fusion_weights,
    reciprocal_rank_fusion,
    references_identifier,
)
from codalith.semantic.store import (
    LanceStore,
    SemanticStoreError,
    StoreProvenance,
    read_provenance,
)

_CACHE_SOURCE = (
    "import os\n"
    "\n"
    "class Cache:\n"
    '    """Holds values."""\n'
    "\n"
    "    limit = 10\n"
    "\n"
    "    def get(self, key):\n"
    "        return key\n"
    "\n"
    "    def put(self, key, value):\n"
    "        return value\n"
    "\n"
    "def build_cache():\n"
    "    return Cache()\n"
)
_NOTES = "The cache keeps hot values in memory.\n"
_FAKE_PROVENANCE = StoreProvenance("fake-16", 16)


def test_symbol_chunks_give_each_line_to_its_smallest_enclosing_symbol() -> None:
    chunks = chunk_file(_CACHE_SOURCE, "python", Config(store_dir=Path("store")))
    assert [(c.symbol, c.kind, c.start_line, c.end_line) for c in chunks] == [
        (None, "window", 1, 2),
        ("Cache", "class", 3, 7),
        ("Cache.get", "method", 8, 9),
        ("Cache.put", "method", 11, 12),
        ("build_cache", "function", 14, 15),
    ]


def test_unparsed_text_is_chunked_into_overlapping_line_windows() -> None:
    text = "\n".join(f"line {number}" for number in range(1, 131))
    chunks = chunk_file(text, "text", Config(store_dir=Path("store")))
    assert [(c.start_line, c.end_line, c.kind) for c in chunks] == [
        (1, 60, "window"),
        (51, 110, "window"),
        (101, 130, "window"),
    ]


def test_reciprocal_rank_fusion_sums_weighted_reciprocal_ranks() -> None:
    assert [item for item, _ in reciprocal_rank_fusion([[1, 2], [2, 3]])] == [2, 1, 3]
    assert [item for item, _ in reciprocal_rank_fusion([[1], [2]], weights=[1.0, 0.4])] == [
        1,
        2,
    ]


@pytest.mark.parametrize(
    ("query", "names_identifier"),
    [
        ("where is the cache invalidated", False),
        ("fix the 3.11 crash, e.g. on startup", False),
        ("why does build_cache fail", True),
        ("AliasGenerator ordering", True),
        ("Fix tuple order in `generate`", True),
        ("Cache.get returns stale values", True),
        ("call render() twice", True),
    ],
)
def test_queries_naming_an_identifier_keep_the_lexical_weight(
    query: str,
    names_identifier: bool,
) -> None:
    config = Config(store_dir=Path("store"))
    assert references_identifier(query) is names_identifier
    assert fusion_weights(query, config) == (
        (config.code_dense_weight, config.code_lexical_weight)
        if names_identifier
        else (config.nl_dense_weight, config.nl_lexical_weight)
    )


def test_blank_and_binary_content_is_not_indexable() -> None:
    assert has_indexable_text(b"def f():\n    pass\n")
    assert not has_indexable_text(b"")
    assert not has_indexable_text(b" \n\t\n")
    assert not has_indexable_text(b"head\x00tail")
    assert has_indexable_text(b"x" * 8192 + b"\x00")


def test_a_created_store_holds_every_text_file_and_answers_searches(tmp_path: Path) -> None:
    source, store = _build_store(tmp_path)
    engine = SemanticEngine.open(Config(store_dir=store, provider="fake"))
    try:
        assert engine.provenance == _FAKE_PROVENANCE
        assert engine.stats().total_files == 2
        assert engine.file_hashes() == {
            path: hashlib.sha256((source / path).read_bytes()).hexdigest()
            for path in ("notes.md", "pkg/cache.py")
        }
        hits = engine.search(_NOTES, 3)
    finally:
        engine.close()
    assert read_provenance(store) == _FAKE_PROVENANCE
    assert (hits[0].path, hits[0].start_line, hits[0].text) == ("notes.md", 1, _NOTES)
    assert hits[0].similarity == pytest.approx(1.0)


def test_create_refuses_an_existing_directory(tmp_path: Path) -> None:
    store = tmp_path / "store"
    store.mkdir()
    with pytest.raises(SemanticStoreError, match="already exists"):
        SemanticEngine.create(Config(store_dir=store, provider="fake"))
    assert list(store.iterdir()) == []


def test_open_refuses_another_embedding_model_and_keeps_the_store(tmp_path: Path) -> None:
    source, store = _build_store(tmp_path)
    before = _store_bytes(store)
    for provenance in (StoreProvenance("another-model", 16), StoreProvenance("fake-16", 32)):
        with pytest.raises(SemanticStoreError, match="refusing to open"):
            LanceStore.open(store, provenance)
    assert _store_bytes(store) == before
    engine = SemanticEngine.open(Config(store_dir=store, provider="fake"))
    try:
        assert engine.stats().total_files == 2
        with pytest.raises(SemanticStoreError, match="read-only"):
            engine.index(source)
    finally:
        engine.close()
    assert _store_bytes(store) == before


def test_reading_a_store_never_writes_to_it(tmp_path: Path) -> None:
    _, store = _build_store(tmp_path)
    before = _store_bytes(store)
    files = [path for path in store.rglob("*") if path.is_file()]
    for path in files:
        path.chmod(stat.S_IREAD)
    try:
        engine = SemanticEngine.open(Config(store_dir=store, provider="fake"))
        try:
            assert engine.search("cache", 3)
            engine.stats()
            engine.file_hashes()
        finally:
            engine.close()
    finally:
        for path in files:
            path.chmod(stat.S_IREAD | stat.S_IWRITE)
    assert _store_bytes(store) == before


def test_store_provenance_must_be_readable_and_complete(tmp_path: Path) -> None:
    with pytest.raises(SemanticStoreError, match="no readable provenance"):
        read_provenance(tmp_path)
    (tmp_path / "meta.json").write_text(
        '{"embed_model": "fake-16", "embed_dim": true}',
        encoding="utf-8",
    )
    with pytest.raises(SemanticStoreError, match="provenance is invalid"):
        read_provenance(tmp_path)


def _build_store(root: Path) -> tuple[Path, Path]:
    source = root / "source"
    (source / "pkg").mkdir(parents=True)
    (source / "pkg" / "cache.py").write_bytes(_CACHE_SOURCE.encode())
    (source / "notes.md").write_bytes(_NOTES.encode())
    (source / "empty.txt").write_bytes(b"")
    (source / "blob.bin").write_bytes(b"\x00\x01\x02")
    store = root / "store"
    engine = SemanticEngine.create(Config(store_dir=store, provider="fake"))
    try:
        engine.index(source)
    finally:
        engine.close()
    return source, store


def _store_bytes(store: Path) -> dict[str, bytes]:
    return {
        path.relative_to(store).as_posix(): path.read_bytes()
        for path in sorted(store.rglob("*"))
        if path.is_file()
    }
