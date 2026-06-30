"""Lightweight UE entity detection from a query string."""

from __future__ import annotations

import re

_IDENT_RE = re.compile(r"\b[A-Z][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)?\b")
_MODULE_HINTS = {
    "Core",
    "CoreUObject",
    "Engine",
    "UnrealEd",
    "Renderer",
    "NetCore",
    "GameplayAbilities",
}


def detect_identifiers(query: str) -> list[str]:
    return list(dict.fromkeys(_IDENT_RE.findall(query)))


def detect_modules(query: str) -> list[str]:
    found = [module for module in _MODULE_HINTS if module.lower() in query.lower()]
    return sorted(found)
