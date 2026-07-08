"""Verify built-in Knowledge Cards against a configured corpus."""

from __future__ import annotations

import argparse
import json
import os

from codalith.cards.generator import attach_source_hashes, built_in_cards
from codalith.cards.verifier import KnowledgeCardVerifier
from codalith.coderag.adapter import CodeRAGAdapter
from codalith.corpus.registry import CorpusRegistry
from codalith.corpus.uri_resolver import URIResolver
from codalith.semantic.store import SemanticStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default="configs/corpus_registry.json")
    parser.add_argument(
        "--version", default=None, help="Corpus version (defaults to the registry default corpus)"
    )
    parser.add_argument("--corpus", help="Explicit corpus id or version alias")
    args = parser.parse_args(argv)
    registry = CorpusRegistry.from_file(args.registry)
    resolver = URIResolver(registry)
    adapter = CodeRAGAdapter(registry)
    semantic_target = os.getenv("CODALITH_SEMANTIC_DSN") or os.getenv("CODALITH_SEMANTIC_DB")
    semantic_store = SemanticStore(semantic_target) if semantic_target else None
    verifier = KnowledgeCardVerifier(resolver, adapter, semantic_store)
    corpus = registry.get_corpus(args.corpus) if args.corpus else registry.get_base(args.version)
    cards = attach_source_hashes(
        built_in_cards(
            corpus_id=corpus.corpus_id,
            version=corpus.version_label,
            seed_cards_path=corpus.seed_cards_path,
        ),
        resolver,
        adapter,
    )
    results = []
    for card in cards:
        result = verifier.verify(card)
        if result.ok and semantic_store is not None:
            semantic_store.upsert_knowledge_card(card.verified())
        results.append({"card_id": card.card_id, **result.as_dict()})
    if semantic_store is not None:
        semantic_store.close()
    print(json.dumps({"results": results}, indent=2))
    return 0 if all(item["ok"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
