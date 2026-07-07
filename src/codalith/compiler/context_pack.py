"""Context Pack schema v0."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class AnswerPolicy:
    version_pinned: bool = True
    must_cite_source: bool = True
    do_not_answer_from_memory: bool = True


@dataclass(frozen=True, slots=True)
class ContextSummary:
    text: str
    generated_by: str = "codalith"


@dataclass(frozen=True, slots=True)
class ContextPack:
    query: str
    version: str
    corpus_id: str
    source_commit: str
    project: str | None
    intent: str
    confidence: str
    modules: list[dict[str, Any]]
    symbols: list[dict[str, Any]]
    cards: list[dict[str, Any]]
    source_spans: list[dict[str, Any]]
    graph_edges: list[dict[str, Any]]
    caveats: list[str]
    recommended_next_calls: list[dict[str, Any]]
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
