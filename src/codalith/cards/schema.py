"""Knowledge Card schema v0."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class CardEvidence:
    uri: str
    reason: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CardEvidence:
        return cls(uri=str(data["uri"]), reason=str(data.get("reason", "source evidence")))


@dataclass(frozen=True, slots=True)
class CardClaim:
    text: str
    evidence: list[CardEvidence]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CardClaim:
        return cls(
            text=str(data["text"]),
            evidence=[CardEvidence.from_dict(item) for item in data.get("evidence", [])],
        )


@dataclass(frozen=True, slots=True)
class KnowledgeCard:
    corpus_id: str
    card_id: str
    card_type: str
    title: str
    version: str
    body_markdown: str
    claims: list[CardClaim]
    related_nodes: list[str] = field(default_factory=list)
    source_hashes: dict[str, str] = field(default_factory=dict)
    verification_status: str = "unverified"
    generated_by: str = "codalith"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KnowledgeCard:
        return cls(
            corpus_id=str(data["corpus_id"]),
            card_id=str(data["card_id"]),
            card_type=str(data["card_type"]),
            title=str(data["title"]),
            version=str(data.get("version", "")),
            body_markdown=str(data.get("body_markdown", "")),
            claims=[CardClaim.from_dict(item) for item in data.get("claims", [])],
            related_nodes=[str(item) for item in data.get("related_nodes", [])],
            source_hashes={str(key): str(value) for key, value in data.get("source_hashes", {}).items()},
            verification_status=str(data.get("verification_status", "unverified")),
            generated_by=str(data.get("generated_by", "codalith")),
        )

    def verified(self) -> KnowledgeCard:
        data = asdict(self)
        data["verification_status"] = "verified"
        return KnowledgeCard.from_dict(data)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
