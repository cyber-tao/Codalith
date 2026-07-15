from __future__ import annotations

import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import anyio
import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from codalith.mcp.http import HTTPConfig, MCPHTTPServer
from conftest import TestEnvironment


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
    server = MCPHTTPServer(
        environment.service(),
        HTTPConfig(port=0, max_request_bytes=max_request_bytes),
    )
    thread = threading.Thread(target=server.serve_forever, name="codalith-test-http")
    thread.start()
    host, port = server.server_address
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
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()
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
