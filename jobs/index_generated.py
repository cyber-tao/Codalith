"""Index an optional generated/log/crash/build-output corpus."""

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
    parser.add_argument("--corpus-id")
    args = parser.parse_args(argv)
    registry = CorpusRegistry.from_file(args.registry)
    if args.corpus_id:
        corpus = registry.generated[args.corpus_id]
    else:
        engine = registry.get_engine(args.version)
        candidates = registry.get_generated_for_engine(engine)
        if not candidates:
            raise SystemExit(f"No generated corpus is configured for engine {engine.corpus_id}")
        corpus = candidates[0]
    print(json.dumps(CodeRAGAdapter(registry).reindex(corpus.corpus_id), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
