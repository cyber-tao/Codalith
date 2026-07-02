"""Simple score normalization and reranking."""

from __future__ import annotations

from codalith.coderag.adapter import RetrievalHit
from codalith.compiler.model_reranker import ModelReranker


def rerank(
    hits: list[RetrievalHit],
    *,
    identifiers: list[str],
    max_hits: int,
    query: str | None = None,
    model_reranker: ModelReranker | None = None,
) -> list[RetrievalHit]:
    identifier_set = {item.lower() for item in identifiers}

    def score(hit: RetrievalHit) -> float:
        exact = 0.2 if any(identifier in hit.snippet.lower() for identifier in identifier_set) else 0.0
        card = 0.1 if "UE_KNOWLEDGE" in hit.path else 0.0
        return hit.score + exact + card

    ordered = sorted(hits, key=score, reverse=True)
    if model_reranker is not None and query:
        ordered = model_reranker.rerank(query, ordered)
    return ordered[:max_hits]
