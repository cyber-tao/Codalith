"""Run v0 semantic extractors over a configured corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ue_context.corpus.registry import CorpusRegistry
from ue_context.semantic.extractors.build_cs import BuildCsExtractor
from ue_context.semantic.extractors.compile_guards import extract_compile_guards
from ue_context.semantic.extractors.uht_reflection import UHTReflectionExtractor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default="configs/corpus_registry.yaml")
    parser.add_argument("--version", default="5.7.4")
    parser.add_argument("--output", default="reports/semantic_summary.json")
    parser.add_argument("--min-modules", type=int, default=0)
    parser.add_argument("--min-reflection-entities", type=int, default=0)
    parser.add_argument("--min-guards", type=int, default=0)
    parser.add_argument("--stop-after-min", action="store_true")
    args = parser.parse_args(argv)

    registry = CorpusRegistry.from_file(args.registry)
    corpus = registry.get_engine(args.version)
    root = corpus.indexed_root if corpus.indexed_root.exists() else corpus.source_root
    summary = extract_semantic_summary(
        root,
        stop_after_min=args.stop_after_min,
        min_modules=args.min_modules,
        min_reflection_entities=args.min_reflection_entities,
        min_guards=args.min_guards,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    _enforce(summary, args.min_modules, args.min_reflection_entities, args.min_guards)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def extract_semantic_summary(
    root: Path,
    *,
    stop_after_min: bool = False,
    min_modules: int = 0,
    min_reflection_entities: int = 0,
    min_guards: int = 0,
) -> dict[str, Any]:
    build_extractor = BuildCsExtractor()
    reflection_extractor = UHTReflectionExtractor()
    modules: set[str] = set()
    deps = 0
    reflection_entities = 0
    guards = 0
    headers_scanned = 0

    for build_file in root.rglob("*.Build.cs"):
        try:
            extracted = build_extractor.extract_file(build_file)
        except OSError:
            continue
        module = build_file.name.removesuffix(".Build.cs")
        modules.add(module)
        deps += len(extracted)

    for header in root.rglob("*.h"):
        if _skip_path(header):
            continue
        try:
            text = header.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        headers_scanned += 1
        reflection_entities += len(reflection_extractor.extract_text(text))
        guards += len(extract_compile_guards(text))
        if stop_after_min and (
            len(modules) >= min_modules
            and reflection_entities >= min_reflection_entities
            and guards >= min_guards
        ):
            break

    return {
        "root": str(root),
        "modules": len(modules),
        "module_dependencies": deps,
        "headers_scanned": headers_scanned,
        "reflection_entities": reflection_entities,
        "compile_guards": guards,
    }


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


def _skip_path(path: Path) -> bool:
    ignored = {"Intermediate", "Binaries", "DerivedDataCache", "Saved", ".git"}
    return any(part in ignored for part in path.parts)


if __name__ == "__main__":
    raise SystemExit(main())
