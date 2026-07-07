from __future__ import annotations

from codalith.semantic.extractors.build_cs import BuildCsExtractor, write_module_deps
from codalith.semantic.extractors.compile_guards import extract_compile_guards
from codalith.semantic.extractors.cpp_symbols import extract_cpp_symbols
from codalith.semantic.extractors.target_cs import extract_target_text
from codalith.semantic.extractors.uht_reflection import UHTReflectionExtractor
from codalith.semantic.extractors.unreal import extract_semantic_summary
from codalith.semantic.graph import query_graph
from codalith.semantic.store import SemanticStore


def test_build_cs_extractor_outputs_dependencies():
    text = """
    PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject" });
    PrivateDependencyModuleNames.Add("Engine");
    DynamicallyLoadedModuleNames.AddRange(new string[] { "Renderer" });
    """
    deps = BuildCsExtractor().extract_text(text, module_name="GameplayAbilities")
    assert {dep.to_module for dep in deps} == {"Core", "CoreUObject", "Engine", "Renderer"}
    store = SemanticStore()
    write_module_deps(store, corpus_id="ue-5.7.4", evidence_uri="codalith://ue-5.7.4/source/X#L1-L3", dependencies=deps)
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


def test_uht_reflection_extractor_parses_nested_meta_and_skips_control_flow():
    text = """
    #include "Thing.generated.h"
    UCLASS()
    class AThing : public AActor {
    GENERATED_BODY()
    UPROPERTY(EditAnywhere, ReplicatedUsing=OnRep_Health,
        meta=(ClampMin="0", ClampMax="100", AllowedClasses=(AActor, APawn)))
    int32 Health;
    void Helper() {
      if (Health > 0) { Health = 0; }
      for (int32 Index = 0; Index < 3; ++Index) {}
    }
    UFUNCTION(BlueprintCallable, meta=(ToolTip="Runs (once)"))
    void DoThing();
    };
    """
    entities = UHTReflectionExtractor().extract_text(text, module_name="Engine")

    prop = next(entity for entity in entities if entity.kind == "uproperty")
    assert prop.name == "Health"
    assert prop.metadata["rep_notify"] == "OnRep_Health"
    assert "ClampMax=\"100\"" in str(prop.specifiers["meta"])
    assert "AllowedClasses=(AActor, APawn)" in str(prop.specifiers["meta"])

    functions = [entity.name for entity in entities if entity.kind == "ufunction"]
    assert functions == ["DoThing"]


def test_upsert_compile_guard_supports_deferred_commit(tmp_path):
    db_path = tmp_path / "semantic.sqlite"
    guard = extract_compile_guards("#if WITH_EDITOR\n#endif\n")[0]

    store = SemanticStore(db_path)
    store.upsert_compile_guard(
        corpus_id="ue-5.7.4",
        path="A.h",
        guard=guard,
        evidence_uri="codalith://ue-5.7.4/source/A.h#L1-L2",
        commit=False,
    )
    store.close()  # Uncommitted work must be discarded on close.
    assert SemanticStore(db_path).semantic_status("ue-5.7.4")["compile_guards"] == 0

    store = SemanticStore(db_path)
    store.upsert_compile_guard(
        corpus_id="ue-5.7.4",
        path="A.h",
        guard=guard,
        evidence_uri="codalith://ue-5.7.4/source/A.h#L1-L2",
        commit=False,
    )
    store.commit()
    store.close()
    assert SemanticStore(db_path).semantic_status("ue-5.7.4")["compile_guards"] == 1


def test_uht_reflection_extractor_detects_enum_interface_and_generated_macro():
    text = """
    #include "Thing.generated.h"
    UENUM(BlueprintType)
    enum class EThingState { Ready };
    UINTERFACE(BlueprintType)
    class UThingInterface : public UInterface { GENERATED_BODY() };
    """
    entities = UHTReflectionExtractor().extract_text(text, module_name="Engine")

    assert any(entity.kind == "uenum" and entity.name == "EThingState" for entity in entities)
    assert any(entity.kind == "uinterface" and entity.name == "UThingInterface" for entity in entities)
    assert any(entity.kind == "generated_macro" and entity.owner == "UThingInterface" for entity in entities)


def test_compile_guard_extractor_detects_known_guards():
    guards = extract_compile_guards(
        "#if WITH_EDITOR\n#endif\n#if !UE_BUILD_SHIPPING\n#endif\n"
        "#if PLATFORM_WINDOWS && WITH_CHAOS\n#endif\n"
    )
    assert {guard.macro for guard in guards} == {"WITH_EDITOR", "UE_BUILD_SHIPPING", "PLATFORM_WINDOWS", "WITH_CHAOS"}
    assert all(guard.end_line is not None for guard in guards)


def test_cpp_symbol_extractor_detects_macros_enums_delegates_and_cvars():
    symbols = extract_cpp_symbols(
        "namespace UE { enum class EMode { A }; }\n"
        "#define UE_SAMPLE 1\n"
        "DECLARE_DYNAMIC_MULTICAST_DELEGATE(FOnDone);\n"
        "static TAutoConsoleVariable<int32> CVarThing(TEXT(\"thing\"), 1, TEXT(\"help\"));\n"
    )
    assert {symbol.kind for symbol in symbols} >= {"enum", "macro", "delegate", "cvar"}


def test_target_cs_extractor_detects_type_modules_and_build_settings():
    target = extract_target_text(
        """
        public class ProjectATarget : TargetRules {
          public ProjectATarget(TargetInfo Target) : base(Target) {
            Type = TargetType.Game;
            DefaultBuildSettings = BuildSettingsVersion.V5;
            ExtraModuleNames.AddRange(new string[] { "ProjectA", "Inventory" });
          }
        }
        """
    )

    assert target is not None
    assert target.name == "ProjectA"
    assert target.target_type == "Game"
    assert target.build_settings == "V5"
    assert target.extra_modules == ["ProjectA", "Inventory"]


def test_semantic_summary_can_populate_graph_store(fake_engine_root, tmp_path):
    store = SemanticStore(tmp_path / "semantic.sqlite")

    summary = extract_semantic_summary(
        fake_engine_root,
        corpus_id="ue-5.7.4",
        store=store,
    )

    assert summary["semantic_store"]["graph_edges"] > 0
    actor_graph = query_graph(store, corpus_id="ue-5.7.4", node="AActor", depth=2)
    assert any(edge["edge_type"] == "replicated_using" for edge in actor_graph["edges"])
    engine_graph = query_graph(store, corpus_id="ue-5.7.4", node="Engine")
    assert any(edge["edge_type"] == "module_private_dependency" for edge in engine_graph["edges"])
    target_graph = query_graph(store, corpus_id="ue-5.7.4", node="target:ProjectA")
    assert any(edge["edge_type"] == "target_uses_module" for edge in target_graph["edges"])
    plugin_graph = query_graph(store, corpus_id="ue-5.7.4", node="plugin:Sample")
    assert any(edge["edge_type"] == "plugin_contains_module" for edge in plugin_graph["edges"])
    project_graph = query_graph(store, corpus_id="ue-5.7.4", node="project:ProjectA")
    assert any(edge["edge_type"] == "project_enables_plugin" for edge in project_graph["edges"])
    assert store.semantic_status("ue-5.7.4")["source_files"] > 0
