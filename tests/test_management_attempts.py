from __future__ import annotations

import io
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from codalith.management.catalog import Catalog, timestamp
from codalith.management.service import ManagementService
from codalith.management.worker import run_job
from test_management import management as management
from test_management import planned


def catalog_job(catalog: Catalog) -> dict[str, Any]:
    return catalog.create_job(
        {
            "job_id": "job-attempt",
            "plan_id": "plan-attempt",
            "status": "running",
            "attempt": 2,
            "updated_at": timestamp(),
            "items": [],
        },
        "attempt-request",
    )


@pytest.mark.parametrize(
    ("attempt", "statuses"),
    [(1, frozenset({"running"})), (2, frozenset({"queued"}))],
)
def test_rejected_job_update_does_not_call_mutator_or_append_an_event(
    tmp_path: Path, attempt: int, statuses: frozenset[str]
) -> None:
    catalog = Catalog(tmp_path)
    job = catalog_job(catalog)
    events = catalog.events(job["job_id"])

    def forbidden_update(value: dict[str, Any]) -> None:
        pytest.fail("A stale writer must not enter the mutator")

    result = Catalog(tmp_path).update_job(
        job["job_id"],
        forbidden_update,
        event="stale-write",
        expected_attempt=attempt,
        allowed_statuses=statuses,
    )
    assert result == job == catalog.get("job", job["job_id"])
    assert catalog.events(job["job_id"]) == events


def test_competing_conditional_updates_are_atomic_across_catalog_connections(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path)
    job = catalog_job(catalog)

    def finish(status: str) -> dict[str, Any]:
        return Catalog(tmp_path).update_job(
            job["job_id"],
            lambda value: value.update(status=status),
            event=status,
            expected_attempt=2,
            allowed_statuses=frozenset({"running"}),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(finish, ("failed", "succeeded")))
    assert results[0] == results[1] == catalog.get("job", job["job_id"])
    assert len(catalog.events(job["job_id"])) == 2


@pytest.mark.parametrize("stopping", [False, True])
@pytest.mark.parametrize("resume_at", ["before_exit", "before_reaper_write"])
def test_old_process_reaping_preserves_and_executes_the_resumed_attempt(
    management: ManagementService,
    monkeypatch: pytest.MonkeyPatch,
    stopping: bool,
    resume_at: str,
) -> None:
    job = management.create_job(planned(management, ["5.7.4"])["plan_id"], "reaper-race")
    original_update = management.catalog.update_job
    original_popen = subprocess.Popen
    resumed: dict[str, Any] = {}
    resumed_events: list[dict[str, Any]] = []
    spawned_attempts: list[int] = []

    def resume() -> None:
        nonlocal resumed, resumed_events
        original_update(job["job_id"], lambda value: value.update(status="failed", phase="failed"))
        resumed = management.resume(job["job_id"])
        resumed_events = management.catalog.events(job["job_id"])

    def update_with_race(*args: Any, **kwargs: Any) -> dict[str, Any]:
        if kwargs.get("event") == "interrupted" and resume_at == "before_reaper_write":
            resume()
        return original_update(*args, **kwargs)

    class Process:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.stdin = io.BytesIO()
            self.stderr = io.BytesIO()
            self.returncode: int | None = None
            self.polled = False
            self.attempt = management.catalog.get("job", job["job_id"])["attempt"]
            spawned_attempts.append(self.attempt)

        def poll(self) -> int | None:
            if not self.polled:
                self.polled = True
                if self.attempt == 1:
                    if resume_at == "before_exit":
                        resume()
                    if stopping:
                        management._stop.set()
                    return None
                run_job(management, job["job_id"])
            self.returncode = 0
            return self.returncode

        def wait(self, timeout: float) -> int:
            self.returncode = 0
            return self.returncode

    def spawn(args: list[str], **kwargs: Any) -> Any:
        if "codalith.management.worker" in args:
            return Process(args, **kwargs)
        return original_popen(args, **kwargs)

    monkeypatch.setattr(management.catalog, "update_job", update_with_race)
    monkeypatch.setattr("codalith.management.service.subprocess.Popen", spawn)
    assert management.run_pending_once()
    assert management.catalog.get("job", job["job_id"]) == resumed
    assert management.catalog.events(job["job_id"]) == resumed_events
    assert resumed["status"] == "queued" and resumed["attempt"] == 2
    management._stop.clear()
    assert management.run_pending_once()
    assert spawned_attempts == [1, 2]
    assert management.catalog.get("job", job["job_id"])["status"] == "succeeded"
    assert not management.run_pending_once()


def test_recovery_does_not_overwrite_a_resume_after_listing_jobs(
    management: ManagementService, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = management.create_job(planned(management, ["5.7.4"])["plan_id"], "recovery-race")
    original_list = management.catalog.list
    resumed: dict[str, Any] = {}
    resumed_events: list[dict[str, Any]] = []

    def list_then_resume(kind: str) -> list[dict[str, Any]]:
        nonlocal resumed, resumed_events
        rows = original_list(kind)
        if kind == "job":
            management.cancel(job["job_id"])
            resumed = management.resume(job["job_id"])
            resumed_events = management.catalog.events(job["job_id"])
        return rows

    monkeypatch.setattr(management.catalog, "list", list_then_resume)
    management.recover()
    assert management.catalog.get("job", job["job_id"]) == resumed
    assert management.catalog.events(job["job_id"]) == resumed_events


@pytest.mark.parametrize("publish", [False, True])
def test_old_worker_progress_and_result_do_not_touch_a_resumed_attempt(
    management: ManagementService, monkeypatch: pytest.MonkeyPatch, publish: bool
) -> None:
    job = management.create_job(planned(management, ["5.7.4"])["plan_id"], "worker-race")
    assert management.catalog.claim_job(job["job_id"], job["attempt"])
    resumed: dict[str, Any] = {}
    resumed_events: list[dict[str, Any]] = []

    def build_then_resume(*args: Any, **kwargs: Any) -> SimpleNamespace:
        nonlocal resumed, resumed_events
        management.catalog.update_job(
            job["job_id"], lambda value: value.update(status="interrupted", phase="interrupted")
        )
        resumed = management.resume(job["job_id"])
        resumed_events = management.catalog.events(job["job_id"])
        kwargs["progress"]({"stage": "publishing", "completed": 1, "total": 1})
        assert kwargs["cancelled"]()
        if not publish:
            raise RuntimeError("Previous attempt failed after the resume")
        return SimpleNamespace(
            snapshot_id="old-snapshot",
            generation_id="old-generation",
            counts={},
            reused_vectors=0,
            new_vectors=1,
            statistics={},
        )

    monkeypatch.setattr("codalith.indexing.shared.SharedPool.build_git_snapshot", build_then_resume)
    run_job(management, job["job_id"])
    assert management.catalog.get("job", job["job_id"]) == resumed
    assert management.catalog.events(job["job_id"]) == resumed_events


def test_old_worker_initialization_failure_does_not_touch_a_resumed_attempt(
    management: ManagementService, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = management.create_job(planned(management, ["5.7.4"])["plan_id"], "profile-race")
    assert management.catalog.claim_job(job["job_id"], job["attempt"])
    resumed: dict[str, Any] = {}
    resumed_events: list[dict[str, Any]] = []

    def profile_then_resume(plan: dict[str, Any]) -> None:
        nonlocal resumed, resumed_events
        management.catalog.update_job(
            job["job_id"], lambda value: value.update(status="interrupted", phase="interrupted")
        )
        resumed = management.resume(job["job_id"])
        resumed_events = management.catalog.events(job["job_id"])
        raise ValueError("Previous attempt profile validation failed")

    monkeypatch.setattr(management, "profile", profile_then_resume)
    run_job(management, job["job_id"])
    assert management.catalog.get("job", job["job_id"]) == resumed
    assert management.catalog.events(job["job_id"]) == resumed_events
