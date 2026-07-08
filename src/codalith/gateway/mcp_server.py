"""Minimal stdio MCP JSON-RPC server for Codalith tools."""

from __future__ import annotations

import json
import sys
from typing import Any

from codalith.corpus.registry import CorpusRegistry
from codalith.errors import CodalithError
from codalith.gateway.resources import read_resource, resource_templates, resources
from codalith.gateway.tools import CodalithTools, call_tool, create_runtime, tool_schemas


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
        result: dict[str, Any]
        if method == "initialize":
            result = {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}, "resources": {}},
                "serverInfo": {"name": "codalith", "version": "0.1.0"},
                "instructions": build_instructions(tools.runtime.registry),
            }
        elif method == "tools/list":
            result = {"tools": tool_schemas(tools.runtime.registry)}
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
        return _error(request_id, -32000, str(exc))
    except Exception as exc:  # noqa: BLE001 - protocol boundary.
        return _error(request_id, -32603, str(exc))


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


def _error(request_id: object, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


if __name__ == "__main__":
    raise SystemExit(main())
