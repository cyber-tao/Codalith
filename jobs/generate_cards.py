"""Generate built-in Knowledge Cards into a corpus card root."""

from __future__ import annotations

import argparse
import json

from ue_context.cards.generator import built_in_cards, write_cards
from ue_context.corpus.registry import CorpusRegistry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default="configs/corpus_registry.yaml")
    parser.add_argument("--version", default="5.7.4")
    args = parser.parse_args(argv)
    registry = CorpusRegistry.from_file(args.registry)
    corpus = registry.get_engine(args.version)
    cards = built_in_cards(corpus_id=corpus.corpus_id, version=corpus.ue_version or args.version)
    verified = [card.verified() for card in cards]
    written = write_cards(verified, corpus.card_root)
    if corpus.indexed_root != corpus.card_root:
        written.extend(write_cards(verified, corpus.indexed_root))
    print(json.dumps({"count": len(written), "paths": [str(path) for path in written]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
