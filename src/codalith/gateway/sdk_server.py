"""Official MCP SDK bindings for Codalith's transport-independent services."""

from __future__ import annotations

import json
import logging
from typing import Any

import anyio
import mcp.types as mcp_types
from mcp.server import Server
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.lowlevel.helper_types import ReadResourceContents
from pydantic import AnyUrl

from codalith import __version__
from codalith.errors import CodalithError
from codalith.gateway.auth import (
    AuthContext,
    reset_current_auth_context,
    set_current_auth_context,
)
from codalith.gateway.resources import read_resource, resource_templates, resources
from codalith.gateway.tools import CodalithTools, call_tool, tool_schemas

_LOG = logging.getLogger(__name__)


def create_sdk_server(tools: CodalithTools, *, instructions: str) -> Server[Any]:
    server: Server[Any] = Server(
        "codalith",
        version=__version__,
        instructions=instructions,
    )

    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_tools() -> list[mcp_types.Tool]:
        return [
            mcp_types.Tool(
                name=str(schema["name"]),
                description=str(schema["description"]),
                inputSchema=dict(schema["inputSchema"]),
                outputSchema=dict(schema["outputSchema"]),
                annotations=mcp_types.ToolAnnotations(
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
            for schema in tool_schemas(tools.runtime.registry)
        ]

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def invoke_tool(
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any] | mcp_types.CallToolResult:
        identity = _request_identity(tools)

        def invoke() -> dict[str, Any]:
            token = set_current_auth_context(identity)
            try:
                result = call_tool(tools, name, arguments)
                if not isinstance(result, dict):
                    raise TypeError(f"Tool {name} returned a non-object result")
                return result
            finally:
                reset_current_auth_context(token)

        try:
            return await anyio.to_thread.run_sync(invoke)
        except (CodalithError, TypeError, ValueError) as exc:
            return _tool_error(exc)
        except Exception:
            _LOG.exception("Unhandled Codalith tool failure: %s", name)
            return _tool_error(RuntimeError("Internal tool error"))

    @server.list_resources()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_resources() -> list[mcp_types.Resource]:
        return [
            mcp_types.Resource(
                uri=AnyUrl(item["uri"]),
                name=item["name"],
                description=item.get("description"),
                mimeType=item.get("mimeType"),
            )
            for item in resources(tools.runtime.registry)
        ]

    @server.list_resource_templates()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_resource_templates() -> list[mcp_types.ResourceTemplate]:
        return [
            mcp_types.ResourceTemplate(
                uriTemplate=item["uriTemplate"],
                name=item["name"],
                description=item.get("description"),
                mimeType="application/json",
            )
            for item in resource_templates()
        ]

    @server.read_resource()  # type: ignore[no-untyped-call, untyped-decorator]
    async def get_resource(uri: AnyUrl) -> list[ReadResourceContents]:
        identity = _request_identity(tools)

        def read() -> dict[str, Any]:
            token = set_current_auth_context(identity)
            try:
                return read_resource(str(uri), tools)
            finally:
                reset_current_auth_context(token)

        payload = await anyio.to_thread.run_sync(read)
        return [
            ReadResourceContents(
                content=json.dumps(payload, ensure_ascii=False, indent=2),
                mime_type="application/json",
            )
        ]

    return server


def _request_identity(tools: CodalithTools) -> AuthContext:
    access_token = get_access_token()
    if access_token is None:
        return tools.runtime.identity
    claims = access_token.claims or {}
    return AuthContext(
        user_id=access_token.subject or access_token.client_id,
        session_id=str(claims.get("session_id") or "mcp-session"),
        client=str(claims.get("client") or access_token.client_id),
        scopes=frozenset(access_token.scopes),
    )


def _tool_error(exc: Exception) -> mcp_types.CallToolResult:
    payload = {
        "error": {
            "type": type(exc).__name__,
            "message": str(exc),
        }
    }
    return mcp_types.CallToolResult(
        content=[
            mcp_types.TextContent(
                type="text",
                text=json.dumps(payload, ensure_ascii=False),
            )
        ],
        structuredContent=payload,
        isError=True,
    )
