"""Index every file of a materialized source snapshot into a fresh store.

Chunking runs across a bounded worker pool. Embedding batches span files; requests and
store writes stay on the calling thread.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING

from codalith.semantic.chunking import chunk_file
from codalith.semantic.chunking.languages import detect_language
from codalith.semantic.config import Config
from codalith.semantic.types import Chunk, IndexStats

if TYPE_CHECKING:
    import numpy as np

    from codalith.semantic.embeddings import EmbeddingProvider
    from codalith.semantic.store import LanceStore

logger = logging.getLogger(__name__)

_BINARY_SNIFF_BYTES = 8192
_PROGRESS_INTERVAL = 500


def has_indexable_text(data: bytes) -> bool:
    """Whether the indexer stores ``data``: blank and binary (NUL in the head) content is skipped."""
    return bool(data.strip()) and b"\x00" not in data[:_BINARY_SNIFF_BYTES]


@dataclass(slots=True)
class _Work:
    rel: str
    language: str
    text: str
    content_hash: str
    mtime: float
    size: int


@dataclass(slots=True)
class _PreparedFile:
    work: _Work
    chunks: list[Chunk]
    vectors: np.ndarray | None = None
    embedded_chunks: int = 0


class Indexer:
    def __init__(
        self,
        config: Config,
        provider: EmbeddingProvider,
        store: LanceStore,
    ) -> None:
        self.config = config
        self.provider = provider
        self.store = store

    def index(self, source_dir: Path) -> IndexStats:
        """Index every readable text file under ``source_dir``, then build search indexes."""

        root = source_dir.resolve()
        work = (
            item
            for item in (self._read(path, rel) for path, rel in _walk(root))
            if item is not None
        )
        processed_files = 0
        processed_chunks = 0
        for prepared in self._embedded(self._prepared(work)):
            item = prepared.work
            processed_chunks += self.store.write_file(
                item.rel,
                item.language,
                item.content_hash,
                item.mtime,
                item.size,
                prepared.chunks,
                prepared.vectors,
            )
            processed_files += 1
            if processed_files == 1 or processed_files % _PROGRESS_INTERVAL == 0:
                logger.info(
                    "Indexed %d files and %d semantic chunks; current path: %s",
                    processed_files,
                    processed_chunks,
                    item.rel,
                )
        logger.info(
            "Optimizing semantic store after %d files and %d chunks",
            processed_files,
            processed_chunks,
        )
        self.store.optimize()
        return self.store.stats()

    def _read(self, path: Path, rel: str) -> _Work | None:
        try:
            st = path.stat()
            if st.st_size > self.config.max_file_bytes:
                return None
            data = path.read_bytes()
        except OSError as exc:
            logger.warning("Cannot read %s: %s", path, exc)
            return None
        if len(data) > self.config.max_file_bytes or not has_indexable_text(data):
            return None
        return _Work(
            rel,
            detect_language(rel),
            data.decode("utf-8", errors="replace"),
            hashlib.sha256(data).hexdigest(),
            st.st_mtime,
            st.st_size,
        )

    def _prepared(
        self, work: Iterable[_Work]
    ) -> Iterator[_PreparedFile]:
        """Chunk files with at most ``index_workers`` pending results."""
        workers = max(1, self.config.index_workers)
        if workers == 1:
            for item in work:
                yield self._prepare(item)
            return
        items = iter(work)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending: set[Future[_PreparedFile]] = {
                pool.submit(self._prepare, item) for item in islice(items, workers)
            }
            while pending:
                completed, _ = wait(pending, return_when=FIRST_COMPLETED)
                while completed:
                    future = completed.pop()
                    pending.remove(future)
                    prepared = future.result()
                    del future
                    next_item = next(items, None)
                    if next_item is not None:
                        pending.add(pool.submit(self._prepare, next_item))
                    yield prepared

    def _prepare(self, item: _Work) -> _PreparedFile:
        return _PreparedFile(item, chunk_file(item.text, item.language, self.config))

    def _embedded(self, files: Iterable[_PreparedFile]) -> Iterator[_PreparedFile]:
        batch_size = max(1, self.config.embed_batch_size)
        texts: list[str] = []
        parts: list[tuple[_PreparedFile, int, int]] = []
        for prepared in files:
            if not prepared.chunks:
                yield prepared
                continue
            start = 0
            while start < len(prepared.chunks):
                end = min(len(prepared.chunks), start + batch_size - len(texts))
                texts.extend(chunk.text for chunk in prepared.chunks[start:end])
                parts.append((prepared, start, end))
                start = end
                if len(texts) == batch_size:
                    yield from self._embed_batch(texts, parts)
                    texts = []
                    parts = []
        if texts:
            yield from self._embed_batch(texts, parts)

    def _embed_batch(
        self, texts: list[str], parts: list[tuple[_PreparedFile, int, int]]
    ) -> Iterator[_PreparedFile]:
        import numpy as np

        vectors = np.asarray(self.provider.embed_documents(texts), dtype="float32")
        expected_shape = (len(texts), self.provider.dim)
        if vectors.shape != expected_shape or not np.isfinite(vectors).all():
            raise ValueError(
                f"Embedding batch must contain finite vectors of shape {expected_shape}; "
                f"received {vectors.shape}"
            )
        offset = 0
        for prepared, start, end in parts:
            if prepared.vectors is None:
                prepared.vectors = np.empty(
                    (len(prepared.chunks), vectors.shape[1]), dtype="float32"
                )
            prepared.vectors[start:end] = vectors[offset : offset + end - start]
            prepared.embedded_chunks += end - start
            offset += end - start
            if prepared.embedded_chunks == len(prepared.chunks):
                yield prepared


def _walk(root: Path) -> Iterator[tuple[Path, str]]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        directory = Path(dirpath)
        for name in sorted(filenames):
            path = directory / name
            yield path, path.relative_to(root).as_posix()
