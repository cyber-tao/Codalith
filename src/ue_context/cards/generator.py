"""Built-in Knowledge Card generator."""

from __future__ import annotations

from pathlib import Path

from ue_context.cards.renderer import render_markdown
from ue_context.cards.schema import CardClaim, CardEvidence, KnowledgeCard

TOPICS: tuple[tuple[str, str, str, str], ...] = (
    ("module-core", "module", "Core Module", "Engine/Source/Runtime/Core/Public/CoreMinimal.h"),
    ("module-coreuobject", "module", "CoreUObject Module", "Engine/Source/Runtime/CoreUObject/Public/UObject/Object.h"),
    ("module-engine", "module", "Engine Module", "Engine/Source/Runtime/Engine/Classes/GameFramework/Actor.h"),
    (
        "module-renderer",
        "module",
        "Renderer Module",
        "Engine/Source/Runtime/Renderer/Private/DeferredShadingRenderer.cpp",
    ),
    ("module-netcore", "module", "NetCore Module", "Engine/Source/Runtime/Net/Core/Public/Net/Core/NetHandle/NetHandle.h"),
    ("mechanism-uobject-gc", "mechanism", "UObject GC", "Engine/Source/Runtime/CoreUObject/Public/UObject/Object.h"),
    ("mechanism-uht-reflection", "mechanism", "UHT Reflection", "Engine/Source/Runtime/CoreUObject/Public/UObject/ObjectMacros.h"),
    ("mechanism-uproperty-replication", "mechanism", "UPROPERTY Replication", "Engine/Source/Runtime/Engine/Classes/GameFramework/Actor.h"),
    ("mechanism-actor-replication", "mechanism", "Actor Replication", "Engine/Source/Runtime/Engine/Classes/GameFramework/Actor.h"),
    ("mechanism-rpc-dispatch", "mechanism", "RPC Dispatch", "Engine/Source/Runtime/Engine/Classes/GameFramework/Actor.h"),
    ("symbol-uobject", "symbol", "UObject", "Engine/Source/Runtime/CoreUObject/Public/UObject/Object.h"),
    ("symbol-uclass", "symbol", "UClass", "Engine/Source/Runtime/CoreUObject/Public/UObject/Class.h"),
    ("symbol-aactor", "symbol", "AActor", "Engine/Source/Runtime/Engine/Classes/GameFramework/Actor.h"),
    ("symbol-uworld", "symbol", "UWorld", "Engine/Source/Runtime/Engine/Classes/Engine/World.h"),
    ("symbol-tarray", "symbol", "TArray", "Engine/Source/Runtime/Core/Public/Containers/Array.h"),
    ("symbol-fname", "symbol", "FName", "Engine/Source/Runtime/Core/Public/UObject/NameTypes.h"),
    ("build-module-system", "build", "Module System", "Engine/Source/Runtime/Engine/Engine.Build.cs"),
    ("build-public-private-dep", "build", "Public vs Private Dependency", "Engine/Source/Runtime/Engine/Engine.Build.cs"),
    ("build-target-rules", "build", "Target Rules", "Engine/Source/Programs/UnrealBuildTool/Configuration/TargetRules.cs"),
    ("recipe-safe-source-read", "recipe", "Safe Source Read", "Engine/Source/Runtime/Core/Public/CoreMinimal.h"),
)


def built_in_cards(*, corpus_id: str = "ue-5.7.4", version: str = "5.7.4") -> list[KnowledgeCard]:
    cards: list[KnowledgeCard] = []
    for card_id, card_type, title, path in TOPICS:
        evidence_uri = f"ue://{version}/source/{path}#L1-L20"
        cards.append(
            KnowledgeCard(
                corpus_id=corpus_id,
                card_id=card_id,
                card_type=card_type,
                title=title,
                version=version,
                body_markdown=(
                    f"{title} is a seed UE knowledge card. It is verified only when "
                    "its evidence URI resolves against the configured corpus."
                ),
                claims=[
                    CardClaim(
                        text=f"{title} must be grounded in UE {version} source evidence.",
                        evidence=[CardEvidence(uri=evidence_uri, reason="seed evidence")],
                    )
                ],
                related_nodes=[title],
            )
        )
    return cards


def write_cards(cards: list[KnowledgeCard], root: str | Path) -> list[Path]:
    root_path = Path(root)
    written: list[Path] = []
    for card in cards:
        target = root_path / "UE_KNOWLEDGE" / card.card_type.title() / f"{card.card_id}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_markdown(card), encoding="utf-8")
        written.append(target)
    return written
