from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from codalith.errors import IndexBuildError
from codalith.indexing.builder import GenerationBuilder, build_lock
from codalith.management.catalog import Catalog
from codalith.management.cli import add_arguments, run
from codalith.management.config import ManagementConfig
from codalith.management.service import ManagementService
from codalith.management.worker import run_job
from conftest import EnvironmentFactory
from test_git_source import make_repository


@pytest.fixture
def management(tmp_path: Path, environment_factory: EnvironmentFactory) -> ManagementService:
    environment = environment_factory(semantic=False)
    source = tmp_path / "allowed"
    make_repository(source / "repo")
    config = ManagementConfig(
        True, tmp_path / "managed", (source,), "test-admin-token-with-32-characters"
    )
    return ManagementService(environment.registry, environment.policy, config)


def planned(service: ManagementService, tags: list[str] | None = None) -> dict[str, Any]:
    repo = service.add_repository("repo", "0")
    database = service.create_database("Engine source", "sample", repo["repo_id"])
    return service.create_plan(database["database_id"], tags or ["5.7.4", "5.8.0"])


def test_management_auth_closed_schema_and_allowed_directories(
    management: ManagementService,
) -> None:
    client = TestClient(Starlette(routes=management.routes()))
    assert client.get("/api/admin/config").status_code == 401
    client.headers["Authorization"] = "Bearer " + management.config.admin_token
    config = client.get("/api/admin/config").json()
    assert config["profiles"][0]["profile_id"] == "sample"
    assert management.config.admin_token not in json.dumps(config)
    directories = client.get("/api/admin/directories", params={"root_id": "0"}).json()
    assert directories["items"][0]["is_repository"]
    assert (
        client.get("/api/admin/directories", params={"root_id": "0", "path": ".."}).status_code
        == 400
    )
    assert (
        client.post(
            "/api/admin/repositories", json={"path": "repo", "root_id": "0", "unexpected": True}
        ).status_code
        == 400
    )
    repo = client.post("/api/admin/repositories", json={"path": "repo", "root_id": "0"}).json()
    tags = client.get(
        f"/api/admin/repositories/{repo['repo_id']}/tags", params={"q": "5.8", "limit": 1}
    ).json()
    assert tags["total"] == 1 and tags["items"][0]["tag"] == "5.8.0"
    assert client.post("/api/admin/repositories", content='{"path":"repo"}').status_code == 400


def test_management_persists_plans_and_job_idempotency(management: ManagementService) -> None:
    plan = planned(management)
    same = management.create_plan(plan["database_id"], ["5.7.4", "5.8.0"])
    assert same["plan_id"] == plan["plan_id"]
    job = management.create_job(plan["plan_id"], "request-one")
    assert management.create_job(plan["plan_id"], "request-one")["job_id"] == job["job_id"]
    assert Catalog(management.config.storage_root).get("job", job["job_id"])["status"] == "queued"
    assert management.catalog.events(job["job_id"])[0]["seq"] > 0
    other = management.create_plan(plan["database_id"], ["5.7.4"])
    with pytest.raises(ValueError, match="another plan"):
        management.create_job(other["plan_id"], "request-one")


def test_worker_imports_multiple_tags_and_records_real_progress(
    management: ManagementService,
) -> None:
    plan = planned(management)
    job = management.create_job(plan["plan_id"], "build-one")
    management.catalog.update_job(job["job_id"], lambda value: value.update(status="running"))
    run_job(management, job["job_id"])
    completed = management.catalog.get("job", job["job_id"])
    assert completed["status"] == "succeeded", completed
    assert completed["completed_tags"] == 2
    assert all(item["snapshot_id"] for item in completed["items"])
    assert all(item["phase_started_at"] <= item["ended_at"] <= completed["updated_at"] for item in completed["items"])
    events = management.catalog.events(job["job_id"])
    assert any(event["type"] == "progress" for event in events)
    assert len({event["seq"] for event in events}) == len(events)
    space = management.space(plan["database_id"])
    assert space["physical_source_bytes"] > 0 and space["vector_count"] > 0
    management.catalog.update_job(job["job_id"], lambda value: value.update(status="running"))
    management.recover()
    assert management.catalog.get("job", job["job_id"])["status"] == "succeeded"


def test_cancel_and_restart_require_explicit_resume(management: ManagementService) -> None:
    plan = planned(management)
    job = management.create_job(plan["plan_id"], "queued")
    management.recover()
    assert management.catalog.get("job", job["job_id"])["status"] == "interrupted"
    resumed = management.resume(job["job_id"])
    assert resumed["attempt"] == 2 and resumed["status"] == "queued"
    assert management.cancel(job["job_id"])["status"] == "cancelled"
    management.resume(job["job_id"])
    management.catalog.update_job(job["job_id"], lambda value: value.update(status="running"))
    assert management.cancel(job["job_id"])["status"] == "cancel_requested"
    run_job(management, job["job_id"])
    assert management.catalog.get("job", job["job_id"])["status"] == "cancelled"


def test_cli_and_http_worker_share_writer_lock(management: ManagementService) -> None:
    with build_lock(management.config.storage_root / "worker"):
        with pytest.raises(IndexBuildError, match="Another index build"):
            management.run_pending_once()


def test_cancel_between_queue_listing_and_claim_never_starts_a_worker(
    management: ManagementService, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = management.create_job(planned(management)["plan_id"], "cancel-before-claim")
    original_list = management.catalog.list

    def list_and_cancel(kind: str) -> list[dict[str, Any]]:
        rows = original_list(kind)
        if kind == "job":
            management.cancel(job["job_id"])
        return rows

    def forbidden_spawn(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Cancelled queued job must not spawn a worker")

    monkeypatch.setattr(management.catalog, "list", list_and_cancel)
    monkeypatch.setattr("codalith.management.service.subprocess.Popen", forbidden_spawn)
    assert not management.run_pending_once()
    assert management.catalog.get("job", job["job_id"])["status"] == "cancelled"
    assert not any(event["type"] == "started" for event in management.catalog.events(job["job_id"]))


def test_persistent_claim_rejects_old_attempts_and_competing_claims(
    management: ManagementService,
) -> None:
    job = management.create_job(planned(management)["plan_id"], "competing-claim")
    management.cancel(job["job_id"])
    resumed = management.resume(job["job_id"])
    assert not management.catalog.claim_job(job["job_id"], job["attempt"])
    catalogs = [Catalog(management.config.storage_root), Catalog(management.config.storage_root)]
    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(
            executor.map(
                lambda catalog: catalog.claim_job(job["job_id"], resumed["attempt"]), catalogs
            )
        )
    assert sorted(claims) == [False, True]
    assert management.catalog.get("job", job["job_id"])["status"] == "running"
    assert (
        sum(event["type"] == "started" for event in management.catalog.events(job["job_id"])) == 1
    )


def test_changed_profile_or_policy_rejects_frozen_plan(management: ManagementService) -> None:
    plan = planned(management)
    management.policy = replace(management.policy, max_file_bytes=100)
    with pytest.raises(ValueError, match="policy changed"):
        management.profile(plan)


def test_independent_management_profile_requires_no_existing_generation(
    management: ManagementService,
) -> None:
    config = replace(
        management.config,
        profiles=(
            {
                "id": "engine",
                "label": "Engine",
                "adapter": "cpp-ue",
                "embedding_provider": "fake",
                "include_extensions": [".h", ".cpp"],
            },
        ),
    )
    service = ManagementService(management.registry, management.policy, config)
    database = service.create_database("Native source", "engine")
    assert database["profile_id"] == "engine"
    assert any(profile["profile_id"] == "engine" for profile in service.public_config()["profiles"])


def test_cli_submission_and_gc_preview_share_the_persistent_catalog(
    management: ManagementService,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan = planned(management)
    configuration = management.registry.path.read_text(encoding="utf-8")
    management.registry.path.write_text(
        configuration
        + "\n[management]\nenabled = true\n"
        + "storage_root = "
        + json.dumps(management.config.storage_root.as_posix())
        + "\n"
        + "source_roots = ["
        + json.dumps(management.config.source_roots[0].as_posix())
        + "]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODALITH_ADMIN_TOKEN", management.config.admin_token)
    parser = argparse.ArgumentParser()
    add_arguments(parser.add_subparsers(dest="command", required=True))
    arguments = parser.parse_args(
        [
            "management",
            "build",
            "--plan-id",
            plan["plan_id"],
            "--idempotency-key",
            "local-cli",
            "--submit-only",
        ]
    )
    assert run(arguments, management.registry, management.policy) == 0
    job = json.loads(capsys.readouterr().out)
    assert management.catalog.get("job", job["job_id"])["status"] == "queued"
    arguments = parser.parse_args(["management", "gc", "--dry-run"])
    assert run(arguments, management.registry, management.policy) == 0
    assert json.loads(capsys.readouterr().out)["deleted_objects"] == 0


def test_recorded_worker_failures_redact_tokens(management: ManagementService) -> None:
    message = management.safe_error(
        RuntimeError("provider failed with " + management.config.admin_token)
    )
    assert "[redacted]" in message and management.config.admin_token not in message


def test_legacy_generation_selection_is_allowlisted(management: ManagementService) -> None:
    corpus = management.profiles["sample"]
    report = GenerationBuilder(management.policy).build(corpus, semantic_mode="build")
    manifest_path = corpus.index_root / "generations" / report.generation_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["semantic_engine_version"] = "1.1.0"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    plan = planned(management, ["5.7.4"])
    registered = management.legacy_generations()
    assert registered
    assert registered[0]["semantic_engine_version"] == "1.1.0"
    with pytest.raises(ValueError, match="registered list"):
        management.create_plan(plan["database_id"], ["5.7.4"], legacy_generation="../../arbitrary")
    selected = management.create_plan(
        plan["database_id"], ["5.7.4"], legacy_generation=registered[0]["generation_id"]
    )
    assert selected["legacy_generation_path"] == registered[0]["path"]


def test_running_worker_loss_is_persistent_and_requires_manual_resume(
    management: ManagementService,
) -> None:
    from codalith.indexing.shared import SharedPool

    plan = planned(management, ["5.7.4"])
    job = management.create_job(plan["plan_id"], "lost-worker")
    assert management.catalog.claim_job(job["job_id"], job["attempt"])
    management.catalog.update_job(
        job["job_id"],
        lambda value: value["items"][0].update(
            status="running",
            phase="embedding",
            completed_units=1,
            total_units=8,
        ),
    )
    restored = ManagementService(management.registry, management.policy, management.config)
    restored.recover()
    interrupted = restored.catalog.get("job", job["job_id"])
    assert interrupted["status"] == "interrupted"
    assert interrupted["items"][0]["status"] == "interrupted"
    assert interrupted["items"][0]["completed_units"] == 1
    assert not restored.run_pending_once()
    assert not SharedPool(management.config.storage_root / "shared", management.policy).snapshots()
    resumed = restored.resume(job["job_id"])
    assert resumed["attempt"] == job["attempt"] + 1
    assert restored.catalog.claim_job(job["job_id"], resumed["attempt"])
    run_job(restored, job["job_id"])
    assert (
        Catalog(management.config.storage_root).get("job", job["job_id"])["status"] == "succeeded"
    )


def test_recovery_reconciles_published_tag_before_job_success_and_resumes_only_unfinished(
    management: ManagementService, monkeypatch: pytest.MonkeyPatch
) -> None:
    from codalith.indexing.shared import SharedPool

    plan = planned(management)
    job = management.create_job(plan["plan_id"], "publication-window")
    assert management.catalog.claim_job(job["job_id"], job["attempt"])
    management.catalog.update_job(
        job["job_id"],
        lambda value: value["items"][0].update(
            status="running",
            phase="publish",
        ),
    )
    pool = SharedPool(management.config.storage_root / "shared", management.policy)
    first = plan["items"][0]
    published = pool.build_git_snapshot(
        management.repository(plan["repo_id"]),
        first["commit"],
        first["target_id"],
        management.profile(plan),
        tag=first["tag"],
        engine_version=first["engine_version"],
    )
    assert management.catalog.get("job", job["job_id"])["items"][0]["snapshot_id"] is None
    restored = ManagementService(management.registry, management.policy, management.config)
    restored.recover()
    recovered = restored.catalog.get("job", job["job_id"])
    assert recovered["status"] == "interrupted" and recovered["completed_tags"] == 1
    assert recovered["items"][0]["status"] == "succeeded"
    assert recovered["items"][0]["snapshot_id"] == published.snapshot_id
    assert recovered["items"][1]["status"] == "interrupted"
    assert not restored.run_pending_once()
    calls: list[str] = []
    original_build = SharedPool.build_git_snapshot

    def record_build(
        self: SharedPool, repository: Any, commit: str, *args: Any, **kwargs: Any
    ) -> Any:
        calls.append(commit)
        return original_build(self, repository, commit, *args, **kwargs)

    monkeypatch.setattr(SharedPool, "build_git_snapshot", record_build)
    resumed = restored.resume(job["job_id"])
    assert restored.catalog.claim_job(job["job_id"], resumed["attempt"])
    run_job(restored, job["job_id"])
    assert calls == [plan["items"][1]["commit"]]
    finished = Catalog(management.config.storage_root).get("job", job["job_id"])
    assert finished["status"] == "succeeded" and finished["completed_tags"] == 2
    assert finished["items"][0]["snapshot_id"] == published.snapshot_id
    assert len(pool.snapshots()) == 2
