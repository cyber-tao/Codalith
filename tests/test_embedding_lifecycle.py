from __future__ import annotations

import json
import queue
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import codalith.indexing.shared as shared_module
import codalith.semantic.engine as engine_module
from codalith.indexing.shared import SharedBuildCancelled
from codalith.semantic.config import Config
from codalith.semantic.embeddings.fake_provider import FakeEmbeddingProvider
from codalith.semantic.embeddings.openai_provider import OpenAIEmbeddingProvider
from codalith.semantic.engine import SemanticEngine
from codalith.semantic.store import SemanticStoreError
from test_shared_build_efficiency import Source, _environment


@pytest.fixture
def local_embedding_endpoint():
    requests = []
    closed_connections = queue.Queue()
    vectors = FakeEmbeddingProvider()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body))
            embeddings = vectors.embed_documents(body["input"])
            payload = json.dumps({
                "object": "list",
                "model": body["model"],
                "data": [
                    {"object": "embedding", "index": index, "embedding": vector.tolist()}
                    for index, vector in enumerate(embeddings)
                ],
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

        def finish(self):
            try:
                super().finish()
            finally:
                closed_connections.put(self.client_address)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests, closed_connections
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_shared_build_closes_real_http_embedding_connection(
    tmp_path, monkeypatch, local_embedding_endpoint,
):
    endpoint, requests, closed_connections = local_embedding_endpoint
    monkeypatch.setenv("CODALITH_EMBEDDING_BASE_URL", endpoint)
    monkeypatch.delenv("CODALITH_EMBEDDING_API_KEY", raising=False)
    closed_clients = []
    original = OpenAIEmbeddingProvider.close

    def close(provider):
        client = provider.__dict__.get("_client")
        original(provider)
        if client is not None:
            closed_clients.append(client)

    monkeypatch.setattr(OpenAIEmbeddingProvider, "close", close)
    pool, corpus, _ = _environment(tmp_path)
    result = pool.build_git_snapshot(
        Source({"source.py": b"def needle():\n    return 1\n"}), "a" * 40, "version",
        replace(corpus, embedding_provider="openai", embedding_model="loopback-test"),
    )
    assert result.new_vectors > 0
    assert len(requests) >= 2
    assert all(path == "/v1/embeddings" for path, _ in requests)
    assert len(closed_clients) == 1
    assert closed_clients[0].is_closed()
    assert closed_connections.get(timeout=2)[0] == "127.0.0.1"


def test_semantic_engine_query_closes_real_http_embedding_connection(tmp_path, local_embedding_endpoint):
    endpoint, requests, closed_connections = local_embedding_endpoint
    source = tmp_path / "source"
    source.mkdir()
    (source / "source.py").write_text("def needle():\n    return 1\n", encoding="utf-8")
    config = Config(
        store_dir=tmp_path / "store", provider="openai", model="loopback-test",
        openai_base_url=endpoint,
    )
    built = SemanticEngine.create(config)
    try:
        built.index(source)
    finally:
        built.close()
    closed_connections.get(timeout=2)
    query_engine = SemanticEngine.open(config)
    client = query_engine.provider._client
    try:
        assert not client.is_closed()
        assert query_engine.search("needle", top_k=1)
        assert any(body["input"] == ["needle"] for _, body in requests)
    finally:
        query_engine.close()
    assert client.is_closed()
    assert closed_connections.get(timeout=2)[0] == "127.0.0.1"


class TrackedProvider(FakeEmbeddingProvider):
    def __init__(self):
        super().__init__()
        self.closes = 0

    def close(self):
        self.closes += 1


@pytest.mark.parametrize("stage", ["source", "structure", "chunk", "embedding", "index", "publish"])
@pytest.mark.parametrize("cancel", [False, True])
def test_shared_build_releases_provider_on_every_failure_stage(tmp_path, monkeypatch, stage, cancel):
    pool, corpus, _ = _environment(tmp_path)
    provider = TrackedProvider()
    monkeypatch.setattr(shared_module, "get_provider", lambda config: provider)
    stopped = False

    def progress(event):
        nonlocal stopped
        if event["stage"] == stage:
            if cancel:
                stopped = True
            else:
                raise RuntimeError("injected build failure")

    with pytest.raises(SharedBuildCancelled if cancel else RuntimeError):
        pool.build_git_snapshot(
            Source({"source.py": b"def sample():\n    return 1\n"}), "a" * 40, "version", corpus,
            progress=progress, cancelled=lambda: stopped,
        )
    assert provider.closes == 1
    assert pool.snapshot("version") is None


def test_shared_build_releases_provider_for_success_and_alias(tmp_path, monkeypatch):
    pool, corpus, _ = _environment(tmp_path)
    providers = []

    def create(config):
        provider = TrackedProvider()
        providers.append(provider)
        return provider

    monkeypatch.setattr(shared_module, "get_provider", create)
    source = Source({"source.py": b"def sample():\n    return 1\n"})
    first = pool.build_git_snapshot(source, "a" * 40, "first", corpus)
    alias = pool.build_git_snapshot(source, "a" * 40, "alias", corpus)
    pool.build_git_snapshot(source, "a" * 40, "alias", corpus)
    assert alias.snapshot_id == first.snapshot_id
    assert len(providers) == 2
    assert [provider.closes for provider in providers] == [1, 1]


def test_shared_dimension_failure_releases_provider(tmp_path, monkeypatch):
    pool, corpus, _ = _environment(tmp_path)

    class BrokenDimension(TrackedProvider):
        @property
        def dim(self):
            raise RuntimeError("dimension unavailable")

    provider = BrokenDimension()
    monkeypatch.setattr(shared_module, "get_provider", lambda config: provider)
    with pytest.raises(RuntimeError, match="dimension unavailable"):
        pool.build_git_snapshot(
            Source({"source.py": b"def sample():\n    return 1\n"}), "a" * 40, "first", corpus,
        )
    assert provider.closes == 1


def test_semantic_engine_owns_provider_until_closed(tmp_path, monkeypatch):
    providers = []

    def create(config):
        provider = TrackedProvider()
        providers.append(provider)
        return provider

    monkeypatch.setattr(engine_module, "get_provider", create)
    config = Config(store_dir=tmp_path / "store", provider="fake")
    built = SemanticEngine.create(config)
    assert providers[-1].closes == 0
    built.close()
    built.close()
    assert providers[-1].closes == 1
    opened = SemanticEngine.open(config)
    assert providers[-1].closes == 0
    opened.close()
    assert providers[-1].closes == 1
    with pytest.raises(SemanticStoreError):
        SemanticEngine.create(config)
    assert providers[-1].closes == 1
    with pytest.raises(SemanticStoreError):
        SemanticEngine.open(Config(store_dir=tmp_path / "missing", provider="fake"))
    assert providers[-1].closes == 1


def test_semantic_engine_initialization_failure_closes_store_and_provider(tmp_path, monkeypatch):
    provider = TrackedProvider()
    closed_stores = []
    original = engine_module.LanceStore.close

    def close(store):
        closed_stores.append(store)
        original(store)

    def fail(*args):
        raise RuntimeError("searcher initialization failed")

    monkeypatch.setattr(engine_module, "get_provider", lambda config: provider)
    monkeypatch.setattr(engine_module.LanceStore, "close", close)
    monkeypatch.setattr(engine_module, "HybridSearcher", fail)
    with pytest.raises(RuntimeError, match="initialization failed"):
        SemanticEngine.create(Config(store_dir=tmp_path / "store", provider="fake"))
    assert provider.closes == 1
    assert len(closed_stores) == 1


def test_semantic_engine_store_close_failure_still_closes_provider(tmp_path, monkeypatch):
    provider = TrackedProvider()
    monkeypatch.setattr(engine_module, "get_provider", lambda config: provider)
    engine = SemanticEngine.create(Config(store_dir=tmp_path / "store", provider="fake"))
    original = engine.store.close

    def fail():
        original()
        raise RuntimeError("store close failed")

    monkeypatch.setattr(engine.store, "close", fail)
    with pytest.raises(RuntimeError, match="store close failed"):
        engine.close()
    assert provider.closes == 1
