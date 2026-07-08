"""Index any configured corpus through the CodeRAG adapter."""

from __future__ import annotations

import argparse
import json

from codalith.coderag.adapter import CodeRAGAdapter
from codalith.corpus.registry import CorpusRegistry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default="configs/corpus_registry.json")
    parser.add_argument("--corpus", help="Corpus id or engine version alias")
    parser.add_argument("--version", help="Engine version alias used when --corpus is omitted")
    parser.add_argument("--path", help="Optional corpus-relative subpath to reindex")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument(
        "--smoke-file",
        default="src/core/cache.py",
        help="Relative path read to verify the corpus mount when --smoke is set",
    )
    args = parser.parse_args(argv)
    registry = CorpusRegistry.from_file(args.registry)
    corpus = registry.get_corpus(args.corpus) if args.corpus else registry.get_engine(args.version)
    adapter = CodeRAGAdapter(registry)
    if args.smoke:
        content = adapter.get_file(corpus.corpus_id, args.smoke_file, 1, 5)
        status = {
            "corpus_id": corpus.corpus_id,
            "source_root": str(corpus.source_root),
            "indexed_root": str(corpus.indexed_root),
            "smoke_file": args.smoke_file,
            "smoke_lines": len(content.splitlines()),
            "mode": "smoke",
        }
        print(json.dumps(status, indent=2, sort_keys=True))
        return 0
    status = adapter.reindex(corpus.corpus_id, path=args.path, full=args.full)
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
