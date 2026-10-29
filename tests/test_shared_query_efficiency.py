from __future__ import annotations

import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from functools import cached_property
from types import SimpleNamespace

import numpy as np
import pytest

from codalith.corpus.generations import GenerationRepository
from codalith.errors import RetrievalError
from codalith.indexing.shared import SharedPool, SharedStructureIndex, _SharedSemantic
from codalith.semantic.config import Config
from codalith.semantic.embeddings.fake_provider import FakeEmbeddingProvider
from codalith.semantic.embeddings.fastembed_provider import FastEmbedProvider
from codalith.semantic.embeddings.openai_provider import OpenAIEmbeddingProvider
from codalith.semantic.shared import SharedVectors


def test_shared_structure_does_not_pin_wal_checkpoint(environment_factory, tmp_path):
    environment = environment_factory(semantic=False)
    pool = SharedPool(tmp_path / "pool", environment.policy)
    generation = GenerationRepository(environment.policy).active(environment.corpus)
    with pool._connect() as connection:
        pool._import_facts(connection, generation.structure_path, "old")
    old = SharedStructureIndex(pool, {"snapshot_id": "old"}, environment.corpus)
    try:
        assert not old._connection.in_transaction
        original = old.get_file("src/core/cache.py")
        assert original is not None
        with pool._connect() as connection:
            for index in range(20):
                connection.execute(
                    "INSERT INTO extraction_cache VALUES(?,?)", (str(index), "x" * 4096)
                )
                connection.commit()
            connection.execute(
                "INSERT INTO facts_files VALUES(?,?,?,?,?,?,?)",
                ("new-file", "new.py", "python", "0" * 64, 10, 1, None),
            )
            connection.execute("INSERT INTO members_files VALUES(?,?)", ("new", "new-file"))
            connection.commit()
            busy, frames, checkpointed = connection.execute(
                "PRAGMA wal_checkpoint(PASSIVE)"
            ).fetchone()
        assert busy == 0
        assert frames > 0
        assert checkpointed == frames
        assert not old._connection.in_transaction
        assert old.get_file("src/core/cache.py") == original
        assert old.get_file("new.py") is None
        new = SharedStructureIndex(pool, {"snapshot_id": "new"}, environment.corpus)
        try:
            assert new.get_file("new.py") is not None
            assert new.get_file("src/core/cache.py") is None
        finally:
            new.close()
    finally:
        old.close()


def test_shared_structure_initialization_failure_closes_connection(
    environment_factory, tmp_path, monkeypatch
):
    environment = environment_factory(semantic=False)
    pool = SharedPool(tmp_path / "pool", environment.policy)
    opened = []
    connect = sqlite3.connect

    def tracked_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", tracked_connect)
    with pytest.raises(sqlite3.OperationalError):
        SharedStructureIndex(pool, {"snapshot_id": "missing"}, environment.corpus)
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")


def test_shared_point_lookups_use_indexes_with_multiple_large_snapshots(
    environment_factory, tmp_path
):
    environment = environment_factory(semantic=False)
    pool = SharedPool(tmp_path / "pool", environment.policy)
    generation = GenerationRepository(environment.policy).active(environment.corpus)
    file_count = 50_000
    path = f"src/file{file_count - 1:05d}.py"
    with pool._connect() as connection:
        pool._import_facts(connection, generation.structure_path, "old")
        connection.executemany(
            "INSERT INTO facts_files VALUES(?,?,?,?,?,?,?)",
            (
                (str(index), f"src/file{index:05d}.py", "python", "0" * 64, 10, 1, None)
                for index in range(file_count)
            ),
        )
        connection.executemany(
            "INSERT INTO members_files VALUES(?,?)",
            ((snapshot, str(index)) for snapshot in ("old", "new") for index in range(file_count)),
        )
        connection.execute(
            "INSERT INTO facts_files VALUES(?,?,?,?,?,?,?)",
            ("replacement", path, "python", "1" * 64, 20, 2, None),
        )
        connection.execute(
            "DELETE FROM members_files WHERE snapshot_id=? AND fact_id=?",
            ("new", str(file_count - 1)),
        )
        connection.execute("INSERT INTO members_files VALUES(?,?)", ("new", "replacement"))
        connection.executemany(
            "INSERT INTO facts_symbols VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                (
                    str(number), f"symbol{number:05d}", f"key{number}", b"0" * 16,
                    f"Module.Symbol{number:05d}", f"Symbol{number:05d}", "function", "old()",
                    f"src/file{number:05d}.py", 1, 1, None, None, "{}",
                )
                for number in range(file_count)
            ),
        )
        connection.executemany(
            "INSERT INTO members_symbols VALUES(?,?)",
            ((snapshot, str(number)) for snapshot in ("old", "new") for number in range(file_count)),
        )
        connection.execute(
            "INSERT INTO facts_symbols VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "replacement", "symbol49999", "key49999", b"1" * 16,
                "Module.Symbol49999", "Symbol49999", "function", "new()", path,
                1, 2, None, None, "{}",
            ),
        )
        connection.execute(
            "DELETE FROM members_symbols WHERE snapshot_id=? AND fact_id=?", ("new", "49999")
        )
        connection.execute("INSERT INTO members_symbols VALUES(?,?)", ("new", "replacement"))
        connection.executemany(
            "INSERT INTO facts_references VALUES(?,?,?,?,?,?,?,?,?)",
            (
                (
                    str(number), number, f"symbol{number:05d}",
                    f"Symbol{(number + 1) % file_count:05d}",
                    f"symbol{(number + 1) % file_count:05d}", "resolved", "call",
                    f"src/file{number:05d}.py", 1,
                )
                for number in range(file_count)
            ),
        )
        connection.executemany(
            "INSERT INTO members_references VALUES(?,?)",
            ((snapshot, str(number)) for snapshot in ("old", "new") for number in range(file_count)),
        )
        connection.executemany(
            "INSERT INTO facts_references VALUES(?,?,?,?,?,?,?,?,?)",
            (
                (
                    "new-outgoing", file_count, "symbol49999", "Symbol00002", "symbol00002",
                    "resolved", "call", path, 2,
                ),
                (
                    "new-incoming", file_count + 1, "symbol00003", "Symbol49999", "symbol49999",
                    "resolved", "call", "src/file00003.py", 2,
                ),
            ),
        )
        connection.executemany(
            "DELETE FROM members_references WHERE snapshot_id=? AND fact_id=?",
            (("new", "49998"), ("new", "49999")),
        )
        connection.executemany(
            "INSERT INTO members_references VALUES(?,?)",
            (("new", "new-outgoing"), ("new", "new-incoming")),
        )
        pool._analyze(connection)
    for snapshot, expected_hash in (("old", "0" * 64), ("new", "1" * 64)):
        index = SharedStructureIndex(pool, {"snapshot_id": snapshot}, environment.corpus)
        instructions = 0

        def progress():
            nonlocal instructions
            instructions += 1
            return 0

        try:
            plan = " ".join(
                str(row[3])
                for row in index._connection.execute(
                    "EXPLAIN QUERY PLAN SELECT * FROM files WHERE path=?", (path,)
                )
            )
            assert "facts_files_path_binary" in plan
            index._connection.set_progress_handler(progress, 1)
            record = index.get_file(path)
            assert record is not None and record.sha256 == expected_hash
            assert index.get_file(path.upper()) is None
            assert instructions < 1_000
            index._connection.set_progress_handler(None, 0)

            def indexed_lookup(operation, *expected_indexes, index=index):
                nonlocal instructions
                statements = []
                instructions = 0
                index._connection.set_trace_callback(statements.append)
                index._connection.set_progress_handler(progress, 1)
                try:
                    result = operation()
                finally:
                    index._connection.set_trace_callback(None)
                    index._connection.set_progress_handler(None, 0)
                assert instructions < 1_000
                plans = " ".join(
                    str(row[3])
                    for sql in statements if sql.startswith("SELECT")
                    for row in index._connection.execute("EXPLAIN QUERY PLAN " + sql)
                )
                for expected_index in expected_indexes:
                    assert expected_index in plans
                return result

            symbol = indexed_lookup(
                lambda index=index: index.get_symbol("symbol49999"), "facts_symbols_symbol_id"
            )
            assert symbol is not None and symbol.signature == f"{snapshot}()"
            symbols = indexed_lookup(
                lambda index=index: index.lookup_symbols("Symbol49999", exact=True),
                "facts_symbols_name", "facts_symbols_qualified_name",
            )
            assert [(item.symbol_id, item.signature) for item in symbols] == [
                ("symbol49999", f"{snapshot}()")
            ]
            outgoing = indexed_lookup(
                lambda index=index: index.references("symbol49999", direction="outgoing"),
                "facts_references_source_symbol_id",
            )
            assert [(item.source_symbol_id, item.target_symbol_id) for item in outgoing] == [
                ("symbol49999", "symbol00000" if snapshot == "old" else "symbol00002")
            ]
            incoming = indexed_lookup(
                lambda index=index: index.references("symbol49999", direction="incoming"),
                "facts_references_target_symbol_id",
            )
            assert [(item.source_symbol_id, item.target_symbol_id) for item in incoming] == [
                ("symbol49998" if snapshot == "old" else "symbol00003", "symbol49999")
            ]
        finally:
            index._connection.set_progress_handler(None, 0)
            index.close()


@pytest.fixture
def shared_semantic(environment_factory, tmp_path, monkeypatch):
    environment = environment_factory(semantic=False)
    pool = SharedPool(tmp_path / "pool", environment.policy)
    corpus = replace(
        environment.corpus,
        embedding_provider="openai",
        embedding_model="test-custom-embedding",
        semantic_store=tmp_path / "vectors",
    )
    original = GenerationRepository(environment.policy).active(environment.corpus)
    generation = replace(
        original,
        semantic_path=corpus.semantic_store,
        manifest=replace(
            original.manifest,
            generation_id="old",
            embedding_provider=corpus.embedding_provider,
            embedding_model=corpus.embedding_model,
            embedding_dimension=3,
        ),
    )
    current = {
        "snapshot_id": "old",
        "dimension": 3,
        "table_version": 1,
        "membership_column": "member_old",
    }
    providers = []
    snapshots = []
    started = threading.Event()
    release = threading.Event()
    embedding_barrier = []

    class Provider(OpenAIEmbeddingProvider):
        def __init__(self):
            super().__init__(corpus.embedding_model)
            self.inputs = []
            self.closed = 0
            self.initializations = 0

        @cached_property
        def _client(self):
            self.initializations += 1
            return SimpleNamespace(close=lambda: None)

        def _embed_batch(self, inputs):
            if embedding_barrier and inputs != ["probe"]:
                embedding_barrier[0].wait(timeout=5)
            self.inputs.extend(inputs)
            return np.ones((len(inputs), 3), dtype="float32")

        def close(self):
            self.closed += 1
            super().close()

    class Snapshot:
        def __init__(self, version, column):
            self.version = version
            self.column = column
            self.closed = 0

        def search(self, query, **kwargs):
            if query == "blocked":
                started.set()
                assert release.wait(5)
            assert self.closed == 0
            return []

        def close(self):
            self.closed += 1

    class Vectors:
        def __init__(self, *args, **kwargs):
            pass

        def open_snapshot(self, version, column):
            snapshot = Snapshot(version, column)
            snapshots.append(snapshot)
            return snapshot

    def provider_factory(config):
        provider = Provider()
        providers.append(provider)
        return provider

    monkeypatch.setattr(pool, "snapshot", lambda target: dict(current))
    monkeypatch.setattr("codalith.indexing.shared.get_provider", provider_factory)
    monkeypatch.setattr("codalith.indexing.shared.SharedVectors", Vectors)
    backend = _SharedSemantic(pool, environment.policy)
    state = SimpleNamespace(
        backend=backend,
        corpus=corpus,
        generation=generation,
        current=current,
        providers=providers,
        snapshots=snapshots,
        started=started,
        release=release,
        embedding_barrier=embedding_barrier,
    )
    try:
        yield state
    finally:
        release.set()
        backend.close()


def test_shared_semantic_reuses_provider_probe_and_snapshot(shared_semantic):
    state = shared_semantic
    for query in ("first", "second", "third"):
        assert state.backend.search(state.corpus, state.generation, query, limit=5) == []
    assert len(state.providers) == len(state.snapshots) == 1
    assert state.providers[0].inputs == ["probe", "first", "second", "third"]
    assert state.providers[0].initializations == 1
    state.backend.close()
    state.backend.close()
    assert state.providers[0].closed == state.snapshots[0].closed == 1
    with pytest.raises(RetrievalError, match="closed"):
        state.backend.search(state.corpus, state.generation, "closed", limit=5)


def test_shared_semantic_concurrent_first_queries_initialize_once(shared_semantic):
    state = shared_semantic
    barrier = threading.Barrier(8)

    def search(index):
        barrier.wait(timeout=5)
        return state.backend.search(state.corpus, state.generation, f"query {index}", limit=5)

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert list(executor.map(search, range(8))) == [[]] * 8
    assert len(state.providers) == len(state.snapshots) == 1
    assert state.providers[0].inputs.count("probe") == 1
    assert len(state.providers[0].inputs) == 9
    assert state.providers[0].initializations == 1


def test_shared_remote_embeddings_can_run_concurrently(shared_semantic):
    state = shared_semantic
    state.embedding_barrier.append(threading.Barrier(2))

    def search(index):
        return state.backend.search(state.corpus, state.generation, f"query {index}", limit=5)

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(search, range(2))) == [[], []]
    assert len(state.providers) == 1
    assert state.providers[0].initializations == 1
    assert state.providers[0].inputs.count("probe") == 1


def test_shared_local_model_initializes_once_before_parallel_embeddings(
    shared_semantic, monkeypatch
):
    state = shared_semantic
    barrier = threading.Barrier(2)
    initialized = []
    model_name = "test-local-model"

    class Model:
        def __init__(self, model, **kwargs):
            initialized.append(model)

        @staticmethod
        def list_supported_models():
            return [{"model": model_name, "dim": 3}]

        def query_embed(self, texts):
            barrier.wait(timeout=5)
            return [np.ones(3, dtype="float32") for text in texts]

    monkeypatch.setitem(sys.modules, "fastembed", SimpleNamespace(TextEmbedding=Model))
    provider = FastEmbedProvider(model_name, device="cpu")
    monkeypatch.setattr("codalith.indexing.shared.get_provider", lambda config: provider)
    corpus = replace(state.corpus, embedding_provider="fastembed", embedding_model=model_name)
    generation = replace(
        state.generation,
        manifest=replace(
            state.generation.manifest, embedding_provider="fastembed", embedding_model=model_name
        ),
    )

    def search(index):
        return state.backend.search(corpus, generation, f"query {index}", limit=5)

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(search, range(2))) == [[], []]
    assert initialized == [model_name]
    state.backend.close()
    assert "_model" not in provider.__dict__


def test_shared_semantic_generation_switch_keeps_active_snapshot(shared_semantic):
    state = shared_semantic
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(
            state.backend.search, state.corpus, state.generation, "blocked", limit=5
        )
        assert state.started.wait(5)
        try:
            state.current.update(
                snapshot_id="new", table_version=2, membership_column="member_new"
            )
            new_generation = replace(
                state.generation, manifest=replace(state.generation.manifest, generation_id="new")
            )
            assert state.backend.search(state.corpus, new_generation, "new query", limit=5) == []
            assert [(item.version, item.column) for item in state.snapshots] == [
                (1, "member_old"),
                (2, "member_new"),
            ]
            assert state.providers[0].closed == state.snapshots[0].closed == 0
        finally:
            state.release.set()
        assert pending.result(timeout=5) == []
    assert state.providers[0].closed == state.snapshots[0].closed == 1
    assert state.providers[1].closed == state.snapshots[1].closed == 0
    state.backend.close()
    assert state.providers[1].closed == state.snapshots[1].closed == 1


def test_shared_semantic_close_defers_until_active_query_finishes(shared_semantic):
    state = shared_semantic
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(
            state.backend.search, state.corpus, state.generation, "blocked", limit=5
        )
        assert state.started.wait(5)
        try:
            state.backend.close()
            assert state.providers[0].closed == state.snapshots[0].closed == 0
            with pytest.raises(RetrievalError, match="closed"):
                state.backend.search(state.corpus, state.generation, "new query", limit=5)
        finally:
            state.release.set()
        assert pending.result(timeout=5) == []
    assert state.providers[0].closed == state.snapshots[0].closed == 1


def test_shared_semantic_initialization_failure_closes_provider(shared_semantic):
    state = shared_semantic
    state.current["dimension"] = 4
    with pytest.raises(RetrievalError, match="provenance mismatch"):
        state.backend.search(state.corpus, state.generation, "query", limit=5)
    assert state.providers[0].closed == 1
    assert state.snapshots == []
    state.current["dimension"] = 3
    assert state.backend.search(state.corpus, state.generation, "retry", limit=5) == []
    assert len(state.providers) == 2


def test_shared_vector_snapshot_remains_pinned_after_publication(tmp_path):
    provider = FakeEmbeddingProvider()
    vectors = SharedVectors(tmp_path / "vectors", provider.dim)

    def append(chunk_id, text):
        vectors.append(
            [{
                "id": chunk_id,
                "input_hash": str(chunk_id),
                "text": text,
                "vector": provider.embed_query(text).tolist(),
            }]
        )

    append(1, "old implementation")
    version, column, _ = vectors.publish("old", {1}, lambda: None)
    old = vectors.open_snapshot(version, column)
    append(2, "new implementation")
    new_version, new_column, _ = vectors.publish("new", {2}, lambda: None)
    new = vectors.open_snapshot(new_version, new_column)
    options = {
        "vector": provider.embed_query("implementation"),
        "config": Config(store_dir=vectors.root),
        "limit": 10,
    }
    try:
        assert {row[0] for row in old.search("implementation", **options)} == {1}
        assert {row[0] for row in new.search("implementation", **options)} == {2}
        assert old._table.version == version
        assert new._table.version == new_version
    finally:
        old.close()
        new.close()
    with pytest.raises(ValueError, match="closed"):
        old.search("implementation", **options)


def test_provider_close_releases_only_initialized_resources(monkeypatch):
    provider = OpenAIEmbeddingProvider("test-custom-embedding")
    provider.close()
    closed = []
    provider.__dict__["_client"] = SimpleNamespace(close=lambda: closed.append(True))
    provider.close()
    provider.close()
    assert closed == [True]

    monkeypatch.setattr(FastEmbedProvider, "_lookup_dim", staticmethod(lambda model: 3))
    local = FastEmbedProvider("test-local-model")
    local.close()
    local.__dict__["_model"] = object()
    local.close()
    assert "_model" not in local.__dict__
