"""Context Pack schema v0."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, NotRequired, TypedDict


@dataclass(frozen=True, slots=True)
class AnswerPolicy:
    version_pinned: bool = True
    must_cite_source: bool = True
    do_not_answer_from_memory: bool = True


@dataclass(frozen=True, slots=True)
class ContextSummary:
    text: str
    generated_by: str = "codalith"


class ModuleEntry(TypedDict):
    name: str
    uri: str
    reason: str


class SymbolEntry(TypedDict, total=False):
    name: str
    uri: str
    kind: str
    reason: str
    qualified_name: NotRequired[str | None]
    module: NotRequired[str | None]


class CardEntry(TypedDict):
    uri: str
    title: str
    verification_status: str


class SourceSpanEntry(TypedDict, total=False):
    uri: str
    path: str
    start_line: int
    end_line: int
    reason: str
    source: str
    corpus_id: NotRequired[str | None]
    corpus_kind: NotRequired[str | None]
    source_hash: NotRequired[str | None]
    language: NotRequired[str | None]
    kind: NotRequired[str | None]
    extractor: NotRequired[object]
    confidence: NotRequired[float]
    module: NotRequired[str | None]
    score: NotRequired[float]
    guard: NotRequired[object]


class RecommendedCall(TypedDict):
    tool: str
    args: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ContextPack:
    query: str
    version: str
    corpus_id: str
    source_revision: str
    project: str | None
    intent: str
    confidence: str
    modules: list[ModuleEntry]
    symbols: list[SymbolEntry]
    cards: list[CardEntry]
    source_spans: list[SourceSpanEntry]
    graph_edges: list[dict[str, Any]]
    caveats: list[str]
    recommended_next_calls: list[RecommendedCall]
    schema_version: str = "0.2"
    answer_policy: AnswerPolicy = field(default_factory=AnswerPolicy)
    summary: ContextSummary | None = None

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if data["summary"] is None:
            data["summary"] = {
                "text": "This context pack contains source-backed retrieval results.",
                "generated_by": "codalith",
            }
        return data
