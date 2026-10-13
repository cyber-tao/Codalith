"""Facade over the embedding provider, LanceDB store, indexer, and hybrid search."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

from codalith.semantic.config import Config
from codalith.semantic.types import IndexStats, SearchHit

if TYPE_CHECKING:
    from codalith.semantic.embeddings import EmbeddingProvider
    from codalith.semantic.indexer import Indexer
    from codalith.semantic.search import HybridSearcher
    from codalith.semantic.store import LanceStore


class SemanticEngine:
    """Index a source snapshot and search it with dense and lexical retrieval."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._provider: EmbeddingProvider | None = None
        self._store: LanceStore | None = None
        self._indexer: Indexer | None = None
        self._searcher: HybridSearcher | None = None
        self._build_lock = threading.RLock()

    @property
    def provider(self) -> EmbeddingProvider:
        if self._provider is None:
            from codalith.semantic.embeddings import get_provider

            with self._build_lock:
                if self._provider is None:
                    self._provider = get_provider(self.config)
        return self._provider

    @property
    def store(self) -> LanceStore:
        if self._store is None:
            from codalith.semantic.store import LanceStore

            with self._build_lock:
                if self._store is None:
                    self.config.store_dir.mkdir(parents=True, exist_ok=True)
                    store = LanceStore(self.config.store_dir, self.provider.dim)
                    store.bootstrap(self.provider.dim, self.provider.model_id)
                    self._store = store
        return self._store

    @property
    def indexer(self) -> Indexer:
        if self._indexer is None:
            from codalith.semantic.indexer import Indexer

            with self._build_lock:
                if self._indexer is None:
                    self._indexer = Indexer(self.config, self.provider, self.store)
        return self._indexer

    @property
    def searcher(self) -> HybridSearcher:
        if self._searcher is None:
            from codalith.semantic.search import HybridSearcher

            with self._build_lock:
                if self._searcher is None:
                    self._searcher = HybridSearcher(self.config, self.provider, self.store)
        return self._searcher

    def index(self, source_dir: Path) -> IndexStats:
        return self.indexer.index(source_dir)

    def search(self, query: str, top_k: int) -> list[SearchHit]:
        """Hybrid dense and lexical search over the indexed source."""

        return self.searcher.search(query, top_k)

    def status(self) -> dict[str, object]:
        """Index statistics and embedding provenance."""

        stats = self.store.stats()
        return {
            "provider": self.config.provider,
            "model": self.provider.model_id,
            "embedding_dim": self.provider.dim,
            "store_dir": str(self.config.store_dir),
            "total_files": stats.total_files,
            "total_chunks": stats.total_chunks,
        }

    def close(self) -> None:
        if self._store is not None:
            self._store.close()
            self._store = None
