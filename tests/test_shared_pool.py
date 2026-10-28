from __future__ import annotations

import errno
import hashlib
import json
import sqlite3
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from codalith.corpus.generations import GenerationRepository
from codalith.corpus.git_source import GitRepository
from codalith.corpus.registry import CorpusRegistry
from codalith.errors import IndexBuildError
from codalith.indexing.builder import GenerationBuilder
from codalith.indexing.shared import SharedBuildCancelled, SharedPool, SharedQueryService
from codalith.languages.base import ReferenceObservation
from codalith.languages.python import PythonAdapter
from codalith.semantic.config import Config
from codalith.semantic.embeddings.fake_provider import FakeEmbeddingProvider
from codalith.semantic.shared import SharedVectors


def git(path: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(path), "-c", "user.name=Test", "-c", "user.email=test@local", *args],
        text=True,
    ).strip()


def repository(tmp_path: Path) -> tuple[GitRepository, list[str]]:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    (root / "stable.py").write_text("def stable():\n    return 'unchanged'\n", encoding="utf-8")
    (root / "changed.py").write_text("def earlier():\n    return 'version-one'\n", encoding="utf-8")
    git(root, "add", "stable.py", "changed.py")
    git(root, "commit", "-qm", "first")
    first = git(root, "rev-parse", "HEAD")
    git(root, "tag", "5.0.0")
    (root / "changed.py").write_text("def later():\n    return 'version-two'\n", encoding="utf-8")
    git(root, "add", "changed.py")
    git(root, "commit", "-qm", "second")
    second = git(root, "rev-parse", "HEAD")
    git(root, "tag", "5.1.0")
    return GitRepository(root), [first, second]


def test_shared_snapshots_deduplicate_and_read_without_worktree(environment_factory, tmp_path):
    environment = environment_factory()
    repo, revisions = repository(tmp_path)
    pool = SharedPool(tmp_path / "pool", environment.policy)
    first = pool.build_git_snapshot(repo, revisions[0], "v-one", environment.corpus, tag="5.0.0")
    second = pool.build_git_snapshot(repo, revisions[1], "v-two", environment.corpus, tag="5.1.0")
    assert first.new_vectors > 0
    assert second.reused_vectors > 0
    with sqlite3.connect(pool.catalog) as connection:
        assert connection.execute("SELECT count(*) FROM source_objects").fetchone()[0] == 3
        assert connection.execute("SELECT count(*) FROM extraction_cache").fetchone()[0] == 3
        assert connection.execute("SELECT count(*) FROM facts_files").fetchone()[0] == 3
        assert connection.execute("SELECT count(*) FROM facts_symbols").fetchone()[0] == 3
        assert connection.execute("SELECT count(*) FROM chunks").fetchone()[0] < (
            first.counts["files"] + second.counts["files"]
        )
    registry = CorpusRegistry(
        path=environment.registry_path,
        default_target="v-one",
        corpora=pool.published_corpora(),
        workspaces={},
    )
    service = SharedQueryService(registry, environment.policy, pool)
    (repo.path / "stable.py").write_text("damaged local worktree", encoding="utf-8")
    try:
        assert service.status(target="v-one").ready
        first_hits = service.search("earlier", target="v-one", strategy="symbol").hits
        assert first_hits and first_hits[0].corpus_id == "v-one"
        assert not service.search("earlier", target="v-two", strategy="symbol").hits
        read = service.read("codalith://v-one/source/stable.py#L1-L2")
        assert read.text == "def stable():\n    return 'unchanged'"
        assert not read.stale
        assert read.sha256 == hashlib.sha256(b"def stable():\n    return 'unchanged'\n").hexdigest()
        text_hits = service.search("version-two", target="v-two", strategy="text").hits
        assert text_hits and text_hits[0].corpus_id == "v-two"
        assert service.search("stable", target="v-one", strategy="semantic").hits
        assert service.context("stable", target="v-one").sources
        assert service.compare("v-one", "v-two").changes
    finally:
        service.close()


def test_shared_cancel_does_not_publish(environment_factory, tmp_path):
    environment = environment_factory()
    repo, revisions = repository(tmp_path)
    pool = SharedPool(tmp_path / "pool", environment.policy)
    with pytest.raises(SharedBuildCancelled):
        pool.build_git_snapshot(
            repo, revisions[0], "cancelled", environment.corpus, cancelled=lambda: True
        )
    assert pool.snapshots() == []


def test_shared_rejects_target_revision_rebinding(environment_factory, tmp_path):
    environment = environment_factory()
    repo, revisions = repository(tmp_path)
    pool = SharedPool(tmp_path / "pool", environment.policy)
    pool.build_git_snapshot(repo, revisions[0], "fixed", environment.corpus)
    with pytest.raises(IndexBuildError, match="another source commit"):
        pool.build_git_snapshot(repo, revisions[1], "fixed", environment.corpus)


@pytest.mark.parametrize("legacy_engine_version", ["1.1.0", "2.0.0"])
def test_legacy_vectors_are_imported_without_mutation_or_embedding(
    environment_factory,
    tmp_path,
    monkeypatch,
    legacy_engine_version,
):
    environment = environment_factory(
        files={
            "stable.py": "def stable():\n    return 'unchanged'\n",
            "changed.py": "def earlier():\n    return 'version-one'\n",
        }
    )
    repo, revisions = repository(tmp_path)
    for item in repo.files(revisions[0]):
        (environment.source_root / item.path).write_bytes(repo.read_blob(item.blob_oid))
    GenerationBuilder(environment.policy).build(environment.corpus, semantic_mode="build")
    generation = GenerationRepository(environment.policy).active(environment.corpus)
    legacy_manifest = json.loads(generation.manifest_path.read_text(encoding="utf-8"))
    legacy_manifest["semantic_engine_version"] = legacy_engine_version
    generation.manifest_path.write_text(json.dumps(legacy_manifest), encoding="utf-8")
    before = {
        path.relative_to(environment.index_root): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in environment.index_root.rglob("*")
        if path.is_file()
    }
    pool = SharedPool(tmp_path / "pool", environment.policy)

    def unexpected_embedding(self, texts):
        raise AssertionError("Compatible legacy inputs must reuse vectors")

    monkeypatch.setattr(FakeEmbeddingProvider, "embed_documents", unexpected_embedding)
    original_read = Path.read_bytes

    def bounded_read(path):
        if path == generation.structure_path:
            raise AssertionError(
                "Legacy structure hashing must stream instead of loading the database"
            )
        return original_read(path)

    with monkeypatch.context() as guard:
        guard.setattr(Path, "read_bytes", bounded_read)
        result = pool.build_git_snapshot(
            repo, revisions[0], "imported", environment.corpus, legacy_generation=generation.root
        )
    assert result.new_vectors == 0
    assert result.reused_vectors > 0
    after = {
        path.relative_to(environment.index_root): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in environment.index_root.rglob("*")
        if path.is_file()
    }
    assert before == after


def test_retry_after_vector_append_before_catalog_mark_is_idempotent(
    environment_factory,
    tmp_path,
    monkeypatch,
):
    environment = environment_factory()
    repo, revisions = repository(tmp_path)
    pool = SharedPool(tmp_path / "pool", environment.policy)
    original = SharedVectors.append
    crash_once = True

    def append_then_crash(self, records):
        nonlocal crash_once
        original(self, records)
        if crash_once:
            crash_once = False
            raise RuntimeError("Simulated crash after vector append")

    monkeypatch.setattr(SharedVectors, "append", append_then_crash)
    with pytest.raises(RuntimeError, match="Simulated crash"):
        pool.build_git_snapshot(repo, revisions[0], "resumed", environment.corpus)
    assert pool.snapshots() == []
    result = pool.build_git_snapshot(repo, revisions[0], "resumed", environment.corpus)
    item = pool.snapshot(result.target_id)
    vectors = SharedVectors(result.corpus.semantic_store, item["dimension"])
    stored = vectors.table.search().select(["id", "input_hash"]).limit(100).to_list()
    assert len(stored) == len({row["id"] for row in stored})
    assert len(stored) == item["chunks"]


def test_same_source_and_contract_alias_share_snapshot(environment_factory, tmp_path):
    environment = environment_factory()
    repo, revisions = repository(tmp_path)
    pool = SharedPool(tmp_path / "pool", environment.policy)
    first = pool.build_git_snapshot(repo, revisions[0], "original", environment.corpus)
    before = pool.storage_stats()["unique_vectors"]
    alias = pool.build_git_snapshot(repo, revisions[0], "alias", environment.corpus)
    assert first.snapshot_id == alias.snapshot_id
    assert pool.storage_stats()["unique_vectors"] == before
    assert len(pool.published_corpora()) == 2
    assert pool.gc()["protected_snapshots"] == 1


def test_lance_release_pin_preserves_old_members_and_scores(environment_factory, tmp_path):
    environment = environment_factory()
    repo, revisions = repository(tmp_path)
    pool = SharedPool(tmp_path / "pool", environment.policy)
    first = pool.build_git_snapshot(repo, revisions[0], "old", environment.corpus)
    item = pool.snapshot(first.target_id)
    store = SharedVectors(first.corpus.semantic_store, item["dimension"])
    table = store.db.open_table("chunks")
    table.checkout(item["table_version"])
    old_result = (
        table.search("stable", query_type="fts")
        .where(
            item["membership_column"] + " = true",
            prefilter=True,
        )
        .select(["id"])
        .limit(10)
        .to_list()
    )
    pool.build_git_snapshot(repo, revisions[1], "new", environment.corpus)
    table = store.db.open_table("chunks")
    table.checkout(item["table_version"])
    assert (
        table.search("stable", query_type="fts")
        .where(
            item["membership_column"] + " = true",
            prefilter=True,
        )
        .select(["id"])
        .limit(10)
        .to_list()
        == old_result
    )


def test_shared_ann_delta_release_preserves_existing_data_files(tmp_path):
    provider = FakeEmbeddingProvider()
    store = SharedVectors(tmp_path / "vectors", provider.dim)

    def records(start, end):
        texts = [f"token{index} source implementation" for index in range(start, end)]
        vectors = provider.embed_documents(texts)
        return [
            {
                "id": index,
                "input_hash": hashlib.sha256(text.encode()).hexdigest(),
                "text": text,
                "vector": vector.tolist(),
            }
            for index, text, vector in zip(range(start, end), texts, vectors, strict=True)
        ]

    store.append(records(0, 300))
    old_version, old_column, _ = store.publish("ann-old", set(range(300)), lambda: None)
    import lance

    before = lance.dataset(str(store.root / "chunks.lance"), version=old_version)
    old_files = {
        data_file.path for fragment in before.get_fragments() for data_file in fragment.data_files()
    }
    store.append(records(300, 310))
    version, column, _ = store.publish("ann-new", {0, 300}, lambda: None)
    latest = lance.dataset(str(store.root / "chunks.lance"), version=version)
    files = {
        data_file.path for fragment in latest.get_fragments() for data_file in fragment.data_files()
    }
    assert old_files <= files
    hits = store.search(
        "token300",
        provider=provider,
        config=Config(store_dir=store.root),
        version=version,
        column=column,
        limit=10,
    )
    assert hits and all(hit[0] in {0, 300} for hit in hits)
    old_hits = store.search(
        "token300",
        provider=provider,
        config=Config(store_dir=store.root),
        version=old_version,
        column=old_column,
        limit=10,
    )
    assert all(hit[0] < 300 for hit in old_hits)


def test_duplicate_reference_observations_share_one_member_and_accurate_count(
    environment_factory,
    tmp_path,
    monkeypatch,
):
    environment = environment_factory()
    repo, revisions = repository(tmp_path)
    original = PythonAdapter.extract

    def duplicates(self, path, text):
        extraction = original(self, path, text)
        reference = ReferenceObservation(None, "external", "call", path, 1)
        return replace(extraction, references=(reference, reference))

    monkeypatch.setattr(PythonAdapter, "extract", duplicates)
    pool = SharedPool(tmp_path / "pool", environment.policy)
    result = pool.build_git_snapshot(repo, revisions[0], "duplicates", environment.corpus)
    assert result.counts["references"] == 2
    with sqlite3.connect(pool.catalog) as connection:
        actual = connection.execute(
            "SELECT count(*) FROM members_references WHERE snapshot_id=?",
            (result.snapshot_id,),
        ).fetchone()[0]
    assert actual == result.counts["references"]


def test_insert_rename_delete_restore_reuse_vectors_and_keep_source_positions(
    environment_factory,
    tmp_path,
    monkeypatch,
):
    environment = environment_factory()
    repo, revisions = repository(tmp_path)
    pool = SharedPool(tmp_path / "pool", environment.policy)
    baseline = pool.build_git_snapshot(repo, revisions[1], "baseline", environment.corpus)
    unique_vectors = pool.storage_stats()["unique_vectors"]

    def forbid_embedding(self, texts):
        raise AssertionError("Moving unchanged code must reuse its original vector input")

    monkeypatch.setattr(FakeEmbeddingProvider, "embed_documents", forbid_embedding)
    original = repo.read_blob(
        next(item.blob_oid for item in repo.files(revisions[1]) if item.path == "stable.py")
    )
    moved_content = b"\n" + original

    def publish(target, *paths):
        git(repo.path, "add", *paths)
        git(repo.path, "commit", "-qm", target)
        result = pool.build_git_snapshot(
            repo, git(repo.path, "rev-parse", "HEAD"), target, environment.corpus
        )
        assert result.new_vectors == 0
        return result

    (repo.path / "stable.py").write_bytes(moved_content)
    inserted = publish("inserted", "stable.py")
    (repo.path / "stable.py").rename(repo.path / "renamed.py")
    renamed = publish("renamed", "stable.py", "renamed.py")
    (repo.path / "renamed.py").unlink()
    deleted = publish("deleted", "renamed.py")
    (repo.path / "renamed.py").write_bytes(moved_content)
    restored = publish("restored", "renamed.py")
    assert pool.storage_stats()["unique_vectors"] == unique_vectors
    registry = CorpusRegistry(
        path=environment.registry_path,
        default_target="baseline",
        corpora=pool.published_corpora(),
        workspaces={},
    )
    service = SharedQueryService(registry, environment.policy, pool)
    try:
        for result, path, line in (
            (baseline, "stable.py", 1),
            (inserted, "stable.py", 2),
            (renamed, "renamed.py", 2),
            (restored, "renamed.py", 2),
        ):
            definition = service.symbol("stable", target=result.target_id).definitions[0]
            assert (definition.path, definition.start_line) == (path, line)
            source = service.read(definition.source_uri)
            assert source.path == path and source.start_line == line and not source.stale
            assert source.text.startswith("def stable():")
            assert (
                source.sha256
                == hashlib.sha256(original if result is baseline else moved_content).hexdigest()
            )
        assert service.symbol("stable", target=deleted.target_id).definitions == []
    finally:
        service.close()


def test_published_snapshot_queries_survive_source_repository_move(environment_factory, tmp_path):
    environment = environment_factory()
    repo, revisions = repository(tmp_path)
    pool = SharedPool(tmp_path / "pool", environment.policy)
    result = pool.build_git_snapshot(repo, revisions[0], "independent", environment.corpus)
    offline = tmp_path / "offline-repository"
    assert repo.path.parent == offline.parent == tmp_path
    repo.path.rename(offline)
    assert not repo.path.exists()
    registry = CorpusRegistry(
        path=environment.registry_path,
        default_target=result.target_id,
        corpora=pool.published_corpora(),
        workspaces={},
    )
    service = SharedQueryService(registry, environment.policy, pool)
    try:
        source = service.read("codalith://independent/source/stable.py#L1-L2")
        assert "unchanged" in source.text and not source.stale
        context = service.context("stable", target=result.target_id)
        assert any(
            item.path == "stable.py" and "unchanged" in item.text for item in context.sources
        )
        hits = service.search("unchanged", target=result.target_id, strategy="text").hits
        assert hits and all(item.path == "stable.py" for item in hits)
    finally:
        service.close()


@pytest.mark.parametrize("failure", ["enospc", "cancel"])
def test_index_phase_failure_does_not_change_published_snapshot(
    environment_factory,
    tmp_path,
    monkeypatch,
    failure,
):
    environment = environment_factory()
    repo, revisions = repository(tmp_path)
    pool = SharedPool(tmp_path / "pool", environment.policy)
    old = pool.build_git_snapshot(repo, revisions[0], "retained", environment.corpus)
    metadata = pool.snapshot(old.target_id)
    registry = CorpusRegistry(
        path=environment.registry_path,
        default_target=old.target_id,
        corpora=pool.published_corpora(),
        workspaces={},
    )
    service = SharedQueryService(registry, environment.policy, pool)
    before = service.context("stable", target=old.target_id).model_dump()
    cancelled = False

    def progress(event):
        nonlocal cancelled
        if failure == "cancel" and event["stage"] == "index":
            cancelled = True

    if failure == "enospc":
        from lancedb.table import LanceTable

        def no_space(self, *args, **kwargs):
            raise OSError(errno.ENOSPC, "Simulated index storage exhaustion")

        monkeypatch.setattr(LanceTable, "create_index", no_space)
    try:
        expected = OSError if failure == "enospc" else SharedBuildCancelled
        with pytest.raises(expected):
            pool.build_git_snapshot(
                repo,
                revisions[1],
                "unpublished",
                environment.corpus,
                progress=progress,
                cancelled=lambda: cancelled,
            )
        assert pool.snapshot("unpublished") is None
        assert pool.snapshot(old.target_id) == metadata
        assert set(pool.published_corpora()) == {old.target_id}
        assert service.status(target=old.target_id).ready
        assert service.context("stable", target=old.target_id).model_dump() == before
        read = service.read("codalith://retained/source/changed.py#L1-L2")
        assert "version-one" in read.text and not read.stale
        assert service.search("version-one", target=old.target_id, strategy="text").hits
    finally:
        service.close()
