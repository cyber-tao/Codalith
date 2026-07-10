"""Render Knowledge Cards into Markdown for the cards directory."""

from __future__ import annotations

import json

from codalith.cards.schema import KnowledgeCard


def render_markdown(card: KnowledgeCard) -> str:
    lines = [
        "---",
        f"corpus_id: {card.corpus_id}",
        f"card_id: {card.card_id}",
        f"card_type: {card.card_type}",
        f"title: {card.title}",
        f"version: {card.version}",
        f"verification_status: {card.verification_status}",
        f"generated_by: {card.generated_by}",
        "related_nodes_json: "
        + json.dumps(card.related_nodes, ensure_ascii=False, separators=(",", ":")),
        "source_hashes_json: "
        + json.dumps(card.source_hashes, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        "claims_json: "
        + json.dumps(
            [
                {
                    "text": claim.text,
                    "evidence": [
                        {"uri": evidence.uri, "reason": evidence.reason}
                        for evidence in claim.evidence
                    ],
                }
                for claim in card.claims
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    ]
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
