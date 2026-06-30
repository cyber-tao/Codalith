from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ue_context.coderag.adapter import CodeRAGAdapter
from ue_context.compiler.context_compiler import ContextCompiler
from ue_context.corpus.registry import CorpusRegistry
from ue_context.corpus.source_policy import SourcePolicy
from ue_context.corpus.uri_resolver import URIResolver
from ue_context.gateway.audit import AuditLogger
from ue_context.gateway.tools import ToolRuntime, UETools


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
            "bool bReplicates;\n"
            "void BeginPlay();\n"
            "};\n"
        ),
        "Engine/Source/Runtime/Engine/Classes/Engine/World.h": "class ENGINE_API UWorld : public UObject {};\n",
        "Engine/Source/Runtime/Engine/Private/ActorReplication.cpp": "void AActor::OnRep_Health() {}\n",
        "Engine/Source/Runtime/Renderer/Private/RendererModule.cpp": "class FRendererModule { void StartupModule(); };\n",
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
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        repeated = content + "\n".join(f"// evidence line {index}" for index in range(1, 30))
        path.write_text(repeated, encoding="utf-8")
    return root


@pytest.fixture()
def registry_path(tmp_path: Path, fake_engine_root: Path) -> Path:
    path = tmp_path / "corpus_registry.yaml"
    data: dict[str, Any] = {
        "engines": {
            "ue-5.7.4": {
                "kind": "engine",
                "ue_version": "5.7.4",
                "source_commit": "TEST",
                "source_root": str(fake_engine_root),
                "indexed_root": str(fake_engine_root),
                "coderag_store": str(tmp_path / "store"),
                "semantic_schema": "ue_5_7_4",
                "card_root": str(tmp_path / "cards"),
                "default": True,
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
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture()
def policy_path(tmp_path: Path) -> Path:
    path = tmp_path / "source_policy.yaml"
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
def tools(tmp_path: Path, registry: CorpusRegistry, policy_path: Path, adapter: CodeRAGAdapter) -> UETools:
    resolver = URIResolver(registry)
    policy = SourcePolicy.from_file(str(policy_path))
    compiler = ContextCompiler(registry, adapter)
    runtime = ToolRuntime(
        registry=registry,
        resolver=resolver,
        policy=policy,
        adapter=adapter,
        compiler=compiler,
        audit=AuditLogger(tmp_path / "audit.jsonl"),
        scopes={"source:read", "index:status", "cards:read", "graph:read", "ue:5.7"},
    )
    return UETools(runtime)
