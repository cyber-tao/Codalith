from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import anyio
import httpx
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from codalith.corpus.managed_registry import ManagedRegistry
from codalith.corpus.registry import CorpusRegistry
from codalith.corpus.source_policy import SourcePolicy
from codalith.indexing.shared import SharedPool, SharedQueryService
from codalith.management.config import ManagementConfig
from codalith.management.service import ManagementService
from codalith.mcp.http import HTTPConfig, create_http_app

TOKEN = "test-only-administrator-token"


def make_repo(root: Path) -> None:
    root.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(root), "-c", "user.name=Fixture", "-c", "user.email=fixture@local", *args], check=True, capture_output=True)

    git("init", "-q")
    (root / "stable.py").write_text("def stable():\n    return 'unchanged'\n")
    (root / "version.py").write_text("def first_only():\n    return 'first-version'\n")
    git("add", "stable.py", "version.py")
    git("commit", "-qm", "first")
    git("tag", "5.0.0")
    (root / "version.py").write_text("def second_only():\n    return 'second-version'\n")
    git("add", "version.py")
    git("commit", "-qm", "second")
    git("tag", "-a", "5.1.0", "-m", "second version")


@contextmanager
def managed_server(root: Path, monkeypatch):
    repo = root / "repo"
    make_repo(repo)
    monkeypatch.setenv("CODALITH_ADMIN_TOKEN", TOKEN)
    config_path = root / "registry.toml"
    config_path.write_text(
        f'schema_version = 2\n[management]\nenabled = true\nstorage_root = {json.dumps((root / "managed").as_posix())}\nsource_roots = [{json.dumps(root.as_posix())}]\n'
        '[[management.profiles]]\nid = "python"\nadapter = "python"\nembedding_provider = "fake"\ninclude_extensions = [".py"]\n', encoding="utf-8",
    )
    registry = CorpusRegistry.from_file(config_path)
    policy = SourcePolicy.from_file("configs/source-policy.toml")
    config = ManagementConfig.from_registry(config_path)
    management = ManagementService(registry, policy, config)
    pool = SharedPool(config.storage_root / "shared", policy)
    dynamic = ManagedRegistry(registry, pool.published_corpora)
    service = SharedQueryService(dynamic, policy, pool)
    management.on_publish = dynamic.refresh
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    app = create_http_app(service, HTTPConfig(port=port), management=management)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    address = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(trust_env=False, timeout=2) as client:
            for _ in range(150):
                try:
                    if client.get(address + "/healthz").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.02)
            else:
                raise AssertionError("Managed HTTP service did not start")
        yield address, management, pool
    finally:
        server.should_exit = True
        thread.join(timeout=20)
        listener.close()
        assert not thread.is_alive()


def test_real_http_multitag_job_publishes_and_queries_shared_versions(tmp_path, monkeypatch):
    with managed_server(tmp_path, monkeypatch) as (address, management, pool), httpx.Client(base_url=address, trust_env=False, timeout=20) as client:
        assert client.get("/readyz").status_code == 503
        assert client.get("/api/admin/config").status_code == 401
        client.headers["Authorization"] = "Bearer " + TOKEN
        profiles = client.get("/api/admin/config").json()["profiles"]
        assert profiles[0]["profile_id"] == "python"
        repo = client.post("/api/admin/repositories", json={"root_id": "0", "path": "repo"})
        assert repo.status_code == 200, repo.text
        repo_id = repo.json()["repo_id"]
        tags = client.get(f"/api/admin/repositories/{repo_id}/tags").json()["items"]
        assert {item["tag"] for item in tags} == {"5.0.0", "5.1.0"}
        database = client.post("/api/admin/databases", json={"name": "Engine demo", "profile_id": "python", "repo_id": repo_id}).json()
        plan = client.post("/api/admin/build-plans", json={"database_id": database["database_id"], "tags": ["5.0.0", "5.1.0"]})
        assert plan.status_code == 200, plan.text
        submitted = client.post("/api/admin/jobs", json={"plan_id": plan.json()["plan_id"], "idempotency_key": "integration-once"})
        assert submitted.status_code == 202, submitted.text
        job = submitted.json()
        for _ in range(600):
            job = client.get("/api/admin/jobs/" + job["job_id"]).json()
            if job["status"] in {"succeeded", "failed", "partial", "interrupted"}:
                break
            time.sleep(0.05)
        assert job["status"] == "succeeded", job
        assert all(item["status"] == "succeeded" for item in job["items"])
        catalog = client.get("/api/catalog").json()["versions"]
        assert len(catalog) == 2
        assert all(item["ready"] for item in catalog)
        duplicate = client.post("/api/admin/jobs", json={"plan_id": plan.json()["plan_id"], "idempotency_key": "integration-once"}).json()
        assert duplicate["job_id"] == job["job_id"]
        assert client.get("/api/admin/jobs/" + job["job_id"] + "/events").json()["items"]
        snapshots = pool.snapshots()
        assert len({item["semantic_namespace"] for item in snapshots}) == 1
        assert sum(item["new_vectors"] for item in snapshots) > 0
        assert any(item["reused_vectors"] for item in snapshots)
        target = job["items"][0]["target_id"]
        (tmp_path / "repo" / "version.py").write_text("worktree is no longer a source of truth")

        async def verify_mcp() -> None:
            async with streamable_http_client(address + "/mcp/targets/" + target) as (read, write, _), ClientSession(read, write) as mcp:
                await mcp.initialize()
                result = await mcp.call_tool("codalith_context", {"query": "first_only"})
                assert not result.isError, result
                sources = result.structuredContent["sources"]
                assert sources and any("first_only" in source["text"] for source in sources)
                assert all(source["corpus_id"] == target and not source["stale"] for source in sources)
                evidence = await mcp.call_tool("codalith_read", {"uri": sources[0]["uri"]})
                assert evidence.structuredContent["sha256"] == sources[0]["sha256"]

        anyio.run(verify_mcp)
