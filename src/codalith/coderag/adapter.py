"""Stable internal adapter over CodeRAG retrieval.

The adapter uses the real ``coderag`` package when it is installed and
preferred, and otherwise falls back to the deterministic local index so
tests and policy validation do not depend on external services.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codalith.coderag.local_index import (
    LocalIndex,
    build_local_index,
    scan_corpus,
    search_local_index,
)
from codalith.coderag.native import load_native_instance, native_store_dir
from codalith.coderag.types import RetrievalHit, module_from_path
from codalith.corpus.registry import Corpus, CorpusRegistry
from codalith.corpus.source_reader import SourceReader
from codalith.corpus.uris import source_uri
from codalith.errors import CodeRAGAdapterError

logger = logging.getLogger(__name__)


class CodeRAGAdapter:
    def __init__(
        self,
        registry: CorpusRegistry,
        *,
        prefer_native: bool | None = None,
    ) -> None:
        self.registry = registry
        self.prefer_native = (
            prefer_native
            if prefer_native is not None
            else os.getenv("CODALITH_USE_NATIVE_CODERAG", "").lower() in {"1", "true", "yes"}
        )
        self._native: dict[str, Any] = {}
        self._local: dict[str, LocalIndex] = {}
        self._indexed_at: dict[str, float] = {}
        self._native_fallbacks: dict[str, int] = {}
        self._source_reader = SourceReader(registry)

    def search_code(
        self,
        corpus_id: str,
        query: str,
        top_k: int = 8,
        filters: dict[str, Any] | None = None,
    ) -> list[RetrievalHit]:
        corpus = self.registry.get_corpus(corpus_id)
        path_prefix = str(filters["path_prefix"]) if filters and filters.get("path_prefix") else None
        if self.prefer_native:
            try:
                return self._native_search(corpus, query, top_k, path_prefix)
            except Exception as exc:
                self._record_native_fallback(corpus_id, "search_code", exc)
        index = self._ensure_local_index(corpus)
        return search_local_index(index, corpus, query, top_k, path_prefix)

    def get_file(
        self,
        corpus_id: str,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        return self._source_reader.read_source(corpus_id, path, start_line, end_line)

    def status(self, corpus_id: str) -> dict[str, Any]:
        corpus = self.registry.get_corpus(corpus_id)
        if self.prefer_native:
            try:
                status = dict(self._native_instance(corpus).status())
                status["corpus_id"] = corpus_id
                status["native_fallbacks"] = self._native_fallbacks.get(corpus_id, 0)
                self._add_index_timestamps(corpus, status)
                return status
            except Exception as exc:
                self._record_native_fallback(corpus_id, "status", exc)
        index = self._ensure_local_index(corpus)
        indexed_at = self._indexed_at.get(corpus_id)
        return {
            "corpus_id": corpus_id,
            "provider": "local",
            "model": "deterministic-token-search",
            "watched_dir": str(self._root(corpus)),
            "store_dir": str(corpus.coderag_store),
            "total_files": len(index.files),
            "total_chunks": len(index.windows),
            "indexed_at": indexed_at,
            "updated_at": _iso_timestamp(indexed_at),
            "native_fallbacks": self._native_fallbacks.get(corpus_id, 0),
        }

    def reindex(self, corpus_id: str, path: str | None = None, full: bool = False) -> dict[str, Any]:
        corpus = self.registry.get_corpus(corpus_id)
        if self.prefer_native:
            try:
                native = self._native_instance(corpus)
                if os.getenv("CODALITH_NATIVE_CODERAG_PROGRESS", "").lower() in {
                    "1",
                    "true",
                    "yes",
                }:
                    target = Path(path).expanduser() if path else None
                    stats = native.indexer.index(target, full=full, progress=True)
                else:
                    stats = native.index(path, full=full)
                if hasattr(stats, "as_dict"):
                    result = dict(stats.as_dict())
                else:
                    result = dict(stats)
                self._indexed_at[corpus_id] = time.time()
                return result
            except Exception as exc:
                self._record_native_fallback(corpus_id, "reindex", exc)
        self._local[corpus_id] = build_local_index(scan_corpus(corpus, self._root(corpus), path))
        self._indexed_at[corpus_id] = time.time()
        return self.status(corpus_id)

    def _record_native_fallback(self, corpus_id: str, operation: str, exc: Exception) -> None:
        if os.getenv("CODALITH_NATIVE_CODERAG_STRICT"):
            raise CodeRAGAdapterError(str(exc)) from exc
        self._native_fallbacks[corpus_id] = self._native_fallbacks.get(corpus_id, 0) + 1
        logger.warning(
            "Native CodeRAG %s failed for %s; falling back to local deterministic retrieval: %s",
            operation,
            corpus_id,
            exc,
        )

    def _native_search(
        self,
        corpus: Corpus,
        query: str,
        top_k: int,
        path_prefix: str | None,
    ) -> list[RetrievalHit]:
        native = self._native_instance(corpus)
        hits = native.search(query, top_k=top_k)
        mapped: list[RetrievalHit] = []
        for hit in hits:
            path = str(hit.path)
            if path_prefix and not path.startswith(path_prefix):
                continue
            mapped.append(
                RetrievalHit(
                    source="coderag",
                    corpus_id=corpus.corpus_id,
                    uri=source_uri(corpus.corpus_id, path, int(hit.start_line), int(hit.end_line)),
                    path=path,
                    start_line=int(hit.start_line),
                    end_line=int(hit.end_line),
                    title=f"{path}:{hit.start_line}-{hit.end_line}",
                    snippet=str(hit.text),
                    score=float(hit.score),
                    kind=str(hit.kind),
                    language=str(hit.language),
                    symbol=hit.symbol,
                    module=module_from_path(path, corpus.module_roots),
                    reason="CodeRAG hybrid retrieval hit.",
                    metadata={"coderag_similarity": float(hit.similarity)},
                )
            )
        return mapped

    def _native_instance(self, corpus: Corpus) -> Any:
        if corpus.corpus_id not in self._native:
            self._native[corpus.corpus_id] = load_native_instance(corpus, self._root(corpus))
        return self._native[corpus.corpus_id]

    def _ensure_local_index(self, corpus: Corpus) -> LocalIndex:
        if corpus.corpus_id not in self._local:
            self.reindex(corpus.corpus_id)
        return self._local.get(corpus.corpus_id, build_local_index([]))

    @staticmethod
    def _root(corpus: Corpus) -> Path:
        return corpus.indexed_root if corpus.indexed_root.exists() else corpus.source_root

    def _add_index_timestamps(self, corpus: Corpus, status: dict[str, Any]) -> None:
        indexed_at = status.get("indexed_at")
        if not isinstance(indexed_at, int | float):
            indexed_at = self._indexed_at.get(corpus.corpus_id) or _latest_mtime(native_store_dir(corpus))
            status["indexed_at"] = indexed_at
        if "updated_at" not in status:
            status["updated_at"] = _iso_timestamp(indexed_at)


def _latest_mtime(path: Path) -> float | None:
    if not path.exists():
        return None
    latest = path.stat().st_mtime
    for child in path.rglob("*"):
        try:
            latest = max(latest, child.stat().st_mtime)
        except OSError:
            continue
    return latest


def _iso_timestamp(timestamp: object) -> str | None:
    if not isinstance(timestamp, int | float):
        return None
    return datetime.fromtimestamp(float(timestamp), UTC).isoformat()
