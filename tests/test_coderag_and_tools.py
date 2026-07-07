from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from codalith.coderag.adapter import (
    _configure_native_batch_embedding,
    _configure_native_index_policy_hash,
    _configure_native_openai_timeout,
    _limit_chunk_texts,
    _native_store_dir,
    _policy_content_hash,
)
from codalith.corpus.source_policy import SourcePolicy, SourceReadRateLimiter
from codalith.errors import SourcePolicyError
from codalith.gateway.auth import AuthContext, AuthError
from codalith.gateway.mcp_server import handle_request
from codalith.gateway.tools import call_tool


@dataclass(frozen=True, slots=True)
class _Chunk:
    text: str


@dataclass(frozen=True, slots=True)
class _LineChunk:
    text: str
    start_line: int
    end_line: int
    language: str = "cpp"
    symbol: str | None = None
    kind: str = "window"


def test_local_coderag_adapter_searches_fixture(adapter):
    status = adapter.reindex("ue-5.7.4")
    assert status["total_files"] >= 5
    assert status["indexed_at"] is not None
    assert status["updated_at"] is not None
    hits = adapter.search_code("ue-5.7.4", "ReplicatedUsing OnRep", top_k=3)
    assert any(hit.path.endswith("Actor.h") for hit in hits)


def test_local_coderag_adapter_ignores_third_party_noise(adapter, fake_engine_root):
    noise = fake_engine_root / "Engine/Source/ThirdParty/Noise/docs/search_index.json"
    noise.parent.mkdir(parents=True, exist_ok=True)
    noise.write_text(
        json.dumps({"tokens": ["AActor", "replication", "ReplicatedUsing", "OnRep"] * 200}),
        encoding="utf-8",
    )

    adapter.reindex("ue-5.7.4")
    hits = adapter.search_code(
        "ue-5.7.4",
        "Where is AActor declared and how is replication represented?",
        top_k=5,
    )

    assert hits
    assert all("ThirdParty" not in hit.path for hit in hits)
    assert any(hit.path.endswith("Actor.h") for hit in hits)


def test_local_index_matches_camel_and_snake_subwords(adapter):
    adapter.reindex("ue-5.7.4")

    # "OnRep" only appears inside "OnRep_Health" / "ReplicatedUsing=OnRep_Health".
    hits = adapter.search_code("ue-5.7.4", "OnRep callback", top_k=5)
    assert any(hit.path.endswith("Actor.h") for hit in hits)

    # CamelCase word from "ReplicatedUsing".
    hits = adapter.search_code("ue-5.7.4", "replicated property", top_k=5)
    assert any(hit.path.endswith("Actor.h") for hit in hits)


def test_local_search_honors_path_prefix_filter(adapter):
    adapter.reindex("ue-5.7.4")
    hits = adapter.search_code(
        "ue-5.7.4",
        "UPROPERTY replication",
        top_k=10,
        filters={"path_prefix": "Source/ProjectA"},
    )
    assert hits
    assert all(hit.path.startswith("Source/ProjectA") for hit in hits)


def test_local_search_ranks_path_token_matches_higher(adapter):
    adapter.reindex("ue-5.7.4")
    hits = adapter.search_code("ue-5.7.4", "InventoryComponent OnRep_Items", top_k=3)
    assert hits
    assert hits[0].path.endswith("InventoryComponent.h") or hits[0].path.endswith(
        "InventoryComponent.cpp"
    )


def test_native_store_dir_prefers_env_override(registry, monkeypatch, tmp_path):
    corpus = registry.get_engine("5.7.4")
    override = tmp_path / "openai-store"
    monkeypatch.setenv("CODERAG_STORE_DIR", str(override))

    assert _native_store_dir(corpus) == override

    monkeypatch.delenv("CODERAG_STORE_DIR")
    assert _native_store_dir(corpus) == Path(corpus.coderag_store)


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


def test_configure_native_index_policy_hash_reindexes_old_hashes(monkeypatch, tmp_path):
    coderag_module = ModuleType("coderag")
    indexer_module = ModuleType("coderag.indexer")

    @dataclass(frozen=True, slots=True)
    class Work:
        rel: str
        language: str
        text: str
        content_hash: str
        mtime: float
        size: int
        existed: bool

    class Indexer:
        def _maybe_work(self, abs_path, rel, language, metas):
            existing = metas.get(rel)
            stat = abs_path.stat()
            if (
                existing is not None
                and existing.get("size") == stat.st_size
                and abs(float(existing.get("mtime") or 0.0) - stat.st_mtime) < 1e-6
            ):
                return None
            data = abs_path.read_bytes()
            content_hash = hashlib.sha256(data).hexdigest()
            if existing is not None and existing.get("content_hash") == content_hash:
                return None
            return Work(
                rel,
                language,
                data.decode("utf-8"),
                content_hash,
                stat.st_mtime,
                len(data),
                True,
            )

    indexer_module.Indexer = Indexer
    coderag_module.indexer = indexer_module
    monkeypatch.setitem(sys.modules, "coderag", coderag_module)
    monkeypatch.setitem(sys.modules, "coderag.indexer", indexer_module)
    monkeypatch.setenv("CODALITH_CODERAG_MAX_CHUNK_CHARS", "3072")
    monkeypatch.setenv("CODALITH_CODERAG_MAX_CHUNK_BYTES", "3584")

    path = tmp_path / "Actor.cpp"
    path.write_text("source", encoding="utf-8")
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()

    _configure_native_index_policy_hash()
    indexer = Indexer()
    old_hash_result = indexer._maybe_work(
        path,
        "Actor.cpp",
        "cpp",
        {
            "Actor.cpp": {
                "content_hash": source_hash,
                "mtime": path.stat().st_mtime,
                "size": path.stat().st_size,
            }
        },
    )

    assert old_hash_result is not None
    assert old_hash_result.content_hash == _policy_content_hash(
        "chunk-budget:chars=3072:bytes=3584",
        source_hash,
    )

    current_hash_result = indexer._maybe_work(
        path,
        "Actor.cpp",
        "cpp",
        {
            "Actor.cpp": {
                "content_hash": old_hash_result.content_hash,
                "mtime": path.stat().st_mtime,
                "size": path.stat().st_size,
            }
        },
    )

    assert current_hash_result is None


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
    assert indexer.writes == [
        ("a b", ["a", "b"], ["a", "b"]),
        ("c", ["c"], ["c"]),
        ("d", ["d"], ["d"]),
    ]
    assert reporter.messages[-1] == "Embedding 3/3 file(s)..."


def test_configure_native_batch_embedding_supports_concurrent_batches(monkeypatch):
    coderag_module = ModuleType("coderag")
    indexer_module = ModuleType("coderag.indexer")

    def chunk_file(text, language, config):
        return [SimpleNamespace(text=part, language=language) for part in text.split()]

    class Provider:
        def __init__(self):
            self.calls = []

        def embed_documents(self, texts):
            self.calls.append(tuple(texts))
            return list(texts)

    class Indexer:
        def __init__(self):
            self.provider = Provider()
            self.config = object()
            self.writes = []

        def _write(self, item, chunks, vectors):
            self.writes.append((item.text, tuple(chunk.text for chunk in chunks), tuple(vectors or ())))
            return len(chunks), 0

        def _embed_and_write(self, work, *, reporter):
            raise AssertionError("original implementation should be patched")

    indexer_module.chunk_file = chunk_file
    indexer_module.Indexer = Indexer
    coderag_module.indexer = indexer_module
    monkeypatch.setitem(sys.modules, "coderag", coderag_module)
    monkeypatch.setitem(sys.modules, "coderag.indexer", indexer_module)
    monkeypatch.setenv("CODALITH_CODERAG_BATCH_CHUNKS", "2")
    monkeypatch.setenv("CODALITH_CODERAG_BATCH_CONCURRENCY", "2")

    _configure_native_batch_embedding()

    reporter = SimpleNamespace(messages=[], update=lambda message: reporter.messages.append(message))
    indexer = Indexer()
    work = [SimpleNamespace(text=str(i), language="text") for i in range(4)]

    results = list(indexer._embed_and_write(work, reporter=reporter))

    assert len(results) == 4
    assert sorted(indexer.provider.calls) == [("0", "1"), ("2", "3")]
    assert sorted(indexer.writes) == [
        ("0", ("0",), ("0",)),
        ("1", ("1",), ("1",)),
        ("2", ("2",), ("2",)),
        ("3", ("3",), ("3",)),
    ]
    assert reporter.messages[-1] == "Embedding 4/4 file(s)..."


def test_configure_native_batch_embedding_splits_failed_batches(monkeypatch):
    coderag_module = ModuleType("coderag")
    indexer_module = ModuleType("coderag.indexer")

    def chunk_file(text, language, config):
        return [SimpleNamespace(text=part, language=language) for part in text.split()]

    class Provider:
        def __init__(self):
            self.calls = []

        def embed_documents(self, texts):
            self.calls.append(tuple(texts))
            if len(texts) > 2:
                raise TimeoutError("slow embedding batch")
            return [f"vec:{text}" for text in texts]

    class Indexer:
        def __init__(self):
            self.provider = Provider()
            self.config = object()
            self.writes = []

        def _write(self, item, chunks, vectors):
            self.writes.append((item.text, tuple(vectors or ())))
            return len(chunks), 0

        def _embed_and_write(self, work, *, reporter):
            raise AssertionError("original implementation should be patched")

    indexer_module.chunk_file = chunk_file
    indexer_module.Indexer = Indexer
    coderag_module.indexer = indexer_module
    monkeypatch.setitem(sys.modules, "coderag", coderag_module)
    monkeypatch.setitem(sys.modules, "coderag.indexer", indexer_module)
    monkeypatch.setenv("CODALITH_CODERAG_BATCH_CHUNKS", "4")

    _configure_native_batch_embedding()

    reporter = SimpleNamespace(messages=[], update=lambda message: reporter.messages.append(message))
    indexer = Indexer()
    work = [SimpleNamespace(text=str(i), language="text") for i in range(4)]

    results = list(indexer._embed_and_write(work, reporter=reporter))

    assert len(results) == 4
    assert indexer.provider.calls == [("0", "1", "2", "3"), ("0", "1"), ("2", "3")]
    assert indexer.writes == [
        ("0", ("vec:0",)),
        ("1", ("vec:1",)),
        ("2", ("vec:2",)),
        ("3", ("vec:3",)),
    ]


def test_configure_native_openai_timeout_patches_provider(monkeypatch):
    coderag_module = ModuleType("coderag")
    embeddings_module = ModuleType("coderag.embeddings")
    provider_module = ModuleType("coderag.embeddings.openai_provider")

    class OpenAIEmbeddingProvider:
        def _embed_batch(self, inputs):
            raise AssertionError("original implementation should be patched")

    class Array:
        def __init__(self, data):
            self.data = data
            self.shape = (len(data), len(data[0]) if data else 0)

    def retry(**kwargs):
        assert kwargs["reraise"] is True

        def decorate(fn):
            return fn

        return decorate

    provider_module.OpenAIEmbeddingProvider = OpenAIEmbeddingProvider
    provider_module.np = SimpleNamespace(array=lambda data, dtype: Array(data))
    provider_module.retry = retry
    provider_module.stop_after_attempt = lambda attempts: ("attempts", attempts)
    provider_module.wait_exponential = lambda **kwargs: ("wait", kwargs)
    embeddings_module.openai_provider = provider_module
    coderag_module.embeddings = embeddings_module
    monkeypatch.setitem(sys.modules, "coderag", coderag_module)
    monkeypatch.setitem(sys.modules, "coderag.embeddings", embeddings_module)
    monkeypatch.setitem(sys.modules, "coderag.embeddings.openai_provider", provider_module)
    monkeypatch.setenv("CODALITH_CODERAG_OPENAI_TIMEOUT_SECONDS", "180")
    monkeypatch.setenv("CODALITH_CODERAG_OPENAI_RETRY_ATTEMPTS", "2")

    _configure_native_openai_timeout()

    calls = []

    class Embeddings:
        def create(self, *, model, input, timeout):
            calls.append((model, list(input), timeout))
            return SimpleNamespace(data=[SimpleNamespace(embedding=[1.0, 2.0]) for _ in input])

    provider = OpenAIEmbeddingProvider()
    provider._model = "Qwen3-Embedding-8B"
    provider._client = SimpleNamespace(embeddings=Embeddings())

    vectors = provider._embed_batch(["chunk"])

    assert calls == [("Qwen3-Embedding-8B", ["chunk"], 180.0)]
    assert vectors.shape == (1, 2)


def test_codalith_read_source_adds_line_numbers_and_audit(tools, tmp_path):
    result = tools.codalith_read_source(
        uri="codalith://ue-5.7.4/source/Engine/Source/Runtime/Engine/Classes/GameFramework/Actor.h#L1-L4"
    )
    assert result["content"].startswith("1|")
    expected_source = (
        '#include "Actor.generated.h"\n'
        "UCLASS()\n"
        "class ENGINE_API AActor : public UObject {\n"
        "GENERATED_BODY()"
    )
    expected_hash = hashlib.sha256(expected_source.encode("utf-8")).hexdigest()
    assert result["source_hash"] == expected_hash
    audit_path = tools.runtime.audit.path
    assert audit_path.exists()
    audit = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert audit["decision"] == "allowed"
    assert audit["source_hash"] == expected_hash
    assert audit["user_id"] == "test-user"


def test_codalith_read_source_requires_corpus_scope(tools):
    tools.runtime.identity = AuthContext(
        user_id="limited",
        session_id="limited-session",
        client="pytest",
        scopes=frozenset({"source:read"}),
    )

    try:
        tools.codalith_read_source(
            uri="codalith://ue-5.7.4/source/Engine/Source/Runtime/Engine/Classes/GameFramework/Actor.h#L1-L4"
        )
    except AuthError as exc:
        assert "ue-5.7.4" in str(exc)
    else:
        raise AssertionError("read_source should require the engine corpus scope")


def test_codalith_context_returns_context_pack(tools):
    pack = tools.codalith_context(query="UPROPERTY ReplicatedUsing OnRep", version="5.7.4")
    assert pack["schema_version"] == "0.2"
    assert pack["version"] == "5.7.4"
    assert pack["corpus_id"] == "ue-5.7.4"
    assert pack["source_spans"]
    assert pack["graph_edges"]
    assert any(span["path"].endswith("Actor.h") for span in pack["source_spans"])
    assert all(
        span["corpus_kind"] == "engine"
        for span in pack["source_spans"]
        if span["source"] != "card-evidence"
    )


def test_ue_graph_returns_semantic_edges(tools):
    graph = tools.codalith_graph(node="AActor", version="5.7.4", depth=2)

    assert any(edge["edge_type"] == "owns_reflection" for edge in graph["edges"])
    assert any(edge["edge_type"] == "replicated_using" for edge in graph["edges"])


def test_ue_lookup_symbol_includes_graph_and_examples(tools):
    result = tools.codalith_lookup_symbol(symbol="AActor", version="5.7.4")

    assert result["context"]["source_spans"]
    assert result["graph"]["edges"]
    assert result["examples"]


def test_project_overlay_context_and_source_read(tools):
    pack = tools.codalith_context(
        query="InventoryComponent OnRep_Items",
        version="5.7.4",
        project="ProjectA",
    )

    assert pack["project"] == "ProjectA"
    assert any(str(span["uri"]).startswith("codalith://ProjectA/") for span in pack["source_spans"])

    result = tools.codalith_read_source(
        uri="codalith://ProjectA/source/Source/ProjectA/Public/InventoryComponent.h#L1-L8"
    )

    assert result["corpus_id"] == "ProjectA"
    assert "UInventoryComponent" in result["content"]


def test_compare_versions_compiles_both_context_packs(tools):
    result = tools.codalith_compare_versions(
        target="AActor",
        from_version="5.7.4",
        to_version="5.7.5",
    )

    assert result["from"]["version"] == "5.7.4"
    assert result["to"]["version"] == "5.7.5"
    assert result["from"]["source_spans"]
    assert result["to"]["source_spans"]


def test_ue_index_status_reports_semantic_store(tools):
    status = tools.codalith_index_status(version="5.7.4")

    assert status["semantic"]["engine"]["graph_edges"] > 0
    assert status["semantic"]["engine"]["cpp_symbols"] > 0
    assert status["semantic"]["engine"]["compile_guards"] > 0


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
                "arguments": {"query": "AActor BeginPlay", "version": "5.7.4"},
            },
        },
        tools,
    )
    assert called is not None
    assert called["result"]["structuredContent"]["source_spans"]


def test_mcp_initialize_instructions_come_from_registry(tools):
    initialized = handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, tools)
    assert initialized is not None
    instructions = initialized["result"]["instructions"]
    # Corpus identity and trigger keywords are advertised from configuration,
    # not hardcoded in the gateway.
    assert "Unreal Engine 5.7.4 (Unreal Engine full source tree)" in instructions
    assert "UHT" in instructions
    assert "ProjectA" in instructions
    assert "codalith_context" in instructions


def test_mcp_tool_schema_version_default_follows_registry_default(tools):
    listed = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, tools)
    assert listed is not None
    schemas = {item["name"]: item for item in listed["result"]["tools"]}
    for name in ("codalith_context", "codalith_lookup_symbol", "codalith_graph", "codalith_examples"):
        version_property = schemas[name]["inputSchema"]["properties"]["version"]
        assert version_property["default"] == "5.7.4"
    descriptions = " ".join(str(schema["description"]) for schema in schemas.values())
    assert "Unreal" not in descriptions and "UE" not in descriptions


def test_codalith_context_defaults_to_registry_default_engine(tools):
    pack = tools.codalith_context(query="AActor BeginPlay")
    assert pack["version"] == "5.7.4"
    assert pack["source_spans"]


def test_mcp_resources_list_templates_and_read(tools):
    listed = handle_request({"jsonrpc": "2.0", "id": 1, "method": "resources/list"}, tools)
    assert listed is not None
    assert any(item["uri"] == "codalith://ue-5.7.4/modules" for item in listed["result"]["resources"])

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
            "params": {"uri": "codalith://ue-5.7.4"},
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
    module = _read_resource_via_rpc(tools, "codalith://ue-5.7.4/module/Engine")
    assert module["kind"] == "module"
    assert module["module"]["module_name"] == "Engine"
    assert module["dependencies"]

    symbol = _read_resource_via_rpc(tools, "codalith://ue-5.7.4/symbol/AActor")
    assert symbol["kind"] == "symbol"
    assert any(match["name"] == "AActor" for match in symbol["matches"])

    source = _read_resource_via_rpc(
        tools,
        "codalith://ue-5.7.4/source/Engine/Source/Runtime/Engine/Classes/GameFramework/Actor.h#L1-L4",
    )
    assert source["corpus_id"] == "ue-5.7.4"
    assert source["content"]

    card_root = tools.runtime.registry.engines["ue-5.7.4"].card_root
    card_file = card_root / "KNOWLEDGE" / "Module" / "module-core.md"
    card_file.parent.mkdir(parents=True, exist_ok=True)
    card_file.write_text("# Core Module\n", encoding="utf-8")
    card = _read_resource_via_rpc(tools, "codalith://ue-5.7.4/card/module/module-core")
    assert card["kind"] == "card"
    assert card["markdown"].startswith("# Core Module")


def test_mcp_resource_read_rejects_unknown_and_traversal_uris(tools):
    for uri in (
        "codalith://ue-5.7.4/module/DoesNotExist",
        "codalith://ue-9.9.9",
        "codalith://ue-5.7.4/card/../escape",
        "codalith://ue-5.7.4/card/module/../../escape",
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
        call_tool(tools, "codalith_context", {"query": "AActor", "bogus": True})
    with pytest.raises(ValueError, match="diff_type"):
        call_tool(
            tools,
            "codalith_compare_versions",
            {
                "target": "AActor",
                "from_version": "5.7.4",
                "to_version": "5.7.5",
                "diff_type": "summary",
            },
        )


class _CountingAdapter:
    def __init__(self, inner) -> None:
        self._inner = inner
        self.get_file_calls = 0

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def get_file(self, *args, **kwargs):
        self.get_file_calls += 1
        return self._inner.get_file(*args, **kwargs)


def test_read_source_rate_limit_precedes_file_read(tools):
    counting = _CountingAdapter(tools.runtime.adapter)
    tools.runtime.adapter = counting
    tools.runtime.rate_limiter = SourceReadRateLimiter(
        SourcePolicy(max_source_reads_per_10min=1),
        time_func=lambda: 100.0,
    )
    uri = "codalith://ue-5.7.4/source/Engine/Source/Runtime/Engine/Classes/GameFramework/Actor.h#L1-L4"

    tools.codalith_read_source(uri=uri)
    assert counting.get_file_calls == 1

    with pytest.raises(SourcePolicyError):
        tools.codalith_read_source(uri=uri)
    assert counting.get_file_calls == 1


def test_read_source_fills_default_window_and_validates_ranges(tools):
    result = tools.codalith_read_source(
        uri="codalith://ue-5.7.4/source/Engine/Source/Runtime/Engine/Classes/GameFramework/Actor.h"
    )
    assert result["start_line"] == 1
    assert result["end_line"] == tools.runtime.policy.default_max_lines

    with pytest.raises(SourcePolicyError):
        tools.codalith_read_source(
            uri="codalith://ue-5.7.4/source/Engine/Source/Runtime/Engine/Classes/GameFramework/Actor.h",
            start_line=0,
            end_line=4,
        )
    with pytest.raises(SourcePolicyError):
        tools.codalith_read_source(
            uri="codalith://ue-5.7.4/source/Engine/Source/Runtime/Engine/Classes/GameFramework/Actor.h",
            start_line=5,
            end_line=2,
        )


def test_lookup_symbol_omits_graph_without_scope(tools):
    tools.runtime.identity = AuthContext(
        user_id="test-user",
        session_id="test-session",
        client="pytest",
        scopes=frozenset({"source:read", "ue:5.7", "project:ProjectA"}),
    )

    result = tools.codalith_lookup_symbol(symbol="AActor", version="5.7.4")

    assert "graph" not in result
    assert any("graph:read" in warning for warning in result["warnings"])
