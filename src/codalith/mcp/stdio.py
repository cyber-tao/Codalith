"""MCP stdio transport."""

from __future__ import annotations

from mcp.server.stdio import stdio_server

from codalith.mcp.server import create_sdk_server
from codalith.query.service import QueryService


async def serve_stdio(service: QueryService) -> None:
    server = create_sdk_server(service)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
