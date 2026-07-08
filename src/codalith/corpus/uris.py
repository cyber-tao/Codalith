"""Canonical codalith:// URI construction shared across all layers.

Every corpus is addressed by its corpus id regardless of kind; the kind of a
corpus is registry data, not URI syntax. Facets under a corpus authority are
``source``, ``module``, ``symbol``, and ``card``.
"""

from __future__ import annotations

import re

SCHEME = "codalith"

_SOURCE_URI_RE = re.compile(
    rf"{SCHEME}://(?P<corpus_id>[^/]+)/source/(?P<path>[^#]+)#L(?P<start>\d+)-L(?P<end>\d+)"
)


def corpus_uri(corpus_id: str) -> str:
    return f"{SCHEME}://{corpus_id}"


def source_uri(corpus_id: str, path: str, start_line: int, end_line: int) -> str:
    return f"{SCHEME}://{corpus_id}/source/{path}#L{start_line}-L{end_line}"


def module_uri(corpus_id: str, module_name: str) -> str:
    return f"{SCHEME}://{corpus_id}/module/{module_name}"


def symbol_uri(corpus_id: str, symbol: str) -> str:
    return f"{SCHEME}://{corpus_id}/symbol/{symbol}"


def card_uri(corpus_id: str, card_type: str, card_id: str) -> str:
    return f"{SCHEME}://{corpus_id}/card/{card_type}/{card_id}"


def parse_source_uri(uri: str) -> tuple[str, str, int, int] | None:
    """Split a ranged source URI into (corpus_id, path, start, end).

    This is a syntactic parse only; resolving against configured corpora is
    the job of ``URIResolver``.
    """
    match = _SOURCE_URI_RE.match(uri)
    if not match:
        return None
    return (
        match.group("corpus_id"),
        match.group("path"),
        int(match.group("start")),
        int(match.group("end")),
    )
