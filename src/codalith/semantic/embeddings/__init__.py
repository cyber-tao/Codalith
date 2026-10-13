"""Pluggable embedding providers.

A provider turns already-chunked texts into float32 vectors, one vector per text. The
embedding dimension is a property of the provider and its model, never a hard-coded constant.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from codalith.semantic.config import Config

if TYPE_CHECKING:
    import numpy as np


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Minimal interface every embedding backend implements."""

    name: str

    @property
    def model_id(self) -> str:
        """Stable identifier of the underlying model, recorded in the store provenance."""

    @property
    def dim(self) -> int:
        """Embedding dimensionality."""

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        """Embed code chunks. Returns a ``(len(texts), dim)`` float32 array."""

    def embed_query(self, text: str) -> np.ndarray:
        """Embed a single query. Returns a ``(dim,)`` float32 array."""


def get_provider(config: Config) -> EmbeddingProvider:
    """Construct the provider named by ``config.provider``; heavy backends import lazily."""

    provider = config.provider.lower()
    if provider == "fake":
        from codalith.semantic.embeddings.fake_provider import FakeEmbeddingProvider

        return FakeEmbeddingProvider()
    if provider == "fastembed":
        from codalith.semantic.embeddings.fastembed_provider import (
            DEFAULT_MODEL,
            FastEmbedProvider,
        )

        return FastEmbedProvider(
            config.model or DEFAULT_MODEL,
            cache_dir=config.cache_dir,
            device=config.embed_device,
            threads=config.embed_threads,
            batch_size=config.embed_batch_size,
        )
    if provider == "openai":
        from codalith.semantic.embeddings.openai_provider import (
            DEFAULT_MODEL,
            OpenAIEmbeddingProvider,
        )

        return OpenAIEmbeddingProvider(
            model=config.model or DEFAULT_MODEL,
            api_key=config.openai_api_key,
            base_url=config.openai_base_url,
            batch_size=config.embed_batch_size,
        )
    raise ValueError(
        f"Unknown embedding provider {config.provider!r}. "
        "Expected one of: fastembed, openai, fake."
    )


__all__ = ["EmbeddingProvider", "get_provider"]
