"""Render Knowledge Cards into Markdown for UE_KNOWLEDGE."""

from __future__ import annotations

from ue_context.cards.schema import KnowledgeCard


def render_markdown(card: KnowledgeCard) -> str:
    lines = [
        "---",
        f"card_id: {card.card_id}",
        f"card_type: {card.card_type}",
        f"version: {card.version}",
        f"verification_status: {card.verification_status}",
        "---",
        "",
        f"# {card.title}",
        "",
        card.body_markdown.strip(),
        "",
        "## Claims",
    ]
    for claim in card.claims:
        lines.append(f"- {claim.text}")
        for evidence in claim.evidence:
            lines.append(f"  - evidence: {evidence.uri} ({evidence.reason})")
    return "\n".join(lines).strip() + "\n"
