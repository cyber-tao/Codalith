"""Built-in Knowledge Card generator.

Seed card topics are domain data and live in configs/seed_cards.json; set
CODALITH_SEED_CARDS to point at an alternative file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

from codalith.cards import CARDS_DIR
from codalith.cards.hashing import source_sha256
from codalith.cards.renderer import render_markdown
from codalith.cards.schema import CardClaim, CardEvidence, KnowledgeCard
from codalith.coderag.adapter import CodeRAGAdapter
from codalith.config import load_config
from codalith.corpus.uri_resolver import URIResolver
from codalith.corpus.uris import source_uri
from codalith.errors import ConfigurationError

_DEFAULT_SEED_CARDS_PATH = "configs/seed_cards.json"


@dataclass(frozen=True, slots=True)
class SeedTopic:
    card_id: str
    card_type: str
    title: str
    path: str
    related_node: str


@lru_cache(maxsize=1)
def seed_topics() -> tuple[SeedTopic, ...]:
    """Load and cache the curated seed card topics."""
    path = _seed_cards_path()
    raw = load_config(path)
    topics = raw.get("topics")
    if not isinstance(topics, list) or not topics:
        raise ConfigurationError(f"{path} must define a non-empty 'topics' list")
    loaded: list[SeedTopic] = []
    for index, item in enumerate(topics):
        if not isinstance(item, dict):
            raise ConfigurationError(f"{path} topics[{index}] must be an object")
        try:
            loaded.append(
                SeedTopic(
                    card_id=str(item["card_id"]),
                    card_type=str(item["card_type"]),
                    title=str(item["title"]),
                    path=str(item["path"]),
                    related_node=str(item["related_node"]),
                )
            )
        except KeyError as exc:
            raise ConfigurationError(f"{path} topics[{index}] is missing key {exc}") from exc
    return tuple(loaded)


def _seed_cards_path() -> Path:
    override = os.getenv("CODALITH_SEED_CARDS")
    if override:
        return Path(override)
    cwd_path = Path(_DEFAULT_SEED_CARDS_PATH)
    if cwd_path.exists():
        return cwd_path
    return Path(__file__).resolve().parents[3] / _DEFAULT_SEED_CARDS_PATH


def built_in_cards(*, corpus_id: str, version: str) -> list[KnowledgeCard]:
    cards: list[KnowledgeCard] = []
    for topic in seed_topics():
        evidence_uri = source_uri(corpus_id, topic.path, 1, 20)
        cards.append(
            KnowledgeCard(
                corpus_id=corpus_id,
                card_id=topic.card_id,
                card_type=topic.card_type,
                title=topic.title,
                version=version,
                body_markdown=(
                    f"{topic.title} is a seed knowledge card. It is verified only when "
                    "its evidence URI resolves against the configured corpus."
                ),
                claims=[
                    CardClaim(
                        text=f"{topic.title} must be grounded in {corpus_id} source evidence.",
                        evidence=[CardEvidence(uri=evidence_uri, reason="seed evidence")],
                    )
                ],
                related_nodes=[topic.related_node],
            )
        )
    return cards


def write_cards(cards: list[KnowledgeCard], root: str | Path) -> list[Path]:
    root_path = Path(root)
    written: list[Path] = []
    for card in cards:
        target = root_path / CARDS_DIR / card.card_type.title() / f"{card.card_id}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_markdown(card), encoding="utf-8")
        written.append(target)
    return written


def attach_source_hashes(
    cards: list[KnowledgeCard],
    resolver: URIResolver,
    adapter: CodeRAGAdapter,
) -> list[KnowledgeCard]:
    hashed_cards: list[KnowledgeCard] = []
    for card in cards:
        source_hashes: dict[str, str] = {}
        for claim in card.claims:
            for evidence in claim.evidence:
                resolved = resolver.resolve_source(evidence.uri)
                if resolved.start_line is None or resolved.end_line is None:
                    continue
                content = adapter.get_file(
                    resolved.corpus_id,
                    resolved.relative_path,
                    resolved.start_line,
                    resolved.end_line,
                )
                source_hashes[evidence.uri] = source_sha256(content)
        hashed_cards.append(replace(card, source_hashes=source_hashes))
    return hashed_cards
