from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest

from codalith.cli.main import main
from codalith.corpus.generations import GenerationRepository
from codalith.corpus.git_source import GitRepository
from codalith.errors import ConfigurationError, IndexInvalidError
from codalith.indexing.shared import SharedPool, SharedQueryService
from codalith.management.config import ManagementConfig
from codalith.query.service import QueryService
from conftest import EnvironmentFactory, TestEnvironment
from test_shared_pool import repository


def enable_management(environment: TestEnvironment, root: Path) -> None:
    with environment.registry_path.open("a", encoding="utf-8") as handle:
        handle.write(
            "\n[management]\nenabled = true\n"
            f"storage_root = {json.dumps(root.as_posix())}\n"
            f"source_roots = [{json.dumps(environment.root.as_posix())}]\n"
            'admin_token_env = "CODALITH_DIAGNOSTIC_TEST_ADMIN_TOKEN"\n'
        )


def cli(environment: TestEnvironment, *command: str) -> int:
    return main(
        [
            "--registry",
            str(environment.registry_path),
            "--policy",
            str(environment.policy_path),
            *command,
        ]
    )


@pytest.fixture
def shared_diagnostics(
    environment_factory: EnvironmentFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestEnvironment, SharedPool, GitRepository, list[str]]:
    environment = environment_factory()
    root = tmp_path / "managed"
    enable_management(environment, root)
    monkeypatch.delenv("CODALITH_DIAGNOSTIC_TEST_ADMIN_TOKEN", raising=False)
    repo, revisions = repository(tmp_path)
    pool = SharedPool(root / "shared", environment.policy)
    pool.build_git_snapshot(repo, revisions[0], "v-one", environment.corpus, tag="5.0.0")
    return environment, pool, repo, revisions


@pytest.mark.parametrize("target", ["sample", "v-one"])
@pytest.mark.parametrize("command", [("index", "status"), ("doctor",), ("doctor", "--deep")])
def test_local_diagnostics_resolve_directory_and_shared_targets_without_admin_credentials(
    shared_diagnostics: tuple[TestEnvironment, SharedPool, GitRepository, list[str]],
    capsys: pytest.CaptureFixture[str],
    target: str,
    command: tuple[str, ...],
) -> None:
    environment, pool, _, _ = shared_diagnostics
    with pytest.raises(ConfigurationError, match="admin token"):
        ManagementConfig.from_registry(environment.registry_path)
    assert cli(environment, *command, "--target", target) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["target"] == target and payload["ready"]
    assert payload["corpora"][0]["corpus_id"] == target
    if "--deep" in command:
        assert payload["deep_checks"] == [
            {"corpus_id": target, "artifacts_valid": True, "semantic_fingerprint_valid": True}
        ]
        assert payload["failures"] == []
    assert not (pool.root.parent / "management.sqlite").exists()


@pytest.mark.parametrize("command", [("index", "status"), ("doctor", "--deep")])
def test_missing_managed_storage_is_reported_without_creating_it(
    environment_factory: EnvironmentFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: tuple[str, ...],
) -> None:
    environment = environment_factory()
    root = tmp_path / "missing-managed-storage"
    enable_management(environment, root)
    monkeypatch.delenv("CODALITH_DIAGNOSTIC_TEST_ADMIN_TOKEN", raising=False)
    assert cli(environment, *command, "--target", "v-one") == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.startswith("error:")
    assert "admin token" not in captured.err
    assert not root.exists()


def test_deep_shared_diagnostics_preserve_old_version_after_pool_append(
    shared_diagnostics: tuple[TestEnvironment, SharedPool, GitRepository, list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    environment, pool, repo, revisions = shared_diagnostics
    assert cli(environment, "doctor", "--deep", "--target", "v-one") == 0
    before = json.loads(capsys.readouterr().out)
    pool.build_git_snapshot(repo, revisions[1], "v-two", environment.corpus, tag="5.1.0")
    assert cli(environment, "doctor", "--deep", "--target", "v-one") == 0
    after = json.loads(capsys.readouterr().out)
    assert after == before
    assert cli(environment, "doctor", "--deep", "--target", "v-two") == 0
    assert json.loads(capsys.readouterr().out)["failures"] == []


@pytest.mark.parametrize(
    "corruption",
    [
        "invalid_json", "non_object", "missing_profile", "profile_type", "missing_profile_field",
        "profile_extensions", "missing_snapshot_id", "mismatched_target", "boolean_count",
        "invalid_dimension",
    ],
)
def test_corrupt_snapshot_metadata_has_a_domain_error_in_pool_and_cli(
    shared_diagnostics: tuple[TestEnvironment, SharedPool, GitRepository, list[str]],
    capsys: pytest.CaptureFixture[str],
    corruption: str,
) -> None:
    environment, pool, _, _ = shared_diagnostics
    snapshot = pool.snapshot("v-one")
    assert snapshot is not None
    if corruption == "missing_profile":
        del snapshot["profile"]
    elif corruption == "profile_type":
        snapshot["profile"] = []
    elif corruption == "missing_profile_field":
        del snapshot["profile"]["source_root"]
    elif corruption == "profile_extensions":
        snapshot["profile"]["include_extensions"] = ".py"
    elif corruption == "missing_snapshot_id":
        del snapshot["snapshot_id"]
    elif corruption == "mismatched_target":
        snapshot["target_id"] = "other-target"
    elif corruption == "boolean_count":
        snapshot["counts"]["files"] = True
    elif corruption == "invalid_dimension":
        snapshot["dimension"] = 0
    metadata = (
        "{" if corruption == "invalid_json" else "[]" if corruption == "non_object"
        else json.dumps(snapshot)
    )
    with pool._connect() as connection:
        connection.execute("UPDATE snapshots SET metadata=? WHERE target_id='v-one'", (metadata,))
    with pytest.raises(IndexInvalidError, match="Invalid shared snapshot"):
        pool.snapshot("v-one")
    with pytest.raises(IndexInvalidError, match="Invalid shared snapshot"):
        pool.snapshots()
    for command in (("index", "status"), ("doctor", "--deep")):
        assert cli(environment, *command, "--target", "v-one") == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.startswith("error: Invalid shared snapshot")
        assert "Traceback" not in captured.err


def test_stale_snapshot_engine_keeps_existing_status_error(
    shared_diagnostics: tuple[TestEnvironment, SharedPool, GitRepository, list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    environment, pool, _, _ = shared_diagnostics
    snapshot = pool.snapshot("v-one")
    assert snapshot is not None
    snapshot["engine_version_id"] = "obsolete-engine-version"
    with pool._connect() as connection:
        connection.execute(
            "UPDATE snapshots SET metadata=? WHERE target_id='v-one'", (json.dumps(snapshot),)
        )
    assert pool.snapshot("v-one") == snapshot
    assert cli(environment, "doctor", "--target", "v-one") == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["corpora"][0]["state"] == "invalid"
    assert payload["corpora"][0]["message"] == "Shared snapshot semantic version is stale"


def test_missing_structure_fingerprint_is_only_rejected_by_deep_diagnostics(
    shared_diagnostics: tuple[TestEnvironment, SharedPool, GitRepository, list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    environment, pool, _, _ = shared_diagnostics
    snapshot = pool.snapshot("v-one")
    assert snapshot is not None
    del snapshot["structure_fingerprint"]
    with pool._connect() as connection:
        connection.execute(
            "UPDATE snapshots SET metadata=? WHERE target_id='v-one'", (json.dumps(snapshot),)
        )
    assert cli(environment, "index", "status", "--target", "v-one") == 0
    assert json.loads(capsys.readouterr().out)["ready"]
    assert cli(environment, "doctor", "--deep", "--target", "v-one") == 1
    payload = json.loads(capsys.readouterr().out)
    assert not payload["deep_checks"][0]["artifacts_valid"]
    assert "rebuild target" in payload["failures"][0]


def test_deep_directory_diagnostics_still_detect_semantic_store_corruption(
    environment_factory: EnvironmentFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    environment = environment_factory()
    generation = GenerationRepository(environment.policy).active(environment.corpus)
    (generation.semantic_path / "unexpected-bytes").write_bytes(b"corruption")
    assert cli(environment, "doctor", "--deep", "--target", "sample") == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["failures"] == ["Semantic store fingerprint mismatch for sample"]
    assert not payload["deep_checks"][0]["artifacts_valid"]


def test_serve_explicitly_initializes_an_empty_managed_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "managed"
    registry = tmp_path / "registry.toml"
    registry.write_text(
        "schema_version = 2\n[management]\nenabled = true\n"
        f"storage_root = {json.dumps(root.as_posix())}\n"
        f"source_roots = [{json.dumps(tmp_path.as_posix())}]\n"
        'admin_token_env = "CODALITH_DIAGNOSTIC_TEST_ADMIN_TOKEN"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODALITH_DIAGNOSTIC_TEST_ADMIN_TOKEN", "test-admin-token-for-serve")
    received: list[QueryService] = []

    async def serve(service: QueryService) -> None:
        received.append(service)
        assert isinstance(service, SharedQueryService)
        assert service.pool.snapshots() == []

    module: Any = importlib.import_module("codalith.cli.main")
    monkeypatch.setattr(module, "serve_stdio", serve)
    assert main(
        [
            "--registry",
            str(registry),
            "--policy",
            "configs/source-policy.toml",
            "serve",
            "--transport",
            "stdio",
        ]
    ) == 0
    assert len(received) == 1
    assert (root / "shared" / "catalog.sqlite").is_file()
