"""Record corpus metadata and report semantic store status for a corpus.

Codalith core ships no domain extractors: a corpus without a semantic profile
is a valid generic source corpus and yields an empty semantic summary.
Populating the graph is delegated to external extractor pipelines writing
through SemanticStore.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from codalith.corpus.registry import Corpus, CorpusRegistry
from codalith.errors import ConfigurationError
from codalith.semantic.store import SemanticStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default="configs/corpus_registry.json")
    parser.add_argument(
        "--version", default=None, help="Corpus version (defaults to the registry default corpus)"
    )
    parser.add_argument("--project")
    parser.add_argument("--corpus", help="Explicit corpus id or version alias")
    parser.add_argument("--corpus-id", help="Output corpus id override")
    parser.add_argument("--output", default="reports/semantic_summary.json")
    parser.add_argument("--semantic-db")
    args = parser.parse_args(argv)

    registry = CorpusRegistry.from_file(args.registry)
    if args.corpus:
        corpus = registry.get_corpus(args.corpus)
    elif args.project:
        corpus = registry.get_project(args.project)
    else:
        corpus = registry.get_engine(args.version)
    if corpus.semantic_profile is not None:
        raise ConfigurationError(
            f"Unknown semantic profile: {corpus.semantic_profile}. "
            "Codalith core ships no domain extractors."
        )
    corpus_id = args.corpus_id or corpus.corpus_id
    summary = _summarize(corpus_id, args.semantic_db, corpus)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _summarize(corpus_id: str, semantic_db: str | None, corpus: Corpus) -> dict[str, Any]:
    # Without a configured store an in-memory store yields the same zeroed
    # status shape, so consumers see a stable summary schema.
    store = SemanticStore(semantic_db) if semantic_db else SemanticStore()
    try:
        if semantic_db:
            store.upsert_corpus(corpus)
        return {
            "profile": None,
            "semantic_store": semantic_db,
            **store.semantic_status(corpus_id),
        }
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
