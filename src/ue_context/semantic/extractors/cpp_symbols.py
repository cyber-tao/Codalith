"""C++ symbol-lite extractor for public lookup."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CppSymbol:
    name: str
    kind: str
    line: int


_CLASS_RE = re.compile(r"\b(class|struct)\s+(?:[A-Z0-9_]+_API\s+)?([A-Za-z_][A-Za-z0-9_]*)")
_FUNC_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_:<>~]*)\s*\([^;{}]*\)\s*(?:const\s*)?[;{]")


def extract_cpp_symbols(text: str) -> list[CppSymbol]:
    symbols: list[CppSymbol] = []
    for number, line in enumerate(text.splitlines(), start=1):
        class_match = _CLASS_RE.search(line)
        if class_match:
            symbols.append(CppSymbol(name=class_match.group(2), kind=class_match.group(1), line=number))
        func_match = _FUNC_RE.search(line)
        if func_match:
            symbols.append(CppSymbol(name=func_match.group(1), kind="function", line=number))
    return symbols
