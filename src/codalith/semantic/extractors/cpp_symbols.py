"""C++ symbol-lite extractor for public lookup."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class CppSymbol:
    name: str
    kind: str
    line: int
    qualified_name: str | None = None
    signature: str | None = None
    build_guard: str | None = None
    is_definition: bool = False
    confidence: float = 1.0
    metadata: dict[str, str] = field(default_factory=dict)


_CLASS_RE = re.compile(r"\b(class|struct)\s+(?:[A-Z0-9_]+_API\s+)?([A-Za-z_][A-Za-z0-9_]*)")
_ENUM_RE = re.compile(r"\benum\s+(?:class\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)")
_NAMESPACE_RE = re.compile(r"\bnamespace\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{")
_DEFINE_RE = re.compile(r"^\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)")
_DELEGATE_RE = re.compile(r"\bDECLARE_[A-Z0-9_]*DELEGATE[A-Z0-9_]*\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)")
_CVAR_RE = re.compile(r"\b(?:TAutoConsoleVariable|FAutoConsole(?:Variable|Command))\b[^;]*\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_FUNC_RE = re.compile(
    r"\b(?P<prefix>(?:[A-Za-z_][A-Za-z0-9_:<>~*&]+\s+)+)"
    r"(?P<name>[A-Za-z_~][A-Za-z0-9_:~]*)\s*"
    r"\((?P<args>[^;{}]*)\)\s*(?:const\s*)?(?P<terminator>[;{])"
)


def extract_cpp_symbols(text: str) -> list[CppSymbol]:
    symbols: list[CppSymbol] = []
    namespace_stack: list[tuple[str, int]] = []
    guard_stack: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        guard_match = re.match(r"^\s*#\s*(?:if|ifdef|ifndef)\s+(?P<expr>.*)$", line)
        if guard_match:
            guard_stack.append(guard_match.group("expr").strip())
        elif re.match(r"^\s*#\s*endif\b", line) and guard_stack:
            guard_stack.pop()
        namespace_match = _NAMESPACE_RE.search(line)
        if namespace_match:
            namespace_stack.append((namespace_match.group(1), _brace_delta(line)))
        elif namespace_stack:
            name, depth = namespace_stack[-1]
            depth += _brace_delta(line)
            if depth <= 0:
                namespace_stack.pop()
            else:
                namespace_stack[-1] = (name, depth)
        namespace = "::".join(item[0] for item in namespace_stack)
        guard = " && ".join(guard_stack) or None

        define_match = _DEFINE_RE.search(line)
        if define_match:
            symbols.append(
                _symbol(
                    define_match.group(1),
                    "macro",
                    number,
                    namespace,
                    stripped,
                    guard,
                )
            )
        delegate_match = _DELEGATE_RE.search(line)
        if delegate_match:
            symbols.append(
                _symbol(
                    delegate_match.group(1),
                    "delegate",
                    number,
                    namespace,
                    stripped,
                    guard,
                )
            )
        cvar_match = _CVAR_RE.search(line)
        if cvar_match:
            symbols.append(
                _symbol(cvar_match.group(1), "cvar", number, namespace, stripped, guard, True)
            )
        class_match = _CLASS_RE.search(line)
        if class_match:
            symbols.append(
                _symbol(class_match.group(2), class_match.group(1), number, namespace, stripped, guard)
            )
        enum_match = _ENUM_RE.search(line)
        if enum_match:
            symbols.append(_symbol(enum_match.group("name"), "enum", number, namespace, stripped, guard))
        func_match = _FUNC_RE.search(line)
        if func_match:
            raw_name = func_match.group("name")
            name = raw_name.split("::")[-1]
            symbols.append(
                _symbol(
                    name,
                    "method" if "::" in raw_name else "function",
                    number,
                    namespace,
                    stripped,
                    guard,
                    func_match.group("terminator") == "{",
                    qualified_override=raw_name if "::" in raw_name else None,
                )
            )
    return _dedupe(symbols)


def _symbol(
    name: str,
    kind: str,
    line: int,
    namespace: str,
    signature: str,
    guard: str | None,
    is_definition: bool = False,
    *,
    qualified_override: str | None = None,
) -> CppSymbol:
    qualified = qualified_override or (f"{namespace}::{name}" if namespace else name)
    return CppSymbol(
        name=name,
        kind=kind,
        line=line,
        qualified_name=qualified,
        signature=signature,
        build_guard=guard,
        is_definition=is_definition,
    )


def _brace_delta(line: str) -> int:
    return line.count("{") - line.count("}")


def _dedupe(symbols: list[CppSymbol]) -> list[CppSymbol]:
    seen: set[tuple[str, str, int]] = set()
    result: list[CppSymbol] = []
    for symbol in symbols:
        key = (symbol.kind, symbol.qualified_name or symbol.name, symbol.line)
        if key not in seen:
            seen.add(key)
            result.append(symbol)
    return result
