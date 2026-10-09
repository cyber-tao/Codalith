"""Immutable configuration for one indexing or search session."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Directories and globs that are never worth indexing. ``<name>/*`` prunes that
# directory anywhere in the tree. Tests are intentionally not ignored.
DEFAULT_IGNORE_GLOBS: tuple[str, ...] = (
    ".git/*",
    ".hg/*",
    ".svn/*",
    "build/*",
    "dist/*",
    "target/*",
    "*.egg-info/*",
    ".next/*",
    ".nuxt/*",
    "__pycache__/*",
    ".mypy_cache/*",
    ".pytest_cache/*",
    ".ruff_cache/*",
    ".tox/*",
    ".ipynb_checkpoints/*",
    ".gradle/*",
    ".terraform/*",
    ".coderag/*",
    ".venv/*",
    "venv/*",
    "env/*",
    "node_modules/*",
    "site-packages/*",
    "vendor/*",
    ".cache/*",
    ".local/*",
    ".npm/*",
    ".cargo/*",
    ".rustup/*",
    ".m2/*",
    ".nuget/*",
    ".idea/*",
    ".vscode/*",
)


@dataclass(frozen=True)
class Config:
    """Settings passed explicitly by the indexing adapter. Nothing here reads the environment."""

    provider: str = "fastembed"
    model: str = "BAAI/bge-small-en-v1.5"
    openai_model: str = "text-embedding-3-small"
    openai_api_key: str | None = field(default=None, repr=False)
    openai_base_url: str | None = None
    # Keep the historical cache location so already-downloaded local models stay warm.
    cache_dir: Path = field(default_factory=lambda: Path.home() / ".cache" / "coderag")

    watched_dir: Path = field(default_factory=Path.cwd)
    store_dir: Path = None  # type: ignore[assignment]

    ignore_globs: tuple[str, ...] = DEFAULT_IGNORE_GLOBS
    use_gitignore: bool = True
    index_all_text: bool = False
    max_file_bytes: int = 1_000_000
    max_chunk_lines: int = 200
    window_lines: int = 60
    window_overlap: int = 10

    top_k: int = 8
    fetch_k: int = 50
    rrf_k: int = 60
    dense_weight: float = 1.0
    lexical_weight: float = 1.0
    adaptive_fusion: bool = False
    nl_dense_weight: float = 1.0
    nl_lexical_weight: float = 0.4
    code_dense_weight: float = 1.0
    code_lexical_weight: float = 1.0

    embed_batch_size: int = 64
    index_workers: int = 4
    embed_device: str = "auto"
    embed_threads: int = 0

    def __post_init__(self) -> None:
        if self.store_dir is None:
            object.__setattr__(self, "store_dir", self.watched_dir / ".coderag")
