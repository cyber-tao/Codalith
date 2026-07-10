from __future__ import annotations

import os
import sys

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_stdio_sdk_transport_lists_and_calls_tools(
    registry_path,
    policy_path,
    tmp_path,
):
    environment = dict(os.environ)
    environment.update(
        {
            "CODALITH_CORPUS_REGISTRY": str(registry_path),
            "CODALITH_SOURCE_POLICY": str(policy_path),
            "CODALITH_AUDIT_LOG": str(tmp_path / "stdio-audit.jsonl"),
        }
    )
    environment.pop("CODALITH_SEMANTIC_DSN", None)
    environment.pop("CODALITH_SEMANTIC_DB", None)

    async def exercise() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "codalith.gateway.mcp_server"],
            env=environment,
            cwd=os.getcwd(),
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.name == "codalith"
                listed = await session.list_tools()
                assert any(tool.name == "codalith_context" for tool in listed.tools)
                result = await session.call_tool(
                    "codalith_context",
                    {"query": "CachedValue ttl", "corpus": "sample"},
                )
                assert result.isError is False
                assert result.structuredContent
                assert result.structuredContent["source_spans"]

    anyio.run(exercise)
