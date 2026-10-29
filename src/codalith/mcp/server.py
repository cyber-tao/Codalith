"""Official MCP SDK bindings for Codalith's query service."""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import Any, Protocol

import anyio
import mcp.types as mcp_types
from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from pydantic import AnyUrl

from codalith import __version__
from codalith.corpus.uris import parse_uri, status_uri
from codalith.errors import INTERNAL_ERROR_CODE, CodalithError, describe_error
from codalith.mcp.schemas import (
    TOOL_BY_NAME,
    TOOLS,
    CatalogInput,
    CompareInput,
    ContextInput,
    GraphInput,
    ReadInput,
    SearchInput,
    StatusInput,
    SymbolInput,
)
from codalith.query.catalog import version_catalog
from codalith.query.models import StrictModel as ResponseModel
from codalith.query.service import QueryService

_LOG = logging.getLogger(__name__)


class ToolCallRecorder(Protocol):
    def record_call(
        self,
        *,
        tool: str,
        arguments: dict[str, Any],
        target: str,
        started_at: datetime,
        duration_ms: float,
        result: dict[str, Any] | None,
        error: Exception | None,
    ) -> None: ...


def create_sdk_server(
    service: QueryService,
    recorder: ToolCallRecorder | None = None,
    *,
    bound_target: str | None = None,
) -> Server[Any]:
    if bound_target is not None:
        service.registry.resolve(bound_target)
    server: Server[Any] = Server(
        "codalith",
        version=__version__,
        instructions=build_instructions(service, bound_target),
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

        def record(result: dict[str, Any] | None, error: Exception | None) -> None:
            if recorder is None:
                return
            recorder.record_call(
                tool=name,
                arguments=arguments,
                target=bound_target or _call_target(name, arguments, service.registry.default_target),
                started_at=started_at,
                duration_ms=max(0.0, (time.perf_counter() - started) * 1_000),
                result=result,
                error=error,
            )

        try:
            scoped = _scope_arguments(service, name, arguments, bound_target)
            if name == "codalith_catalog":
                CatalogInput.model_validate(scoped)
                result = (await anyio.to_thread.run_sync(version_catalog, service, bound_target)).model_dump(mode="json")
            else:
                result = await anyio.to_thread.run_sync(_invoke, service, name, scoped)
        except Exception as exc:
            if describe_error(exc)[0] == INTERNAL_ERROR_CODE:
                _LOG.exception("Unhandled Codalith tool failure: %s", name)
            record(None, exc)
            return _tool_error(exc)
        record(result, None)
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
            for corpus in (service.registry.resolve(bound_target).corpora if bound_target else service.registry.corpora.values())
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
            _scope_uri(service, str(uri), bound_target)
            payload = await anyio.to_thread.run_sync(_read_resource, service, str(uri))
        except (CodalithError, TypeError, ValueError) as exc:
            payload = _error_payload(exc)
        return [
            ReadResourceContents(
                content=json.dumps(payload, ensure_ascii=False, indent=2),
                mime_type="application/json",
            )
        ]

    return server


def build_instructions(service: QueryService, bound_target: str | None = None) -> str:
    corpora = ", ".join(
        f"{item.corpus_id} ({item.revision})" for item in (service.registry.resolve(bound_target).corpora if bound_target else service.registry.corpora.values())
    )
    workspaces = ", ".join(
        f"{item.workspace_id} [{', '.join(item.corpus_ids)}]"
        for item in service.registry.workspaces.values() if bound_target is None
    )
    return (
        "Codalith provides version-pinned source evidence for AI coding agents. "
        f"Registered corpora: {corpora}. Registered workspaces: {workspaces or 'none'}. "
        f"Default target: {bound_target or service.registry.default_target}. Registration is not readiness; "
        "use codalith_status when target/version or index state is uncertain, not before "
        "every query. Pass the requested target explicitly. Use codalith_context for "
        "bounded implementation evidence, codalith_symbol for known identifiers, and "
        "codalith_search for discovery. Reuse sufficient returned text without redundant "
        "reads. Cite returned codalith:// source URIs exactly; symbol/status/corpus URIs "
        "are not source citations. For a new line range, call codalith_read and cite its "
        "returned URI. Check revision, generation, warnings, stale and truncated state. "
        "Static source and graph edges do not establish a running process's callback "
        "membership, registration history or execution order. Stop when the evidence "
        "answers the question or the remaining fact requires runtime evidence. "
        "Keep the answer to supported claims needed for the question; a valid URI alone "
        "does not prove a claim or an exhaustive always/never assertion."
    )


def _scope_uri(service: QueryService, uri: str, target: str | None) -> None:
    if target is None:
        return
    allowed = {item.corpus_id for item in service.registry.resolve(target).corpora}
    if parse_uri(uri).corpus_id not in allowed:
        raise ValueError("URI is outside this endpoint's bound version")


def _scope_arguments(
    service: QueryService, name: str, arguments: dict[str, Any], target: str | None,
) -> dict[str, Any]:
    scoped = dict(arguments)
    if target is None:
        return scoped
    if name == "codalith_compare":
        raise ValueError("Cross-version comparison requires the unbound MCP endpoint")
    if name in {"codalith_search", "codalith_context", "codalith_symbol", "codalith_status"}:
        if scoped.get("target") not in {None, target}:
            raise ValueError("Requested target differs from this endpoint's bound version")
        scoped["target"] = target
    for key in ("uri", "root_uri"):
        if key in scoped:
            _scope_uri(service, scoped[key], target)
    return scoped


def _call_target(name: str, arguments: dict[str, Any], fallback: str) -> str:
    definition = TOOL_BY_NAME.get(name)
    if definition is None:
        return fallback
    try:
        parsed = definition.input_model.model_validate(arguments)
        if isinstance(parsed, ReadInput):
            return parse_uri(parsed.uri).corpus_id
        if isinstance(parsed, GraphInput):
            return parse_uri(parsed.root_uri).corpus_id
        if isinstance(parsed, CompareInput):
            return parsed.from_corpus
        if isinstance(parsed, (SearchInput, ContextInput, SymbolInput, StatusInput)):
            return parsed.target or fallback
    except (CodalithError, ValueError, TypeError):
        return fallback
    return fallback


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
    elif isinstance(parsed, CatalogInput):
        response = version_catalog(service)
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


def _tool_error(exc: Exception) -> mcp_types.CallToolResult:
    payload = _error_payload(exc)
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


def _error_payload(exc: Exception) -> dict[str, Any]:
    code, retryable = describe_error(exc)
    return {
        "error": {
            "code": code,
            "message": "Internal tool error" if code == INTERNAL_ERROR_CODE else str(exc),
            "retryable": retryable,
            "details": {},
        }
    }
