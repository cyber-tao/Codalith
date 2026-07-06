"""Deterministic UE source entry-point locator.

CodeRAG provides broad semantic retrieval. This module adds high-confidence UE
source priors for canonical engine concepts so Context Packs still cite stable
source evidence when an embedding provider is intentionally low fidelity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from codalith.coderag.adapter import RetrievalHit
from codalith.corpus.registry import Corpus


@dataclass(frozen=True, slots=True)
class SourcePrior:
    path: str
    title: str
    module: str | None
    triggers: tuple[str, ...]
    line_terms: tuple[str, ...] = ()


SOURCE_PRIORS: tuple[SourcePrior, ...] = (
    SourcePrior(
        path="Engine/Source/Runtime/Engine/Classes/GameFramework/Actor.h",
        title="AActor declarations and replication",
        module="Engine",
        triggers=(
            "aactor",
            "actor",
            "replication",
            "replicatedusing",
            "onrep",
            "rpc",
            "breplicates",
            "beginplay",
            "project overlay",
            "symbol resolution",
        ),
        line_terms=("AActor", "ReplicatedUsing", "OnRep", "bReplicates", "BeginPlay"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/CoreUObject/Public/UObject/Object.h",
        title="UObject base declarations",
        module="CoreUObject",
        triggers=("uobject", "garbage collection", " gc", "object flags", "base declarations"),
        line_terms=("UObject", "EObjectFlags", "Garbage"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/CoreUObject/Public/UObject/ObjectPtr.h",
        title="TObjectPtr hard reference declarations",
        module="CoreUObject",
        triggers=("tobjectptr", "objectptr", "raw pointer", "hard uobject reference", "gc"),
        line_terms=("TObjectPtr", "ObjectPtr"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Core/Public/UObject/WeakObjectPtrTemplates.h",
        title="TWeakObjectPtr weak reference declarations",
        module="Core",
        triggers=("tweakobjectptr", "weakobjectptr", "weak pointer", "non-owning", "raw pointer"),
        line_terms=("TWeakObjectPtr", "WeakObjectPtr"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/CoreUObject/Public/UObject/UObjectGlobals.h",
        title="UObject construction helpers",
        module="CoreUObject",
        triggers=(
            "createdefaultsubobject",
            "newobject",
            "default subobject",
            "subobject",
            "constructor helper",
            "constructor",
        ),
        line_terms=("CreateDefaultSubobject", "NewObject"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/CoreUObject/Private/UObject/UObjectGlobals.cpp",
        title="UObject construction implementation",
        module="CoreUObject",
        triggers=(
            "createdefaultsubobject",
            "default subobject",
            "duplicate default subobject",
            "blueprint component",
            "subobject name",
        ),
        line_terms=("CreateDefaultSubobject", "DefaultSubobject", "Duplicate"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/CoreUObject/Public/UObject/ObjectMacros.h",
        title="UHT reflection macros and metadata",
        module="CoreUObject",
        triggers=(
            "uht",
            "reflection",
            "macro",
            "uproperty",
            "ufunction",
            "ustruct",
            "uclass",
            "uenum",
            "blueprintcallable",
            "blueprintnativeevent",
            "generated.h",
            "reflected header",
            "metadata declared",
            "class metadata",
            "reflection graph",
        ),
        line_terms=(
            "UPROPERTY",
            "UFUNCTION",
            "USTRUCT",
            "UCLASS",
            "UENUM",
            "BlueprintCallable",
            "BlueprintNativeEvent",
        ),
    ),
    SourcePrior(
        path="Engine/Source/Programs/Shared/EpicGames.UHT/Parsers/UhtHeaderFileParser.cs",
        title="UHT generated header parser checks",
        module=None,
        triggers=(
            "uht",
            "unrealheadertool",
            "generated.h",
            "generated header",
            "last include",
            "reflection parsing",
            "where does uht enforce",
        ),
        line_terms=(".generated.h", "HeaderFileParser", "include"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/CoreUObject/Public/UObject/Class.h",
        title="UClass declarations",
        module="CoreUObject",
        triggers=("uclass declaration", "uclass declarations", "class metadata", "inspect uclass"),
        line_terms=("UClass", "EClassFlags"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Core/Public/Containers/Array.h",
        title="TArray container API",
        module="Core",
        triggers=("tarray", "array container", "array.h", "container behavior", "array api"),
        line_terms=("TArray", "ArrayNum", "Num()"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Core/Public/UObject/NameTypes.h",
        title="FName API",
        module="Core",
        triggers=("fname", "nametypes", "name api"),
        line_terms=("FName", "NameTypes"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Engine/Classes/Engine/World.h",
        title="UWorld gameplay runtime declarations",
        module="Engine",
        triggers=(
            "uworld",
            "world.h",
            "gameplay runtime",
            "engine world",
            "spawnactor",
            "spawn actor",
            "spawn collision",
            "spawnactordeferred",
        ),
        line_terms=("UWorld", "WorldType", "SpawnActor", "SpawnActorDeferred"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Engine/Private/LevelActor.cpp",
        title="Actor spawning implementation",
        module="Engine",
        triggers=("spawnactor", "spawn actor", "spawn collision", "spawnactors", "level actor"),
        line_terms=("SpawnActor", "SpawnCollisionHandlingOverride"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Engine/Classes/Kismet/GameplayStatics.h",
        title="GameplayStatics deferred actor spawning",
        module="Engine",
        triggers=("finishspawningactor", "finish spawning", "spawnactordeferred", "construction script"),
        line_terms=("FinishSpawningActor", "BeginDeferredActorSpawnFromClass"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Renderer/Private/DeferredShadingRenderer.cpp",
        title="Renderer implementation",
        module="Renderer",
        triggers=("renderer", "rendering api", "deferredshadingrenderer", "rendering"),
        line_terms=("FDeferredShadingSceneRenderer", "Renderer", "Render"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Net/Core/Public/Net/Core/NetHandle/NetHandle.h",
        title="NetCore handle declarations",
        module="NetCore",
        triggers=("netcore", "net handle", "nethandle", "networking handles", "networking code"),
        line_terms=("FNetHandle", "NetHandle"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Engine/Engine.Build.cs",
        title="Engine module dependencies",
        module="Engine",
        triggers=(
            "build.cs",
            "module dependency",
            "module dependencies",
            "publicdependency",
            "privatedependency",
            "public dependencies",
            "private dependencies",
            "dynamically loaded",
            "unrealed",
            "runtime modules",
            "module system",
            "dependency graph",
        ),
        line_terms=(
            "PublicDependencyModuleNames",
            "PrivateDependencyModuleNames",
            "DynamicallyLoadedModuleNames",
        ),
    ),
    SourcePrior(
        path="Engine/Source/Programs/UnrealBuildTool/Configuration/ModuleRules.cs",
        title="UnrealBuildTool module dependency rules",
        module=None,
        triggers=(
            "modulerules",
            "publicdependencymodulenames",
            "privatedependencymodulenames",
            "build.cs",
            "unreal build tool",
            "ubt",
            "module dependency",
            "header include errors",
            "unresolved symbol",
        ),
        line_terms=("PublicDependencyModuleNames", "PrivateDependencyModuleNames"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Core/Public/Misc/Build.h",
        title="Build configuration and editor guard macros",
        module="Core",
        triggers=(
            "with_editor",
            "with_editoronly_data",
            "ue_build_shipping",
            "ue_build_development",
            "ue_build_debug",
            "build configuration",
            "packaged build",
            "editor-only",
            "editor only",
        ),
        line_terms=("WITH_EDITOR", "UE_BUILD_SHIPPING", "UE_BUILD_DEVELOPMENT"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/GameplayAbilities/GameplayAbilities.Build.cs",
        title="GameplayAbilities module dependencies",
        module="GameplayAbilities",
        triggers=("gameplayabilities", "gameplay abilities"),
        line_terms=("GameplayAbilities", "PublicDependencyModuleNames", "PrivateDependencyModuleNames"),
    ),
    SourcePrior(
        path="Engine/Plugins/Runtime/GameplayAbilities/Source/GameplayAbilities/GameplayAbilities.Build.cs",
        title="GameplayAbilities plugin module dependencies",
        module="GameplayAbilities",
        triggers=("gameplayabilities", "gameplay abilities"),
        line_terms=("GameplayAbilities", "PublicDependencyModuleNames", "PrivateDependencyModuleNames"),
    ),
    SourcePrior(
        path="Engine/Source/Programs/UnrealBuildTool/Configuration/TargetRules.cs",
        title="TargetRules configuration",
        module=None,
        triggers=("targetrules", "target rules", "target.cs"),
        line_terms=("TargetRules",),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Core/Public/Delegates/DelegateSignatureImpl.inl",
        title="Dynamic delegate binding helpers",
        module="Core",
        triggers=(
            "adddynamic",
            "dynamic delegate",
            "dynamic multicast",
            "bindufunction",
            "matching signature",
            "delegate source",
        ),
        line_terms=("AddDynamic", "BindUFunction", "Dynamic"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Core/Public/UObject/ScriptDelegates.h",
        title="Script delegate reflection binding",
        module="Core",
        triggers=(
            "adddynamic",
            "dynamic delegate",
            "dynamic multicast",
            "script delegate",
            "ufunction",
            "matching signature",
        ),
        line_terms=("TScriptDelegate", "FScriptDelegate", "UFunction"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Engine/Classes/Components/ActorComponent.h",
        title="ActorComponent tick and registration declarations",
        module="Engine",
        triggers=(
            "actorcomponent",
            "component",
            "primarycomponenttick",
            "registercomponent",
            "registercomponentwithworld",
            "tickcomponent",
            "newobject",
        ),
        line_terms=("RegisterComponent", "PrimaryComponentTick", "TickComponent"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Engine/Private/Components/ActorComponent.cpp",
        title="ActorComponent registration implementation",
        module="Engine",
        triggers=("registercomponent", "registercomponentwithworld", "component created", "render or tick"),
        line_terms=("RegisterComponent", "RegisterComponentWithWorld"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Engine/Classes/Components/SceneComponent.h",
        title="SceneComponent attachment APIs",
        module="Engine",
        triggers=("setupattachment", "attachtocomponent", "scenecomponent", "attach to component"),
        line_terms=("SetupAttachment", "AttachToComponent"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Engine/Public/TimerManager.h",
        title="Timer manager API",
        module="Engine",
        triggers=(
            "ftimermanager",
            "ftimerhandle",
            "cleartimer",
            "clearalltimersforobject",
            "timer",
            "timers",
        ),
        line_terms=("ClearTimer", "ClearAllTimersForObject", "FTimerHandle"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Engine/Private/TimerManager.cpp",
        title="Timer manager implementation",
        module="Engine",
        triggers=("destroyed uobject", "destroyed actor", "object-bound timer", "timers firing", "timer"),
        line_terms=("TimerDelegate", "ClearAllTimersForObject", "HasSameObject"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Engine/Private/Collision/WorldCollision.cpp",
        title="World collision trace implementation",
        module="Engine",
        triggers=("linetrace", "line trace", "linetracesinglebychannel", "trace channel", "collision"),
        line_terms=("LineTraceSingleByChannel", "Trace", "Collision"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Engine/Public/CollisionQueryParams.h",
        title="Collision query params",
        module="Engine",
        triggers=("fcollisionqueryparams", "btracecomplex", "addignoredactor", "ignored actor"),
        line_terms=("FCollisionQueryParams", "bTraceComplex", "AddIgnoredActor"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Engine/Public/Net/UnrealNetwork.h",
        title="Replication lifetime property macros",
        module="Engine",
        triggers=(
            "doreplifetime",
            "getlifetimereplicatedprops",
            "replicatedusing",
            "replicated property",
            "replicate a property",
        ),
        line_terms=("DOREPLIFETIME", "GetLifetimeReplicatedProps"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Net/Iris/Private/Iris/ReplicationSystem/PropertyReplicationFragment.cpp",
        title="RepNotify state application",
        module="Net",
        triggers=("repnotify", "onrep", "replicatedusing", "received state", "property replication"),
        line_terms=("RepNotify", "CallRepNotifies"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Engine/Private/DataReplication.cpp",
        title="Legacy object data replication repnotify dispatch",
        module="Engine",
        triggers=("repnotify", "onrep", "datareplication", "received state", "property replication"),
        line_terms=("CallRepNotifies", "RepNotify", "FObjectReplicator"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Engine/Private/Actor.cpp",
        title="Actor RPC callspace implementation",
        module="Engine",
        triggers=(
            "getfunctioncallspace",
            "owning connection",
            "client-to-server rpc",
            "netmulticast",
            "rpc",
            "actor rpc",
        ),
        line_terms=("GetFunctionCallspace", "RemoteFunction", "NetMulticast"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Engine/Private/NetDriver.cpp",
        title="NetDriver RPC processing",
        module="Engine",
        triggers=(
            "processremotefunction",
            "owning connection",
            "func_netmulticast",
            "func_netreliable",
            "reliable rpc",
            "unreliable multicast",
            "netdriver",
            "rpc",
        ),
        line_terms=("ProcessRemoteFunction", "FUNC_NetMulticast", "FUNC_NetReliable"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Engine/Private/NetConnection.cpp",
        title="NetConnection reliable bunch handling",
        module="Engine",
        triggers=("netconnection", "reliable rpc", "unreliable", "bunch", "channel capacity"),
        line_terms=("Reliable", "Bunch", "NetConnection"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Engine/Private/ActorReplication.cpp",
        title="Actor movement replication implementation",
        module="Engine",
        triggers=("breplicatemovement", "replicatedmovement", "onrep_replicatedmovement", "movement replication"),
        line_terms=("ReplicatedMovement", "OnRep_ReplicatedMovement", "bReplicateMovement"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Engine/Classes/GameFramework/CharacterMovementComponent.h",
        title="Character movement network prediction declarations",
        module="Engine",
        triggers=("charactermovementcomponent", "character movement", "network prediction", "servermove"),
        line_terms=("UCharacterMovementComponent", "ServerMove", "NetworkPrediction"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Engine/Private/Components/CharacterMovementComponent.cpp",
        title="Character movement network prediction implementation",
        module="Engine",
        triggers=("charactermovementcomponent", "character movement", "network prediction", "servermove"),
        line_terms=("ServerMove", "ClientAdjustPosition", "NetworkPrediction"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Net/Core/Classes/Net/Serialization/FastArraySerializer.h",
        title="Fast array dirty tracking",
        module="Net",
        triggers=("ffastarrayserializer", "markitemdirty", "markarraydirty", "fast array"),
        line_terms=("FFastArraySerializer", "MarkItemDirty", "MarkArrayDirty"),
    ),
    SourcePrior(
        path="Engine/Plugins/Runtime/GameplayAbilities/Source/GameplayAbilities/Public/AbilitySystemComponent.h",
        title="Gameplay Ability System component declarations",
        module="GameplayAbilities",
        triggers=(
            "abilitysystemcomponent",
            "initabilityactorinfo",
            "setisreplicated",
            "replication mode",
            "asc",
            "gameplay ability system",
        ),
        line_terms=("UAbilitySystemComponent", "InitAbilityActorInfo", "SetIsReplicated"),
    ),
    SourcePrior(
        path="Engine/Plugins/Runtime/GameplayAbilities/Source/GameplayAbilities/Private/AbilitySystemComponent.cpp",
        title="Gameplay Ability System component implementation",
        module="GameplayAbilities",
        triggers=("abilitysystemcomponent", "initabilityactorinfo", "asc", "avatar", "owner"),
        line_terms=("InitAbilityActorInfo", "AbilityActorInfo"),
    ),
    SourcePrior(
        path="Engine/Plugins/Runtime/GameplayAbilities/Source/GameplayAbilities/Public/AttributeSet.h",
        title="Gameplay Ability System attribute replication helpers",
        module="GameplayAbilities",
        triggers=(
            "attributeset",
            "attribute_accessors",
            "gameplayattribute_repnotify",
            "attributes replicate",
            "attribute onrep",
        ),
        line_terms=("ATTRIBUTE_ACCESSORS", "GAMEPLAYATTRIBUTE_REPNOTIFY", "FGameplayAttribute"),
    ),
    SourcePrior(
        path="Engine/Plugins/EnhancedInput/Source/EnhancedInput/Public/EnhancedInputSubsystems.h",
        title="Enhanced Input mapping context subsystem",
        module="EnhancedInput",
        triggers=(
            "enhanced input",
            "inputmappingcontext",
            "uinputmappingcontext",
            "uenhancedinputlocalplayersubsystem",
            "addmappingcontext",
            "mapping context",
        ),
        line_terms=("UEnhancedInputLocalPlayerSubsystem", "AddMappingContext", "UInputMappingContext"),
    ),
    SourcePrior(
        path="Engine/Plugins/EnhancedInput/Source/EnhancedInput/Public/EnhancedInputComponent.h",
        title="Enhanced Input action binding component",
        module="EnhancedInput",
        triggers=(
            "enhanced input",
            "bindaction",
            "uenhancedinputcomponent",
            "uinputaction",
            "setupplayerinputcomponent",
            "input action",
        ),
        line_terms=("UEnhancedInputComponent", "BindAction", "UInputAction"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/CoreUObject/Public/UObject/SoftObjectPtr.h",
        title="Soft object pointer declarations",
        module="CoreUObject",
        triggers=("tsoftobjectptr", "soft reference", "soft object", "hard asset loads"),
        line_terms=("TSoftObjectPtr", "FSoftObjectPtr"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Engine/Classes/Engine/StreamableManager.h",
        title="StreamableManager async loading",
        module="Engine",
        triggers=("fstreamablemanager", "requestasyncload", "async load", "streamable"),
        line_terms=("FStreamableManager", "RequestAsyncLoad"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/CoreUObject/Public/UObject/ConstructorHelpers.h",
        title="ConstructorHelpers constructor-only asset lookup",
        module="CoreUObject",
        triggers=("constructorhelpers", "constructor helpers", "objectfinder", "classfinder", "constructors"),
        line_terms=("ConstructorHelpers", "CheckIfIsInConstructor", "FObjectFinder"),
    ),
    SourcePrior(
        path="Engine/Source/Runtime/Core/Public/CoreMinimal.h",
        title="CoreMinimal source-read anchor",
        module="Core",
        triggers=(
            "coreminimal",
            "codalith_read_source",
            "read source",
            "ai read",
            "source read",
            "safe source",
            "bounded source",
            "source snippets",
            "audit policy",
            "source-backed",
            "ai coding agents",
        ),
        line_terms=("CoreMinimal",),
    ),
)


def locate_source_priors(
    corpus: Corpus,
    *,
    query: str,
    identifiers: list[str],
    max_hits: int,
) -> list[RetrievalHit]:
    scored: list[tuple[float, SourcePrior]] = []
    normalized_query = _normalize(query)
    identifier_terms = {_normalize(identifier) for identifier in identifiers}
    query_tokens = set(_query_tokens(normalized_query))
    for prior in SOURCE_PRIORS:
        score = _score_prior(prior, normalized_query, identifier_terms, query_tokens)
        if score > 0:
            scored.append((score, prior))

    hits: list[RetrievalHit] = []
    for score, prior in sorted(scored, key=lambda item: item[0], reverse=True):
        hit = _hit_for_prior(corpus, prior, query=query, score=score)
        if hit is not None:
            hits.append(hit)
        if len(hits) >= max_hits:
            break
    return hits


def _score_prior(
    prior: SourcePrior,
    normalized_query: str,
    identifier_terms: set[str],
    query_tokens: set[str],
) -> float:
    score = 0.0
    for trigger in prior.triggers:
        normalized_trigger = _normalize(trigger)
        if not normalized_trigger:
            continue
        if " " in normalized_trigger:
            if normalized_trigger in normalized_query:
                score += 8.0
            continue
        if normalized_trigger in identifier_terms:
            score += 10.0
        elif normalized_trigger in query_tokens:
            score += 6.0
        elif normalized_trigger in normalized_query:
            score += 3.0
    basename = _normalize(Path(prior.path).name)
    if basename and basename in normalized_query:
        score += 10.0
    if prior.module and _normalize(prior.module) in normalized_query:
        score += 4.0
    return score


def _hit_for_prior(corpus: Corpus, prior: SourcePrior, *, query: str, score: float) -> RetrievalHit | None:
    full_path = _root(corpus) / prior.path
    if not full_path.is_file():
        return None
    try:
        lines = full_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    if not lines:
        return None
    start, end = _window(lines, query=query, line_terms=prior.line_terms)
    snippet = "\n".join(lines[start - 1 : end])
    return RetrievalHit(
        source="ue-source-locator",
        corpus_id=corpus.corpus_id,
        uri=_uri_for(corpus, prior.path, start, end),
        path=prior.path,
        start_line=start,
        end_line=end,
        title=f"{prior.title}: {Path(prior.path).name}",
        snippet=snippet,
        score=score + 1000.0,
        kind="source-prior",
        language=_language(prior.path),
        module=prior.module,
        reason="High-confidence UE source entry point matched from query terms.",
        metadata={"matched_by": "ue-source-locator"},
    )


def _window(lines: list[str], *, query: str, line_terms: tuple[str, ...]) -> tuple[int, int]:
    search_terms = [term for term in [*_query_tokens(query), *line_terms] if len(term) >= 3]
    lowered_terms = [_normalize(term) for term in search_terms]
    best_line = 1
    for index, line in enumerate(lines, start=1):
        normalized_line = _normalize(line)
        if any(term and term in normalized_line for term in lowered_terms):
            best_line = index
            break
    start = max(1, best_line - 4)
    end = min(len(lines), best_line + 15)
    return start, end


def _root(corpus: Corpus) -> Path:
    return corpus.indexed_root if corpus.indexed_root.exists() else corpus.source_root


def _uri_for(corpus: Corpus, path: str, start: int, end: int) -> str:
    if corpus.kind == "project":
        return f"ue-project://{corpus.corpus_id}/source/{path}#L{start}-L{end}"
    version = corpus.ue_version or corpus.corpus_id.removeprefix("ue-")
    return f"ue://{version}/source/{path}#L{start}-L{end}"


def _language(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in {".h", ".hpp", ".inl", ".cpp", ".c"}:
        return "cpp"
    if suffix == ".cs":
        return "csharp"
    return "text"


def _query_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", _normalize(text))


def _normalize(text: str) -> str:
    return text.lower().replace("-", " ")
