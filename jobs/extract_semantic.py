"""Run the configured domain extractor profile over a corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from codalith.corpus.registry import CorpusRegistry
from codalith.semantic.extractors import run_profile
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
    parser.add_argument("--min-modules", type=int, default=0)
    parser.add_argument("--min-reflection-entities", type=int, default=0)
    parser.add_argument("--min-guards", type=int, default=0)
    parser.add_argument("--semantic-db")
    parser.add_argument("--stop-after-min", action="store_true")
    args = parser.parse_args(argv)

    registry = CorpusRegistry.from_file(args.registry)
    if args.corpus:
        corpus = registry.get_corpus(args.corpus)
    elif args.project:
        corpus = registry.get_project(args.project)
    else:
        corpus = registry.get_engine(args.version)
    root = corpus.indexed_root if corpus.indexed_root.exists() else corpus.source_root
    store = SemanticStore(args.semantic_db) if args.semantic_db else None
    if store is not None:
        store.upsert_corpus(corpus)
    summary = run_profile(
        corpus.semantic_profile,
        root,
        corpus_id=args.corpus_id or corpus.corpus_id,
        store=store,
        stop_after_min=args.stop_after_min,
        min_modules=args.min_modules,
        min_reflection_entities=args.min_reflection_entities,
        min_guards=args.min_guards,
    )
    if store is not None:
        store.close()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    _enforce(summary, args.min_modules, args.min_reflection_entities, args.min_guards)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _enforce(summary: dict[str, Any], min_modules: int, min_reflection_entities: int, min_guards: int) -> None:
    failures = []
    if summary["modules"] < min_modules:
        failures.append(f"modules {summary['modules']} < {min_modules}")
    if summary["reflection_entities"] < min_reflection_entities:
        failures.append(f"reflection_entities {summary['reflection_entities']} < {min_reflection_entities}")
    if summary["compile_guards"] < min_guards:
        failures.append(f"compile_guards {summary['compile_guards']} < {min_guards}")
    if failures:
        raise SystemExit("; ".join(failures))


if __name__ == "__main__":
    raise SystemExit(main())
