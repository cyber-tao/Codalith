"""Index a project corpus."""

from __future__ import annotations

import argparse
import json

from codalith.coderag.adapter import CodeRAGAdapter
from codalith.corpus.registry import CorpusRegistry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("--registry", default="configs/corpus_registry.json")
    args = parser.parse_args(argv)
    registry = CorpusRegistry.from_file(args.registry)
    corpus = registry.get_project(args.project)
    print(json.dumps(CodeRAGAdapter(registry).reindex(corpus.corpus_id), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
