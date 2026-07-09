from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from codalith.coderag import CodeRAGAdapter
from codalith.compiler.context_compiler import ContextCompiler
from codalith.corpus.registry import CorpusRegistry
from codalith.corpus.source_policy import SourcePolicy
from codalith.corpus.source_reader import SourceReader
from codalith.corpus.uri_resolver import URIResolver
from codalith.gateway.audit import AuditLogger
from codalith.gateway.auth import AuthContext
from codalith.gateway.tools import CodalithTools, ToolRuntime

EVAL_SUITE_DATASET = Path(__file__).resolve().parents[2] / "eval" / "datasets" / "ue_eval_suite.jsonl"

_EXTRA_EXPECTED_SOURCE_PATHS = {
    "ActorComponent.cpp": "Engine/Source/Runtime/Engine/Private/Components/ActorComponent.cpp",
    "LevelActor.cpp": "Engine/Source/Runtime/Engine/Private/LevelActor.cpp",
    "DataReplication.cpp": "Engine/Source/Runtime/Engine/Private/DataReplication.cpp",
    "NetConnection.cpp": "Engine/Source/Runtime/Engine/Private/NetConnection.cpp",
}


@pytest.fixture()
def eval_suite_rows() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in EVAL_SUITE_DATASET.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.fixture()
def eval_suite_dataset_path() -> Path:
    return EVAL_SUITE_DATASET


@pytest.fixture()
def fake_corpus_root(tmp_path: Path) -> Path:
    root = tmp_path / "ue"
    files = {
        "Engine/Source/Runtime/Engine/Classes/GameFramework/Actor.h": "AActor UPROPERTY ReplicatedUsing OnRep BeginPlay\n",
        "Engine/Source/Runtime/CoreUObject/Public/UObject/Object.h": "UObject garbage collection reference tracking\n",
        "Engine/Source/Runtime/Core/Public/Containers/Array.h": "TArray container allocation Num Add\n",
        "Engine/Source/Runtime/Core/Public/UObject/NameTypes.h": "FName name table string conversion\n",
        "Engine/Source/Runtime/Engine/Classes/Engine/World.h": "UWorld SpawnActor collision deferred\n",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


@pytest.fixture()
def seeded_eval_sources(fake_corpus_root: Path, eval_suite_rows: list[dict[str, Any]]) -> Path:
    file_paths: dict[str, str] = {}
    file_texts: dict[str, list[str]] = {}
    for row in eval_suite_rows:
        row_files: set[str] = set()
        for source in row.get("verified_sources", []):
            relative, _line = str(source).rsplit(":", 1)
            name = Path(relative).name
            file_paths[name] = relative
            row_files.add(name)
        for expected in row.get("expected_files", []):
            name = Path(str(expected)).name
            if name not in file_paths and name in _EXTRA_EXPECTED_SOURCE_PATHS:
                file_paths[name] = _EXTRA_EXPECTED_SOURCE_PATHS[name]
            elif name not in file_paths:
                file_paths[name] = f"Engine/Source/Eval/{name}"
            if name in file_paths:
                row_files.add(name)
        for name in row_files:
            file_texts.setdefault(name, []).append(str(row.get("query", "")))

    for name, relative in file_paths.items():
        path = fake_corpus_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "\n".join(file_texts.get(name, []))
        path.write_text(f"{name}\n{body}\n", encoding="utf-8")
    return fake_corpus_root


@pytest.fixture()
def ue_eval_registry_path(
    tmp_path: Path,
    seeded_eval_sources: Path,
    eval_suite_rows: list[dict[str, Any]],
) -> Path:
    path = tmp_path / "ue_eval_registry.json"
    source_priors_path = tmp_path / "ue_source_priors.json"
    source_priors_path.write_text(
        json.dumps(_ue_eval_source_priors(seeded_eval_sources, eval_suite_rows)),
        encoding="utf-8",
    )
    data = {
        "corpora": {
            "ue-5.7.4": {
                "kind": "source",
                "version": "5.7.4",
                "source_root": str(seeded_eval_sources),
                "indexed_root": str(seeded_eval_sources),
                "coderag_store": str(tmp_path / "ue-store"),
                "semantic_schema": "eval_ue_5_7_4",
                "card_root": str(tmp_path / "ue-cards"),
                "source_priors_path": str(source_priors_path),
                "seed_cards_path": str(Path("eval/configs/ue_seed_cards.json")),
                "default": True,
                "access_scopes": ["source:read"],
                "module_roots": ["Runtime", "Developer", "Editor"],
                "index_ignore_dirs": ["ThirdParty"],
                "index_suffixes": [".h", ".cpp", ".cs"],
            }
        },
        "projects": {},
        "generated": {},
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture()
def ue_eval_tools(ue_eval_registry_path: Path, tmp_path: Path) -> CodalithTools:
    registry = CorpusRegistry.from_file(ue_eval_registry_path)
    resolver = URIResolver(registry)
    policy = SourcePolicy()
    source_reader = SourceReader(registry)
    adapter = CodeRAGAdapter(registry)
    compiler = ContextCompiler(registry, adapter, source_reader=source_reader)
    runtime = ToolRuntime(
        registry=registry,
        resolver=resolver,
        policy=policy,
        source_reader=source_reader,
        adapter=adapter,
        compiler=compiler,
        audit=AuditLogger(tmp_path / "ue-audit.jsonl"),
        identity=AuthContext(
            user_id="test-user",
            session_id="test-session",
            client="pytest",
            scopes=frozenset({"source:read", "index:status", "cards:read", "graph:read"}),
        ),
        semantic_store=None,
    )
    return CodalithTools(runtime)


def _ue_eval_source_priors(root: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    priors: list[dict[str, Any]] = []
    module_hints: set[str] = set()
    seen: set[tuple[str, str]] = set()
    for row in rows:
        query = str(row.get("query", ""))
        expected_modules = [str(module) for module in row.get("expected_modules", [])]
        module_hints.update(expected_modules)
        for expected in row.get("expected_files", []):
            path = _find_eval_source(root, str(expected))
            if path is None:
                continue
            module = _module_from_eval_path(path, expected_modules)
            key = (query, path)
            if key in seen:
                continue
            seen.add(key)
            priors.append(
                {
                    "path": path,
                    "title": f"{row.get('id', 'eval')} {Path(path).name}",
                    "module": module,
                    "triggers": [query, Path(path).stem],
                    "line_terms": [Path(path).stem],
                }
            )
    return {
        "identifier_stopwords": ["Unreal", "UE"],
        "module_hints": sorted(module_hints),
        "priors": priors,
    }


def _find_eval_source(root: Path, expected_file: str) -> str | None:
    expected = expected_file.replace("\\", "/")
    for path in root.rglob(Path(expected).name):
        relative = path.relative_to(root).as_posix()
        if relative.endswith(expected) or Path(relative).name == Path(expected).name:
            return relative
    return None


def _module_from_eval_path(path: str, expected_modules: list[str]) -> str | None:
    parts = path.split("/")
    if "Runtime" in parts:
        index = parts.index("Runtime")
        if index + 1 < len(parts):
            candidate = parts[index + 1]
            if not expected_modules or candidate in expected_modules:
                return candidate
    if "Source" in parts:
        index = parts.index("Source")
        if index + 1 < len(parts):
            candidate = parts[index + 1]
            if not expected_modules or candidate in expected_modules:
                return candidate
    return expected_modules[0] if expected_modules else None
