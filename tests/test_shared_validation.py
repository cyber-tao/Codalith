from __future__ import annotations

import hashlib
import sqlite3

import lance
import pytest

from codalith.corpus.registry import CorpusRegistry
from codalith.errors import IndexBuildError, IndexInvalidError
from codalith.indexing.shared import SharedPool, SharedQueryService
from codalith.indexing.shared_validation import verify_shared_snapshot
from test_shared_pool import repository


@pytest.fixture
def published(environment_factory, tmp_path):
    environment = environment_factory(semantic=False)
    repo, revisions = repository(tmp_path)
    pool = SharedPool(tmp_path / "managed" / "shared", environment.policy)
    pool.build_git_snapshot(repo, revisions[0], "v-one", environment.corpus)
    snapshot = pool.snapshot("v-one")
    assert snapshot is not None
    return environment, pool, snapshot, repo, revisions


def test_deep_checks_are_read_only_and_do_not_load_a_provider(published, monkeypatch):
    environment, pool, snapshot, repo, revisions = published
    pool.build_git_snapshot(repo, revisions[1], "v-two", environment.corpus)
    # Read-only WAL connections may create SQLite coordination sidecars, never data pages.
    before = {path.relative_to(pool.root): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in pool.root.rglob("*")
              if path.is_file() and path.name not in {"catalog.sqlite-wal", "catalog.sqlite-shm"}}

    def unexpected_provider(*args, **kwargs):
        pytest.fail("Diagnostics must not load an embedding provider")

    monkeypatch.setattr("codalith.indexing.shared.get_provider", unexpected_provider)
    readonly = SharedPool(pool.root, environment.policy, readonly=True)
    verify_shared_snapshot(readonly, snapshot)
    with pytest.raises(IndexBuildError, match="read-only"):
        readonly.upgrade()
    with pytest.raises(IndexBuildError, match="read-only"):
        readonly.build_git_snapshot(repo, revisions[0], "forbidden", environment.corpus)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        with readonly._connect() as connection:
            connection.execute("DELETE FROM snapshots")
    after = {path.relative_to(pool.root): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in pool.root.rglob("*")
             if path.is_file() and path.name not in {"catalog.sqlite-wal", "catalog.sqlite-shm"}}
    assert after == before
    assert (pool.root / "catalog.sqlite-wal").stat().st_size == 0


@pytest.mark.parametrize(("sql", "message"), [
    ("DELETE FROM members_files WHERE rowid=(SELECT min(rowid) FROM members_files)", "member count"),
    ("DELETE FROM facts_symbols WHERE rowid=(SELECT min(rowid) FROM facts_symbols)", "has no fact"),
    ("UPDATE facts_symbols SET name='corrupted'", "fact hash"),
    ("DELETE FROM snapshot_sources WHERE rowid=(SELECT min(rowid) FROM snapshot_sources)", "source member"),
    ("DELETE FROM source_objects WHERE rowid=(SELECT min(rowid) FROM source_objects)", "source object"),
    ("UPDATE source_objects SET git_oid='0000000000000000000000000000000000000000'", "integrity check"),
    ("UPDATE source_objects SET size_bytes=size_bytes+1", "source object"),
    ("DELETE FROM occurrences WHERE rowid=(SELECT min(rowid) FROM occurrences)", "occurrences"),
    ("UPDATE occurrences SET end_line=end_line+1", "occurrences"),
    ("UPDATE occurrences SET language='text'", "occurrences"),
    ("UPDATE chunks SET stored=0", "invalid chunk"),
    ("UPDATE chunks SET namespace='wrong'", "invalid chunk"),
    ("UPDATE chunks SET input_hash='wrong' WHERE id=(SELECT min(id) FROM chunks)", "occurrences"),
])
def test_deep_checks_detect_catalog_corruption(published, sql, message):
    _, pool, snapshot, _, _ = published
    with sqlite3.connect(pool.catalog) as connection:
        connection.execute(sql)
    with pytest.raises(IndexInvalidError, match=message):
        verify_shared_snapshot(pool, snapshot)


@pytest.mark.parametrize(("field", "value", "message"), [
    ("table_version", 999999, "tag/version"),
    ("membership_column", "s_wrong", "release identity"),
    ("semantic_namespace", "../escape", "invalid semantic_namespace"),
    ("source_fingerprint", "wrong", "source fingerprint"),
    ("chunks", 999999, "chunk member count"),
    ("commit", "0" * 40, "contract identity"),
    ("source_commit", "0" * 40, "contract identity"),
    ("generation_id", "0" * 32, "contract identity"),
    ("structure_fingerprint", "", "rebuild target"),
])
def test_deep_checks_detect_metadata_corruption(published, field, value, message):
    _, pool, snapshot, _, _ = published
    snapshot[field] = value
    with pytest.raises(IndexInvalidError, match=message):
        verify_shared_snapshot(pool, snapshot)


def test_missing_retention_tag_fails_deep_but_status_remains_lightweight(published):
    environment, pool, snapshot, _, _ = published
    dataset = lance.dataset(str(pool.root / "semantic" / snapshot["semantic_namespace"] / "chunks.lance"))
    dataset.tags.delete(snapshot["lance_tag"])
    registry = CorpusRegistry(path=environment.registry_path, default_target="v-one",
                              corpora=pool.published_corpora(), workspaces={})
    service = SharedQueryService(registry, environment.policy, pool)
    try:
        assert service.status(target="v-one").ready
        with pytest.raises(IndexInvalidError, match="tag/version"):
            service.verify_generation(registry.get_corpus("v-one"))
    finally:
        service.close()


def test_deep_checks_reject_membership_different_from_occurrences(published):
    _, pool, snapshot, _, _ = published
    root = pool.root / "semantic" / snapshot["semantic_namespace"] / "chunks.lance"
    dataset = lance.dataset(str(root))
    dataset.update({snapshot["membership_column"]: "false"})
    dataset.tags.update(snapshot["lance_tag"], dataset.version)
    snapshot["table_version"] = dataset.version
    with pytest.raises(IndexInvalidError, match="different snapshot membership"):
        verify_shared_snapshot(pool, snapshot)


def test_deep_checks_reject_valid_facts_from_another_snapshot(published):
    environment, pool, snapshot, repo, revisions = published
    second = pool.build_git_snapshot(repo, revisions[1], "v-two", environment.corpus)
    with sqlite3.connect(pool.catalog) as connection:
        connection.execute("DELETE FROM members_symbols WHERE snapshot_id=?", (snapshot["snapshot_id"],))
        connection.execute(
            "INSERT INTO members_symbols SELECT ?,fact_id FROM members_symbols WHERE snapshot_id=?",
            (snapshot["snapshot_id"], second.snapshot_id),
        )
    with pytest.raises(IndexInvalidError, match="structural membership fingerprint"):
        verify_shared_snapshot(pool, snapshot)


def test_malformed_snapshot_does_not_mask_the_integrity_error(published):
    _, pool, snapshot, _, _ = published
    del snapshot["snapshot_id"]
    with pytest.raises(IndexInvalidError, match="integrity check failed"):
        verify_shared_snapshot(pool, snapshot)


def test_cli_deep_detects_corrupt_shared_members(published, capsys):
    import json

    from codalith.cli.main import main
    from test_shared_diagnostics_cli import enable_management

    environment, pool, _, _, _ = published
    enable_management(environment, pool.root.parent)
    with sqlite3.connect(pool.catalog) as connection:
        connection.execute("DELETE FROM members_symbols")
    assert main([
        "--registry", str(environment.registry_path), "--policy", str(environment.policy_path),
        "doctor", "--deep", "--target", "v-one",
    ]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["failures"] == ["Shared snapshot symbols member count mismatch"]
    assert not result["deep_checks"][0]["artifacts_valid"]
