"""Knowledge Card verifier."""

from __future__ import annotations

from dataclasses import dataclass, field

from codalith.cards.hashing import source_sha256
from codalith.cards.schema import KnowledgeCard
from codalith.coderag.adapter import CodeRAGAdapter
from codalith.corpus.uri_resolver import URIResolver
from codalith.semantic.store import SemanticStore


@dataclass(frozen=True, slots=True)
class VerificationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {"ok": self.ok, "errors": self.errors}


class KnowledgeCardVerifier:
    def __init__(
        self,
        resolver: URIResolver,
        adapter: CodeRAGAdapter,
        semantic_store: SemanticStore | None = None,
    ) -> None:
        self.resolver = resolver
        self.adapter = adapter
        self.semantic_store = semantic_store

    def verify(self, card: KnowledgeCard) -> VerificationResult:
        errors: list[str] = []
        evidence_semantically_scanned = False
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
                    if (
                        self.semantic_store is not None
                        and self.semantic_store.source_file_exists(card.corpus_id, resolved.relative_path)
                    ):
                        evidence_semantically_scanned = True
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
            elif self.semantic_store is not None:
                self._verify_related_node(
                    card.corpus_id,
                    node,
                    errors,
                    evidence_semantically_scanned=evidence_semantically_scanned,
                )
        if self.semantic_store is not None and not card.related_nodes:
            errors.append("Card must declare related semantic nodes when semantic verification is enabled")
        return VerificationResult(ok=not errors, errors=errors)

    def _verify_related_node(
        self,
        corpus_id: str,
        node: str,
        errors: list[str],
        *,
        evidence_semantically_scanned: bool,
    ) -> None:
        store = self.semantic_store
        if store is None:
            return
        if node.startswith("module:"):
            module = node.split(":", maxsplit=1)[1]
            if not store.module_exists(corpus_id, module):
                errors.append(f"Related module does not exist in semantic DB: {module}")
        elif node.startswith("symbol:") and not store.symbol_exists(corpus_id, node):
            if evidence_semantically_scanned:
                errors.append(f"Related semantic node does not exist in semantic DB: {node}")
