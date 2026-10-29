from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from codalith.corpus.git_source import GitRepository
from test_managed_http import TOKEN, managed_server

TOOLS = {
    "codalith_search", "codalith_context", "codalith_read", "codalith_symbol",
    "codalith_graph", "codalith_compare", "codalith_status", "codalith_catalog",
}


def publish_version(client: httpx.Client, database_id: str, tag: str) -> str:
    planned = client.post("/api/admin/build-plans", json={"database_id": database_id, "tags": [tag]})
    assert planned.status_code == 200, planned.text
    submitted = client.post("/api/admin/jobs", json={
        "plan_id": planned.json()["plan_id"], "idempotency_key": f"http-flow-{tag}",
    })
    assert submitted.status_code == 202, submitted.text
    job = submitted.json()
    deadline = time.monotonic() + 30
    while job["status"] not in {"succeeded", "failed", "partial", "interrupted", "cancelled"}:
        assert time.monotonic() < deadline, job
        time.sleep(0.05)
        response = client.get("/api/admin/jobs/" + job["job_id"])
        assert response.status_code == 200, response.text
        job = response.json()
    assert job["status"] == "succeeded", job
    assert job["items"][0]["status"] == "succeeded", job
    return str(job["items"][0]["target_id"])


async def call(mcp: ClientSession, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await mcp.call_tool(name, arguments)
    assert not result.isError, result
    assert result.structuredContent is not None, result
    return result.structuredContent


async def verify_version(
    address: str,
    target: str,
    symbol: str,
    absent_symbol: str,
    commit: str,
    hashes: dict[str, str],
) -> dict[str, Any]:
    async with (
        streamable_http_client(address + "/mcp/targets/" + target) as (read, write, _),
        ClientSession(read, write) as mcp,
    ):
        initialized = await mcp.initialize()
        assert initialized.serverInfo.name == "codalith"
        assert f"Default target: {target}" in (initialized.instructions or "")
        tools = await mcp.list_tools()
        assert len(tools.tools) == 8 and {tool.name for tool in tools.tools} == TOOLS
        catalog = await call(mcp, "codalith_catalog", {})
        assert len(catalog["versions"]) == 1
        version = catalog["versions"][0]
        assert (version["target_id"], version["source_commit"], version["ready"]) == (target, commit, True)
        status = await call(mcp, "codalith_status", {})
        assert status["target"] == target and status["ready"]
        generation = status["corpora"][0]["generation_id"]

        context = await call(mcp, "codalith_context", {"query": symbol})
        assert not context["degraded"] and not context["warnings"]
        assert any(symbol in source["text"] for source in context["sources"])
        for source in context["sources"]:
            evidence = await call(mcp, "codalith_read", {"uri": source["uri"]})
            for field in ("uri", "text", "sha256", "indexed_sha256", "generation_id", "revision"):
                assert evidence[field] == source[field]
            assert evidence["corpus_id"] == target and evidence["revision"] == commit
            assert evidence["generation_id"] == generation
            assert evidence["sha256"] == evidence["indexed_sha256"] == hashes[evidence["path"]]
            assert not evidence["stale"] and not evidence["truncated"]

        definitions = await call(mcp, "codalith_symbol", {"query": symbol})
        assert len(definitions["definitions"]) == 1
        definition = definitions["definitions"][0]
        assert definition["corpus_id"] == target and definition["generation_id"] == generation
        absent = await call(mcp, "codalith_symbol", {"query": absent_symbol})
        assert not absent["definitions"]
        graph = await call(mcp, "codalith_graph", {"root_uri": definition["uri"], "direction": "outgoing"})
        assert not graph["truncated"] and not graph["warnings"]
        assert graph["nodes"] and all(
            node["corpus_id"] == target and node["revision"] == commit
            and node["generation_id"] == generation for node in graph["nodes"]
        )
        edge = next(edge for edge in graph["edges"] if edge["target_name"].split(".")[-1] == "stable")
        assert edge["resolution"] == "resolved"
        assert edge["target_uri"].startswith(f"codalith://{target}/symbol/")
        edge_source = await call(mcp, "codalith_read", {"uri": edge["evidence_uri"]})
        assert "stable()" in edge_source["text"]
        assert edge_source["sha256"] == edge_source["indexed_sha256"] == hashes["version.py"]
        assert edge_source["generation_id"] == generation and not edge_source["stale"]
        full_source = await call(mcp, "codalith_read", {"uri": f"codalith://{target}/source/version.py#L1-L20"})
        assert symbol in full_source["text"] and absent_symbol not in full_source["text"]
        return {"version": version, "source": full_source, "definition": definition, "graph": graph}


def test_real_http_keeps_citations_and_graphs_bound_after_appending_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with (
        managed_server(tmp_path, monkeypatch) as (address, _management, pool),
        httpx.Client(base_url=address, trust_env=False, timeout=20) as client,
    ):
        initial_readiness = client.get("/readyz")
        assert initial_readiness.status_code == 503
        assert client.get("/api/admin/config").status_code == 401
        client.headers["Authorization"] = "Bearer " + TOKEN
        registered = client.post("/api/admin/repositories", json={"root_id": "0", "path": "repo"})
        assert registered.status_code == 200, registered.text
        database = client.post("/api/admin/databases", json={
            "name": "HTTP citation fixture", "profile_id": "python", "repo_id": registered.json()["repo_id"],
        })
        assert database.status_code == 200, database.text
        database_id = database.json()["database_id"]
        repo = GitRepository(tmp_path / "repo")
        commits = {tag: repo.resolve_tag(tag).commit for tag in ("5.0.0", "5.1.0")}
        hashes = {
            tag: {item.path: hashlib.sha256(repo.read_blob(item.blob_oid)).hexdigest() for item in repo.files(commit)}
            for tag, commit in commits.items()
        }
        first = publish_version(client, database_id, "5.0.0")
        before = anyio.run(verify_version, address, first, "first_only", "second_only", commits["5.0.0"], hashes["5.0.0"])
        first_snapshot = pool.snapshot(first)
        second = publish_version(client, database_id, "5.1.0")
        assert first != second and pool.snapshot(first) == first_snapshot
        readiness = client.get("/readyz")
        assert readiness.status_code == 503 and readiness.json() == initial_readiness.json()
        catalog = client.get("/api/catalog").json()["versions"]
        assert {row["target_id"]: row["source_commit"] for row in catalog} == {
            first: commits["5.0.0"], second: commits["5.1.0"],
        }
        assert all(row["ready"] for row in catalog)
        (tmp_path / "repo" / "version.py").write_text("uncommitted worktree damage", encoding="utf-8")
        after = anyio.run(verify_version, address, first, "first_only", "second_only", commits["5.0.0"], hashes["5.0.0"])
        assert after == before
        anyio.run(verify_version, address, second, "second_only", "first_only", commits["5.1.0"], hashes["5.1.0"])

        async def verify_unbound_catalog() -> None:
            async with (
                streamable_http_client(address + "/mcp") as (read, write, _),
                ClientSession(read, write) as mcp,
            ):
                await mcp.initialize()
                result = await call(mcp, "codalith_catalog", {})
                assert {row["target_id"] for row in result["versions"]} == {first, second}

        anyio.run(verify_unbound_catalog)
