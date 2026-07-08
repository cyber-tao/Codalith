"""Domain-neutral semantic store input types."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ModuleDependency:
    from_module: str
    to_module: str
    dep_kind: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CompileGuard:
    macro: str
    line: int
    expression: str
    end_line: int | None = None


@dataclass(frozen=True, slots=True)
class SourceSymbol:
    name: str
    kind: str
    line: int
    qualified_name: str | None = None
    signature: str | None = None
    build_guard: str | None = None
    is_definition: bool = False
    confidence: float = 1.0
    metadata: dict[str, str] = field(default_factory=dict)
