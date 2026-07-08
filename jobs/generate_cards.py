"""Generate built-in Knowledge Cards into a corpus card root."""

from __future__ import annotations

import argparse
import json

from codalith.cards.generator import attach_source_hashes, built_in_cards, write_cards
from codalith.cards.verifier import KnowledgeCardVerifier
from codalith.coderag.adapter import CodeRAGAdapter
from codalith.corpus.registry import CorpusRegistry
from codalith.corpus.uri_resolver import URIResolver


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default="configs/corpus_registry.json")
    parser.add_argument(
        "--version", default=None, help="Corpus version (defaults to the registry default corpus)"
    )
    parser.add_argument("--corpus", help="Explicit corpus id or version alias")
    args = parser.parse_args(argv)
    registry = CorpusRegistry.from_file(args.registry)
    corpus = registry.get_corpus(args.corpus) if args.corpus else registry.get_base(args.version)
    resolver = URIResolver(registry)
    adapter = CodeRAGAdapter(registry)
    cards = attach_source_hashes(
        built_in_cards(
            corpus_id=corpus.corpus_id,
            version=corpus.version_label,
            seed_cards_path=corpus.seed_cards_path,
        ),
        resolver,
        adapter,
    )
    # Cards only ship as "verified" after passing evidence verification here.
    # The context compiler relies on this invariant when it reports card hits
    # as verified in Context Packs.
    verifier = KnowledgeCardVerifier(resolver, adapter)
    failures: dict[str, list[str]] = {}
    verified = []
    for card in cards:
        result = verifier.verify(card)
        if result.ok:
            verified.append(card.verified())
        else:
            failures[card.card_id] = result.errors
    if failures:
        print(json.dumps({"error": "card verification failed", "failures": failures}, indent=2))
        return 1
    written = write_cards(verified, corpus.card_root)
    if corpus.indexed_root != corpus.card_root:
        written.extend(write_cards(verified, corpus.indexed_root))
    print(json.dumps({"count": len(written), "paths": [str(path) for path in written]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
