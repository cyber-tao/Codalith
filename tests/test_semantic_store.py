from __future__ import annotations

import os
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from codalith.errors import ConfigurationError
from codalith.semantic.graph import aggregate_graph_neighborhood, query_graph
from codalith.semantic.store import SemanticStore
from codalith.semantic.types import CompileGuard, ModuleDependency, SourceSymbol


def test_semantic_store_records_generic_modules_symbols_and_edges():
    store = SemanticStore()
    dependency = ModuleDependency("core", "shared", "public")

    store.upsert_module_dep(
        corpus_id="sample-codebase",
        dependency=dependency,
        evidence_uri="codalith://sample-codebase/source/src/core/cache.py#L1-L3",
    )
    store.upsert_symbol(
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


def test_semantic_status_reports_generic_counts():
    store = SemanticStore()
    store.upsert_symbol(
        corpus_id="sample-codebase",
        path="src/core/events.py",
        symbol=SourceSymbol(name="EventBus", kind="class", line=1),
        evidence_uri="codalith://sample-codebase/source/src/core/events.py#L1-L10",
        module_name="core",
    )

    status = store.semantic_status("sample-codebase")
    assert status["corpus_id"] == "sample-codebase"
    assert status["symbols"] == 1
    assert status["graph_edges"] >= 1
    assert set(status) == {
        "corpus_id",
        "dialect",
        "source_files",
        "modules",
        "module_dependencies",
        "symbols",
        "compile_guards",
        "graph_edges",
        "graph_nodes",
    }


def test_semantic_store_uses_thread_local_connections(tmp_path):
    store = SemanticStore(tmp_path / "semantic.sqlite")

    def write_module(index: int) -> None:
        store.upsert_module(
            corpus_id="sample-codebase",
            module_name=f"module-{index}",
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write_module, range(20)))

    assert all(
        store.get_module("sample-codebase", f"module-{index}") is not None
        for index in range(20)
    )
    store.close()


def test_semantic_store_rejects_unversioned_legacy_schema(tmp_path):
    path = tmp_path / "legacy.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE codalith_corpora (corpus_id TEXT PRIMARY KEY)")
    connection.commit()
    connection.close()

    with pytest.raises(ConfigurationError, match="unversioned legacy schema"):
        SemanticStore(path)


def test_graph_limits_never_return_orphan_edges():
    store = SemanticStore()
    for index in range(5):
        store.upsert_graph_edge(
            corpus_id="sample-codebase",
            from_node="module:core",
            edge_type="uses",
            to_node=f"symbol:Item{index}",
            evidence_uri=f"codalith://sample/source/item.py#L{index + 1}",
            extractor="test",
        )

    graph = query_graph(
        store,
        corpus_id="sample-codebase",
        node="module:core",
        max_nodes=3,
    )
    node_ids = {node["id"] for node in graph["nodes"]}

    assert len(node_ids) <= 3
    assert all(
        edge["from"] in node_ids and edge["to"] in node_ids
        for edge in graph["edges"]
    )


def test_graph_edge_aggregates_evidence_and_preserves_corpus_provenance():
    store = SemanticStore()
    for corpus_id in ("base", "overlay"):
        for line in (1, 2):
            store.upsert_graph_edge(
                corpus_id=corpus_id,
                from_node="module:core",
                edge_type="uses",
                to_node="symbol:EventBus",
                evidence_uri=f"codalith://{corpus_id}/source/events.py#L{line}",
                extractor="test",
            )

    base_edges = store.list_graph_edges("base", node="module:core")
    assert len(base_edges) == 1
    assert len(base_edges[0].evidence_uris) == 2

    graph = aggregate_graph_neighborhood(
        store,
        corpus_ids=["base", "overlay"],
        seed_nodes=["module:core"],
        include_corpus_id=True,
    )
    assert {edge["corpus_id"] for edge in graph["edges"]} == {"base", "overlay"}


def test_find_symbols_is_case_insensitive_and_escapes_like_wildcards():
    store = SemanticStore()
    for name in ("Foo_Bar", "FooXBar"):
        store.upsert_symbol(
            corpus_id="sample-codebase",
            path=f"{name}.py",
            symbol=SourceSymbol(
                name=name,
                qualified_name=f"Namespace::{name}",
                kind="class",
                line=1,
            ),
            evidence_uri=f"codalith://sample-codebase/source/{name}.py#L1",
        )

    matches = store.find_symbols("sample-codebase", "foo_bar")

    assert [match["name"] for match in matches] == ["Foo_Bar"]


def test_postgres_and_sqlite_symbol_query_parity(tmp_path):
    dsn = os.getenv("CODALITH_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("CODALITH_TEST_POSTGRES_DSN is not configured")
    corpus_id = f"parity-{uuid.uuid4().hex}"
    stores = [
        SemanticStore(tmp_path / "parity.sqlite"),
        SemanticStore(dsn),
    ]
    snapshots: list[list[tuple[str, str]]] = []
    try:
        for store in stores:
            for name in ("Foo_Bar", "FooXBar"):
                store.upsert_symbol(
                    corpus_id=corpus_id,
                    path=f"{name}.py",
                    symbol=SourceSymbol(
                        name=name,
                        qualified_name=f"Namespace::{name}",
                        kind="class",
                        line=1,
                    ),
                    evidence_uri=f"codalith://{corpus_id}/source/{name}.py#L1",
                )
            snapshots.append(
                [
                    (str(row["name"]), str(row["qualified_name"]))
                    for row in store.find_symbols(corpus_id, "foo_bar")
                ]
            )
        assert snapshots[0] == snapshots[1] == [("Foo_Bar", "Namespace::Foo_Bar")]
    finally:
        postgres = stores[1]
        for table in ("codalith_graph_edges", "codalith_symbols"):
            postgres._execute(  # noqa: SLF001 - parity test cleanup.
                f"DELETE FROM {table} WHERE corpus_id = ?",  # noqa: S608
                (corpus_id,),
            )
        postgres.commit()
        for store in stores:
            store.close()
