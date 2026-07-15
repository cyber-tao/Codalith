"""Strict public query models shared by services and MCP schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SearchHit(StrictModel):
    corpus_id: str
    revision: str
    generation_id: str
    uri: str
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    language: str
    symbol: str | None = None
    symbol_id: str | None = None
    kind: str
    score: float = Field(ge=0)
    backends: list[str]
    snippet: str


class SearchResponse(StrictModel):
    query: str
    target: str
    strategy: Literal["auto", "semantic", "text", "symbol"]
    degraded: bool
    warnings: list[str]
    hits: list[SearchHit]


class ContextSource(SearchHit):
    stale: bool
    text: str
    sha256: str
    indexed_sha256: str
    truncated: bool
    decode_replacements: int = Field(ge=0)


class ContextResponse(StrictModel):
    query: str
    target: str
    confidence: Literal["high", "medium", "low", "none"]
    degraded: bool
    warnings: list[str]
    sources: list[ContextSource]


class ReadResponse(StrictModel):
    corpus_id: str
    revision: str
    uri: str
    path: str
    start_line: int
    end_line: int
    total_lines: int
    text: str
    sha256: str
    indexed_sha256: str
    stale: bool
    truncated: bool
    decode_replacements: int


class SymbolDefinition(StrictModel):
    corpus_id: str
    revision: str
    generation_id: str
    uri: str
    symbol_id: str
    qualified_name: str
    name: str
    kind: str
    signature: str
    source_uri: str
    path: str
    start_line: int
    end_line: int
    module: str | None
    metadata: dict[str, Any]


class SymbolResponse(StrictModel):
    query: str
    target: str
    exact: bool
    definitions: list[SymbolDefinition]
    warnings: list[str]


class GraphNode(StrictModel):
    corpus_id: str
    revision: str
    symbol_id: str
    uri: str
    qualified_name: str
    kind: str
    source_uri: str


class GraphEdge(StrictModel):
    source_uri: str | None
    target_uri: str | None
    target_name: str
    kind: str
    resolution: Literal["resolved", "ambiguous", "unresolved"]
    evidence_uri: str


class GraphResponse(StrictModel):
    root_uri: str
    direction: Literal["incoming", "outgoing", "both"]
    depth: int
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncated: bool
    warnings: list[str]


class CompareChange(StrictModel):
    comparison_key: str
    status: Literal["added", "removed", "changed", "unchanged", "ambiguous"]
    from_symbols: list[SymbolDefinition]
    to_symbols: list[SymbolDefinition]
    changed_fields: list[str]
    truncated: bool


class CompareResponse(StrictModel):
    from_corpus: str
    to_corpus: str
    changes: list[CompareChange]
    truncated: bool


class CorpusStatus(StrictModel):
    corpus_id: str
    revision: str
    state: Literal["ready", "degraded", "missing", "invalid"]
    generation_id: str | None
    semantic_available: bool
    files: int
    symbols: int
    references: int
    module_dependencies: int
    message: str | None = None


class StatusResponse(StrictModel):
    target: str
    ready: bool
    corpora: list[CorpusStatus]
