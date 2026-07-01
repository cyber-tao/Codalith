"""Prepare a CodeRAG indexed root layout for a corpus."""

from __future__ import annotations

import argparse
import json

from codalith.corpus.registry import CorpusRegistry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default="configs/corpus_registry.yaml")
    parser.add_argument("--version", default="5.7.4")
    args = parser.parse_args(argv)
    corpus = CorpusRegistry.from_file(args.registry).get_engine(args.version)
    corpus.indexed_root.mkdir(parents=True, exist_ok=True)
    corpus.card_root.mkdir(parents=True, exist_ok=True)
    print(json.dumps({"indexed_root": str(corpus.indexed_root), "card_root": str(corpus.card_root)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
