"""Bounded deterministic fallback retrieval backend."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path

from codalith.coderag.local_index import (
    IndexedFile,
    LocalIndex,
    build_local_index,
    scan_corpus,
    search_local_index,
)
from codalith.coderag.types import RetrievalHit
from codalith.corpus.registry import Corpus
from codalith.errors import CodeRAGAdapterError

DEFAULT_LOCAL_MAX_FILES = 10_000
DEFAULT_LOCAL_MAX_BYTES = 256 * 1024 * 1024


class LocalRetrievalBackend:
    name = "local"

    def __init__(self) -> None:
        self._indexes: dict[str, LocalIndex] = {}
        self._indexed_at: dict[str, float] = {}

    def search(
        self,
        corpus: Corpus,
        query: str,
        *,
        top_k: int,
        path_prefix: str | None = None,
    ) -> list[RetrievalHit]:
        return search_local_index(
            self._ensure_index(corpus),
            corpus,
            query,
            top_k,
            path_prefix,
        )

    def reindex(
        self,
        corpus: Corpus,
        *,
        path: str | None = None,
        full: bool = False,
    ) -> dict[str, object]:
        del full
        root = _corpus_root(corpus)
        if path is None:
            files = self._scan(corpus, root)
        else:
            current = self._ensure_index(corpus)
            target = _normalize_subpath(path)
            replacement = self._scan(corpus, root, target)
            prefix = f"{target.rstrip('/')}/"
            retained = [
                item
                for item in current.files
                if item.path != target and not item.path.startswith(prefix)
            ]
            files = sorted([*retained, *replacement], key=lambda item: item.path)
        self._indexes[corpus.corpus_id] = build_local_index(files)
        self._indexed_at[corpus.corpus_id] = time.time()
        return self.status(corpus)

    def status(self, corpus: Corpus) -> dict[str, object]:
        index = self._ensure_index(corpus)
        indexed_at = self._indexed_at.get(corpus.corpus_id)
        return {
            "corpus_id": corpus.corpus_id,
            "backend": self.name,
            "provider": "local",
            "model": "deterministic-token-search",
            "watched_dir": str(_corpus_root(corpus)),
            "store_dir": str(corpus.coderag_store),
            "total_files": len(index.files),
            "total_chunks": len(index.windows),
            "indexed_at": indexed_at,
            "updated_at": _iso_timestamp(indexed_at),
        }

    def _ensure_index(self, corpus: Corpus) -> LocalIndex:
        index = self._indexes.get(corpus.corpus_id)
        if index is None:
            self.reindex(corpus)
            index = self._indexes[corpus.corpus_id]
        return index

    @staticmethod
    def _scan(corpus: Corpus, root: Path, path: str | None = None) -> list[IndexedFile]:
        return scan_corpus(
            corpus,
            root,
            path,
            max_files=_positive_env_int(
                "CODALITH_LOCAL_INDEX_MAX_FILES", DEFAULT_LOCAL_MAX_FILES
            ),
            max_bytes=_positive_env_int(
                "CODALITH_LOCAL_INDEX_MAX_BYTES", DEFAULT_LOCAL_MAX_BYTES
            ),
        )


def _corpus_root(corpus: Corpus) -> Path:
    return corpus.indexed_root if corpus.indexed_root.exists() else corpus.source_root


def _normalize_subpath(path: str) -> str:
    normalized = path.replace("\\", "/").strip("/")
    if not normalized or ".." in normalized.split("/"):
        raise CodeRAGAdapterError(f"Invalid corpus-relative reindex path: {path}")
    return normalized


def _positive_env_int(name: str, default: int) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise CodeRAGAdapterError(f"{name} must be an integer") from exc
    return value if value > 0 else None


def _iso_timestamp(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, UTC).isoformat()
