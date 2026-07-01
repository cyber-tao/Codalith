"""Run v0 semantic extractors over a configured corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from codalith.corpus.registry import CorpusRegistry
from codalith.semantic.db import SemanticStore
from codalith.semantic.extractors.build_cs import BuildCsExtractor, write_module_deps
from codalith.semantic.extractors.compile_guards import extract_compile_guards
from codalith.semantic.extractors.cpp_symbols import extract_cpp_symbols
from codalith.semantic.extractors.uht_reflection import UHTReflectionExtractor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default="configs/corpus_registry.yaml")
    parser.add_argument("--version", default="5.7.4")
    parser.add_argument("--output", default="reports/semantic_summary.json")
    parser.add_argument("--min-modules", type=int, default=0)
    parser.add_argument("--min-reflection-entities", type=int, default=0)
    parser.add_argument("--min-guards", type=int, default=0)
    parser.add_argument("--semantic-db")
    parser.add_argument("--stop-after-min", action="store_true")
    args = parser.parse_args(argv)

    registry = CorpusRegistry.from_file(args.registry)
    corpus = registry.get_engine(args.version)
    root = corpus.indexed_root if corpus.indexed_root.exists() else corpus.source_root
    store = SemanticStore(args.semantic_db) if args.semantic_db else None
    summary = extract_semantic_summary(
        root,
        corpus_id=corpus.corpus_id,
        version=corpus.ue_version or args.version,
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


def extract_semantic_summary(
    root: Path,
    *,
    corpus_id: str = "ue-5.7.4",
    version: str = "5.7.4",
    store: SemanticStore | None = None,
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
    cpp_symbols = 0
    headers_scanned = 0

    for build_file in root.rglob("*.Build.cs"):
        try:
            extracted = build_extractor.extract_file(build_file)
        except OSError:
            continue
        build_module_name = build_file.name.removesuffix(".Build.cs")
        modules.add(build_module_name)
        deps += len(extracted)
        if store is not None:
            write_module_deps(
                store,
                corpus_id=corpus_id,
                evidence_uri=_source_uri(version, root, build_file, 1, 1),
                dependencies=extracted,
            )

    for header in root.rglob("*.h"):
        if _skip_path(header):
            continue
        try:
            text = header.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        headers_scanned += 1
        module_name = _module_from_path(header)
        declaration_uri = _source_uri(version, root, header, 1, max(1, len(text.splitlines())))
        extracted_reflection = reflection_extractor.extract_text(
            text,
            module_name=module_name,
            declaration_uri=declaration_uri,
        )
        reflection_entities += len(extracted_reflection)
        extracted_guards = extract_compile_guards(text)
        guards += len(extracted_guards)
        extracted_symbols = extract_cpp_symbols(text)
        cpp_symbols += len(extracted_symbols)
        if store is not None:
            relative = header.relative_to(root).as_posix()
            for entity in extracted_reflection:
                store.upsert_reflection_entity(corpus_id=corpus_id, entity=entity)
            for guard in extracted_guards:
                store.upsert_compile_guard(
                    corpus_id=corpus_id,
                    path=relative,
                    guard=guard,
                    evidence_uri=_source_uri(version, root, header, guard.line, guard.line),
                )
            for symbol in extracted_symbols:
                store.upsert_cpp_symbol(
                    corpus_id=corpus_id,
                    path=relative,
                    symbol=symbol,
                    evidence_uri=_source_uri(version, root, header, symbol.line, symbol.line),
                    module_name=module_name,
                )
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
        "cpp_symbols": cpp_symbols,
        "semantic_store": store.semantic_status(corpus_id) if store is not None else None,
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


def _source_uri(version: str, root: Path, path: Path, start: int, end: int) -> str:
    relative = path.relative_to(root).as_posix()
    return f"ue://{version}/source/{relative}#L{start}-L{end}"


def _module_from_path(path: Path) -> str | None:
    parts = path.parts
    for marker in ("Runtime", "Developer", "Editor"):
        if marker in parts:
            index = parts.index(marker)
            if index + 1 < len(parts):
                return parts[index + 1]
    return None


if __name__ == "__main__":
    raise SystemExit(main())
