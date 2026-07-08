from __future__ import annotations

from codalith.semantic.extractors import run_profile
from codalith.semantic.graph import query_graph
from codalith.semantic.store import SemanticStore
from codalith.semantic.types import CompileGuard, ModuleDependency, ReflectionEntity, SourceSymbol


def test_run_profile_without_domain_extractor_is_noop(sample_corpus_root):
    summary = run_profile(None, sample_corpus_root, corpus_id="sample-codebase")

    assert summary["corpus_id"] == "sample-codebase"
    assert summary["profile"] is None
    assert summary["modules"] == 0


def test_semantic_store_records_generic_modules_symbols_and_edges():
    store = SemanticStore()
    dependency = ModuleDependency("core", "shared", "public")

    store.upsert_module_dep(
        corpus_id="sample-codebase",
        dependency=dependency,
        evidence_uri="codalith://sample-codebase/source/src/core/cache.py#L1-L3",
    )
    store.upsert_cpp_symbol(
        corpus_id="sample-codebase",
        path="src/core/cache.py",
        symbol=SourceSymbol(name="CachedValue", kind="class", line=4),
        evidence_uri="codalith://sample-codebase/source/src/core/cache.py#L1-L8",
        module_name="core",
    )

    rows = store.list_module_deps("sample-codebase", "core")
    assert len(rows) == 1
    graph = query_graph(store, corpus_id="sample-codebase", node="core")
    assert any(edge["to"] == "module:shared" for edge in graph["edges"])
    assert any(edge["to"] == "symbol:CachedValue" for edge in graph["edges"])


def test_upsert_compile_guard_supports_deferred_commit(tmp_path):
    db_path = tmp_path / "semantic.sqlite"
    guard = CompileGuard(macro="FEATURE_FLAG", line=1, expression="FEATURE_FLAG", end_line=2)

    store = SemanticStore(db_path)
    store.upsert_compile_guard(
        corpus_id="sample-codebase",
        path="src/core/cache.py",
        guard=guard,
        evidence_uri="codalith://sample-codebase/source/src/core/cache.py#L1-L2",
        commit=False,
    )
    store.close()
    assert SemanticStore(db_path).semantic_status("sample-codebase")["compile_guards"] == 0

    store = SemanticStore(db_path)
    store.upsert_compile_guard(
        corpus_id="sample-codebase",
        path="src/core/cache.py",
        guard=guard,
        evidence_uri="codalith://sample-codebase/source/src/core/cache.py#L1-L2",
        commit=False,
    )
    store.commit()
    store.close()
    assert SemanticStore(db_path).semantic_status("sample-codebase")["compile_guards"] == 1


def test_reflection_entities_remain_domain_data_not_core_extractors():
    store = SemanticStore()
    store.upsert_reflection_entity(
        corpus_id="sample-codebase",
        entity=ReflectionEntity(
            kind="schema",
            name="CachedValue",
            owner="Cache",
            specifiers={"source": "manual"},
            declaration_uri="codalith://sample-codebase/source/src/core/cache.py#L1-L8",
            module_name="core",
        ),
    )

    graph = query_graph(store, corpus_id="sample-codebase", node="CachedValue")
    assert any(edge["edge_type"] == "has_reflection" for edge in graph["edges"])
