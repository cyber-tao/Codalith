"""Index every file of a materialized source snapshot into a fresh store.

Chunking and embedding, the CPU and network cost, may run across worker threads; store
writes stay on the calling thread, the store's only writer.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
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
        work = [
            item
            for item in (self._read(path, rel) for path, rel in _walk(root))
            if item is not None
        ]
        for item, chunks, vectors in self._prepared(work):
            self.store.write_file(
                item.rel,
                item.language,
                item.content_hash,
                item.mtime,
                item.size,
                chunks,
                vectors,
            )
        self.store.optimize()
        return self.store.stats()

    def _read(self, path: Path, rel: str) -> _Work | None:
        try:
            st = path.stat()
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
        self, work: list[_Work]
    ) -> Iterator[tuple[_Work, list[Chunk], np.ndarray | None]]:
        """Chunk and embed each file, across worker threads when ``index_workers > 1``.

        The pool completes out of order, so each result carries its ``_Work`` item.
        """
        workers = max(1, self.config.index_workers)
        if workers == 1 or len(work) < 2:
            for item in work:
                chunks, vectors = self._prepare(item)
                yield item, chunks, vectors
            return
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self._prepare, item): item for item in work}
            for future in as_completed(futures):
                chunks, vectors = future.result()
                yield futures[future], chunks, vectors

    def _prepare(self, item: _Work) -> tuple[list[Chunk], np.ndarray | None]:
        chunks = chunk_file(item.text, item.language, self.config)
        if not chunks:
            return [], None
        return chunks, self.provider.embed_documents([c.text for c in chunks])


def _walk(root: Path) -> Iterator[tuple[Path, str]]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        directory = Path(dirpath)
        for name in sorted(filenames):
            path = directory / name
            yield path, path.relative_to(root).as_posix()
