from __future__ import annotations

from copy import deepcopy

import pytest

from codalith.indexing.shared import SharedPool
from codalith.management.service import ManagementService
from codalith.management.worker import run_job
from test_management import management as management
from test_management import planned


def test_engine_upgrade_creates_a_new_plan_and_preserves_the_old_snapshot(
    management: ManagementService, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_plan = planned(management, ["5.7.4"])
    first_job = management.create_job(first_plan["plan_id"], "before-upgrade")
    assert management.catalog.claim_job(first_job["job_id"], first_job["attempt"])
    run_job(management, first_job["job_id"])
    assert management.catalog.get("job", first_job["job_id"])["status"] == "succeeded"
    pool = SharedPool(management.config.storage_root / "shared", management.policy)
    first_target = first_plan["items"][0]["target_id"]
    first_snapshot = pool.snapshot(first_target)
    assert first_snapshot is not None

    next_engine = first_plan["profile"]["semantic_engine_version"] + "-next"
    monkeypatch.setattr("codalith.management.service.ENGINE_VERSION", next_engine)
    monkeypatch.setattr("codalith.indexing.shared.ENGINE_VERSION", next_engine)
    with pytest.raises(ValueError, match="Semantic engine changed.*create a new plan"):
        management.profile(first_plan)
    next_plan = management.create_plan(first_plan["database_id"], ["5.7.4"])
    assert next_plan["plan_id"] != first_plan["plan_id"]
    assert next_plan["items"][0]["target_id"] != first_target
    assert next_plan["profile"]["semantic_engine_version"] == next_engine
    assert management.create_plan(first_plan["database_id"], ["5.7.4"]) == next_plan

    next_job = management.create_job(next_plan["plan_id"], "after-upgrade")
    assert management.catalog.claim_job(next_job["job_id"], next_job["attempt"])
    run_job(management, next_job["job_id"])
    assert management.catalog.get("job", next_job["job_id"])["status"] == "succeeded"
    assert pool.snapshot(first_target) == first_snapshot
    assert len(pool.snapshots()) == 2


def test_worker_rejects_a_plan_without_a_semantic_engine_contract(
    management: ManagementService,
) -> None:
    plan = deepcopy(planned(management, ["5.7.4"]))
    del plan["profile"]["semantic_engine_version"]
    management.catalog.put("plan", plan["plan_id"], plan)
    job = management.create_job(plan["plan_id"], "legacy-plan")
    assert management.catalog.claim_job(job["job_id"], job["attempt"])
    run_job(management, job["job_id"])
    result = management.catalog.get("job", job["job_id"])
    assert result["status"] == "failed"
    assert "Semantic engine changed after planning; create a new plan" in result["error"]
    assert not SharedPool(management.config.storage_root / "shared", management.policy).snapshots()
