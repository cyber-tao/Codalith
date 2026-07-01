"""Simple score normalization and reranking."""

from __future__ import annotations

from codalith.coderag.adapter import RetrievalHit


def rerank(hits: list[RetrievalHit], *, identifiers: list[str], max_hits: int) -> list[RetrievalHit]:
    identifier_set = {item.lower() for item in identifiers}

    def score(hit: RetrievalHit) -> float:
        exact = 0.2 if any(identifier in hit.snippet.lower() for identifier in identifier_set) else 0.0
        card = 0.1 if "UE_KNOWLEDGE" in hit.path else 0.0
        return hit.score + exact + card

    return sorted(hits, key=score, reverse=True)[:max_hits]
