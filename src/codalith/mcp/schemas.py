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

_TARGET_DESCRIPTION = (
    "Registered corpus or workspace id matching the requested project/version. "
    "Omitting it selects the server default."
)


class SearchInput(StrictModel):
    query: str = Field(min_length=1, max_length=4096)
    target: str | None = Field(default=None, description=_TARGET_DESCRIPTION)
    strategy: Literal["auto", "semantic", "text", "symbol"] = Field(
        default="auto",
        description=(
            "auto for mixed discovery; semantic for concepts; text matches the entire "
            "literal query, not a natural-language question; symbol for names."
        ),
    )
    limit: int = Field(default=10, ge=1, le=50)


class ContextInput(StrictModel):
    query: str = Field(min_length=1, max_length=4096)
    target: str | None = Field(default=None, description=_TARGET_DESCRIPTION)
    max_spans: int = Field(default=8, ge=1, le=20)
    max_chars: int = Field(default=24_000, ge=1_000, le=100_000)


class ReadInput(StrictModel):
    uri: str = Field(
        min_length=1,
        max_length=8192,
        description=(
            "Source URI, never a symbol URI: codalith://<corpus>/source/<path>#Lx-Ly. "
            "To fetch a new range, change the fragment here; cite the response's actual URI."
        ),
    )


class SymbolInput(StrictModel):
    query: str = Field(min_length=1, max_length=4096)
    target: str | None = Field(default=None, description=_TARGET_DESCRIPTION)
    exact: bool = True
    limit: int = Field(default=20, ge=1, le=100)


class GraphInput(StrictModel):
    root_uri: str = Field(
        min_length=1,
        max_length=8192,
        description="A returned codalith://<corpus>/symbol/<symbol_id> URI, not a source URI.",
    )
    direction: Literal["incoming", "outgoing", "both"] = "both"
    depth: int = Field(default=1, ge=1, le=3)
    limit: int = Field(default=200, ge=1, le=1000)


class CompareInput(StrictModel):
    from_corpus: str = Field(min_length=1, max_length=64)
    to_corpus: str = Field(min_length=1, max_length=64)
    include_unchanged: bool = False
    limit: int = Field(default=500, ge=1, le=1000)


class StatusInput(StrictModel):
    target: str | None = Field(default=None, description=_TARGET_DESCRIPTION)


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_model: type[StrictModel]
    output_model: type[StrictModel]


TOOLS = (
    ToolDefinition(
        "codalith_search",
        "Discover versioned source with ranked snippets and source URIs. Use context/read "
        "when a snippet lacks the implementation needed to support a claim.",
        SearchInput,
        SearchResponse,
    ),
    ToolDefinition(
        "codalith_context",
        "Retrieve a bounded, source-hashed implementation context pack. Fresh matching-hash "
        "text can be cited directly using its unchanged source URI; no verification reread needed.",
        ContextInput,
        ContextResponse,
    ),
    ToolDefinition(
        "codalith_read",
        "Read missing source or request a different line range, verifying indexed/current "
        "hashes. Cite the returned source URI exactly, including any clamped range.",
        ReadInput,
        ReadResponse,
    ),
    ToolDefinition(
        "codalith_symbol",
        "Resolve exact/fuzzy definitions. Definition uri identifies a symbol; source_uri "
        "locates its source. Read missing implementation text before making behavioral claims.",
        SymbolInput,
        SymbolResponse,
    ),
    ToolDefinition(
        "codalith_graph",
        "Traverse static references from a symbol URI. Inspect resolution and truncation; "
        "this does not enumerate runtime registrations or establish callback execution order.",
        GraphInput,
        GraphResponse,
    ),
    ToolDefinition(
        "codalith_compare",
        "Compare structural symbols between two indexed versions. Changes are static "
        "evidence, not a guarantee of runtime equivalence.",
        CompareInput,
        CompareResponse,
    ),
    ToolDefinition(
        "codalith_status",
        "Check index readiness/version/provenance when uncertain. Registration alone is "
        "not readiness. Does not load models or scan source; reuse a still-applicable result.",
        StatusInput,
        StatusResponse,
    ),
)

TOOL_BY_NAME = {definition.name: definition for definition in TOOLS}
