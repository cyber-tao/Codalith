"""Minimal stdio MCP JSON-RPC server for UE Context tools."""

from __future__ import annotations

import json
import sys
from typing import Any

from ue_context.gateway.resources import read_resource, resource_templates, resources
from ue_context.gateway.tools import UETools, call_tool, create_runtime, tool_schemas

INSTRUCTIONS = (
    "Use this server first for any Unreal Engine / UE5 source-level question. "
    "Call ue_context before answering implementation, API, module, UHT, reflection, "
    "Build.cs, networking, rendering, gameplay framework, editor, asset, GC, "
    "serialization, or version-specific questions."
)


def handle_request(request: dict[str, Any], tools: UETools) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "notifications/initialized":
        return None
    try:
        result: dict[str, Any]
        if method == "initialize":
            result = {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}, "resources": {}},
                "serverInfo": {"name": "ue-context-engine", "version": "0.1.0"},
                "instructions": INSTRUCTIONS,
            }
        elif method == "tools/list":
            result = {"tools": tool_schemas()}
        elif method == "tools/call":
            params = request.get("params", {})
            name = str(params.get("name"))
            arguments = params.get("arguments") or {}
            structured = call_tool(tools, name, arguments)
            result = {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(structured, ensure_ascii=False, indent=2),
                    }
                ],
                "structuredContent": structured,
            }
        elif method == "resources/list":
            result = {"resources": resources(tools.runtime.registry)}
        elif method == "resources/templates/list":
            result = {"resourceTemplates": resource_templates()}
        elif method == "resources/read":
            params = request.get("params", {})
            uri = str(params.get("uri"))
            semantic_status = _semantic_status_for_uri(uri, tools)
            structured = read_resource(uri, tools.runtime.registry, semantic_status)
            result = {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(structured, ensure_ascii=False, indent=2),
                    }
                ]
            }
        else:
            return _error(request_id, -32601, f"Method not found: {method}")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except Exception as exc:  # noqa: BLE001 - protocol boundary.
        return _error(request_id, -32000, str(exc))


def serve(tools: UETools) -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        response = handle_request(json.loads(line), tools)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def main() -> int:
    runtime = create_runtime()
    serve(UETools(runtime))
    return 0


def _error(request_id: object, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _semantic_status_for_uri(uri: str, tools: UETools) -> dict[str, Any] | None:
    if tools.runtime.semantic_store is None:
        return None
    for corpus in tools.runtime.registry.engines.values():
        version = corpus.ue_version or corpus.corpus_id.removeprefix("ue-")
        if uri == f"ue://{version}" or uri.startswith(f"ue://{version}/"):
            return tools.runtime.semantic_store.semantic_status(corpus.corpus_id)
    for project_id, corpus in tools.runtime.registry.projects.items():
        if uri == f"ue-project://{project_id}" or uri.startswith(f"ue-project://{project_id}/"):
            return tools.runtime.semantic_store.semantic_status(corpus.corpus_id)
    return None


if __name__ == "__main__":
    raise SystemExit(main())
