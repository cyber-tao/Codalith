"""Verify built-in Knowledge Cards against a configured corpus."""

from __future__ import annotations

import argparse
import json

from ue_context.cards.generator import attach_source_hashes, built_in_cards
from ue_context.cards.verifier import KnowledgeCardVerifier
from ue_context.coderag.adapter import CodeRAGAdapter
from ue_context.corpus.registry import CorpusRegistry
from ue_context.corpus.uri_resolver import URIResolver


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default="configs/corpus_registry.yaml")
    parser.add_argument("--version", default="5.7.4")
    args = parser.parse_args(argv)
    registry = CorpusRegistry.from_file(args.registry)
    resolver = URIResolver(registry)
    adapter = CodeRAGAdapter(registry)
    verifier = KnowledgeCardVerifier(resolver, adapter)
    corpus = registry.get_engine(args.version)
    cards = attach_source_hashes(
        built_in_cards(corpus_id=corpus.corpus_id, version=corpus.ue_version or args.version),
        resolver,
        adapter,
    )
    results = [
        {"card_id": card.card_id, **verifier.verify(card).as_dict()}
        for card in cards
    ]
    print(json.dumps({"results": results}, indent=2))
    return 0 if all(item["ok"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
