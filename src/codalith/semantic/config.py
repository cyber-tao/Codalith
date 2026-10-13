"""Immutable configuration for one semantic store."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, kw_only=True)
class Config:
    """Settings passed explicitly by the indexing layer. Nothing here reads the environment."""

    store_dir: Path
    provider: str = "fastembed"
    # ``None`` selects the provider's default model.
    model: str | None = None
    openai_api_key: str | None = field(default=None, repr=False)
    openai_base_url: str | None = None
    cache_dir: Path = field(
        default_factory=lambda: Path.home() / ".cache" / "codalith" / "fastembed"
    )

    max_file_bytes: int = 1_000_000
    max_chunk_lines: int = 200
    window_lines: int = 60
    window_overlap: int = 10

    fetch_k: int = 50
    rrf_k: int = 60
    nl_dense_weight: float = 1.0
    nl_lexical_weight: float = 0.4
    code_dense_weight: float = 1.0
    code_lexical_weight: float = 1.0

    embed_batch_size: int = 64
    index_workers: int = 4
    embed_device: str = "auto"
    embed_threads: int = 0
