"""Canonical codalith:// URI construction shared across all layers.

Every corpus is addressed by its corpus id regardless of kind; the kind of a
corpus is registry data, not URI syntax. Facets under a corpus authority are
``source``, ``module``, ``symbol``, and ``card``.
"""

from __future__ import annotations

import re

SCHEME = "codalith"

_SOURCE_URI_RE = re.compile(
    rf"{SCHEME}://(?P<corpus_id>[^/]+)/source/(?P<path>[^#]+)(?:#(?P<fragment>.*))?$"
)
_LINE_RE = re.compile(r"^L(?P<start>[1-9]\d*)(?:-L?(?P<end>[1-9]\d*))?$")


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


def parse_line_fragment(fragment: str) -> tuple[int | None, int | None]:
    """Parse a source URI line fragment into ``(start, end)``.

    Accepts ``Lstart``, ``Lstart-Lend``, and ``Lstart-end``. An empty fragment
    yields ``(None, None)``. When only a start line is present, end defaults to
    start.
    """
    if not fragment:
        return None, None
    match = _LINE_RE.match(fragment)
    if not match:
        raise ValueError(f"Invalid line fragment: #{fragment}")
    start = int(match.group("start"))
    end = int(match.group("end") or start)
    if end < start:
        raise ValueError(f"Invalid descending line range: #{fragment}")
    return start, end


def parse_source_uri(uri: str) -> tuple[str, str, int, int] | None:
    """Split a source URI into (corpus_id, path, start, end).

    Line fragments may be ``#Lstart`` or ``#Lstart-Lend``; a single start line
    uses end = start. This is a syntactic parse only; resolving against
    configured corpora is the job of ``URIResolver``.
    """
    match = _SOURCE_URI_RE.match(uri)
    if not match:
        return None
    fragment = match.group("fragment") or ""
    try:
        start, end = parse_line_fragment(fragment)
    except ValueError:
        return None
    if start is None or end is None:
        return None
    return (
        match.group("corpus_id"),
        match.group("path"),
        start,
        end,
    )
