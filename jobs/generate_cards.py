"""Generate built-in Knowledge Cards into a corpus card root."""

from __future__ import annotations

import argparse
import json

from codalith.cards.generator import write_cards
from codalith.cards.verifier import KnowledgeCardVerifier
from codalith.coderag import CodeRAGAdapter
from codalith.corpus.uri_resolver import URIResolver
from jobs.common import add_corpus_arguments, load_seed_cards, resolve_corpus


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    add_corpus_arguments(parser)
    args = parser.parse_args(argv)
    registry, corpus = resolve_corpus(args)
    resolver = URIResolver(registry)
    adapter = CodeRAGAdapter(registry)
    cards = load_seed_cards(corpus, resolver, adapter)
    # Cards only ship as "verified" after passing evidence verification here;
    # Context Packs report whatever status the rendered front matter carries.
    verifier = KnowledgeCardVerifier(resolver, adapter)
    failures: dict[str, list[str]] = {}
    verified = []
    for card in cards:
        result = verifier.verify(card)
        if result.ok:
            verified.append(result.verified_card(card))
        else:
            failures[card.card_id] = result.errors
    if failures:
        print(json.dumps({"error": "card verification failed", "failures": failures}, indent=2))
        return 1
    written = write_cards(verified, corpus.card_root)
    print(json.dumps({"count": len(written), "paths": [str(path) for path in written]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
