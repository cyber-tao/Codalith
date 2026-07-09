"""Shared text normalization and tokenization primitives.

Intent detection, entity detection, source priors, and local retrieval all
match query text against the same normalized token space; keeping the
primitives in one module prevents the per-layer tokenizers from drifting
apart.
"""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[a-z_][a-z0-9_]*")
_WORD_TERM_RE = re.compile(r"^[a-z0-9_ ]+$")
_CAMEL_WORD_RE = re.compile(r"[A-Z][a-z0-9]+")


def normalize(text: str) -> str:
    """Lowercase text and fold hyphens into spaces."""
    return text.lower().replace("-", " ")


def tokenize(text: str, *, min_length: int = 1) -> list[str]:
    """Identifier-style tokens of the normalized text, in order of appearance.

    Tokens start with a letter or underscore, so bare numbers never become
    tokens; they would otherwise inflate substring-count scoring.
    """
    return [token for token in _TOKEN_RE.findall(normalize(text)) if len(token) >= min_length]


def contains_word(term: str, lower_text: str) -> bool:
    """Whether ``term`` occurs in ``lower_text`` (which must be lowercased).

    ASCII word terms (including spaced phrases) require word boundaries so
    "error" does not match "terror"; CJK and mixed terms have no word
    boundaries and keep substring semantics.
    """
    if _WORD_TERM_RE.match(term):
        return re.search(rf"\b{re.escape(term)}\b", lower_text) is not None
    return term in lower_text


def camel_words(identifier: str) -> list[str]:
    """Capitalized words of a CamelCase identifier ("NetCore" -> ["Net", "Core"])."""
    return _CAMEL_WORD_RE.findall(identifier)
