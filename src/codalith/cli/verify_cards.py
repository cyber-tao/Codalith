"""Verify built-in Knowledge Cards against a configured corpus."""

from __future__ import annotations

import argparse
import json
import os

from codalith.cards.verifier import KnowledgeCardVerifier
from codalith.cli.common import add_corpus_arguments, load_seed_cards, resolve_corpus
from codalith.coderag import CodeRAGAdapter
from codalith.corpus.uri_resolver import URIResolver
from codalith.semantic.store import SemanticStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    add_corpus_arguments(parser)
    args = parser.parse_args(argv)
    registry, corpus = resolve_corpus(args)
    resolver = URIResolver(registry)
    adapter = CodeRAGAdapter(registry)
    semantic_target = os.getenv("CODALITH_SEMANTIC_DSN") or os.getenv("CODALITH_SEMANTIC_DB")
    semantic_store = SemanticStore(semantic_target) if semantic_target else None
    verifier = KnowledgeCardVerifier(resolver, adapter, semantic_store)
    cards = load_seed_cards(corpus, resolver, adapter)
    results = []
    for card in cards:
        result = verifier.verify(card)
        status = result.verified_card(card).verification_status if result.ok else "unverified"
        results.append(
            {
                "card_id": card.card_id,
                "verification_status": status,
                **result.as_dict(),
            }
        )
    if semantic_store is not None:
        semantic_store.close()
    print(json.dumps({"results": results}, indent=2))
    return 0 if all(item["ok"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
