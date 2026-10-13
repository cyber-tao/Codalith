from __future__ import annotations

import gc
import threading
import weakref
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import numpy as np
import pytest

from codalith.semantic.config import Config
from codalith.semantic.indexer import Indexer, _PreparedFile, _Work
from codalith.semantic.store import LanceStore
from codalith.semantic.types import Chunk, IndexStats


class _BatchProvider:
    name = "test"
    model_id = "test-1"
    dim = 1

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        self.calls.append(list(texts))
        return np.array([[float(text)] for text in texts], dtype="float32")

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed_documents([text])[0]


class _RecordingStore:
    def __init__(self) -> None:
        self.paths: list[str] = []
        self.texts: dict[str, list[str]] = {}
        self.vectors: dict[str, list[list[float]] | None] = {}
        self.writer_threads: set[int] = set()
        self.chunks = 0
        self.optimized = False

    def write_file(
        self,
        rel: str,
        language: str,
        content_hash: str,
        mtime: float,
        size: int,
        chunks: Sequence[Chunk],
        vectors: np.ndarray | None,
    ) -> int:
        self.paths.append(rel)
        self.texts[rel] = [chunk.text for chunk in chunks]
        self.vectors[rel] = vectors.tolist() if vectors is not None else None
        self.writer_threads.add(threading.get_ident())
        self.chunks += len(chunks)
        return len(chunks)

    def optimize(self) -> None:
        self.optimized = True

    def stats(self) -> IndexStats:
        return IndexStats(len(self.paths), self.chunks)


@pytest.mark.parametrize("workers", [1, 3])
def test_index_streams_files_and_releases_completed_vectors(
    tmp_path: Path, workers: int
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    file_count = 40
    for number in range(file_count):
        (source / f"{number:03}.txt").write_text(str(number), encoding="utf-8")
    read_count = 0
    reads_at_first_embed: list[int] = []
    vector_references: list[weakref.ReferenceType[np.ndarray]] = []
    peak_live_vectors = 0
    batch_size = 4

    class StreamingIndexer(Indexer):
        def _read(self, path: Path, rel: str) -> _Work | None:
            nonlocal read_count
            read_count += 1
            return super()._read(path, rel)

    class StreamingProvider(_BatchProvider):
        def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
            if not reads_at_first_embed:
                reads_at_first_embed.append(read_count)
            return super().embed_documents(texts)

    class StreamingStore(_RecordingStore):
        def write_file(
            self,
            rel: str,
            language: str,
            content_hash: str,
            mtime: float,
            size: int,
            chunks: Sequence[Chunk],
            vectors: np.ndarray | None,
        ) -> int:
            nonlocal peak_live_vectors
            assert vectors is not None
            vector_references.append(weakref.ref(vectors))
            gc.collect()
            peak_live_vectors = max(
                peak_live_vectors, sum(reference() is not None for reference in vector_references)
            )
            return super().write_file(
                rel, language, content_hash, mtime, size, chunks, vectors
            )

    store = StreamingStore()
    provider = StreamingProvider()
    indexer = StreamingIndexer(
        Config(
            store_dir=tmp_path / "store", index_workers=workers, embed_batch_size=batch_size
        ),
        provider,
        cast(LanceStore, store),
    )
    assert indexer.index(source) == IndexStats(file_count, file_count)
    assert reads_at_first_embed[0] <= workers + batch_size
    assert peak_live_vectors <= batch_size + 1
    assert set(store.paths) == {f"{number:03}.txt" for number in range(file_count)}
    assert store.vectors == {
        f"{number:03}.txt": [[float(number)]] for number in range(file_count)
    }
    assert [len(batch) for batch in provider.calls] == [batch_size] * (file_count // batch_size)
    assert store.writer_threads == {threading.get_ident()}
    assert store.optimized
    gc.collect()
    assert all(reference() is None for reference in vector_references)


@pytest.mark.parametrize(("batch_size", "expected_sizes"), [(3, [3, 3, 1]), (0, [1] * 7)])
def test_large_file_spans_batches_and_shares_its_final_batch_with_the_next_file(
    tmp_path: Path, batch_size: int, expected_sizes: list[int]
) -> None:
    provider = _BatchProvider()
    store = _RecordingStore()
    indexer = Indexer(
        Config(
            store_dir=tmp_path / "store",
            embed_batch_size=batch_size,
            window_lines=1,
            window_overlap=0,
            index_workers=1,
        ),
        provider,
        cast(LanceStore, store),
    )
    source = tmp_path / "source"
    source.mkdir()
    (source / "first.txt").write_text("0\n1\n2\n3\n4", encoding="utf-8", newline="\n")
    (source / "second.txt").write_text("5\n6", encoding="utf-8", newline="\n")
    assert indexer.index(source) == IndexStats(2, 7)
    assert [len(batch) for batch in provider.calls] == expected_sizes
    assert store.texts == {
        "first.txt": [str(number) for number in range(5)],
        "second.txt": ["5", "6"],
    }
    assert store.vectors == {
        "first.txt": [[float(number)] for number in range(5)],
        "second.txt": [[5.0], [6.0]],
    }


def test_small_files_share_embedding_batches_and_empty_chunk_files_are_recorded(
    tmp_path: Path,
) -> None:
    class EmptyChunkIndexer(Indexer):
        def _prepare(self, item: _Work) -> _PreparedFile:
            if item.rel == "empty.txt":
                return _PreparedFile(item, [])
            return super()._prepare(item)

    source = tmp_path / "source"
    source.mkdir()
    for name, text in (("first", "0"), ("empty", "skip"), ("second", "1"), ("third", "2")):
        (source / f"{name}.txt").write_text(text, encoding="utf-8")
    provider = _BatchProvider()
    store = _RecordingStore()
    indexer = EmptyChunkIndexer(
        Config(store_dir=tmp_path / "store", index_workers=1, embed_batch_size=2),
        provider,
        cast(LanceStore, store),
    )
    assert indexer.index(source) == IndexStats(4, 3)
    assert provider.calls == [["0", "1"], ["2"]]
    assert store.vectors == {
        "first.txt": [[0.0]],
        "empty.txt": None,
        "second.txt": [[1.0]],
        "third.txt": [[2.0]],
    }


@pytest.mark.parametrize("failure_call", [1, 2])
def test_embedding_failure_never_writes_an_incomplete_file(
    tmp_path: Path, failure_call: int
) -> None:
    class FailingProvider(_BatchProvider):
        def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
            if len(self.calls) + 1 == failure_call:
                raise RuntimeError("Embedding failed")
            return super().embed_documents(texts)

    source = tmp_path / "source"
    source.mkdir()
    (source / "large.txt").write_text("0\n1\n2\n3\n4", encoding="utf-8")
    store = _RecordingStore()
    indexer = Indexer(
        Config(
            store_dir=tmp_path / "store",
            embed_batch_size=2,
            window_lines=1,
            window_overlap=0,
        ),
        FailingProvider(),
        cast(LanceStore, store),
    )
    with pytest.raises(RuntimeError, match="Embedding failed"):
        indexer.index(source)
    assert store.paths == []
    assert not store.optimized


@pytest.mark.parametrize(
    "bad_vectors",
    [
        np.zeros((1, 1), dtype="float32"),
        np.zeros((2, 2), dtype="float32"),
        np.array([[0.0], [np.nan]], dtype="float32"),
    ],
)
def test_invalid_embedding_batch_does_not_write_any_file(
    tmp_path: Path, bad_vectors: np.ndarray
) -> None:
    class InvalidProvider(_BatchProvider):
        def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
            return bad_vectors

    source = tmp_path / "source"
    source.mkdir()
    (source / "first.txt").write_text("0", encoding="utf-8")
    (source / "second.txt").write_text("1", encoding="utf-8")
    store = _RecordingStore()
    indexer = Indexer(
        Config(store_dir=tmp_path / "store", embed_batch_size=2),
        InvalidProvider(),
        cast(LanceStore, store),
    )
    with pytest.raises(ValueError, match="Embedding batch must contain finite vectors"):
        indexer.index(source)
    assert store.paths == []
    assert not store.optimized


def test_oversized_files_are_rejected_without_reading_their_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "large.txt"
    source.write_text("too large", encoding="utf-8")
    indexer = Indexer(
        Config(store_dir=tmp_path / "store", max_file_bytes=2),
        _BatchProvider(),
        cast(LanceStore, _RecordingStore()),
    )

    def unexpected_read(path: Path) -> bytes:
        raise AssertionError(f"Oversized file must not be read: {path}")

    monkeypatch.setattr(Path, "read_bytes", unexpected_read)
    assert indexer._read(source, source.name) is None
