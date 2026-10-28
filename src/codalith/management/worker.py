"""One supervised import process with persistent progress and cooperative cancellation."""

from __future__ import annotations

import json
import os
import select
import sys
import threading
import time
from pathlib import Path
from typing import Any

from codalith.corpus.registry import CorpusRegistry
from codalith.corpus.source_policy import SourcePolicy
from codalith.management.config import ManagementConfig
from codalith.management.service import ManagementService


def run_job(
    service: ManagementService, job_id: str, disconnected: threading.Event | None = None
) -> None:
    from codalith.indexing.shared import SharedPool

    catalog = service.catalog
    job = catalog.get("job", job_id)
    attempt = job["attempt"]
    plan = catalog.get("plan", job["plan_id"])
    pool = SharedPool(service.config.storage_root / "shared", service.policy)

    def cancelled() -> bool:
        current = catalog.get("job", job_id)
        return (
            bool(disconnected is not None and disconnected.is_set())
            or current["attempt"] != attempt
            or current["status"] in {"cancel_requested", "cancelled", "interrupted"}
        )

    try:
        profile = service.profile(plan)
        repository = service.repository(job["repo_id"])

        def run_item(index: int, item: dict[str, Any]) -> bool:
            last_at = 0.0
            last_phase = ""

            def progress(event: dict[str, object]) -> None:
                nonlocal last_at, last_phase
                phase = str(event.get("stage", "building"))
                now = time.monotonic()
                if phase == last_phase and now - last_at < 0.5:
                    return
                last_at, last_phase = now, phase

                def update(value: dict[str, Any]) -> None:
                    if value["attempt"] != attempt or value["status"] in {
                        "interrupted",
                        "cancelled",
                    }:
                        return
                    child = value["items"][index]
                    child.update(
                        status="running",
                        phase=event.get("stage", "building"),
                        completed_units=event.get("completed", 0),
                        total_units=event.get("total"),
                        unit=event.get("unit", "files"),
                        message=str(event.get("message", ""))[:1000],
                    )
                    value["phase"] = child["phase"]

                catalog.update_job(job_id, update, event="progress")

            progress({"stage": "starting", "completed": 0, "total": None, "unit": "files"})
            try:
                result = pool.build_git_snapshot(
                    repository,
                    item["commit"],
                    item["target_id"],
                    profile,
                    tag=item["tag"],
                    engine_version=item["engine_version"],
                    progress=progress,
                    cancelled=cancelled,
                    legacy_generation=Path(plan["legacy_generation_path"])
                    if plan.get("legacy_generation_path")
                    else None,
                )
            except Exception as exc:
                if cancelled():
                    return False

                def failure(value: dict[str, Any], message: str = service.safe_error(exc)) -> None:
                    if value["attempt"] != attempt:
                        return
                    value["items"][index].update(status="failed", phase="failed", error=message)

                catalog.update_job(job_id, failure, event="tag_failed")
                return True

            def success(value: dict[str, Any]) -> None:
                if value["attempt"] != attempt:
                    return
                value["items"][index].update(
                    status="succeeded",
                    phase="published",
                    snapshot_id=result.snapshot_id,
                    generation_id=result.generation_id,
                    completed_units=1,
                    total_units=1,
                    unit="snapshots",
                    counts=result.counts,
                    reused_vectors=result.reused_vectors,
                    new_vectors=result.new_vectors,
                    statistics=result.statistics,
                )
                value["completed_tags"] = sum(
                    child["status"] == "succeeded" for child in value["items"]
                )
                value["completed_units"] = value["completed_tags"]

            catalog.update_job(job_id, success, event="published")
            return True

        for index, item in enumerate(job["items"]):
            if item["status"] == "succeeded":
                continue
            if cancelled() or not run_item(index, item):
                break

        def finish(value: dict[str, Any]) -> None:
            if value["attempt"] != attempt:
                return
            succeeded = sum(child["status"] == "succeeded" for child in value["items"])
            if succeeded == len(value["items"]):
                status = "succeeded"
            elif disconnected is not None and disconnected.is_set():
                status = "interrupted"
            elif value["status"] in {"cancel_requested", "cancelled"}:
                status = "cancelled"
            elif value["status"] == "interrupted":
                status = "interrupted"
            else:
                status = "partial" if succeeded else "failed"
            value.update(
                status=status,
                phase=status,
                completed_tags=succeeded,
                completed_units=succeeded,
                error=next(
                    (child["error"] for child in value["items"] if child.get("error")), None
                ),
            )
            for child in value["items"]:
                if child["status"] in {"queued", "running"}:
                    child.update(
                        status="interrupted" if status == "interrupted" else "cancelled",
                        phase="interrupted" if status == "interrupted" else "cancelled",
                    )

        catalog.update_job(job_id, finish, event="finished")
    except Exception as exc:

        def fail(value: dict[str, Any], message: str = service.safe_error(exc)) -> None:
            if value["attempt"] == attempt:
                value.update(status="failed", phase="failed", error=message)

        catalog.update_job(job_id, fail, event="failed")


def main() -> int:
    registry = CorpusRegistry.from_file(sys.argv[1])
    config = ManagementConfig.from_registry(registry.path)
    raw = json.loads(os.environ["CODALITH_JOB_POLICY"])
    raw["deny_globs"] = tuple(raw["deny_globs"])
    service = ManagementService(registry, SourcePolicy(**raw), config)
    disconnected = threading.Event()
    stopped = threading.Event()
    monitor = threading.Thread(target=_watch_parent, args=(disconnected, stopped), daemon=True)
    monitor.start()
    try:
        run_job(service, sys.argv[2], disconnected)
    finally:
        stopped.set()
        monitor.join(timeout=1)
    return 0


def _watch_parent(disconnected: threading.Event, stopped: threading.Event) -> None:
    descriptor = sys.stdin.fileno()
    if sys.platform == "win32":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        peek = ctypes.WinDLL("kernel32", use_last_error=True).PeekNamedPipe
        peek.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.LPDWORD,
            wintypes.LPDWORD,
            wintypes.LPDWORD,
        ]
        peek.restype = wintypes.BOOL
        handle = wintypes.HANDLE(msvcrt.get_osfhandle(descriptor))
        while not stopped.is_set():
            if not peek(handle, None, 0, None, None, None):
                disconnected.set()
                return
            stopped.wait(0.2)
    else:
        while not stopped.is_set():
            readable, _, _ = select.select([descriptor], [], [], 0.2)
            if readable and not os.read(descriptor, 1):
                disconnected.set()
                return


if __name__ == "__main__":
    raise SystemExit(main())
