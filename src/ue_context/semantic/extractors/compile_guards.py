"""Compile guard extractor for UE-specific preprocessor branches."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CompileGuard:
    macro: str
    line: int
    expression: str


_GUARD_RE = re.compile(r"^\s*#\s*(?:if|ifdef|ifndef)\s+(?P<expr>.*)$")
_KNOWN_RE = re.compile(r"\b(WITH_EDITOR|UE_BUILD_SHIPPING|UE_SERVER|WITH_SERVER_CODE)\b")


def extract_compile_guards(text: str) -> list[CompileGuard]:
    guards: list[CompileGuard] = []
    for number, line in enumerate(text.splitlines(), start=1):
        match = _GUARD_RE.match(line)
        if not match:
            continue
        expression = match.group("expr").strip()
        for macro in _KNOWN_RE.findall(expression):
            guards.append(CompileGuard(macro=macro, line=number, expression=expression))
    return guards
