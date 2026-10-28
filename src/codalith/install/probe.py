"""Small, read-only Streamable HTTP handshake for the standalone installer."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

DEFAULT_ENDPOINT = "http://127.0.0.1:8765/mcp"
PROTOCOL_VERSION = "2025-06-18"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
EXPECTED_TOOLS = frozenset(
    f"codalith_{name}" for name in ("search", "context", "read", "symbol", "graph", "compare", "status", "catalog")
)


def validate_endpoint(url: str) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(character.isspace() for character in url)
    ):
        raise ValueError("Use an HTTP(S) MCP endpoint without embedded credentials, query or fragment.")
    try:
        _ = parsed.port
    except ValueError:
        raise ValueError("Invalid MCP endpoint port.") from None
    return url


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _decode_result(response: Any, identifier: int) -> dict[str, Any]:
    if "text/event-stream" not in response.headers.get("Content-Type", ""):
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("MCP response exceeds the installer's response limit.")
        payload = json.loads(raw)
    else:
        size = 0
        data: list[str] = []
        payload = None
        while True:
            line = response.readline(MAX_RESPONSE_BYTES + 1)
            size += len(line)
            if size > MAX_RESPONSE_BYTES:
                raise ValueError("MCP event stream exceeds the installer's response limit.")
            if line.startswith(b"data:"):
                data.append(line[5:].decode("utf-8").lstrip(" ").rstrip("\r\n"))
            if not line.strip():
                if data:
                    candidate = json.loads("\n".join(data))
                    if isinstance(candidate, dict) and candidate.get("id") == identifier:
                        payload = candidate
                        break
                data = []
            if not line:
                break
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0" or payload.get("id") != identifier:
        raise ValueError("MCP did not return the requested JSON-RPC response.")
    result = payload.get("result")
    if "error" in payload or not isinstance(result, dict):
        raise ValueError("MCP handshake returned a protocol error.")
    return result


def probe_endpoint(url: str, *, timeout: float = 10) -> dict[str, Any]:
    validate_endpoint(url)
    opener = build_opener(_NoRedirect())
    session: str | None = None
    version: str | None = None

    def request(method: str, identifier: int | None, params: dict[str, Any] | None = None) -> dict[str, Any]:
        nonlocal session
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if identifier is not None:
            payload["id"] = identifier
        if params is not None:
            payload["params"] = params
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if session:
            headers["Mcp-Session-Id"] = session
        if version:
            headers["MCP-Protocol-Version"] = version
        call = Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        with opener.open(call, timeout=timeout) as response:
            if method == "initialize":
                session = response.headers.get("Mcp-Session-Id")
            return _decode_result(response, identifier) if identifier is not None else {}

    try:
        init = request("initialize", 1, {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "codalith-installer", "version": "1"},
        })
        version = init.get("protocolVersion")
        if not isinstance(version, str) or not version:
            raise ValueError("MCP initialize response has no protocol version.")
        server = init.get("serverInfo")
        if not isinstance(server, dict) or server.get("name") != "codalith":
            raise ValueError("The endpoint is not a Codalith MCP server.")
        request("notifications/initialized", None)
        listed = request("tools/list", 2)
        tools = listed.get("tools")
        names = {tool.get("name") for tool in tools if isinstance(tool, dict)} if isinstance(tools, list) else set()
        if not EXPECTED_TOOLS.issubset(names):
            raise ValueError("The MCP endpoint does not advertise the Codalith tool contract.")
        status_result = request("tools/call", 3, {"name": "codalith_status", "arguments": {}})
        status = status_result.get("structuredContent")
        if status_result.get("isError") or not isinstance(status, dict) or status.get("ready") is not True:
            raise ValueError("Codalith is connected but its default target has no usable index. Configure or build the intended corpus first.")
        return {"connected": True, "server": "codalith", "version": server.get("version"), "tools": sorted(EXPECTED_TOOLS),
                "target": status.get("target"), "ready": True,
                "corpora": [{key: row.get(key) for key in ("corpus_id", "revision", "generation_id", "state", "semantic_available")} for row in status.get("corpora", []) if isinstance(row, dict)]}
    except HTTPError as exc:
        raise ValueError(f"MCP handshake failed (HTTP {exc.code}); check the endpoint and service access.") from None
    except (URLError, TimeoutError, OSError):
        raise ValueError("Cannot connect to Codalith MCP; start the service or use --no-probe for offline configuration.") from None
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError("MCP returned an invalid JSON or UTF-8 handshake response.") from None
    finally:
        if session:
            headers = {"Mcp-Session-Id": session}
            if version:
                headers["MCP-Protocol-Version"] = version
            try:
                with opener.open(Request(url, headers=headers, method="DELETE"), timeout=timeout):
                    pass
            except (URLError, OSError):
                pass
