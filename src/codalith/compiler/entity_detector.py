"""Lightweight entity detection from a query string.

Detection is generic; corpus-specific vocabulary is passed in by the caller.
"""

from __future__ import annotations

import re

from codalith.text import camel_words, contains_word

_IDENT_RE = re.compile(r"\b[A-Z][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)?\b")

# Capitalized English question/aux/filler words the identifier regex would
# otherwise mistake for symbols at sentence starts. Domain vocabulary that
# must also be ignored comes from identifier_stopwords() in the config.
_ENGLISH_STOPWORDS = frozenset(
    {
        "A",
        "An",
        "And",
        "Are",
        "At",
        "But",
        "By",
        "Can",
        "Describe",
        "Do",
        "Does",
        "Explain",
        "Find",
        "For",
        "From",
        "How",
        "If",
        "In",
        "Is",
        "It",
        "My",
        "Not",
        "Of",
        "On",
        "Or",
        "Should",
        "Show",
        "That",
        "The",
        "These",
        "This",
        "Those",
        "To",
        "Use",
        "We",
        "What",
        "When",
        "Where",
        "Which",
        "Who",
        "Why",
        "Will",
        "With",
        "You",
        "Your",
    }
)


def detect_identifiers(query: str, *, stopwords: frozenset[str] = frozenset()) -> list[str]:
    ignored = _ENGLISH_STOPWORDS | stopwords
    found = dict.fromkeys(_IDENT_RE.findall(query))
    return [identifier for identifier in found if identifier not in ignored]


def detect_modules(query: str, *, module_hints: frozenset[str] = frozenset()) -> list[str]:
    lower = query.lower()
    found = [
        module
        for module in module_hints
        if any(contains_word(variant, lower) for variant in _module_variants(module))
    ]
    return sorted(found)


def _module_variants(module: str) -> set[str]:
    # "EnhancedInput" should match both "enhancedinput" and "enhanced input".
    variants = {module.lower()}
    words = camel_words(module)
    if len(words) > 1 and "".join(words) == module:
        variants.add(" ".join(word.lower() for word in words))
    return variants
