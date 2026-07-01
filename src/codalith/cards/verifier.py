"""Knowledge Card verifier."""

from __future__ import annotations

from dataclasses import dataclass, field

from codalith.cards.hashing import source_sha256
from codalith.cards.schema import KnowledgeCard
from codalith.coderag.adapter import CodeRAGAdapter
from codalith.corpus.uri_resolver import URIResolver


@dataclass(frozen=True, slots=True)
class VerificationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {"ok": self.ok, "errors": self.errors}


class KnowledgeCardVerifier:
    def __init__(self, resolver: URIResolver, adapter: CodeRAGAdapter) -> None:
        self.resolver = resolver
        self.adapter = adapter

    def verify(self, card: KnowledgeCard) -> VerificationResult:
        errors: list[str] = []
        if not card.claims:
            errors.append("Card must contain at least one claim")
        for index, claim in enumerate(card.claims):
            if not claim.evidence:
                errors.append(f"Claim {index} has no evidence")
            for evidence in claim.evidence:
                try:
                    resolved = self.resolver.resolve_source(evidence.uri)
                    if resolved.start_line is None or resolved.end_line is None:
                        errors.append(f"Evidence URI has no line range: {evidence.uri}")
                        continue
                    content = self.adapter.get_file(
                        resolved.corpus_id,
                        resolved.relative_path,
                        resolved.start_line,
                        resolved.end_line,
                    )
                    if not content.strip():
                        errors.append(f"Evidence URI has empty content: {evidence.uri}")
                    expected_hash = card.source_hashes.get(evidence.uri)
                    if expected_hash:
                        actual_hash = source_sha256(content)
                        if actual_hash != expected_hash:
                            errors.append(f"Evidence hash mismatch: {evidence.uri}")
                except Exception as exc:  # noqa: BLE001 - verifier reports all failures.
                    errors.append(f"Invalid evidence URI {evidence.uri}: {exc}")
        for node in card.related_nodes:
            if not node.strip():
                errors.append("Related node cannot be blank")
        return VerificationResult(ok=not errors, errors=errors)
