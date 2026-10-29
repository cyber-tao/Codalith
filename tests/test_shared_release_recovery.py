from __future__ import annotations

import hashlib

import lance
import pytest
from lance.dataset import Tags

from codalith.indexing.shared import SharedBuildCancelled, SharedPool
from codalith.semantic.embeddings.fake_provider import FakeEmbeddingProvider
from codalith.semantic.shared import SharedVectors
from test_shared_pool import repository


@pytest.mark.parametrize("failure_point", ["before_tag", "after_tag", "before_catalog"])
def test_retry_reuses_the_retained_release(
    environment_factory, tmp_path, monkeypatch, failure_point,
):
    environment = environment_factory(semantic=False)
    repo, commits = repository(tmp_path)
    pool = SharedPool(tmp_path / "pool", environment.policy)
    first = pool.build_git_snapshot(repo, commits[0], "old", environment.corpus)
    previous = pool.snapshot(first.target_id)
    original_create = Tags.create

    def interrupted_create(self, tag, reference=None):
        if failure_point == "before_tag":
            raise RuntimeError("Injected publication interruption")
        original_create(self, tag, reference)
        if failure_point == "after_tag":
            raise RuntimeError("Injected publication interruption")

    def progress(event):
        if failure_point == "before_catalog" and event["stage"] == "publish":
            raise RuntimeError("Injected publication interruption")

    with monkeypatch.context() as guard:
        guard.setattr(Tags, "create", interrupted_create)
        with pytest.raises(RuntimeError, match="Injected publication interruption"):
            pool.build_git_snapshot(
                repo, commits[1], "resumed", environment.corpus, progress=progress,
            )
    assert pool.snapshot("resumed") is None
    result = pool.build_git_snapshot(repo, commits[1], "resumed", environment.corpus)
    snapshot = pool.snapshot(result.target_id)
    native = lance.dataset(str(result.corpus.semantic_store / "chunks.lance"))
    assert native.tags.get_version(snapshot["lance_tag"]) == snapshot["table_version"]
    assert pool.snapshot(first.target_id) == previous
    assert pool.read_bytes(first.snapshot_id, "changed.py") == repo.read_blob(
        next(item.blob_oid for item in repo.files(commits[0]) if item.path == "changed.py")
    )


def test_retained_release_rejects_changed_members_without_modifying_versions(tmp_path):
    provider = FakeEmbeddingProvider()
    store = SharedVectors(tmp_path / "vectors", provider.dim)
    texts = ["first implementation", "second implementation"]
    store.append([
        {"id": index, "input_hash": hashlib.sha256(text.encode()).hexdigest(),
         "text": text, "vector": vector.tolist()}
        for index, (text, vector) in enumerate(
            zip(texts, provider.embed_documents(texts), strict=True)
        )
    ])
    release = store.publish("snapshot", {0}, lambda: None)
    versions = store.table.list_versions()
    assert store.publish("snapshot", {0}, lambda: None) == release
    with pytest.raises(ValueError, match="different snapshot membership"):
        store.publish("snapshot", {1}, lambda: None)
    assert store.table.list_versions() == versions


def test_retained_release_rejects_a_tag_without_its_membership_column(tmp_path):
    provider = FakeEmbeddingProvider()
    store = SharedVectors(tmp_path / "vectors", provider.dim)
    tag = "snapshot_" + hashlib.sha256(b"snapshot").hexdigest()[:24]
    native = lance.dataset(str(store.root / "chunks.lance"))
    native.tags.create(tag, int(store.table.version))
    with pytest.raises(ValueError, match="missing its snapshot membership column"):
        store.publish("snapshot", set(), lambda: None)


def test_retained_release_cancellation_preserves_the_release(tmp_path):
    provider = FakeEmbeddingProvider()
    store = SharedVectors(tmp_path / "vectors", provider.dim)
    store.append([{
        "id": 1, "input_hash": "one", "text": "source implementation",
        "vector": provider.embed_query("source implementation").tolist(),
    }])
    release = store.publish("snapshot", {1}, lambda: None)
    versions = store.table.list_versions()
    checks = 0

    def cancelled():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise SharedBuildCancelled("Cancelled during retained membership validation")

    with pytest.raises(SharedBuildCancelled, match="membership validation"):
        store.publish("snapshot", {1}, cancelled)
    assert store.table.list_versions() == versions
    assert store.publish("snapshot", {1}, lambda: None) == release


def test_retained_release_requires_search_indexes(tmp_path):
    provider = FakeEmbeddingProvider()
    store = SharedVectors(tmp_path / "vectors", provider.dim)
    store.append([{
        "id": 1, "input_hash": "one", "text": "source implementation",
        "vector": provider.embed_query("source implementation").tolist(),
    }])
    digest = hashlib.sha256(b"snapshot").hexdigest()[:24]
    store.table.add_columns({"s_" + digest: "true"})
    native = lance.dataset(str(store.root / "chunks.lance"))
    native.tags.create("snapshot_" + digest, int(store.table.version))
    versions = store.table.list_versions()
    with pytest.raises(ValueError, match="missing required search indexes"):
        store.publish("snapshot", {1}, lambda: None)
    assert store.table.list_versions() == versions
