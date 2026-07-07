"""Canonical codalith:// URI construction shared across all layers.

Every corpus is addressed by its corpus id regardless of kind; the kind of a
corpus is registry data, not URI syntax. Facets under a corpus authority are
``source``, ``module``, ``symbol``, and ``card``.
"""

from __future__ import annotations

SCHEME = "codalith"


def corpus_uri(corpus_id: str) -> str:
    return f"{SCHEME}://{corpus_id}"


def source_uri(corpus_id: str, path: str, start_line: int, end_line: int) -> str:
    return f"{SCHEME}://{corpus_id}/source/{path}#L{start_line}-L{end_line}"


def module_uri(corpus_id: str, module_name: str) -> str:
    return f"{SCHEME}://{corpus_id}/module/{module_name}"


def symbol_uri(corpus_id: str, symbol: str) -> str:
    return f"{SCHEME}://{corpus_id}/symbol/{symbol}"
