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
        triggers=("uworld", "world.h", "gameplay runtime", "engine world"),
        line_terms=("UWorld", "WorldType"),
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
