"""Record corpus metadata and report semantic store status for a corpus.

Codalith core ships no domain extractors. Populating the graph is delegated to
external extractor pipelines writing through SemanticStore; this job only
upserts corpus metadata and reports store counts.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from codalith.cli.common import add_corpus_arguments, resolve_corpus
from codalith.corpus.registry import Corpus
from codalith.semantic.store import SemanticStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    add_corpus_arguments(parser)
    parser.add_argument("--corpus-id", help="Output corpus id override")
    parser.add_argument("--output", default="reports/semantic_summary.json")
    parser.add_argument("--semantic-db")
    args = parser.parse_args(argv)

    _, corpus = resolve_corpus(args)
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
            "semantic_store": semantic_db,
            **store.semantic_status(corpus_id),
        }
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
