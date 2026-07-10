"""Knowledge Card schema v0."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, cast

CardVerificationStatus = Literal[
    "unverified",
    "evidence_verified",
    "semantic_verified",
]
VERIFIED_CARD_STATUSES = frozenset({"evidence_verified", "semantic_verified"})


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
    verification_status: CardVerificationStatus = "unverified"
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
            verification_status=_verification_status(data.get("verification_status", "unverified")),
            generated_by=str(data.get("generated_by", "codalith")),
        )

    def with_verification(self, status: CardVerificationStatus) -> KnowledgeCard:
        data = asdict(self)
        data["verification_status"] = status
        return KnowledgeCard.from_dict(data)

    @property
    def has_verified_evidence(self) -> bool:
        return self.verification_status in VERIFIED_CARD_STATUSES

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _verification_status(value: object) -> CardVerificationStatus:
    normalized = str(value)
    if normalized not in {"unverified", "evidence_verified", "semantic_verified"}:
        raise ValueError(f"Unknown card verification status: {normalized}")
    return cast(CardVerificationStatus, normalized)
