"""Facade over the embedding provider, LanceDB store, indexer, and hybrid search."""

from __future__ import annotations

from pathlib import Path

from codalith.semantic.config import Config
from codalith.semantic.embeddings import EmbeddingProvider, get_provider
from codalith.semantic.indexer import Indexer
from codalith.semantic.search import HybridSearcher
from codalith.semantic.store import LanceStore, SemanticStoreError, StoreProvenance
from codalith.semantic.types import IndexStats, SearchHit


class SemanticEngine:
    """Build a store from one source snapshot, or open an existing store read-only."""

    def __init__(
        self,
        config: Config,
        provider: EmbeddingProvider,
        store: LanceStore,
        *,
        writable: bool,
    ) -> None:
        self.config = config
        self.provider = provider
        self.store = store
        self._writable = writable
        self._searcher = HybridSearcher(config, provider, store)

    @classmethod
    def create(cls, config: Config) -> SemanticEngine:
        """Create an empty store at ``config.store_dir``, which must not exist yet."""

        provider = get_provider(config)
        return cls(
            config,
            provider,
            LanceStore.create(config.store_dir, _provenance(provider)),
            writable=True,
        )

    @classmethod
    def open(cls, config: Config) -> SemanticEngine:
        """Open an existing store without writing to it.

        Raises :class:`SemanticStoreError` when the store records another embedding model or
        dimension than the configured provider.
        """

        provider = get_provider(config)
        return cls(
            config,
            provider,
            LanceStore.open(config.store_dir, _provenance(provider)),
            writable=False,
        )

    @property
    def provenance(self) -> StoreProvenance:
        return self.store.provenance

    def index(self, source_dir: Path) -> IndexStats:
        """Index every file under ``source_dir`` into the newly created store."""

        if not self._writable:
            raise SemanticStoreError(f"Semantic store {self.config.store_dir} is read-only")
        return Indexer(self.config, self.provider, self.store).index(source_dir)

    def search(self, query: str, top_k: int) -> list[SearchHit]:
        """Hybrid dense and lexical search over the indexed source."""

        return self._searcher.search(query, top_k)

    def stats(self) -> IndexStats:
        return self.store.stats()

    def file_hashes(self) -> dict[str, str]:
        return self.store.file_hashes()

    def close(self) -> None:
        self.store.close()


def _provenance(provider: EmbeddingProvider) -> StoreProvenance:
    return StoreProvenance(str(provider.model_id), int(provider.dim))
