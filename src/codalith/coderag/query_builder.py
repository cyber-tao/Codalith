"""Query expansion helpers for UE-aware retrieval."""

from __future__ import annotations


def build_queries(query: str, identifiers: list[str] | None = None) -> list[str]:
    queries = [query]
    if identifiers:
        queries.extend(identifiers)
        queries.append(" ".join(identifiers))
    return list(dict.fromkeys(item for item in queries if item.strip()))
