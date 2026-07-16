"""Official MCP SDK bindings for Codalith's query service."""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

import anyio
import mcp.types as mcp_types
from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from pydantic import AnyUrl, ValidationError

from codalith import __version__
from codalith.corpus.uris import parse_uri, status_uri
from codalith.dashboard.telemetry import TelemetryStore, default_target, monotonic_duration_ms
from codalith.errors import CodalithError
from codalith.mcp.schemas import (
    TOOL_BY_NAME,
    TOOLS,
    CompareInput,
    ContextInput,
    GraphInput,
    ReadInput,
    SearchInput,
    StatusInput,
    SymbolInput,
)
from codalith.query.models import StrictModel as ResponseModel
from codalith.query.service import QueryService

_LOG = logging.getLogger(__name__)


def create_sdk_server(
    service: QueryService,
    telemetry: TelemetryStore | None = None,
) -> Server[Any]:
    server: Server[Any] = Server(
        "codalith",
        version=__version__,
        instructions=build_instructions(service),
    )

    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_tools() -> list[mcp_types.Tool]:
        return [
            mcp_types.Tool(
                name=definition.name,
                description=definition.description,
                inputSchema=definition.input_model.model_json_schema(),
                outputSchema=definition.output_model.model_json_schema(),
                annotations=mcp_types.ToolAnnotations(
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
            for definition in TOOLS
        ]

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def call_tool(
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any] | mcp_types.CallToolResult:
        started_at = datetime.now(UTC)
        started = time.perf_counter()
        target = default_target(arguments, service.registry.default_target)
        try:
            result = await anyio.to_thread.run_sync(_invoke, service, name, arguments)
        except (CodalithError, ValidationError, TypeError, ValueError) as exc:
            if telemetry is not None:
                telemetry.record_call(
                    tool=name,
                    arguments=arguments,
                    target=target,
                    started_at=started_at,
                    duration_ms=monotonic_duration_ms(started),
                    result=None,
                    error=exc,
                )
            return _tool_error(exc, retryable=False)
        except Exception as exc:
            _LOG.exception("Unhandled Codalith tool failure: %s", name)
            if telemetry is not None:
                telemetry.record_call(
                    tool=name,
                    arguments=arguments,
                    target=target,
                    started_at=started_at,
                    duration_ms=monotonic_duration_ms(started),
                    result=None,
                    error=exc,
                )
            return _tool_error(RuntimeError("Internal tool error"), retryable=True)
        if telemetry is not None:
            telemetry.record_call(
                tool=name,
                arguments=arguments,
                target=target,
                started_at=started_at,
                duration_ms=monotonic_duration_ms(started),
                result=result,
                error=None,
            )
        return result

    @server.list_resources()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_resources() -> list[mcp_types.Resource]:
        return [
            mcp_types.Resource(
                uri=AnyUrl(status_uri(corpus.corpus_id)),
                name=f"{corpus.label} status",
                description=corpus.description or None,
                mimeType="application/json",
            )
            for corpus in service.registry.corpora.values()
        ]

    @server.list_resource_templates()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_resource_templates() -> list[mcp_types.ResourceTemplate]:
        return [
            mcp_types.ResourceTemplate(
                uriTemplate="codalith://{corpus}/source/{path}",
                name="Indexed source",
                description="Read an indexed source path; use a #Lx-Ly fragment for a range.",
                mimeType="application/json",
            ),
            mcp_types.ResourceTemplate(
                uriTemplate="codalith://{corpus}/symbol/{symbol_id}",
                name="Indexed symbol",
                description="Read a structural symbol definition.",
                mimeType="application/json",
            ),
        ]

    @server.read_resource()  # type: ignore[no-untyped-call, untyped-decorator]
    async def read_resource(uri: AnyUrl) -> list[ReadResourceContents]:
        try:
            payload = await anyio.to_thread.run_sync(_read_resource, service, str(uri))
        except (CodalithError, TypeError, ValueError) as exc:
            payload = _error_payload(exc, retryable=False)
        return [
            ReadResourceContents(
                content=json.dumps(payload, ensure_ascii=False, indent=2),
                mime_type="application/json",
            )
        ]

    return server


def build_instructions(service: QueryService) -> str:
    corpora = ", ".join(
        f"{item.corpus_id} ({item.revision})" for item in service.registry.corpora.values()
    )
    return (
        "Codalith provides version-pinned source evidence for AI coding agents. "
        f"Available corpora: {corpora}. Use codalith_context for implementation questions, "
        "codalith_search for discovery, and codalith_read for exact cited ranges. "
        "Every source result carries a codalith:// URI, revision, and generation id."
    )


def _invoke(
    service: QueryService,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if name not in TOOL_BY_NAME:
        raise ValueError(f"Unknown tool: {name}")
    definition = TOOL_BY_NAME[name]
    parsed = definition.input_model.model_validate(arguments)
    response: ResponseModel
    if isinstance(parsed, SearchInput):
        response = service.search(**parsed.model_dump())
    elif isinstance(parsed, ContextInput):
        response = service.context(**parsed.model_dump())
    elif isinstance(parsed, ReadInput):
        response = service.read(parsed.uri)
    elif isinstance(parsed, SymbolInput):
        response = service.symbol(**parsed.model_dump())
    elif isinstance(parsed, GraphInput):
        response = service.graph(**parsed.model_dump())
    elif isinstance(parsed, CompareInput):
        response = service.compare(**parsed.model_dump())
    elif isinstance(parsed, StatusInput):
        response = service.status(**parsed.model_dump())
    else:  # pragma: no cover - TOOL definitions and union above are exhaustive
        raise TypeError(f"Unsupported tool input: {type(parsed).__name__}")
    return response.model_dump(mode="json")


def _read_resource(service: QueryService, uri: str) -> dict[str, Any]:
    parsed = parse_uri(uri)
    if parsed.kind == "source":
        return service.read(uri).model_dump(mode="json")
    if parsed.kind == "symbol":
        return service.resolve_symbol_uri(uri).model_dump(mode="json")
    if parsed.kind == "status":
        return service.status(target=parsed.corpus_id).model_dump(mode="json")
    raise ValueError(f"Unsupported resource URI: {uri}")


def _tool_error(exc: Exception, *, retryable: bool) -> mcp_types.CallToolResult:
    payload = _error_payload(exc, retryable=retryable)
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


def _error_payload(exc: Exception, *, retryable: bool) -> dict[str, Any]:
    return {
        "error": {
            "code": _error_code(exc),
            "message": str(exc),
            "retryable": retryable,
            "details": {},
        }
    }


def _error_code(exc: Exception) -> str:
    name = type(exc).__name__
    return re_snake_case(name.removesuffix("Error")) or "internal"


def re_snake_case(value: str) -> str:
    output: list[str] = []
    for index, character in enumerate(value):
        if character.isupper() and index and output[-1] != "_":
            output.append("_")
        output.append(character.lower())
    return "".join(output)
