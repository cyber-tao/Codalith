"""Run v0 semantic extractors over a configured corpus."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from codalith.cards.hashing import source_sha256
from codalith.corpus.registry import CorpusRegistry
from codalith.semantic.extractors.build_cs import BuildCsExtractor, write_module_deps
from codalith.semantic.extractors.compile_guards import extract_compile_guards
from codalith.semantic.extractors.cpp_symbols import extract_cpp_symbols
from codalith.semantic.extractors.target_cs import extract_target_file
from codalith.semantic.extractors.uht_reflection import UHTReflectionExtractor
from codalith.semantic.extractors.uplugin import extract_uplugin
from codalith.semantic.extractors.uproject import extract_uproject
from codalith.semantic.store import SemanticStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default="configs/corpus_registry.yaml")
    parser.add_argument("--version", default="5.7.4")
    parser.add_argument("--project")
    parser.add_argument("--corpus-id")
    parser.add_argument("--output", default="reports/semantic_summary.json")
    parser.add_argument("--min-modules", type=int, default=0)
    parser.add_argument("--min-reflection-entities", type=int, default=0)
    parser.add_argument("--min-guards", type=int, default=0)
    parser.add_argument("--semantic-db")
    parser.add_argument("--stop-after-min", action="store_true")
    args = parser.parse_args(argv)

    registry = CorpusRegistry.from_file(args.registry)
    corpus = registry.get_project(args.project) if args.project else registry.get_engine(args.version)
    root = corpus.indexed_root if corpus.indexed_root.exists() else corpus.source_root
    store = SemanticStore(args.semantic_db) if args.semantic_db else None
    if store is not None:
        store.upsert_corpus(corpus)
    summary = extract_semantic_summary(
        root,
        corpus_id=args.corpus_id or corpus.corpus_id,
        version=corpus.ue_version or args.version,
        project_id=corpus.corpus_id if corpus.kind == "project" else None,
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
    project_id: str | None = None,
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
    source_files = 0
    targets = 0
    plugins = 0
    projects = 0

    for build_file in _iter_files(root, ".Build.cs"):
        try:
            text = build_file.read_text(encoding="utf-8", errors="replace")
            extracted = build_extractor.extract_text(
                text,
                module_name=build_file.name.removesuffix(".Build.cs"),
            )
        except OSError:
            continue
        build_module_name = build_file.name.removesuffix(".Build.cs")
        modules.add(build_module_name)
        deps += len(extracted)
        source_files += 1
        if store is not None:
            relative = build_file.relative_to(root).as_posix()
            store.upsert_source_file(
                corpus_id=corpus_id,
                path=relative,
                language="csharp",
                module_name=build_module_name,
                line_count=len(text.splitlines()),
                source_hash=source_sha256(text),
                commit=False,
            )
            store.upsert_module(
                corpus_id=corpus_id,
                module_name=build_module_name,
                public_include_paths=_include_paths(text, "PublicIncludePaths"),
                private_include_paths=_include_paths(text, "PrivateIncludePaths"),
                source_uri=_source_uri(version, root, build_file, 1, 1, project_id=project_id),
                metadata={"source": "Build.cs"},
                commit=False,
            )
            write_module_deps(
                store,
                corpus_id=corpus_id,
                evidence_uri=_source_uri(version, root, build_file, 1, 1, project_id=project_id),
                dependencies=extracted,
                commit=False,
            )
            store.commit()
        if stop_after_min and len(modules) >= min_modules:
            break

    for target_file in _iter_files(root, ".Target.cs"):
        try:
            text = target_file.read_text(encoding="utf-8", errors="replace")
            target = extract_target_file(target_file)
        except OSError:
            continue
        if target is None:
            continue
        targets += 1
        source_files += 1
        if store is not None:
            relative = target_file.relative_to(root).as_posix()
            store.upsert_source_file(
                corpus_id=corpus_id,
                path=relative,
                language="csharp",
                line_count=len(text.splitlines()),
                source_hash=source_sha256(text),
                metadata={"kind": "target"},
                commit=False,
            )
            store.upsert_target(
                corpus_id=corpus_id,
                target=target,
                evidence_uri=_source_uri(version, root, target_file, 1, max(1, len(text.splitlines())), project_id=project_id),
                commit=False,
            )
            store.commit()

    for plugin_file in _iter_files(root, ".uplugin"):
        try:
            text = plugin_file.read_text(encoding="utf-8", errors="replace")
            plugin = extract_uplugin(plugin_file, root=root)
        except (OSError, ValueError):
            continue
        plugins += 1
        source_files += 1
        if store is not None:
            relative = plugin_file.relative_to(root).as_posix()
            store.upsert_source_file(
                corpus_id=corpus_id,
                path=relative,
                language="json",
                line_count=len(text.splitlines()),
                source_hash=source_sha256(text),
                metadata={"kind": "uplugin"},
                commit=False,
            )
            store.upsert_plugin(
                corpus_id=corpus_id,
                plugin=plugin,
                evidence_uri=_source_uri(version, root, plugin_file, 1, max(1, len(text.splitlines())), project_id=project_id),
                commit=False,
            )
            store.commit()

    for project_file in _iter_files(root, ".uproject"):
        try:
            text = project_file.read_text(encoding="utf-8", errors="replace")
            project = extract_uproject(project_file, root=root)
        except (OSError, ValueError):
            continue
        projects += 1
        source_files += 1
        if store is not None:
            relative = project_file.relative_to(root).as_posix()
            store.upsert_source_file(
                corpus_id=corpus_id,
                path=relative,
                language="json",
                line_count=len(text.splitlines()),
                source_hash=source_sha256(text),
                metadata={"kind": "uproject"},
                commit=False,
            )
            store.upsert_project(
                corpus_id=corpus_id,
                project=project,
                evidence_uri=_source_uri(version, root, project_file, 1, max(1, len(text.splitlines())), project_id=project_id),
                commit=False,
            )
            store.commit()

    for header in _iter_files(root, ".h"):
        try:
            text = header.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        headers_scanned += 1
        module_name = _module_from_path(header)
        declaration_uri = _source_uri(
            version,
            root,
            header,
            1,
            max(1, len(text.splitlines())),
            project_id=project_id,
        )
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
        source_files += 1
        if store is not None:
            relative = header.relative_to(root).as_posix()
            store.upsert_source_file(
                corpus_id=corpus_id,
                path=relative,
                language="cpp",
                module_name=module_name,
                line_count=len(text.splitlines()),
                source_hash=source_sha256(text),
                commit=False,
            )
            for entity in extracted_reflection:
                store.upsert_reflection_entity(corpus_id=corpus_id, entity=entity, commit=False)
            for guard in extracted_guards:
                store.upsert_compile_guard(
                    corpus_id=corpus_id,
                    path=relative,
                    guard=guard,
                    evidence_uri=_source_uri(version, root, header, guard.line, guard.line, project_id=project_id),
                    commit=False,
                )
            for symbol in extracted_symbols:
                store.upsert_cpp_symbol(
                    corpus_id=corpus_id,
                    path=relative,
                    symbol=symbol,
                    evidence_uri=_source_uri(version, root, header, symbol.line, symbol.line, project_id=project_id),
                    module_name=module_name,
                    commit=False,
                )
            store.commit()
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
        "source_files": source_files,
        "reflection_entities": reflection_entities,
        "compile_guards": guards,
        "cpp_symbols": cpp_symbols,
        "targets": targets,
        "plugins": plugins,
        "projects": projects,
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


def _iter_files(root: Path, suffix: str) -> Iterator[Path]:
    seen: set[Path] = set()
    for scan_root in _semantic_scan_roots(root):
        for dirpath, dirnames, filenames in os.walk(scan_root):
            dirnames[:] = [
                dirname for dirname in dirnames if not _skip_path(Path(dirpath) / dirname)
            ]
            for filename in filenames:
                if not filename.endswith(suffix):
                    continue
                path = Path(dirpath) / filename
                if path in seen:
                    continue
                seen.add(path)
                yield path


def _semantic_scan_roots(root: Path) -> list[Path]:
    candidates = (
        root / "Engine" / "Source" / "Runtime",
        root / "Engine" / "Source" / "Developer",
        root / "Engine" / "Source" / "Editor",
        root,
    )
    roots: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        if not candidate.exists():
            continue
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        roots.append(candidate)
    return roots


def _skip_path(path: Path) -> bool:
    ignored = {"Intermediate", "Binaries", "DerivedDataCache", "Saved", ".git", "ThirdParty"}
    return any(part in ignored for part in path.parts)


def _source_uri(
    version: str,
    root: Path,
    path: Path,
    start: int,
    end: int,
    *,
    project_id: str | None = None,
) -> str:
    relative = path.relative_to(root).as_posix()
    if project_id:
        return f"ue-project://{project_id}/source/{relative}#L{start}-L{end}"
    return f"ue://{version}/source/{relative}#L{start}-L{end}"


def _module_from_path(path: Path) -> str | None:
    parts = path.parts
    for marker in ("Runtime", "Developer", "Editor"):
        if marker in parts:
            index = parts.index(marker)
            if index + 1 < len(parts):
                return parts[index + 1]
    if "Source" in parts:
        index = parts.index("Source")
        if index + 1 < len(parts):
            return parts[index + 1]
    return None


def _include_paths(text: str, property_name: str) -> list[str]:
    import re

    pattern = re.compile(
        rf"\b{re.escape(property_name)}\s*\.\s*Add(?:Range)?\s*\((?P<body>.*?)\)\s*;",
        re.DOTALL,
    )
    return list(
        dict.fromkeys(
            value
            for match in pattern.finditer(text)
            for value in re.findall(r'"([^"]+)"', match.group("body"))
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
