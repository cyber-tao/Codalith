"""Strict MCP input schemas and tool metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from codalith.query.models import (
    CompareResponse,
    ContextResponse,
    GraphResponse,
    ReadResponse,
    SearchResponse,
    StatusResponse,
    StrictModel,
    SymbolResponse,
)


class SearchInput(StrictModel):
    query: str = Field(min_length=1, max_length=4096)
    target: str | None = None
    strategy: Literal["auto", "semantic", "text", "symbol"] = "auto"
    limit: int = Field(default=10, ge=1, le=50)


class ContextInput(StrictModel):
    query: str = Field(min_length=1, max_length=4096)
    target: str | None = None
    max_spans: int = Field(default=8, ge=1, le=20)
    max_chars: int = Field(default=24_000, ge=1_000, le=100_000)


class ReadInput(StrictModel):
    uri: str = Field(min_length=1, max_length=8192)


class SymbolInput(StrictModel):
    query: str = Field(min_length=1, max_length=4096)
    target: str | None = None
    exact: bool = True
    limit: int = Field(default=20, ge=1, le=100)


class GraphInput(StrictModel):
    root_uri: str = Field(min_length=1, max_length=8192)
    direction: Literal["incoming", "outgoing", "both"] = "both"
    depth: int = Field(default=1, ge=1, le=3)
    limit: int = Field(default=200, ge=1, le=1000)


class CompareInput(StrictModel):
    from_corpus: str = Field(min_length=1, max_length=64)
    to_corpus: str = Field(min_length=1, max_length=64)
    include_unchanged: bool = False
    limit: int = Field(default=500, ge=1, le=1000)


class StatusInput(StrictModel):
    target: str | None = None


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_model: type[StrictModel]
    output_model: type[StrictModel]


TOOLS = (
    ToolDefinition(
        "codalith_search",
        "Search versioned source using semantic, exact-text, and structural retrieval.",
        SearchInput,
        SearchResponse,
    ),
    ToolDefinition(
        "codalith_context",
        "Compile a bounded, source-hashed context pack for an implementation question.",
        ContextInput,
        ContextResponse,
    ),
    ToolDefinition(
        "codalith_read",
        "Read the exact line range identified by a canonical Codalith source URI.",
        ReadInput,
        ReadResponse,
    ),
    ToolDefinition(
        "codalith_symbol",
        "Resolve exact or fuzzy symbol definitions in an indexed corpus or workspace.",
        SymbolInput,
        SymbolResponse,
    ),
    ToolDefinition(
        "codalith_graph",
        "Traverse a bounded, evidence-backed symbol reference graph.",
        GraphInput,
        GraphResponse,
    ),
    ToolDefinition(
        "codalith_compare",
        "Compare structural symbols between two immutable corpus generations.",
        CompareInput,
        CompareResponse,
    ),
    ToolDefinition(
        "codalith_status",
        "Read index readiness and provenance without loading models or scanning source.",
        StatusInput,
        StatusResponse,
    ),
)

TOOL_BY_NAME = {definition.name: definition for definition in TOOLS}
