"""Typed records returned by the structural index."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class FileRecord:
    path: str
    language: str
    sha256: str
    size_bytes: int
    line_count: int
    module: str | None


@dataclass(frozen=True, slots=True)
class SymbolRecord:
    symbol_id: str
    comparison_key: str
    qualified_name: str
    name: str
    kind: str
    signature: str
    path: str
    start_line: int
    end_line: int
    module: str | None
    parent_symbol_id: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ReferenceRecord:
    reference_id: int
    source_symbol_id: str | None
    target_name: str
    target_symbol_id: str | None
    resolution: str
    kind: str
    path: str
    line: int


@dataclass(frozen=True, slots=True)
class ModuleDependencyRecord:
    dependency_id: int
    source_module: str
    target_module: str
    kind: str
    path: str
    line: int
