from __future__ import annotations

from jobs.extract_semantic import extract_semantic_summary

from codalith.semantic.db import SemanticStore
from codalith.semantic.extractors.build_cs import BuildCsExtractor, write_module_deps
from codalith.semantic.extractors.compile_guards import extract_compile_guards
from codalith.semantic.extractors.uht_reflection import UHTReflectionExtractor
from codalith.semantic.graph import query_graph


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
    graph = query_graph(store, corpus_id="ue-5.7.4", node="GameplayAbilities")
    assert any(edge["to"] == "module:Engine" for edge in graph["edges"])


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


def test_semantic_summary_can_populate_graph_store(fake_engine_root, tmp_path):
    store = SemanticStore(tmp_path / "semantic.sqlite")

    summary = extract_semantic_summary(
        fake_engine_root,
        corpus_id="ue-5.7.4",
        version="5.7.4",
        store=store,
    )

    assert summary["semantic_store"]["graph_edges"] > 0
    actor_graph = query_graph(store, corpus_id="ue-5.7.4", node="AActor", depth=2)
    assert any(edge["edge_type"] == "replicated_using" for edge in actor_graph["edges"])
    engine_graph = query_graph(store, corpus_id="ue-5.7.4", node="Engine")
    assert any(edge["edge_type"] == "module_private_dependency" for edge in engine_graph["edges"])
