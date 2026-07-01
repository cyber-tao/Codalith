"""Render Knowledge Cards into Markdown for UE_KNOWLEDGE."""

from __future__ import annotations

from codalith.cards.schema import KnowledgeCard


def render_markdown(card: KnowledgeCard) -> str:
    lines = [
        "---",
        f"card_id: {card.card_id}",
        f"card_type: {card.card_type}",
        f"version: {card.version}",
        f"verification_status: {card.verification_status}",
    ]
    if card.source_hashes:
        lines.append("source_hashes:")
        for uri, source_hash in sorted(card.source_hashes.items()):
            lines.append(f"  {uri}: {source_hash}")
    lines.extend(
        [
            "---",
            "",
            f"# {card.title}",
            "",
            card.body_markdown.strip(),
            "",
            "## Claims",
        ]
    )
    for claim in card.claims:
        lines.append(f"- {claim.text}")
        for evidence in claim.evidence:
            hash_suffix = ""
            if evidence.uri in card.source_hashes:
                hash_suffix = f"; sha256={card.source_hashes[evidence.uri]}"
            lines.append(f"  - evidence: {evidence.uri} ({evidence.reason}{hash_suffix})")
    return "\n".join(lines).strip() + "\n"
