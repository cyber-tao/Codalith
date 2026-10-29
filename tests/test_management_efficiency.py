from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from codalith.management.catalog import Catalog
from codalith.management.config import ManagementConfig
from codalith.management.service import ManagementService
from conftest import EnvironmentFactory


@pytest.fixture
def service(tmp_path: Path, environment_factory: EnvironmentFactory) -> ManagementService:
    environment = environment_factory(semantic=False)
    config = ManagementConfig(True, tmp_path / "managed", (tmp_path,), "isolated-admin-token")
    return ManagementService(environment.registry, environment.policy, config)


def test_catalog_closes_connections_and_rolls_back_failed_updates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connections: list[sqlite3.Connection] = []
    original = sqlite3.connect

    def connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        connection = original(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    catalog = Catalog(tmp_path)
    catalog.put("job", "example", {"items": [], "status": "queued"})
    for _ in range(20):
        assert catalog.get("job", "example")["status"] == "queued"
    with pytest.raises(KeyError):
        catalog.get("job", "missing")

    def fail(job: dict[str, Any]) -> None:
        job["status"] = "running"
        raise RuntimeError("Interrupted update")

    with pytest.raises(RuntimeError, match="Interrupted update"):
        catalog.update_job("example", fail)
    assert catalog.get("job", "example")["status"] == "queued"
    assert catalog.events("example") == []
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_event_tail_returns_latest_page_then_incremental_events(service: ManagementService) -> None:
    service.catalog.put("job", "example", {"items": []})
    with service.catalog.connect() as connection:
        connection.executemany(
            "INSERT INTO events(job_id,at,type,payload) VALUES(?,?,?,?)",
            [("example", "2026-10-29T00:00:00Z", "progress", "{}") for _ in range(1001)],
        )
    client = TestClient(Starlette(routes=service.routes()))
    client.headers["Authorization"] = "Bearer " + service.config.admin_token
    path = "/api/admin/jobs/example/events"
    initial = client.get(path, params={"tail": 500}).json()["items"]
    assert [item["seq"] for item in initial] == list(range(502, 1002))
    legacy = client.get(path).json()["items"]
    assert [item["seq"] for item in legacy] == list(range(1, 501))
    service.catalog.update_job("example", lambda value: value.update(status="succeeded"))
    following = client.get(path, params={"after_seq": 1001}).json()["items"]
    assert [item["seq"] for item in following] == [1002]
    assert client.get(path, params={"after_seq": 1002}).json()["items"] == []
    for params in ({"tail": 500, "after_seq": 0}, {"tail": 0}, {"tail": 501}, {"after_seq": -1}):
        assert client.get(path, params=params).status_code == 400


def test_space_cache_refreshes_after_independent_publication_and_explicit_request(
    service: ManagementService, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared = service.config.storage_root / "shared"
    shared.mkdir()
    path = shared / "catalog.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE snapshots(created_at TEXT NOT NULL)")
    connection.close()
    measured = 0

    def measure() -> dict[str, Any]:
        nonlocal measured
        measured += 1
        return {"physical_bytes": measured, "breakdown": {"total_bytes": measured}}

    monkeypatch.setattr(service, "_measure_space", measure)
    first = service.space()
    assert first["measured_at"]
    first["breakdown"]["total_bytes"] = -1
    for _ in range(5):
        assert service.space()["breakdown"]["total_bytes"] == 1
    assert measured == 1
    writer = sqlite3.connect(path)
    try:
        writer.execute("INSERT INTO snapshots VALUES('2026-10-29T00:00:00Z')")
        writer.commit()
    finally:
        writer.close()
    assert service.space()["physical_bytes"] == 2
    database = service.create_database("Example", "sample")
    client = TestClient(Starlette(routes=service.routes()))
    client.headers["Authorization"] = "Bearer " + service.config.admin_token
    endpoint = f"/api/admin/databases/{database['database_id']}/space"
    assert client.get(endpoint).json()["physical_bytes"] == 2
    assert client.get(endpoint, params={"refresh": "true"}).json()["physical_bytes"] == 3
    assert client.get(endpoint).json()["physical_bytes"] == 3
    assert client.get(endpoint, params={"refresh": "invalid"}).status_code == 400
    assert measured == 3


def test_concurrent_space_refreshes_share_one_measurement(
    service: ManagementService, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()
    release = threading.Event()
    all_waiting = threading.Event()
    original_wait = service._space_condition.wait
    waiting = 0
    measured = 0

    def wait(timeout: float | None = None) -> bool:
        nonlocal waiting
        waiting += 1
        if waiting == 3:
            all_waiting.set()
        return original_wait(timeout)

    def measure() -> dict[str, Any]:
        nonlocal measured
        measured += 1
        started.set()
        assert release.wait(5)
        return {"physical_bytes": measured}

    monkeypatch.setattr(service._space_condition, "wait", wait)
    monkeypatch.setattr(service, "_measure_space", measure)
    with ThreadPoolExecutor(max_workers=4) as executor:
        first = executor.submit(service.space, refresh=True)
        assert started.wait(5)
        others = [executor.submit(service.space, refresh=True) for _ in range(3)]
        try:
            assert all_waiting.wait(5)
        finally:
            release.set()
        results = [future.result() for future in [first, *others]]
    assert measured == 1
    assert all(result == results[0] for result in results)


def test_failed_space_measurement_does_not_poison_refresh(
    service: ManagementService, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail() -> dict[str, Any]:
        raise OSError("Measurement unavailable")

    monkeypatch.setattr(service, "_measure_space", fail)
    with pytest.raises(OSError, match="Measurement unavailable"):
        service.space()
    monkeypatch.setattr(service, "_measure_space", lambda: {"physical_bytes": 7})
    assert service.space()["physical_bytes"] == 7


def test_concurrent_failed_refresh_does_not_return_the_previous_cache_as_success(
    service: ManagementService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "_measure_space", lambda: {"physical_bytes": 1})
    assert service.space()["physical_bytes"] == 1
    started = threading.Event()
    release = threading.Event()
    waiting = threading.Event()
    original_wait = service._space_condition.wait
    measurements = 0

    def wait(timeout: float | None = None) -> bool:
        waiting.set()
        return original_wait(timeout)

    def fail() -> dict[str, Any]:
        nonlocal measurements
        measurements += 1
        started.set()
        assert release.wait(5)
        raise OSError("Refresh failed")

    monkeypatch.setattr(service._space_condition, "wait", wait)
    monkeypatch.setattr(service, "_measure_space", fail)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(service.space, refresh=True)
        assert started.wait(5)
        second = executor.submit(service.space, refresh=True)
        try:
            assert waiting.wait(5)
        finally:
            release.set()
        for result in (first, second):
            with pytest.raises(OSError, match="Refresh failed"):
                result.result()
    assert measurements == 1
    monkeypatch.setattr(service, "_measure_space", lambda: {"physical_bytes": 2})
    assert service.space(refresh=True)["physical_bytes"] == 2
