from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from codalith.eval.mcp_runner import run_mcp_eval
from codalith.eval.metrics import file_recall_at_k, module_accuracy
from codalith.gateway.http_server import StreamableHTTPConfig, create_http_server

DATASET_PATH = Path("eval/datasets/ue57_common_issues_30.jsonl")

EXTRA_EXPECTED_SOURCE_PATHS = {
    "ActorComponent.cpp": "Engine/Source/Runtime/Engine/Private/Components/ActorComponent.cpp",
    "LevelActor.cpp": "Engine/Source/Runtime/Engine/Private/LevelActor.cpp",
    "DataReplication.cpp": "Engine/Source/Runtime/Engine/Private/DataReplication.cpp",
    "NetConnection.cpp": "Engine/Source/Runtime/Engine/Private/NetConnection.cpp",
}

SOURCE_FIXTURE_TEXT = """
UObject TObjectPtr TWeakObjectPtr UPROPERTY garbage collection raw pointer weak pointer
CreateDefaultSubobject NewObject DefaultSubobject duplicate default subobject Blueprint component
UCLASS USTRUCT UENUM UFUNCTION UPROPERTY GENERATED_BODY generated.h UnrealHeaderTool UHT
PublicDependencyModuleNames PrivateDependencyModuleNames ModuleRules Build.cs WITH_EDITOR
UE_BUILD_SHIPPING UE_BUILD_DEVELOPMENT AddDynamic BindUFunction TScriptDelegate FScriptDelegate
PrimaryActorTick PrimaryComponentTick RegisterComponent RegisterComponentWithWorld TickComponent
SetupAttachment AttachToComponent SpawnActor SpawnActorDeferred FinishSpawningActor
FActorSpawnParameters SpawnCollisionHandlingOverride FTimerManager FTimerHandle ClearTimer
ClearAllTimersForObject LineTraceSingleByChannel FCollisionQueryParams bTraceComplex AddIgnoredActor
DOREPLIFETIME GetLifetimeReplicatedProps RepNotify OnRep DataReplication GetFunctionCallspace
ProcessRemoteFunction owning connection NetMulticast FUNC_NetMulticast FUNC_NetReliable Reliable
NetConnection bReplicateMovement ReplicatedMovement OnRep_ReplicatedMovement CharacterMovementComponent
ServerMove NetworkPrediction FFastArraySerializer MarkItemDirty MarkArrayDirty AbilitySystemComponent
InitAbilityActorInfo SetIsReplicated AttributeSet ATTRIBUTE_ACCESSORS GAMEPLAYATTRIBUTE_REPNOTIFY
Enhanced Input UEnhancedInputLocalPlayerSubsystem UInputMappingContext AddMappingContext
UEnhancedInputComponent UInputAction BindAction TSoftObjectPtr FStreamableManager RequestAsyncLoad
ConstructorHelpers FObjectFinder CheckIfIsInConstructor
"""


def test_ue57_common_issue_dataset_passes_mcp_context_recall(tools: Any, fake_engine_root: Path) -> None:
    rows = _read_dataset()
    _seed_expected_sources(fake_engine_root, rows)

    failures: list[dict[str, object]] = []
    for row in rows:
        pack = tools.codalith_context(
            query=str(row["query"]),
            version=str(row.get("version", "5.7.4")),
            mode=str(row.get("mode", "explain")),
            max_source_spans=5,
            include_project_overlay=False,
        )
        expected_files = [str(path) for path in row.get("expected_files", [])]
        expected_modules = [str(module) for module in row.get("expected_modules", [])]
        file_score = file_recall_at_k(pack, expected_files, k=5)
        module_score = module_accuracy(pack, expected_modules)
        if file_score < 1.0 or module_score < 1.0:
            failures.append(
                {
                    "id": row.get("id"),
                    "file_recall@5": file_score,
                    "module_accuracy": module_score,
                    "expected_files": expected_files,
                    "expected_modules": expected_modules,
                    "source_paths": [
                        str(span.get("path", "")) for span in pack.get("source_spans", [])[:5]
                    ],
                    "modules": [str(module.get("name", "")) for module in pack.get("modules", [])],
                }
            )

    assert failures == []


def test_ue57_common_issue_dataset_passes_http_mcp_eval(tools: Any, fake_engine_root: Path) -> None:
    rows = _read_dataset()
    _seed_expected_sources(fake_engine_root, rows)
    server = create_http_server(tools, StreamableHTTPConfig(port=0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address

    try:
        report = run_mcp_eval(
            endpoint=f"http://{host}:{port}/mcp",
            dataset_path=DATASET_PATH,
            label="ue57_common_issues",
            max_source_spans=5,
            metric_k=5,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert report.count == 30
    assert report.file_recall_at_k == 1.0
    assert report.candidate_file_recall == 1.0
    assert report.module_accuracy == 1.0
    assert {row["failure_class"] for row in report.rows} == {"pass"}


def _read_dataset() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in DATASET_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _seed_expected_sources(root: Path, rows: list[dict[str, Any]]) -> None:
    source_paths: dict[str, str] = {}
    for row in rows:
        for source in row.get("verified_sources", []):
            path, _line = str(source).rsplit(":", 1)
            source_paths[Path(path).name] = path
    source_paths.update(EXTRA_EXPECTED_SOURCE_PATHS)

    for relative in source_paths.values():
        path = root / relative
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{Path(relative).name}\n{SOURCE_FIXTURE_TEXT}", encoding="utf-8")
