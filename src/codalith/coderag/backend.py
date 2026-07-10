"""Retrieval backend contract used by the CodeRAG facade."""

from __future__ import annotations

from typing import Protocol

from codalith.coderag.types import RetrievalHit
from codalith.corpus.registry import Corpus


class RetrievalBackend(Protocol):
    name: str

    def search(
        self,
        corpus: Corpus,
        query: str,
        *,
        top_k: int,
        path_prefix: str | None = None,
    ) -> list[RetrievalHit]: ...

    def reindex(
        self,
        corpus: Corpus,
        *,
        path: str | None = None,
        full: bool = False,
    ) -> dict[str, object]: ...

    def status(self, corpus: Corpus) -> dict[str, object]: ...
