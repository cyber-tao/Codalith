"""Compile guard extractor for UE-specific preprocessor branches."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CompileGuard:
    macro: str
    line: int
    expression: str
    end_line: int | None = None


_GUARD_RE = re.compile(r"^\s*#\s*(?:if|ifdef|ifndef)\s+(?P<expr>.*)$")
_ENDIF_RE = re.compile(r"^\s*#\s*endif\b")
_KNOWN_RE = re.compile(
    r"\b("
    r"WITH_EDITOR|WITH_EDITORONLY_DATA|WITH_SERVER_CODE|WITH_CHAOS|WITH_NANITE|"
    r"UE_SERVER|UE_BUILD_SHIPPING|UE_BUILD_DEVELOPMENT|UE_BUILD_DEBUG|UE_BUILD_TEST|"
    r"PLATFORM_[A-Z0-9_]+"
    r")\b"
)


def extract_compile_guards(text: str) -> list[CompileGuard]:
    guards: list[CompileGuard] = []
    pending: list[tuple[int, str, list[str]]] = []
    lines = text.splitlines()
    for number, line in enumerate(lines, start=1):
        match = _GUARD_RE.match(line)
        if match:
            expression = match.group("expr").strip()
            macros = list(dict.fromkeys(_KNOWN_RE.findall(expression)))
            if macros:
                pending.append((number, expression, macros))
            continue
        if _ENDIF_RE.match(line) and pending:
            start, expression, macros = pending.pop()
            for macro in macros:
                guards.append(
                    CompileGuard(macro=macro, line=start, expression=expression, end_line=number)
                )
    final_line = max(1, len(lines))
    for start, expression, macros in pending:
        for macro in macros:
            guards.append(CompileGuard(macro=macro, line=start, expression=expression, end_line=final_line))
    return guards
