from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from codalith.coderag.native import (
    _configure_native_batch_embedding,
    _configure_native_env_aliases,
    _limit_chunk_texts,
    native_store_dir,
)
from codalith.corpus.source_policy import SourcePolicy, SourceReadRateLimiter
from codalith.corpus.source_reader import SourceReader
from codalith.errors import SourcePolicyError
from codalith.gateway.auth import AuthContext, AuthError
from codalith.gateway.mcp_server import handle_request
from codalith.gateway.tools import call_tool, create_runtime


@dataclass(frozen=True, slots=True)
class _Chunk:
    text: str


@dataclass(frozen=True, slots=True)
class _LineChunk:
    text: str
    start_line: int
    end_line: int
    language: str = "python"
    symbol: str | None = None
    kind: str = "window"


def test_local_coderag_adapter_searches_sample_fixture(adapter):
    status = adapter.reindex("sample-codebase")
    assert status["total_files"] >= 3
    assert status["indexed_at"] is not None
    hits = adapter.search_code("sample-codebase", "CachedValue ttl expiration", top_k=3)
    assert any(hit.path.endswith("cache.py") for hit in hits)


def test_local_coderag_adapter_ignores_configured_noise(adapter, sample_corpus_root):
    noise = sample_corpus_root / "build" / "search_index.json"
    noise.parent.mkdir(parents=True, exist_ok=True)
    noise.write_text(json.dumps({"tokens": ["CachedValue", "EventBus"] * 200}), encoding="utf-8")

    adapter.reindex("sample-codebase")
    hits = adapter.search_code("sample-codebase", "CachedValue ttl", top_k=5)

    assert hits
    assert all("build/" not in hit.path for hit in hits)
    assert any(hit.path.endswith("cache.py") for hit in hits)


def test_local_index_matches_camel_and_snake_subwords(adapter):
    adapter.reindex("sample-codebase")

    hits = adapter.search_code("sample-codebase", "cached value", top_k=5)
    assert any(hit.path.endswith("cache.py") for hit in hits)

    hits = adapter.search_code("sample-codebase", "cache value ttl", top_k=5)
    assert any(hit.path.endswith("cache.py") for hit in hits)


def test_local_search_honors_path_prefix_filter(adapter):
    adapter.reindex("sample-codebase")
    hits = adapter.search_code(
        "sample-codebase",
        "EventBus",
        top_k=10,
        filters={"path_prefix": "src/core"},
    )
    assert hits
    assert all(hit.path.startswith("src/core") for hit in hits)


def test_local_search_ranks_path_token_matches_higher(adapter):
    adapter.reindex("sample-codebase")
    hits = adapter.search_code("sample-codebase", "events EventBus dispatch", top_k=3)
    assert hits
    assert hits[0].path.endswith("events.py")


def test_native_store_dir_uses_corpus_store_not_global_env(registry, monkeypatch, tmp_path):
    corpus = registry.get_base("sample")
    monkeypatch.setenv("CODERAG_STORE_DIR", str(tmp_path / "wrong-store"))

    assert native_store_dir(corpus) == Path(corpus.coderag_store)


def test_configure_native_env_aliases_uses_codalith_names(monkeypatch):
    monkeypatch.delenv("CODERAG_PROVIDER", raising=False)
    monkeypatch.delenv("CODERAG_OPENAI_MODEL", raising=False)
    monkeypatch.delenv("CODERAG_OPENAI_BATCH", raising=False)
    monkeypatch.delenv("CODERAG_WORKERS", raising=False)
    monkeypatch.setenv("CODALITH_CODERAG_PROVIDER", "openai")
    monkeypatch.setenv("CODALITH_CODERAG_EMBEDDING_MODEL", "Qwen3-Embedding-8B")
    monkeypatch.setenv("CODALITH_CODERAG_EMBEDDING_BATCH_SIZE", "32")
    monkeypatch.setenv("CODALITH_CODERAG_WORKERS", "4")

    _configure_native_env_aliases()

    assert os.environ["CODERAG_PROVIDER"] == "openai"
    assert os.environ["CODERAG_OPENAI_MODEL"] == "Qwen3-Embedding-8B"
    assert os.environ["CODERAG_OPENAI_BATCH"] == "32"
    assert os.environ["CODERAG_WORKERS"] == "4"


def test_configure_native_env_aliases_preserves_upstream_names(monkeypatch):
    monkeypatch.setenv("CODERAG_PROVIDER", "hash")
    monkeypatch.setenv("CODALITH_CODERAG_PROVIDER", "openai")

    _configure_native_env_aliases()

    assert os.environ["CODERAG_PROVIDER"] == "hash"


def test_runtime_loads_registry_from_environment(monkeypatch, tmp_path):
    source_root = tmp_path / "env_corpus"
    source_root.mkdir()
    (source_root / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "corpora": {
                    "env-corpus": {
                        "kind": "source",
                        "version": "env",
                        "source_root": str(source_root),
                        "indexed_root": str(source_root),
                        "coderag_store": str(tmp_path / "coderag"),
                        "semantic_schema": "env_corpus",
                        "card_root": str(tmp_path / "cards"),
                        "default": True,
                        "access_scopes": ["source:read"],
                    }
                },
                "projects": {},
                "generated": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODALITH_CORPUS_REGISTRY", str(registry_path))
    monkeypatch.setenv("CODALITH_SEMANTIC_DB", str(tmp_path / "semantic.sqlite"))

    runtime = create_runtime(audit_log=str(tmp_path / "audit.jsonl"))
    try:
        assert runtime.registry.get_base().corpus_id == "env-corpus"
    finally:
        if runtime.semantic_store is not None:
            runtime.semantic_store.close()


def test_source_reader_prefers_source_root_when_indexed_root_is_partial(tmp_path, registry):
    corpus = registry.get_base()
    indexed = tmp_path / "indexed"
    indexed.mkdir()
    registry.corpora[corpus.corpus_id] = dataclass_replace(corpus, indexed_root=indexed)

    content = SourceReader(registry).read_source(corpus.corpus_id, "src/core/cache.py", 1, 2)

    assert "dataclass" in content


def test_limit_chunk_texts_splits_oversized_chunks():
    chunks = [_Chunk("abcd"), _Chunk("abcdef")]

    limited = _limit_chunk_texts(chunks, 4)

    assert limited[0] is chunks[0]
    assert [chunk.text for chunk in limited[1:]] == ["abcd", "ef"]
    assert chunks[1].text == "abcdef"


def test_limit_chunk_texts_preserves_line_ranges_with_byte_budget():
    chunks = [_LineChunk("aa\n汉汉汉汉\nbb", 10, 12)]

    limited = _limit_chunk_texts(chunks, max_chars=100, max_bytes=8)

    assert [(chunk.text, chunk.start_line, chunk.end_line) for chunk in limited] == [
        ("aa", 10, 10),
        ("汉汉", 11, 11),
        ("汉汉", 11, 11),
        ("bb", 12, 12),
    ]


def test_configure_native_batch_embedding_batches_across_files(monkeypatch):
    coderag_module = ModuleType("coderag")
    indexer_module = ModuleType("coderag.indexer")

    def chunk_file(text, language, config):
        return [SimpleNamespace(text=part, language=language) for part in text.split()]

    class Provider:
        def __init__(self):
            self.calls = []

        def embed_documents(self, texts):
            self.calls.append(list(texts))
            return list(texts)

    class Indexer:
        def __init__(self):
            self.provider = Provider()
            self.config = object()
            self.writes = []

        def _write(self, item, chunks, vectors):
            self.writes.append((item.text, [chunk.text for chunk in chunks], list(vectors or [])))
            return len(chunks), 0

        def _embed_and_write(self, work, *, reporter):
            raise AssertionError("original implementation should be patched")

    indexer_module.chunk_file = chunk_file
    indexer_module.Indexer = Indexer
    coderag_module.indexer = indexer_module
    monkeypatch.setitem(sys.modules, "coderag", coderag_module)
    monkeypatch.setitem(sys.modules, "coderag.indexer", indexer_module)
    monkeypatch.setenv("CODALITH_CODERAG_BATCH_CHUNKS", "3")

    _configure_native_batch_embedding()

    reporter = SimpleNamespace(messages=[], update=lambda message: reporter.messages.append(message))
    indexer = Indexer()
    work = [
        SimpleNamespace(text="a b", language="text"),
        SimpleNamespace(text="c", language="text"),
        SimpleNamespace(text="d", language="text"),
    ]

    results = list(indexer._embed_and_write(work, reporter=reporter))

    assert len(results) == 3
    assert indexer.provider.calls == [["a", "b", "c"], ["d"]]
    assert reporter.messages[-1] == "Embedding 3/3 file(s)..."


def test_codalith_read_source_adds_line_numbers_and_audit(tools):
    result = tools.codalith_read_source(
        uri="codalith://sample-codebase/source/src/core/cache.py#L1-L4"
    )
    assert result["content"].startswith("1|")
    expected_source = "\n".join(
        [
            "from dataclasses import dataclass",
            "",
            "@dataclass(frozen=True)",
            "class CachedValue:",
        ]
    )
    expected_hash = hashlib.sha256(expected_source.encode("utf-8")).hexdigest()
    assert result["source_hash"] == expected_hash
    audit = json.loads(tools.runtime.audit.path.read_text(encoding="utf-8").splitlines()[0])
    assert audit["decision"] == "allowed"
    assert audit["source_hash"] == expected_hash
    assert audit["user_id"] == "test-user"


def test_codalith_read_source_requires_source_scope(tools):
    tools.runtime.identity = AuthContext(
        user_id="limited",
        session_id="limited-session",
        client="pytest",
        scopes=frozenset(),
    )

    with pytest.raises((AuthError, SourcePolicyError)):
        tools.codalith_read_source(
            uri="codalith://sample-codebase/source/src/core/cache.py#L1-L4"
        )


def test_codalith_context_returns_context_pack(tools):
    pack = tools.codalith_context(query="CachedValue ttl expiration", version="sample")
    assert pack["schema_version"] == "0.2"
    assert pack["version"] == "sample"
    assert pack["corpus_id"] == "sample-codebase"
    assert pack["source_spans"]
    assert pack["graph_edges"]
    assert any(span["path"].endswith("cache.py") for span in pack["source_spans"])


def test_graph_returns_semantic_edges(tools):
    graph = tools.codalith_graph(node="EventBus", version="sample", depth=2)

    assert any(edge["edge_type"] == "declares_symbol" for edge in graph["edges"])


def test_lookup_symbol_includes_graph_and_examples(tools):
    result = tools.codalith_lookup_symbol(symbol="EventBus", version="sample")

    assert result["context"]["source_spans"]
    assert result["graph"]["edges"]
    assert result["examples"]


def test_project_overlay_context_and_source_read(tools):
    pack = tools.codalith_context(
        query="ProjectFeature EventBus",
        version="sample",
        project="SampleProject",
    )

    assert pack["project"] == "SampleProject"
    assert any(str(span["uri"]).startswith("codalith://SampleProject/") for span in pack["source_spans"])

    result = tools.codalith_read_source(
        uri="codalith://SampleProject/source/src/project/feature.py#L1-L5"
    )

    assert result["corpus_id"] == "SampleProject"
    assert "ProjectFeature" in result["content"]


def test_compare_versions_compiles_both_context_packs(tools):
    result = tools.codalith_compare_versions(
        target="CachedValue",
        from_version="sample",
        to_version="sample-next",
    )

    assert result["from"]["version"] == "sample"
    assert result["to"]["version"] == "sample-next"
    assert result["from"]["source_spans"]
    assert result["to"]["source_spans"]


def test_index_status_reports_semantic_store(tools):
    status = tools.codalith_index_status(version="sample")

    assert status["semantic"]["base"]["graph_edges"] > 0
    assert status["semantic"]["base"]["symbols"] > 0


def test_mcp_tools_list_and_call(tools):
    listed = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, tools)
    assert listed is not None
    names = {item["name"] for item in listed["result"]["tools"]}
    assert {
        "codalith_context",
        "codalith_read_source",
        "codalith_index_status",
        "codalith_lookup_symbol",
        "codalith_graph",
        "codalith_examples",
        "codalith_compare_versions",
    } <= names
    called = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "codalith_context",
                "arguments": {"query": "CachedValue ttl", "version": "sample"},
            },
        },
        tools,
    )
    assert called is not None
    assert called["result"]["structuredContent"]["source_spans"]


def test_mcp_tools_call_rejects_null_params_as_invalid_params(tools):
    for request in (
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": None},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": None}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": "bogus"},
    ):
        response = handle_request(request, tools)
        assert response is not None
        assert response["error"]["code"] == -32602


def test_mcp_initialize_instructions_come_from_registry(tools):
    initialized = handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, tools)
    assert initialized is not None
    instructions = initialized["result"]["instructions"]
    assert "Sample Codebase sample (Neutral source corpus)" in instructions
    assert "cache" in instructions
    assert "SampleProject" in instructions
    assert "codalith_context" in instructions
    assert "Unreal" not in instructions and "UE" not in instructions


def test_mcp_tool_schema_version_default_follows_registry_default(tools):
    listed = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, tools)
    assert listed is not None
    schemas = {item["name"]: item for item in listed["result"]["tools"]}
    for name in ("codalith_context", "codalith_lookup_symbol", "codalith_graph", "codalith_examples"):
        version_property = schemas[name]["inputSchema"]["properties"]["version"]
        assert version_property["default"] == "sample"
    descriptions = " ".join(str(schema["description"]) for schema in schemas.values())
    assert "Unreal" not in descriptions and "UE" not in descriptions


def test_codalith_context_defaults_to_registry_default_engine(tools):
    pack = tools.codalith_context(query="EventBus dispatch")
    assert pack["version"] == "sample"
    assert pack["source_spans"]


def test_mcp_resources_list_templates_and_read(tools):
    listed = handle_request({"jsonrpc": "2.0", "id": 1, "method": "resources/list"}, tools)
    assert listed is not None
    assert any(item["uri"] == "codalith://sample-codebase/modules" for item in listed["result"]["resources"])

    templates = handle_request(
        {"jsonrpc": "2.0", "id": 2, "method": "resources/templates/list"},
        tools,
    )
    assert templates is not None
    assert any(
        item["uriTemplate"] == "codalith://{corpus}/symbol/{symbol}"
        for item in templates["result"]["resourceTemplates"]
    )

    read = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "resources/read",
            "params": {"uri": "codalith://sample-codebase"},
        },
        tools,
    )
    assert read is not None
    content = json.loads(read["result"]["contents"][0]["text"])
    assert content["semantic"]["graph_edges"] > 0


def _read_resource_via_rpc(tools, uri: str) -> dict[str, Any]:
    response = handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {"uri": uri}},
        tools,
    )
    assert response is not None
    if "error" in response:
        raise AssertionError(response["error"]["message"])
    return json.loads(response["result"]["contents"][0]["text"])


def test_mcp_resource_templates_resolve_module_symbol_source_and_card(tools):
    module = _read_resource_via_rpc(tools, "codalith://sample-codebase/module/core")
    assert module["kind"] == "module"
    assert module["module"]["module_name"] == "core"

    symbol = _read_resource_via_rpc(tools, "codalith://sample-codebase/symbol/EventBus")
    assert symbol["kind"] == "symbol"
    assert any(match["name"] == "EventBus" for match in symbol["matches"])

    source = _read_resource_via_rpc(
        tools,
        "codalith://sample-codebase/source/src/core/cache.py#L1-L4",
    )
    assert source["corpus_id"] == "sample-codebase"
    assert source["content"]

    card_root = tools.runtime.registry.corpora["sample-codebase"].card_root
    card_file = card_root / "KNOWLEDGE" / "Module" / "module-core-cache.md"
    card_file.parent.mkdir(parents=True, exist_ok=True)
    card_file.write_text("# Core Cache API\n", encoding="utf-8")
    card = _read_resource_via_rpc(tools, "codalith://sample-codebase/card/module/module-core-cache")
    assert card["kind"] == "card"
    assert card["markdown"].startswith("# Core Cache API")


def test_mcp_resource_read_rejects_unknown_and_traversal_uris(tools):
    for uri in (
        "codalith://sample-codebase/module/DoesNotExist",
        "codalith://sample-missing",
        "codalith://sample-codebase/card/../escape",
        "codalith://sample-codebase/card/module/../../escape",
    ):
        response = handle_request(
            {"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {"uri": uri}},
            tools,
        )
        assert response is not None
        assert "error" in response, uri


def test_call_tool_rejects_unknown_tools_and_invalid_arguments(tools):
    with pytest.raises(ValueError, match="Unknown tool"):
        call_tool(tools, "_scopes", {})
    with pytest.raises(ValueError, match="missing required"):
        call_tool(tools, "codalith_context", {})
    with pytest.raises(ValueError, match="unexpected argument"):
        call_tool(tools, "codalith_context", {"query": "CachedValue", "bogus": True})
    with pytest.raises(ValueError, match="must be a string"):
        call_tool(tools, "codalith_context", {"query": 123})
    with pytest.raises(ValueError, match="must not be null"):
        call_tool(tools, "codalith_context", {"query": None})
    with pytest.raises(ValueError, match="must be >="):
        call_tool(tools, "codalith_context", {"query": "x", "max_source_spans": 0})
    with pytest.raises(ValueError, match="must be <="):
        call_tool(tools, "codalith_graph", {"node": "core", "max_nodes": 1000})
    with pytest.raises(ValueError, match="items must be strings"):
        call_tool(tools, "codalith_graph", {"node": "core", "edge_types": [1]})
    with pytest.raises(ValueError, match="diff_type"):
        call_tool(
            tools,
            "codalith_compare_versions",
            {
                "target": "CachedValue",
                "from_version": "sample",
                "to_version": "sample-next",
                "diff_type": "summary",
            },
        )


class _CountingSourceReader:
    def __init__(self, inner) -> None:
        self._inner = inner
        self.read_source_calls = 0

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def read_source(self, *args, **kwargs):
        self.read_source_calls += 1
        return self._inner.read_source(*args, **kwargs)


def test_read_source_rate_limit_precedes_file_read(tools):
    counting = _CountingSourceReader(tools.runtime.source_reader)
    tools.runtime.source_reader = counting
    tools.runtime.rate_limiter = SourceReadRateLimiter(
        SourcePolicy(max_source_reads_per_10min=1),
        time_func=lambda: 100.0,
    )
    uri = "codalith://sample-codebase/source/src/core/cache.py#L1-L4"

    tools.codalith_read_source(uri=uri)
    assert counting.read_source_calls == 1

    with pytest.raises(SourcePolicyError):
        tools.codalith_read_source(uri=uri)
    assert counting.read_source_calls == 1


def test_read_source_fills_default_window_and_validates_ranges(tools):
    result = tools.codalith_read_source(uri="codalith://sample-codebase/source/src/core/cache.py")
    assert result["start_line"] == 1
    assert result["end_line"] == tools.runtime.policy.default_max_lines

    with pytest.raises(SourcePolicyError):
        tools.codalith_read_source(
            uri="codalith://sample-codebase/source/src/core/cache.py",
            start_line=0,
            end_line=4,
        )
    with pytest.raises(SourcePolicyError):
        tools.codalith_read_source(
            uri="codalith://sample-codebase/source/src/core/cache.py",
            start_line=5,
            end_line=2,
        )


def test_lookup_symbol_omits_graph_without_scope(tools):
    tools.runtime.identity = AuthContext(
        user_id="limited",
        session_id="limited-session",
        client="pytest",
        scopes=frozenset({"source:read", "index:status", "cards:read"}),
    )

    result = tools.codalith_lookup_symbol(symbol="EventBus", version="sample")

    assert "graph" not in result
    assert any("Graph neighborhood omitted" in warning for warning in result["warnings"])
