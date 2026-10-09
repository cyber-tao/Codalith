from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import anyio
import httpx
import pytest
import uvicorn
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient

from codalith import errors
from codalith.mcp.http import HTTPConfig, create_http_app
from codalith.mcp.server import _error_payload
from conftest import TestEnvironment

DOCS = Path(__file__).parents[1] / "docs"


@pytest.mark.parametrize(
    ("error", "code", "retryable"),
    [
        (errors.URIResolutionError("bad uri"), "invalid_uri", False),
        (errors.IndexMissingError("none"), "index_missing", False),
        (errors.IndexInvalidError("drift"), "index_invalid", False),
        (errors.SymbolNotFoundError("stale"), "symbol_not_found", False),
        (errors.RetrievalError("timeout"), "retrieval_failed", True),
        (ValueError("limit must be between 1 and 50"), "invalid_arguments", False),
        (RuntimeError("D:/private/path exploded"), "internal", True),
    ],
)
def test_tool_errors_follow_the_documented_contract(
    error: Exception,
    code: str,
    retryable: bool,
) -> None:
    payload = _error_payload(error)["error"]
    assert (payload["code"], payload["retryable"]) == (code, retryable)
    if code == "internal":
        assert payload["message"] == "Internal tool error"


def test_every_error_code_is_documented() -> None:
    def subclasses(cls: type[errors.CodalithError]) -> set[type[errors.CodalithError]]:
        found = set(cls.__subclasses__())
        for child in list(found):
            found |= subclasses(child)
        return found

    codes = {cls.code for cls in subclasses(errors.CodalithError)}
    codes |= {errors.INVALID_ARGUMENTS_CODE, errors.INTERNAL_ERROR_CODE}
    documented = (DOCS / "mcp-api.md").read_text(encoding="utf-8")
    assert {code for code in codes if f"`{code}`" not in documented} == set()


@pytest.mark.parametrize("endpoint", ["/", "//mcp", "/../mcp", "/mcp?debug=1"])
def test_http_config_rejects_noncanonical_endpoint(endpoint: str) -> None:
    with pytest.raises(ValueError, match="absolute non-root path"):
        HTTPConfig(endpoint=endpoint).validate()


@contextmanager
def running_http_server(
    environment: TestEnvironment,
    *,
    max_request_bytes: int = 1_048_576,
) -> Iterator[str]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    host, port = listener.getsockname()[:2]
    server = uvicorn.Server(
        uvicorn.Config(
            create_http_app(
                environment.service(),
                HTTPConfig(port=port, max_request_bytes=max_request_bytes),
            ),
            log_level="warning",
        )
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        name="codalith-test-http",
    )
    thread.start()
    endpoint = f"http://{host}:{port}/mcp"
    health = f"http://{host}:{port}/healthz"
    try:
        with httpx.Client(timeout=1, trust_env=False) as client:
            for _ in range(100):
                try:
                    if client.get(health).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.02)
            else:
                raise RuntimeError("Codalith test server did not start")
        yield endpoint
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()


async def _exercise_http_contract(endpoint: str) -> None:
    async with streamable_http_client(endpoint) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "codalith"
            tools = await session.list_tools()
            assert [tool.name for tool in tools.tools] == [
                "codalith_search",
                "codalith_context",
                "codalith_read",
                "codalith_symbol",
                "codalith_graph",
                "codalith_compare",
                "codalith_status",
            ]
            status = await session.call_tool("codalith_status", {})
            assert not status.isError
            assert status.structuredContent["ready"] is True  # type: ignore[index]
            context = await session.call_tool(
                "codalith_context",
                {"query": "Where is CachedValue created?", "target": "sample"},
            )
            assert not context.isError
            assert context.structuredContent["sources"]  # type: ignore[index]
            invalid = await session.call_tool(
                "codalith_status",
                {"unexpected": True},
            )
            assert invalid.isError
            resources = await session.list_resources()
            assert [str(item.uri) for item in resources.resources] == [
                "codalith://sample/status"
            ]
            templates = await session.list_resource_templates()
            assert len(templates.resourceTemplates) == 2
    dashboard_url = endpoint.removesuffix("/mcp") + "/api/dashboard/snapshot"
    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
        dashboard = await client.get(
            dashboard_url,
            params={"range": "1h", "target": "sample"},
        )
    assert dashboard.status_code == 200
    payload = dashboard.json()
    assert payload["service"]["ready"] is True
    assert payload["summary"]["total_queries"] >= 2
    assert {item["name"] for item in payload["tools"]} >= {
        "codalith_status",
        "codalith_context",
    }
    assert payload["corpora"][0]["corpus_id"] == "sample"


def test_streamable_http_uses_the_official_mcp_contract(
    semantic_environment: TestEnvironment,
) -> None:
    with running_http_server(semantic_environment) as endpoint:
        anyio.run(_exercise_http_contract, endpoint)


async def _exercise_stdio(environment: TestEnvironment) -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "codalith.cli.main",
            "--registry",
            str(environment.registry_path),
            "--policy",
            str(environment.policy_path),
            "serve",
            "--transport",
            "stdio",
        ],
        cwd=environment.root,
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(
                "codalith_symbol",
                {"query": "CachedValue", "target": "sample"},
            )
            assert not result.isError
            assert result.structuredContent["definitions"]  # type: ignore[index]


def test_stdio_transport_keeps_protocol_output_clean(
    semantic_environment: TestEnvironment,
) -> None:
    anyio.run(_exercise_stdio, semantic_environment)


def test_stdio_transport_does_not_load_the_dashboard() -> None:
    code = (
        "import sys; import codalith.mcp.stdio; "
        "assert not any(name.startswith('codalith.dashboard') for name in sys.modules)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_http_security_applies_to_every_route(
    semantic_environment: TestEnvironment,
) -> None:
    routes = (
        "/healthz",
        "/readyz",
        "/api/dashboard/snapshot?range=1h&target=sample",
        "/dashboard",
        "/dashboard/index.html",
        "/mcp",
    )
    with running_http_server(semantic_environment) as endpoint:
        base = endpoint.removesuffix("/mcp")
        with httpx.Client(timeout=5, trust_env=False) as client:
            for route in routes:
                rebinding = client.get(base + route, headers={"Host": "evil.example"})
                assert rebinding.status_code == 421, route
                cross_origin = client.get(
                    base + route,
                    headers={"Origin": "https://evil.example"},
                )
                assert cross_origin.status_code == 403, route
            assert client.get(base + "/healthz").status_code == 200
            snapshot = client.get(
                base + "/api/dashboard/snapshot",
                params={"range": "1h", "target": "sample"},
                headers={"Origin": base},
            )
            assert snapshot.status_code == 200


def test_dashboard_assets_are_served_when_built(
    semantic_environment: TestEnvironment,
    tmp_path: Path,
) -> None:
    missing = create_http_app(
        semantic_environment.service(),
        HTTPConfig(),
        dashboard_dir=tmp_path / "not-built",
    )
    with TestClient(missing, base_url="http://127.0.0.1:8765") as client:
        response = client.get("/dashboard", follow_redirects=False)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "dashboard_not_built"
    assets = tmp_path / "dist"
    assets.mkdir()
    (assets / "index.html").write_text("<html>dashboard</html>", encoding="utf-8")
    built = create_http_app(semantic_environment.service(), HTTPConfig(), dashboard_dir=assets)
    with TestClient(built, base_url="http://127.0.0.1:8765") as client:
        assert client.get("/dashboard", follow_redirects=False).status_code == 307
        page = client.get("/dashboard/")
        assert page.status_code == 200
        assert "dashboard" in page.text


def test_http_security_rejects_rebinding_and_oversized_streams(
    semantic_environment: TestEnvironment,
) -> None:
    with running_http_server(semantic_environment, max_request_bytes=64) as endpoint:
        with httpx.Client(timeout=5, trust_env=False) as client:
            malicious_origin = client.post(
                endpoint,
                content=b"{}",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                    "Origin": "https://evil.example",
                },
            )
            assert malicious_origin.status_code == 403
            malicious_host = client.post(
                endpoint,
                content=b"{}",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                    "Host": "evil.example",
                },
            )
            assert malicious_host.status_code == 421
            declared = client.post(
                endpoint,
                content=b"x" * 65,
                headers={"Content-Type": "application/json"},
            )
            assert declared.status_code == 413

            def chunks() -> Iterator[bytes]:
                yield b"{" + b"x" * 31
                yield b"y" * 40 + b"}"

            streamed = client.post(
                endpoint,
                content=chunks(),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )
            assert streamed.status_code == 413
