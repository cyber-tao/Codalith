from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ue_context.coderag.adapter import _limit_chunk_texts, _native_store_dir
from ue_context.gateway.mcp_server import handle_request


@dataclass(frozen=True, slots=True)
class _Chunk:
    text: str


def test_local_coderag_adapter_searches_fixture(adapter):
    status = adapter.reindex("ue-5.7.4")
    assert status["total_files"] >= 5
    hits = adapter.search_code("ue-5.7.4", "ReplicatedUsing OnRep", top_k=3)
    assert any(hit.path.endswith("Actor.h") for hit in hits)


def test_native_store_dir_prefers_env_override(registry, monkeypatch, tmp_path):
    corpus = registry.get_engine("5.7.4")
    override = tmp_path / "ollama-store"
    monkeypatch.setenv("CODERAG_STORE_DIR", str(override))

    assert _native_store_dir(corpus) == override

    monkeypatch.delenv("CODERAG_STORE_DIR")
    assert _native_store_dir(corpus) == Path(corpus.coderag_store)


def test_limit_chunk_texts_truncates_oversized_chunks():
    chunks = [_Chunk("abcd"), _Chunk("abcdef")]

    limited = _limit_chunk_texts(chunks, 4)

    assert limited[0] is chunks[0]
    assert limited[1].text == "abcd"
    assert chunks[1].text == "abcdef"


def test_ue_read_source_adds_line_numbers_and_audit(tools, tmp_path):
    result = tools.ue_read_source(
        uri="ue://5.7.4/source/Engine/Source/Runtime/Engine/Classes/GameFramework/Actor.h#L1-L4"
    )
    assert result["content"].startswith("1|")
    audit_path = tools.runtime.audit.path
    assert audit_path.exists()
    audit = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert audit["decision"] == "allowed"


def test_ue_context_returns_context_pack(tools):
    pack = tools.ue_context(query="UPROPERTY ReplicatedUsing OnRep", version="5.7.4")
    assert pack["schema_version"] == "0.1"
    assert pack["version"] == "5.7.4"
    assert pack["source_spans"]
    assert pack["graph_edges"]
    assert any(span["path"].endswith("Actor.h") for span in pack["source_spans"])


def test_ue_graph_returns_semantic_edges(tools):
    graph = tools.ue_graph(node="AActor", version="5.7.4", depth=2)

    assert any(edge["edge_type"] == "owns_reflection" for edge in graph["edges"])
    assert any(edge["edge_type"] == "replicated_using" for edge in graph["edges"])


def test_ue_lookup_symbol_includes_graph_and_examples(tools):
    result = tools.ue_lookup_symbol(symbol="AActor", version="5.7.4")

    assert result["context"]["source_spans"]
    assert result["graph"]["edges"]
    assert result["examples"]


def test_ue_index_status_reports_semantic_store(tools):
    status = tools.ue_index_status(version="5.7.4")

    assert status["semantic"]["engine"]["graph_edges"] > 0


def test_mcp_tools_list_and_call(tools):
    listed = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, tools)
    assert listed is not None
    names = {item["name"] for item in listed["result"]["tools"]}
    assert {
        "ue_context",
        "ue_read_source",
        "ue_index_status",
        "ue_lookup_symbol",
        "ue_graph",
        "ue_examples",
        "ue_compare_versions",
    } <= names
    called = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "ue_context",
                "arguments": {"query": "AActor BeginPlay", "version": "5.7.4"},
            },
        },
        tools,
    )
    assert called is not None
    assert called["result"]["structuredContent"]["source_spans"]


def test_mcp_resources_list_templates_and_read(tools):
    listed = handle_request({"jsonrpc": "2.0", "id": 1, "method": "resources/list"}, tools)
    assert listed is not None
    assert any(item["uri"] == "ue://5.7.4/modules" for item in listed["result"]["resources"])

    templates = handle_request(
        {"jsonrpc": "2.0", "id": 2, "method": "resources/templates/list"},
        tools,
    )
    assert templates is not None
    assert any(
        item["uriTemplate"] == "ue://{version}/symbol/{symbol}"
        for item in templates["result"]["resourceTemplates"]
    )

    read = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "resources/read",
            "params": {"uri": "ue://5.7.4"},
        },
        tools,
    )
    assert read is not None
    content = json.loads(read["result"]["contents"][0]["text"])
    assert content["semantic"]["graph_edges"] > 0
