from __future__ import annotations

import json
from pathlib import Path

from ue_context.coderag.adapter import _native_store_dir
from ue_context.gateway.mcp_server import handle_request


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
    assert any(span["path"].endswith("Actor.h") for span in pack["source_spans"])


def test_mcp_tools_list_and_call(tools):
    listed = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, tools)
    assert listed is not None
    names = {item["name"] for item in listed["result"]["tools"]}
    assert {"ue_context", "ue_read_source", "ue_index_status"} <= names
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
