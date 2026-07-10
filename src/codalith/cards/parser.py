"""Parse Codalith's deterministic Knowledge Card Markdown format."""

from __future__ import annotations

import json
from typing import Any

from codalith.cards.schema import KnowledgeCard

_JSON_FIELDS = {
    "related_nodes_json": "related_nodes",
    "source_hashes_json": "source_hashes",
    "claims_json": "claims",
}


def parse_card_markdown(markdown: str) -> KnowledgeCard:
    lines = markdown.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("Knowledge Card must start with front matter")
    try:
        front_end = lines.index("---", 1)
    except ValueError as exc:
        raise ValueError("Knowledge Card front matter is not terminated") from exc
    front: dict[str, str] = {}
    for line in lines[1:front_end]:
        key, separator, value = line.partition(":")
        if not separator or not key.strip():
            raise ValueError(f"Invalid Knowledge Card front-matter line: {line!r}")
        front[key.strip()] = value.strip()
    data: dict[str, Any] = {
        key: _required(front, key)
        for key in (
            "corpus_id",
            "card_id",
            "card_type",
            "title",
            "version",
            "verification_status",
            "generated_by",
        )
    }
    for encoded_key, target_key in _JSON_FIELDS.items():
        try:
            data[target_key] = json.loads(_required(front, encoded_key))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in Knowledge Card field {encoded_key!r}") from exc
    content = lines[front_end + 1 :]
    heading = f"# {data['title']}"
    if heading not in content:
        raise ValueError("Knowledge Card title heading does not match front matter")
    heading_index = content.index(heading)
    try:
        claims_index = content.index("## Claims", heading_index + 1)
    except ValueError as exc:
        raise ValueError("Knowledge Card is missing its Claims section") from exc
    data["body_markdown"] = "\n".join(content[heading_index + 1 : claims_index]).strip()
    return KnowledgeCard.from_dict(data)


def _required(front: dict[str, str], key: str) -> str:
    value = front.get(key)
    if value is None or not value:
        raise ValueError(f"Knowledge Card front matter is missing {key!r}")
    return value
