"""Lightweight UE entity detection from a query string."""

from __future__ import annotations

import re

_IDENT_RE = re.compile(r"\b[A-Z][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)?\b")
_CAMEL_SPLIT_RE = re.compile(r"[A-Z][a-z0-9]+")

# Capitalized English question/aux/filler words the identifier regex would
# otherwise mistake for UE symbols at sentence starts.
_IDENT_STOPWORDS = frozenset(
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
        "Unreal",
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

_MODULE_HINTS = {
    "Core",
    "CoreUObject",
    "EnhancedInput",
    "Engine",
    "GameplayAbilities",
    "Net",
    "Renderer",
    "NetCore",
    "UnrealEd",
}


def detect_identifiers(query: str) -> list[str]:
    found = dict.fromkeys(_IDENT_RE.findall(query))
    return [identifier for identifier in found if identifier not in _IDENT_STOPWORDS]


def detect_modules(query: str) -> list[str]:
    lower = query.lower()
    found = [
        module
        for module in _MODULE_HINTS
        if any(_phrase_matches(variant, lower) for variant in _module_variants(module))
    ]
    return sorted(found)


def _module_variants(module: str) -> set[str]:
    # "EnhancedInput" should match both "enhancedinput" and "enhanced input".
    variants = {module.lower()}
    words = _CAMEL_SPLIT_RE.findall(module)
    if len(words) > 1 and "".join(words) == module:
        variants.add(" ".join(word.lower() for word in words))
    return variants


def _phrase_matches(phrase: str, lower_query: str) -> bool:
    return re.search(rf"\b{re.escape(phrase)}\b", lower_query) is not None
