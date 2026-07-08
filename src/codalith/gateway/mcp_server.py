"""Minimal stdio MCP JSON-RPC server for Codalith tools."""

from __future__ import annotations

import json
import sys
from typing import Any

from codalith import __version__
from codalith.corpus.registry import CorpusRegistry
from codalith.errors import CodalithError
from codalith.gateway.resources import read_resource, resource_templates, resources
from codalith.gateway.tools import CodalithTools, call_tool, create_runtime, tool_schemas

# Protocol revision this server implements and advertises on initialize.
PROTOCOL_VERSION = "2025-11-25"


def build_instructions(registry: CorpusRegistry) -> str:
    """Assemble the server self-description from the configured corpus registry.

    Nothing domain-specific is hardcoded here: corpus names, descriptions, and
    trigger keywords all come from configs/corpus_registry.json, so a deployment
    that indexes a different codebase advertises that codebase instead.
    """
    corpus_labels = [
        f"{corpus.label} ({corpus.description})" if corpus.description else corpus.label
        for corpus in registry.corpora.values()
    ]
    project_ids = sorted(registry.projects)
    keywords = list(
        dict.fromkeys(
            keyword for corpus in registry.corpora.values() for keyword in corpus.keywords
        )
    )
    parts = ["This server provides version-pinned, source-backed context for indexed code corpora."]
    if corpus_labels:
        parts.append("Indexed corpora: " + "; ".join(corpus_labels) + ".")
    if project_ids:
        parts.append("Project overlays: " + ", ".join(project_ids) + ".")
    parts.append(
        "Use this server first for any source-level question about these codebases, even when "
        "the question does not mention source code. Call codalith_context before answering "
        "implementation, API, behavior, or version-specific questions."
    )
    parts.append(
        "Source citations use codalith://<corpus>/source/<path>#L<start>-L<end> URIs; "
        "read them with codalith_read_source."
    )
    if keywords:
        parts.append("Trigger topics include: " + ", ".join(keywords) + ".")
    return " ".join(parts)


def handle_request(request: dict[str, Any], tools: CodalithTools) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "notifications/initialized":
        return None
    try:
        params = _params(request)
        result: dict[str, Any]
        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}, "resources": {}},
                "serverInfo": {"name": "codalith", "version": __version__},
                "instructions": build_instructions(tools.runtime.registry),
            }
        elif method == "tools/list":
            result = {"tools": tool_schemas(tools.runtime.registry)}
        elif method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str) or not name:
                raise ValueError("tools/call requires a string 'name' parameter")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                raise ValueError("tools/call 'arguments' must be an object")
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
            uri = params.get("uri")
            if not isinstance(uri, str) or not uri:
                raise ValueError("resources/read requires a string 'uri' parameter")
            structured = read_resource(uri, tools)
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
    except (ValueError, TypeError) as exc:
        return _error(request_id, -32602, str(exc))
    except CodalithError as exc:
        return _error(request_id, -32000, str(exc), data={"type": type(exc).__name__})
    except Exception as exc:  # noqa: BLE001 - protocol boundary.
        return _error(request_id, -32603, str(exc))


def _params(request: dict[str, Any]) -> dict[str, Any]:
    params = request.get("params")
    if params is None:
        return {}
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    return params


def serve(tools: CodalithTools) -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        response: dict[str, Any] | None
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            response = _error(None, -32700, f"Parse error: {exc}")
        else:
            response = handle_request(request, tools)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def main() -> int:
    runtime = create_runtime()
    serve(CodalithTools(runtime))
    return 0


def _error(
    request_id: object,
    code: int,
    message: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


if __name__ == "__main__":
    raise SystemExit(main())
