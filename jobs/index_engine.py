"""Index an engine corpus through the CodeRAG adapter."""

from __future__ import annotations

import argparse
import json

from codalith.coderag.adapter import CodeRAGAdapter
from codalith.corpus.registry import CorpusRegistry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default="configs/corpus_registry.json")
    parser.add_argument(
        "--version", default=None, help="Engine version (defaults to the registry default engine)"
    )
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument(
        "--smoke-file",
        default="Engine/Source/Runtime/Engine/Classes/GameFramework/Actor.h",
        help="Relative path read to verify the corpus mount when --smoke is set",
    )
    args = parser.parse_args(argv)
    registry = CorpusRegistry.from_file(args.registry)
    corpus = registry.get_engine(args.version)
    adapter = CodeRAGAdapter(registry)
    if args.smoke:
        content = adapter.get_file(corpus.corpus_id, args.smoke_file, 1, 5)
        status = {
            "corpus_id": corpus.corpus_id,
            "watched_dir": str(corpus.source_root),
            "smoke_file": args.smoke_file,
            "smoke_lines": len(content.splitlines()),
            "mode": "smoke",
        }
        print(json.dumps(status, indent=2, sort_keys=True))
        return 0
    status = adapter.reindex(corpus.corpus_id)
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
