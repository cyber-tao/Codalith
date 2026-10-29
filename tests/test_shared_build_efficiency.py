from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

import codalith.indexing.shared as shared
from codalith.corpus.git_source import GitFile, GitRepository
from codalith.corpus.registry import Corpus
from codalith.corpus.source_policy import SourcePolicy
from codalith.errors import IndexBuildError
from codalith.indexing.shared import SharedBuildCancelled, SharedPool
from codalith.indexing.structure.writer import write_structure


class Source:
    def __init__(self, files: dict[str, bytes], object_format: str = "sha1") -> None:
        self.objects: dict[str, bytes] = {}
        self.entries = []
        self.reads: list[str] = []
        for path, data in files.items():
            oid = hashlib.new(
                object_format, b"blob " + str(len(data)).encode() + b"\0" + data
            ).hexdigest()
            self.objects[oid] = data
            self.entries.append(GitFile(path, oid, len(data), "100644"))

    def files(self, commit):
        return self.entries

    def read_blobs(self, oids):
        for oid in oids:
            self.reads.append(oid)
            yield oid, self.objects[oid]

    def read_blob(self, oid):
        self.reads.append(oid)
        return self.objects[oid]


def _environment(tmp_path):
    policy = SourcePolicy(40, 200, 1_000_000, ("**/.env",))
    corpus = Corpus(
        "sample", "Sample", "", "synthetic", tmp_path, tmp_path / "unused", None,
        "python", "fake", "", (".py",), (),
    )
    return SharedPool(tmp_path / "pool", policy), corpus, policy


def _downgrade(pool):
    with pool._connect() as connection:
        connection.execute("DROP TABLE source_blob_map")
        for table in ("files", "symbols"):
            connection.execute(f"DROP INDEX IF EXISTS facts_{table}_path_binary")
        connection.execute("UPDATE pool_metadata SET value='1' WHERE key='schema'")


def test_concurrent_constructor_cannot_open_half_initialized_catalog(tmp_path, monkeypatch):
    root = tmp_path / "pool"
    policy = SourcePolicy(40, 200, 1_000_000, ("**/.env",))
    reached = threading.Event()
    release = threading.Event()
    original = SharedPool._upgrade_schema

    def pause(connection):
        reached.set()
        assert release.wait(timeout=5)
        original(connection)

    monkeypatch.setattr(SharedPool, "_upgrade_schema", staticmethod(pause))
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(SharedPool, root, policy)
        try:
            assert reached.wait(timeout=5)
            assert not (root / "catalog.sqlite").exists()
            with pytest.raises(IndexBuildError, match="Another index build"):
                SharedPool(root, policy)
        finally:
            release.set()
        assert first.result(timeout=5).schema_version == 2
    assert SharedPool(root, policy).schema_version == 2
    assert not list(root.glob(".catalog-init-*"))


def test_failed_initialization_never_exposes_partial_catalog(tmp_path, monkeypatch):
    root = tmp_path / "pool"
    policy = SourcePolicy(40, 200, 1_000_000, ("**/.env",))

    def fail(connection):
        raise RuntimeError("Interrupted initialization")

    with monkeypatch.context() as guard:
        guard.setattr(SharedPool, "_upgrade_schema", staticmethod(fail))
        with pytest.raises(RuntimeError, match="Interrupted initialization"):
            SharedPool(root, policy)
    assert not (root / "catalog.sqlite").exists()
    assert not list(root.glob(".catalog-init-*"))
    assert SharedPool(root, policy).schema_version == 2


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
def test_incremental_source_import_reads_only_unknown_objects(tmp_path, monkeypatch, object_format):
    pool, corpus, _ = _environment(tmp_path)
    original = shared._git
    commands = []

    def run_git(root, *args, **kwargs):
        commands.append(args)
        return original(root, *args, **kwargs)

    monkeypatch.setattr(shared, "_git", run_git)
    stable = b"def stable():\n    return 1\n"
    files = {"a.py": stable, "copy.py": stable, "b.py": b"def old():\n    return 2\n"}
    versions = [(dict(files), 2)]
    files["b.py"] = b"def changed():\n    return 3\n"
    versions.append((dict(files), 1))
    files["renamed.py"] = files.pop("a.py")
    versions.append((dict(files), 0))
    files.pop("copy.py")
    versions.append((dict(files), 0))
    files["copy.py"] = stable
    versions.append((dict(files), 0))
    for number, (content, expected_reads) in enumerate(versions, 1):
        source = Source(content, object_format)
        result = pool.build_git_snapshot(source, str(number) * 40, f"v{number}", corpus)
        assert len(source.reads) == expected_reads
        assert len(source.reads) == len(set(source.reads))
        assert result.counts["files"] == len(content)
        for path, data in content.items():
            assert pool.read_bytes(result.snapshot_id, path) == data
    assert not any("repack" in args for args in commands)
    with pool._connect() as connection:
        assert connection.execute("SELECT count(*) FROM source_blob_map").fetchone()[0] == 3
        assert connection.execute("SELECT DISTINCT object_format FROM source_blob_map").fetchone()[0] == object_format


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
def test_real_git_incremental_import_preserves_versions(tmp_path, monkeypatch, object_format):
    pool, corpus, _ = _environment(tmp_path)
    root = tmp_path / "repository"
    environment = GitRepository._environment()
    initialized = subprocess.run(
        ["git", "init", "-q", f"--object-format={object_format}", str(root)],
        env=environment, capture_output=True, text=True, check=False,
    )
    if initialized.returncode and object_format == "sha256" and any(
        message in initialized.stderr.lower()
        for message in ("unknown hash algorithm", "not supported", "unknown option")
    ):
        pytest.skip("Installed Git does not support SHA-256 repositories")
    initialized.check_returncode()

    def git(*args):
        return subprocess.check_output(
            [
                "git", "-C", str(root), "-c", "user.name=Test", "-c",
                "user.email=test@local", "-c", "commit.gpgSign=false", *args,
            ],
            env=environment, text=True,
        ).strip()

    stable = b"def stable():\n    return 'unchanged'\n"
    earlier = b"def earlier():\n    return 'first-version'\n"
    later = b"def later():\n    return 'second-version'\n"
    (root / "stable.py").write_bytes(stable)
    (root / "changed.py").write_bytes(earlier)
    git("add", "stable.py", "changed.py")
    git("commit", "-qm", "First version")
    first_commit = git("rev-parse", "HEAD")
    repository = GitRepository(root)
    reads = []
    original = repository.read_blobs

    def read_blobs(oids):
        for oid, data in original(oids):
            reads.append(oid)
            yield oid, data

    monkeypatch.setattr(repository, "read_blobs", read_blobs)
    first = pool.build_git_snapshot(repository, first_commit, "first", corpus)
    assert len(reads) == 2
    assert all(len(oid) == (40 if object_format == "sha1" else 64) for oid in reads)
    (root / "changed.py").write_bytes(later)
    git("add", "changed.py")
    git("commit", "-qm", "Change one source file")
    second_commit = git("rev-parse", "HEAD")
    changed_oid = git("rev-parse", f"{second_commit}:changed.py")
    reads.clear()
    second = pool.build_git_snapshot(repository, second_commit, "second", corpus)
    assert reads == [changed_oid]
    for result, changed in ((first, earlier), (second, later)):
        assert result.counts["files"] == 2
        for path, expected in (("stable.py", stable), ("changed.py", changed)):
            actual = pool.read_bytes(result.snapshot_id, path)
            assert actual == expected
            with pool._connect(readonly=True) as connection:
                indexed_hash = connection.execute(
                    "SELECT sha256 FROM snapshot_sources WHERE snapshot_id=? AND path=?",
                    (result.snapshot_id, path),
                ).fetchone()[0]
            assert hashlib.sha256(actual).hexdigest() == indexed_hash
    with pool._connect(readonly=True) as connection:
        formats = connection.execute(
            "SELECT DISTINCT object_format FROM source_blob_map"
        ).fetchall()
        assert [row[0] for row in formats] == [object_format]


def test_retry_reuses_sources_imported_before_failure(tmp_path):
    pool, corpus, _ = _environment(tmp_path)
    source = Source({"stable.py": b"def stable():\n    return 1\n"})

    def fail(event):
        if event["stage"] == "source" and event["completed"] == event["total"]:
            raise RuntimeError("Interrupted after source import")

    with pytest.raises(RuntimeError, match="Interrupted"):
        pool.build_git_snapshot(source, "a" * 40, "retry", corpus, progress=fail)
    assert pool.snapshot("retry") is None
    source.reads.clear()
    result = pool.build_git_snapshot(source, "a" * 40, "retry", corpus)
    assert source.reads == []
    assert pool.read_bytes(result.snapshot_id, "stable.py") == source.objects[next(iter(source.objects))]


def test_cancellation_during_publication_statistics_does_not_publish(tmp_path, monkeypatch):
    pool, corpus, _ = _environment(tmp_path)
    content = {"stable.py": b"def stable():\n    return 1\n"}
    first = pool.build_git_snapshot(Source(content), "a" * 40, "old", corpus)
    cancelled = False
    original = pool._analyze

    def analyze(connection):
        nonlocal cancelled
        original(connection)
        cancelled = True

    monkeypatch.setattr(pool, "_analyze", analyze)
    with pytest.raises(SharedBuildCancelled):
        pool.build_git_snapshot(
            Source(content), "b" * 40, "cancelled", corpus, cancelled=lambda: cancelled
        )
    assert pool.snapshot("cancelled") is None
    assert pool.snapshot("old")["snapshot_id"] == first.snapshot_id


def test_old_catalog_requires_explicit_backed_up_upgrade(tmp_path):
    pool, corpus, policy = _environment(tmp_path)
    data = b"def stable():\n    return 1\n"
    published = pool.build_git_snapshot(Source({"a.py": data}), "a" * 40, "old", corpus)
    _downgrade(pool)
    before = pool.catalog.read_bytes()
    old = SharedPool(pool.root, policy)
    assert old.schema_version == 1
    assert old.snapshot("old")["snapshot_id"] == published.snapshot_id
    assert old.catalog.read_bytes() == before
    with pytest.raises(IndexBuildError, match="management upgrade-storage"):
        old.build_git_snapshot(Source({"renamed.py": data}), "b" * 40, "new", corpus)
    upgraded = old.upgrade()
    assert upgraded["previous_version"] == 1
    assert upgraded["schema_version"] == 2
    with closing(sqlite3.connect(upgraded["backup_path"])) as connection:
        assert connection.execute("SELECT value FROM pool_metadata WHERE key='schema'").fetchone()[0] == "1"
        assert connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 1
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name='source_blob_map'").fetchone()
    source = Source({"renamed.py": data})
    old.build_git_snapshot(source, "b" * 40, "new", corpus)
    assert source.reads == []
    assert old.upgrade()["backup_path"] is None
    with old._connect() as connection:
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(facts_files)")}
        assert "facts_files_path_binary" in indexes
        assert connection.execute("SELECT count(*) FROM sqlite_stat1").fetchone()[0] > 0


def test_failed_upgrade_rolls_back_schema_and_keeps_backup(tmp_path, monkeypatch):
    pool, _, _ = _environment(tmp_path)
    _downgrade(pool)
    original = pool._upgrade_schema

    def fail(connection):
        original(connection)
        raise RuntimeError("Upgrade interrupted")

    monkeypatch.setattr(pool, "_upgrade_schema", fail)
    with pytest.raises(RuntimeError, match="Upgrade interrupted"):
        pool.upgrade()
    assert pool.schema_version == 1
    with pool._connect() as connection:
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name='source_blob_map'").fetchone()
    assert len(list((pool.root / "backups").glob("*.sqlite"))) == 1


@pytest.mark.parametrize("failure", [False, True])
def test_pool_connections_close_with_transaction_semantics(tmp_path, failure):
    pool, _, _ = _environment(tmp_path)
    captured = None
    try:
        with pool._connect() as connection:
            captured = connection
            connection.execute("INSERT INTO pool_metadata VALUES('test', 'value')")
            if failure:
                raise RuntimeError("abort")
    except RuntimeError:
        pass
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        captured.execute("SELECT 1")
    with pool._connect() as connection:
        value = connection.execute("SELECT value FROM pool_metadata WHERE key='test'").fetchone()
        assert bool(value) is not failure


def test_fact_staging_matches_final_facts_without_query_indexes(tmp_path):
    _, corpus, policy = _environment(tmp_path)
    files = [
        ("a.py", b"class Widget:\n    def work(self):\n        return helper()\n"),
        ("b.py", b"def helper():\n    return 1\n"),
    ]
    full = tmp_path / "full.sqlite"
    staging = tmp_path / "staging.sqlite"
    normal = write_structure(corpus, policy, full, sources=files)
    staged = write_structure(corpus, policy, staging, sources=files, fact_staging=True)
    assert normal == staged
    with closing(sqlite3.connect(full)) as first, closing(sqlite3.connect(staging)) as second:
        for table in shared._TABLES:
            assert first.execute(f'SELECT * FROM "{table}"').fetchall() == second.execute(f'SELECT * FROM "{table}"').fetchall()
        assert not second.execute("SELECT 1 FROM sqlite_master WHERE name='symbol_search'").fetchone()
        assert not second.execute("SELECT 1 FROM sqlite_master WHERE name='symbols_name_idx'").fetchone()


def test_cached_observations_and_facts_use_bounded_batches(tmp_path, monkeypatch):
    pool, corpus, _ = _environment(tmp_path)
    statements = []
    batches = []
    original = sqlite3.connect

    class Connection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            statements.append(sql)
            return super().execute(sql, parameters)

        def executemany(self, sql, parameters):
            batches.append(sql)
            return super().executemany(sql, parameters)

    def connect(*args, **kwargs):
        return original(*args, **kwargs, factory=Connection)

    monkeypatch.setattr(sqlite3, "connect", connect)
    data = b"".join(f"def method_{number}():\n    return {number}\n\n".encode() for number in range(80))
    first = pool.build_git_snapshot(Source({"many.py": data}), "a" * 40, "one", corpus)
    statements.clear()
    batches.clear()
    second = pool.build_git_snapshot(Source({"many.py": data}), "b" * 40, "two", corpus)
    assert first.counts == second.counts
    assert second.statistics["reused_parses"] == 1
    assert sum("SELECT key,payload FROM extraction_objects" in sql for sql in statements) == 1
    assert not any(sql.startswith("INSERT OR IGNORE INTO facts_") for sql in statements)
    assert any(sql.startswith("INSERT OR IGNORE INTO facts_symbols") for sql in batches)
    with pool._connect() as connection:
        count = connection.execute("SELECT count(*) FROM facts_symbols_search").fetchone()[0]
        assert count == first.counts["symbols"]


def test_storage_stats_walks_pool_once(tmp_path, monkeypatch):
    pool, _, _ = _environment(tmp_path)
    files = {
        "sources.git/objects/pack/example.pack": b"source",
        "semantic/model/chunks.lance/data/file": b"vectors",
        "semantic/model/chunks.lance/_indices/index": b"index",
    }
    for relative, data in files.items():
        path = pool.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    calls = []
    original = shared.os.walk

    def walk(path):
        calls.append(path)
        return original(path)

    monkeypatch.setattr(shared.os, "walk", walk)
    stats = pool.storage_stats()
    assert calls == [pool.root]
    assert stats["source_physical_bytes"] == len(b"source")
    assert stats["semantic_physical_bytes"] == len(b"vectorsindex")
    assert stats["index_physical_bytes"] == len(b"index")
    assert stats["physical_bytes"] == sum(path.stat().st_size for path in pool.root.rglob("*") if path.is_file())
