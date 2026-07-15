"""Language-adapter contracts for deterministic structural extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class SymbolObservation:
    qualified_name: str
    name: str
    kind: str
    signature: str
    path: str
    start_line: int
    end_line: int
    module: str | None
    parent_qualified_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReferenceObservation:
    source_qualified_name: str | None
    target_name: str
    kind: str
    path: str
    line: int


@dataclass(frozen=True, slots=True)
class ModuleDependencyObservation:
    source_module: str
    target_module: str
    kind: str
    path: str
    line: int


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    language: str
    symbols: tuple[SymbolObservation, ...] = ()
    references: tuple[ReferenceObservation, ...] = ()
    module_dependencies: tuple[ModuleDependencyObservation, ...] = ()
    warnings: tuple[str, ...] = ()


class LanguageAdapter(Protocol):
    adapter_id: str
    version: int

    def supports(self, path: Path) -> bool: ...

    def extract(self, path: str, text: str) -> ExtractionResult: ...
