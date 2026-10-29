from __future__ import annotations

import json
import sqlite3

from codalith.cli.main import main
from codalith.indexing.builder import build_lock
from codalith.indexing.shared import SharedPool
from codalith.management.config import ManagementConfig


def test_explicit_upgrade_cli_backs_up_old_catalog_and_is_idempotent(
    environment_factory, tmp_path, monkeypatch, capsys,
):
    environment = environment_factory(semantic=False)
    storage = tmp_path / "managed"
    pool = SharedPool(storage / "shared", environment.policy)
    with pool._connect() as connection:
        connection.execute("DROP TABLE source_blob_map")
        connection.execute("UPDATE pool_metadata SET value='1' WHERE key='schema'")
    config = ManagementConfig(True, storage, (environment.source_root,), "isolated-admin-token")
    monkeypatch.setattr(ManagementConfig, "from_registry", lambda path: config)
    args = [
        "--registry", str(environment.registry_path), "--policy", str(environment.policy_path),
        "management", "upgrade-storage",
    ]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["previous_version"] == 1
    assert result["schema_version"] == 2
    backup = sqlite3.connect(result["backup_path"])
    try:
        assert backup.execute("SELECT value FROM pool_metadata WHERE key='schema'").fetchone()[0] == "1"
        assert backup.execute(
            "SELECT name FROM sqlite_master WHERE name='source_blob_map'"
        ).fetchone() is None
    finally:
        backup.close()
    assert main(args) == 0
    repeated = json.loads(capsys.readouterr().out)
    assert repeated["backup_path"] is None
    assert repeated["schema_version"] == 2


def test_upgrade_cli_rejects_missing_storage_without_creating_it(
    environment_factory, tmp_path, monkeypatch, capsys,
):
    environment = environment_factory(semantic=False)
    storage = tmp_path / "missing"
    config = ManagementConfig(True, storage, (environment.source_root,), "isolated-admin-token")
    monkeypatch.setattr(ManagementConfig, "from_registry", lambda path: config)
    assert main([
        "--registry", str(environment.registry_path), "--policy", str(environment.policy_path),
        "management", "upgrade-storage",
    ]) == 2
    assert "No shared catalog exists" in capsys.readouterr().err
    assert not storage.exists()


def test_upgrade_cli_respects_an_active_shared_builder(
    environment_factory, tmp_path, monkeypatch, capsys,
):
    environment = environment_factory(semantic=False)
    storage = tmp_path / "managed"
    pool = SharedPool(storage / "shared", environment.policy)
    with pool._connect() as connection:
        connection.execute("DROP TABLE source_blob_map")
        connection.execute("UPDATE pool_metadata SET value='1' WHERE key='schema'")
    config = ManagementConfig(True, storage, (environment.source_root,), "isolated-admin-token")
    monkeypatch.setattr(ManagementConfig, "from_registry", lambda path: config)
    with build_lock(pool.root):
        assert main([
            "--registry", str(environment.registry_path), "--policy", str(environment.policy_path),
            "management", "upgrade-storage",
        ]) == 2
    assert capsys.readouterr().err
    assert pool.schema_version == 1
    assert not (pool.root / "backups").exists()
