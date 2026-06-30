"""Index an engine corpus through the CodeRAG adapter."""

from __future__ import annotations

import argparse
import json

from ue_context.coderag.adapter import CodeRAGAdapter
from ue_context.corpus.registry import CorpusRegistry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default="configs/corpus_registry.yaml")
    parser.add_argument("--version", default="5.7.4")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args(argv)
    registry = CorpusRegistry.from_file(args.registry)
    corpus = registry.get_engine(args.version)
    adapter = CodeRAGAdapter(registry)
    if args.smoke:
        actor_path = "Engine/Source/Runtime/Engine/Classes/GameFramework/Actor.h"
        content = adapter.get_file(corpus.corpus_id, actor_path, 1, 5)
        status = {
            "corpus_id": corpus.corpus_id,
            "watched_dir": str(corpus.source_root),
            "smoke_file": actor_path,
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
