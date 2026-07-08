"""Domain-neutral semantic store input types."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ModuleDependency:
    from_module: str
    to_module: str
    dep_kind: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReflectionEntity:
    kind: str
    name: str
    owner: str | None
    specifiers: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    declaration_uri: str | None = None
    generated_header: str | None = None
    module_name: str | None = None
    confidence: float = 1.0


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


@dataclass(frozen=True, slots=True)
class TargetDefinition:
    name: str
    target_type: str | None = None
    extra_modules: list[str] = field(default_factory=list)
    build_settings: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PluginModule:
    name: str
    module_type: str | None = None
    loading_phase: str | None = None
    supported_platforms: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PluginDescriptor:
    name: str
    path: str
    modules: list[PluginModule]
    supported_platforms: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProjectDescriptor:
    name: str
    path: str
    modules: list[PluginModule]
    plugins: dict[str, bool] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
