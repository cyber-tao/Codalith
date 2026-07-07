from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jobs.extract_semantic import extract_semantic_summary

from codalith.coderag.adapter import CodeRAGAdapter
from codalith.compiler.context_compiler import ContextCompiler
from codalith.corpus.registry import CorpusRegistry
from codalith.corpus.source_policy import SourcePolicy
from codalith.corpus.uri_resolver import URIResolver
from codalith.gateway.audit import AuditLogger
from codalith.gateway.auth import AuthContext
from codalith.gateway.tools import CodalithTools, ToolRuntime
from codalith.semantic.store import SemanticStore

EVAL_SUITE_DATASET = Path(__file__).parents[1] / "eval" / "datasets" / "ue_eval_suite.jsonl"

# Files referenced by eval-suite rows that are not named in any verified_sources entry.
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
def seeded_eval_sources(fake_engine_root: Path, eval_suite_rows: list[dict[str, Any]]) -> Path:
    """Seed fixture files for eval-suite sources missing from the base engine tree.

    Each seeded file only contains the query text of the rows that reference it, so
    files stay relevant to their own questions instead of matching every query.
    """
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
            if name in file_paths:
                row_files.add(name)
        for name in row_files:
            file_texts.setdefault(name, []).append(str(row.get("query", "")))

    for name, relative in file_paths.items():
        path = fake_engine_root / relative
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "\n".join(file_texts.get(name, []))
        path.write_text(f"{name}\n{body}\n", encoding="utf-8")
    return fake_engine_root


@pytest.fixture()
def fake_engine_root(tmp_path: Path) -> Path:
    root = tmp_path / "ue"
    files = {
        "Engine/Source/Runtime/Core/Public/CoreMinimal.h": "Core CoreMinimal safe source read\n",
        "Engine/Source/Runtime/Core/Public/Containers/Array.h": "template<class T> class TArray { public: int Num() const; };\n",
        "Engine/Source/Runtime/Core/Public/UObject/NameTypes.h": "class FName { public: FName(); };\n",
        "Engine/Source/Runtime/CoreUObject/Public/UObject/Object.h": "UCLASS()\nclass COREUOBJECT_API UObject { GENERATED_BODY() };\n",
        "Engine/Source/Runtime/CoreUObject/Public/UObject/Class.h": "class COREUOBJECT_API UClass : public UObject {};\n",
        "Engine/Source/Runtime/CoreUObject/Public/UObject/ObjectMacros.h": (
            "#define UCLASS(...)\n#define USTRUCT(...)\n#define UENUM(...)\n"
            "#define UFUNCTION(...)\n#define UPROPERTY(...)\nBlueprintCallable BlueprintNativeEvent\n"
        ),
        "Engine/Source/Runtime/Engine/Classes/GameFramework/Actor.h": (
            "#include \"Actor.generated.h\"\n"
            "UCLASS()\n"
            "class ENGINE_API AActor : public UObject {\n"
            "GENERATED_BODY()\n"
            "UPROPERTY(ReplicatedUsing=OnRep_Health)\n"
            "int32 Health;\n"
            "UFUNCTION(BlueprintCallable)\n"
            "void OnRep_Health();\n"
            "#if WITH_EDITOR\n"
            "void EditorOnlyPreview();\n"
            "#endif\n"
            "#if !UE_BUILD_SHIPPING\n"
            "void DebugOnlyReplicationTrace();\n"
            "#endif\n"
            "bool bReplicates;\n"
            "void BeginPlay();\n"
            "};\n"
        ),
        "Engine/Source/Runtime/Engine/Classes/Engine/World.h": "class ENGINE_API UWorld : public UObject {};\n",
        "Engine/Source/Runtime/Engine/Private/ActorReplication.cpp": "void AActor::OnRep_Health() {}\n",
        "Engine/Source/Runtime/Renderer/Private/DeferredShadingRenderer.cpp": "class FRendererModule { void StartupModule(); };\n",
        "Engine/Source/Runtime/Net/Core/Public/Net/Core/NetHandle/NetHandle.h": "struct FNetHandle { int32 Value; };\n",
        "Engine/Source/Runtime/Engine/Engine.Build.cs": (
            "PublicDependencyModuleNames.AddRange(new string[] { \"Core\", \"CoreUObject\" });\n"
            "PrivateDependencyModuleNames.Add(\"NetCore\");\n"
            "DynamicallyLoadedModuleNames.AddRange(new string[] { \"Renderer\" });\n"
        ),
        "Engine/Source/Programs/UnrealBuildTool/Configuration/TargetRules.cs": "public class TargetRules {}\n",
        "Engine/Source/Runtime/GameplayAbilities/GameplayAbilities.Build.cs": (
            "PublicDependencyModuleNames.AddRange(new string[] { \"Core\", \"Engine\" });\n"
        ),
        "Source/ProjectA/ProjectA.Build.cs": (
            "PublicDependencyModuleNames.AddRange(new string[] { \"Core\", \"CoreUObject\", \"Engine\" });\n"
            "PrivateDependencyModuleNames.AddRange(new string[] { \"GameplayAbilities\" });\n"
        ),
        "ProjectA.uproject": (
            "{\n"
            "  \"EngineAssociation\": \"5.7\",\n"
            "  \"Modules\": [{\"Name\": \"ProjectA\", \"Type\": \"Runtime\", \"LoadingPhase\": \"Default\"}],\n"
            "  \"Plugins\": [{\"Name\": \"GameplayAbilities\", \"Enabled\": true}]\n"
            "}\n"
        ),
        "Source/ProjectA/ProjectATarget.Target.cs": (
            "public class ProjectATarget : TargetRules {\n"
            "  public ProjectATarget(TargetInfo Target) : base(Target) {\n"
            "    Type = TargetType.Game;\n"
            "    DefaultBuildSettings = BuildSettingsVersion.V5;\n"
            "    ExtraModuleNames.AddRange(new string[] { \"ProjectA\" });\n"
            "  }\n"
            "}\n"
        ),
        "Engine/Plugins/Runtime/Sample/Sample.uplugin": (
            "{\n"
            "  \"FriendlyName\": \"Sample\",\n"
            "  \"SupportedTargetPlatforms\": [\"Win64\"],\n"
            "  \"Modules\": [{\"Name\": \"SampleRuntime\", \"Type\": \"Runtime\", \"LoadingPhase\": \"Default\"}]\n"
            "}\n"
        ),
        "Source/ProjectA/Public/InventoryComponent.h": (
            "#pragma once\n"
            "UCLASS()\n"
            "class PROJECTA_API UInventoryComponent : public UActorComponent {\n"
            "GENERATED_BODY()\n"
            "UPROPERTY(ReplicatedUsing=OnRep_Items)\n"
            "int32 ItemCount;\n"
            "UFUNCTION()\n"
            "void OnRep_Items();\n"
            "};\n"
        ),
        "Source/ProjectA/Private/InventoryComponent.cpp": "void UInventoryComponent::OnRep_Items() {}\n",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        repeated = content + "\n".join(f"// evidence line {index}" for index in range(1, 30))
        path.write_text(repeated, encoding="utf-8")
    return root


@pytest.fixture()
def registry_path(tmp_path: Path, fake_engine_root: Path) -> Path:
    path = tmp_path / "corpus_registry.json"
    data: dict[str, Any] = {
        "engines": {
            "ue-5.7.4": {
                "kind": "engine",
                "ue_version": "5.7.4",
                "display_name": "Unreal Engine",
                "description": "Unreal Engine full source tree",
                "keywords": ["Unreal Engine", "UE5", "UHT", "GC"],
                "source_commit": "TEST",
                "source_root": str(fake_engine_root),
                "indexed_root": str(fake_engine_root),
                "coderag_store": str(tmp_path / "store"),
                "semantic_schema": "ue_5_7_4",
                "card_root": str(tmp_path / "cards"),
                "default": True,
                "access_scopes": ["ue:5.7", "source:read"],
            },
            "ue-5.7.5": {
                "kind": "engine",
                "ue_version": "5.7.5",
                "source_commit": "TEST-NEXT",
                "source_root": str(fake_engine_root),
                "indexed_root": str(fake_engine_root),
                "coderag_store": str(tmp_path / "store-next"),
                "semantic_schema": "ue_5_7_5",
                "card_root": str(tmp_path / "cards-next"),
                "default": False,
                "access_scopes": ["ue:5.7", "source:read"],
            }
        },
        "projects": {
            "ProjectA": {
                "kind": "project",
                "engine_corpus": "ue-5.7.4",
                "source_root": str(fake_engine_root),
                "indexed_root": str(fake_engine_root),
                "coderag_store": str(tmp_path / "project-store"),
                "semantic_schema": "project_a",
                "card_root": str(tmp_path / "project-cards"),
                "access_scopes": ["project:ProjectA", "source:read"],
            }
        },
        "generated": {
            "generated-ue-5.7.4": {
                "kind": "generated",
                "engine_corpus": "ue-5.7.4",
                "ue_version": "5.7.4",
                "source_root": str(fake_engine_root / "Saved" / "Generated"),
                "indexed_root": str(fake_engine_root / "Saved" / "Generated"),
                "coderag_store": str(tmp_path / "generated-store"),
                "semantic_schema": "generated_ue_5_7_4",
                "card_root": str(tmp_path / "generated-cards"),
                "access_scopes": ["generated:read", "source:read"],
            }
        },
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture()
def policy_path(tmp_path: Path) -> Path:
    path = tmp_path / "source_policy.json"
    path.write_text(
        json.dumps(
            {
                "limits": {
                    "default_max_lines": 20,
                    "hard_max_lines": 25,
                    "max_source_reads_per_10min": 100,
                    "max_total_lines_per_10min": 10000,
                },
                "deny_patterns": ["Engine/Platforms/PS5/**"],
                "sensitive_patterns": [
                    {
                        "pattern": "Engine/Source/ThirdParty/**",
                        "required_scope": "thirdparty:read",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def registry(registry_path: Path) -> CorpusRegistry:
    return CorpusRegistry.from_file(registry_path)


@pytest.fixture()
def adapter(registry: CorpusRegistry) -> CodeRAGAdapter:
    return CodeRAGAdapter(registry)


@pytest.fixture()
def tools(
    tmp_path: Path,
    fake_engine_root: Path,
    registry: CorpusRegistry,
    policy_path: Path,
    adapter: CodeRAGAdapter,
) -> CodalithTools:
    resolver = URIResolver(registry)
    policy = SourcePolicy.from_file(str(policy_path))
    semantic_store = SemanticStore(tmp_path / "semantic.sqlite")
    extract_semantic_summary(
        fake_engine_root,
        corpus_id="ue-5.7.4",
        version="5.7.4",
        store=semantic_store,
    )
    compiler = ContextCompiler(registry, adapter, semantic_store=semantic_store)
    runtime = ToolRuntime(
        registry=registry,
        resolver=resolver,
        policy=policy,
        adapter=adapter,
        compiler=compiler,
        audit=AuditLogger(tmp_path / "audit.jsonl"),
        identity=AuthContext(
            user_id="test-user",
            session_id="test-session",
            client="pytest",
            scopes=frozenset(
                {
                    "source:read",
                    "index:status",
                    "cards:read",
                    "graph:read",
                    "ue:5.7",
                    "project:ProjectA",
                }
            ),
        ),
        semantic_store=semantic_store,
    )
    return CodalithTools(runtime)
