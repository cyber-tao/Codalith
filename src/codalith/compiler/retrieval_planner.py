"""Retrieval planning for v0 context compilation."""

from __future__ import annotations

from codalith.coderag.query_builder import build_queries


def plan_queries(query: str, identifiers: list[str]) -> list[str]:
    return build_queries(query, identifiers)
