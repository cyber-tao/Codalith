"""Unreal Engine semantic extraction profile.

This module owns every Unreal-specific convention used to build the semantic
graph: which directories to scan, which artifact types to parse (Build.cs,
Target.cs, .uplugin, .uproject, C++ headers with UHT reflection macros), and
how to infer a module name from a source path.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from codalith.cards.hashing import source_sha256
from codalith.corpus.uris import source_uri
from codalith.semantic.extractors.build_cs import BuildCsExtractor, write_module_deps
from codalith.semantic.extractors.compile_guards import extract_compile_guards
from codalith.semantic.extractors.cpp_symbols import extract_cpp_symbols
from codalith.semantic.extractors.target_cs import extract_target_file
from codalith.semantic.extractors.uht_reflection import UHTReflectionExtractor
from codalith.semantic.extractors.uplugin import extract_uplugin
from codalith.semantic.extractors.uproject import extract_uproject
from codalith.semantic.store import SemanticStore

PROFILE_NAME = "unreal"

# Preferred scan roots inside an engine tree; the corpus root itself is the
# fallback so project overlays and fixtures are still covered.
_SCAN_ROOT_CANDIDATES = (
    ("Engine", "Source", "Runtime"),
    ("Engine", "Source", "Developer"),
    ("Engine", "Source", "Editor"),
    (),
)

_IGNORED_DIRS = {"Intermediate", "Binaries", "DerivedDataCache", "Saved", ".git", "ThirdParty"}


def extract_semantic_summary(
    root: Path,
    *,
    corpus_id: str,
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
                source_uri=_source_uri(corpus_id, root, build_file, 1, 1),
                metadata={"source": "Build.cs"},
                commit=False,
            )
            write_module_deps(
                store,
                corpus_id=corpus_id,
                evidence_uri=_source_uri(corpus_id, root, build_file, 1, 1),
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
                evidence_uri=_source_uri(corpus_id, root, target_file, 1, max(1, len(text.splitlines()))),
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
                evidence_uri=_source_uri(corpus_id, root, plugin_file, 1, max(1, len(text.splitlines()))),
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
                evidence_uri=_source_uri(corpus_id, root, project_file, 1, max(1, len(text.splitlines()))),
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
            corpus_id,
            root,
            header,
            1,
            max(1, len(text.splitlines())),
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
                    evidence_uri=_source_uri(corpus_id, root, header, guard.line, guard.line),
                    commit=False,
                )
            for symbol in extracted_symbols:
                store.upsert_cpp_symbol(
                    corpus_id=corpus_id,
                    path=relative,
                    symbol=symbol,
                    evidence_uri=_source_uri(corpus_id, root, header, symbol.line, symbol.line),
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


def _iter_files(root: Path, suffix: str) -> Iterator[Path]:
    seen: set[Path] = set()
    for scan_root in _scan_roots(root):
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


def _scan_roots(root: Path) -> list[Path]:
    roots: list[Path] = []
    seen: set[Path] = set()
    for parts in _SCAN_ROOT_CANDIDATES:
        candidate = root.joinpath(*parts) if parts else root
        if not candidate.exists():
            continue
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        roots.append(candidate)
    return roots


def _skip_path(path: Path) -> bool:
    return any(part in _IGNORED_DIRS for part in path.parts)


def _source_uri(
    corpus_id: str,
    root: Path,
    path: Path,
    start: int,
    end: int,
) -> str:
    relative = path.relative_to(root).as_posix()
    return source_uri(corpus_id, relative, start, end)


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
