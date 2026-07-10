"""Filesystem-backed Knowledge Card source of truth."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from codalith.cards import CARDS_DIR
from codalith.cards.parser import parse_card_markdown
from codalith.cards.schema import KnowledgeCard
from codalith.corpus.registry import CorpusRegistry
from codalith.corpus.uris import card_uri
from codalith.text import tokenize

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CardDocument:
    card: KnowledgeCard
    path: Path
    markdown: str

    @property
    def uri(self) -> str:
        return card_uri(self.card.corpus_id, self.card.card_type, self.card.card_id)


@dataclass(frozen=True, slots=True)
class CardMatch:
    document: CardDocument
    score: float


class FileCardRepository:
    def __init__(self, registry: CorpusRegistry) -> None:
        self.registry = registry

    def list_documents(self, corpus_id: str) -> list[CardDocument]:
        corpus = self.registry.get_corpus(corpus_id)
        root = corpus.card_root / CARDS_DIR
        if not root.is_dir():
            return []
        documents: list[CardDocument] = []
        for path in sorted(root.glob("*/*.md")):
            try:
                markdown = path.read_text(encoding="utf-8")
                card = parse_card_markdown(markdown)
                if card.corpus_id != corpus.corpus_id:
                    raise ValueError(
                        f"card corpus_id {card.corpus_id!r} does not match {corpus.corpus_id!r}"
                    )
                documents.append(CardDocument(card=card, path=path, markdown=markdown))
            except (OSError, ValueError) as exc:
                _LOG.warning("Skipping invalid Knowledge Card %s: %s", path, exc)
        return documents

    def get_document(
        self,
        corpus_id: str,
        card_type: str,
        card_id: str,
    ) -> CardDocument | None:
        return next(
            (
                document
                for document in self.list_documents(corpus_id)
                if document.card.card_type == card_type and document.card.card_id == card_id
            ),
            None,
        )

    def search(
        self,
        corpus_ids: list[str],
        query: str,
        *,
        identifiers: list[str] | None = None,
        limit: int = 8,
    ) -> list[CardMatch]:
        query_tokens = set(tokenize(query, min_length=2))
        identifier_tokens = {item.lower() for item in identifiers or []}
        lowered_query = query.lower().strip()
        matches: list[CardMatch] = []
        for corpus_id in corpus_ids:
            for document in self.list_documents(corpus_id):
                card = document.card
                if not card.has_verified_evidence:
                    continue
                text = "\n".join(
                    [
                        card.card_id,
                        card.card_type,
                        card.title,
                        card.body_markdown,
                        *card.related_nodes,
                        *(claim.text for claim in card.claims),
                    ]
                ).lower()
                text_tokens = set(tokenize(text, min_length=2))
                score = float(len(query_tokens & text_tokens))
                score += 3.0 * len(identifier_tokens & text_tokens)
                if lowered_query and lowered_query in text:
                    score += 5.0
                if score > 0:
                    matches.append(CardMatch(document=document, score=score))
        return sorted(
            matches,
            key=lambda match: (
                -match.score,
                match.document.card.title,
                match.document.card.card_id,
            ),
        )[:limit]
