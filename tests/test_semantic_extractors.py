from __future__ import annotations

from ue_context.semantic.db import SemanticStore
from ue_context.semantic.extractors.build_cs import BuildCsExtractor, write_module_deps
from ue_context.semantic.extractors.compile_guards import extract_compile_guards
from ue_context.semantic.extractors.uht_reflection import UHTReflectionExtractor


def test_build_cs_extractor_outputs_dependencies():
    text = """
    PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject" });
    PrivateDependencyModuleNames.Add("Engine");
    DynamicallyLoadedModuleNames.AddRange(new string[] { "Renderer" });
    """
    deps = BuildCsExtractor().extract_text(text, module_name="GameplayAbilities")
    assert {dep.to_module for dep in deps} == {"Core", "CoreUObject", "Engine", "Renderer"}
    store = SemanticStore()
    write_module_deps(store, corpus_id="ue-5.7.4", evidence_uri="ue://5.7.4/source/X#L1-L3", dependencies=deps)
    rows = store.list_module_deps("ue-5.7.4", "GameplayAbilities")
    assert len(rows) == 4


def test_uht_reflection_extractor_detects_replicated_using():
    text = """
    #include "Thing.generated.h"
    UCLASS(BlueprintType)
    class AThing : public AActor {
    GENERATED_BODY()
    UPROPERTY(ReplicatedUsing=OnRep_Health, meta=(ClampMin="0"))
    int32 Health;
    UFUNCTION(BlueprintCallable)
    void OnRep_Health();
    };
    """
    entities = UHTReflectionExtractor().extract_text(text, module_name="Engine")
    assert any(entity.kind == "uclass" and entity.name == "AThing" for entity in entities)
    prop = next(entity for entity in entities if entity.kind == "uproperty")
    assert prop.metadata["rep_notify"] == "OnRep_Health"
    assert any(entity.kind == "ufunction" and entity.name == "OnRep_Health" for entity in entities)


def test_compile_guard_extractor_detects_known_guards():
    guards = extract_compile_guards("#if WITH_EDITOR\n#endif\n#if !UE_BUILD_SHIPPING\n#endif\n")
    assert {guard.macro for guard in guards} == {"WITH_EDITOR", "UE_BUILD_SHIPPING"}
