"""Stable internal adapter over CodeRAG retrieval.

The adapter uses the real ``coderag`` package when it is installed and
preferred, and otherwise falls back to the deterministic local index so
tests and policy validation do not depend on external services.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from codalith.coderag.local_backend import LocalRetrievalBackend
from codalith.coderag.native_backend import NativeCodeRAGBackend
from codalith.coderag.types import RetrievalHit
from codalith.corpus.registry import CorpusRegistry
from codalith.corpus.source_reader import SourceReader
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
        self.native = NativeCodeRAGBackend()
        self.local = LocalRetrievalBackend()
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
                return self.native.search(
                    corpus,
                    query,
                    top_k=top_k,
                    path_prefix=path_prefix,
                )
            except Exception as exc:
                self._record_native_fallback(corpus_id, "search_code", exc)
        return self.local.search(
            corpus,
            query,
            top_k=top_k,
            path_prefix=path_prefix,
        )

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
                status = self.native.status(corpus)
                status["native_fallbacks"] = self._native_fallbacks.get(corpus_id, 0)
                return status
            except Exception as exc:
                self._record_native_fallback(corpus_id, "status", exc)
        status = self.local.status(corpus)
        status["native_fallbacks"] = self._native_fallbacks.get(corpus_id, 0)
        return status

    def reindex(self, corpus_id: str, path: str | None = None, full: bool = False) -> dict[str, Any]:
        corpus = self.registry.get_corpus(corpus_id)
        if self.prefer_native:
            try:
                return self.native.reindex(corpus, path=path, full=full)
            except Exception as exc:
                self._record_native_fallback(corpus_id, "reindex", exc)
        return self.local.reindex(corpus, path=path, full=full)

    def _record_native_fallback(self, corpus_id: str, operation: str, exc: Exception) -> None:
        if _env_flag("CODALITH_NATIVE_CODERAG_STRICT"):
            raise CodeRAGAdapterError(str(exc)) from exc
        self._native_fallbacks[corpus_id] = self._native_fallbacks.get(corpus_id, 0) + 1
        logger.warning(
            "Native CodeRAG %s failed for %s; falling back to local deterministic retrieval: %s",
            operation,
            corpus_id,
            exc,
        )


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}
