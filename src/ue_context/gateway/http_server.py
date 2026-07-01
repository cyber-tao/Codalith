"""Streamable HTTP transport for the UE Context MCP server."""

from __future__ import annotations

import argparse
import json
import os
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from ue_context.gateway.mcp_server import handle_request
from ue_context.gateway.tools import UETools, create_runtime

SUPPORTED_PROTOCOL_VERSIONS = {"2025-11-25", "2025-06-18", "2025-03-26"}
DEFAULT_PROTOCOL_VERSION = "2025-03-26"


@dataclass(frozen=True, slots=True)
class StreamableHTTPConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    endpoint: str = "/mcp"
    allowed_origins: tuple[str, ...] = ()


class MCPHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler_class: type[BaseHTTPRequestHandler],
        *,
        tools: UETools,
        config: StreamableHTTPConfig,
    ) -> None:
        super().__init__(server_address, request_handler_class)
        self.tools = tools
        self.config = config
        self.sessions: set[str] = set()


class StreamableHTTPHandler(BaseHTTPRequestHandler):
    server: MCPHTTPServer
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        if not self._preflight():
            return
        if not self._accepts("application/json") or not self._accepts("text/event-stream"):
            self._write_json_error(
                HTTPStatus.NOT_ACCEPTABLE,
                "Accept header must include application/json and text/event-stream",
            )
            return
        if not self._protocol_version_is_valid():
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = self.rfile.read(length).decode("utf-8")
            request = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._write_json_error(HTTPStatus.BAD_REQUEST, "Request body must be UTF-8 JSON")
            return
        if not isinstance(request, dict):
            self._write_json_error(HTTPStatus.BAD_REQUEST, "Body must be a single JSON-RPC message")
            return
        if not self._session_is_valid(request):
            return
        response = handle_request(request, self.server.tools)
        if response is None:
            self.send_response(HTTPStatus.ACCEPTED)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        headers: dict[str, str] = {}
        if request.get("method") == "initialize":
            session_id = uuid.uuid4().hex
            self.server.sessions.add(session_id)
            headers["MCP-Session-Id"] = session_id
        self._write_json(HTTPStatus.OK, response, headers=headers)

    def do_GET(self) -> None:
        if not self._preflight():
            return
        if not self._accepts("text/event-stream"):
            self._write_json_error(
                HTTPStatus.NOT_ACCEPTABLE,
                "Accept header must include text/event-stream",
            )
            return
        if not self._protocol_version_is_valid():
            return
        payload = f"id: {uuid.uuid4().hex}\nevent: ping\ndata: {{}}\nretry: 1000\n\n"
        raw = payload.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_DELETE(self) -> None:
        if not self._preflight():
            return
        session_id = self.headers.get("MCP-Session-Id")
        if session_id and session_id in self.server.sessions:
            self.server.sessions.remove(session_id)
            self.send_response(HTTPStatus.ACCEPTED)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._write_json_error(HTTPStatus.NOT_FOUND, "Unknown MCP session")

    def log_message(self, format: str, *args: object) -> None:
        if os.getenv("UE_CONTEXT_HTTP_LOG"):
            super().log_message(format, *args)

    def _preflight(self) -> bool:
        if urlparse(self.path).path != self.server.config.endpoint:
            self._write_json_error(HTTPStatus.NOT_FOUND, "MCP endpoint not found")
            return False
        if not origin_allowed(self.headers.get("Origin"), self.server.config.allowed_origins):
            self._write_json_error(HTTPStatus.FORBIDDEN, "Origin is not allowed")
            return False
        return True

    def _accepts(self, content_type: str) -> bool:
        accept = self.headers.get("Accept", "")
        return "*/*" in accept or content_type in {item.strip().split(";", 1)[0] for item in accept.split(",")}

    def _protocol_version_is_valid(self) -> bool:
        version = self.headers.get("MCP-Protocol-Version", DEFAULT_PROTOCOL_VERSION)
        if version not in SUPPORTED_PROTOCOL_VERSIONS:
            self._write_json_error(HTTPStatus.BAD_REQUEST, f"Unsupported MCP protocol version: {version}")
            return False
        return True

    def _session_is_valid(self, request: dict[str, Any]) -> bool:
        if request.get("method") == "initialize" or not self.server.sessions:
            return True
        session_id = self.headers.get("MCP-Session-Id")
        if session_id in self.server.sessions:
            return True
        self._write_json_error(HTTPStatus.BAD_REQUEST, "Missing or invalid MCP-Session-Id")
        return False

    def _write_json_error(self, status: HTTPStatus, message: str) -> None:
        payload = {
            "jsonrpc": "2.0",
            "error": {"code": -32000, "message": message},
        }
        self._write_json(status, payload)

    def _write_json(
        self,
        status: HTTPStatus,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)


def origin_allowed(origin: str | None, allowed_origins: tuple[str, ...]) -> bool:
    if not origin:
        return True
    if "*" in allowed_origins or origin in allowed_origins:
        return True
    parsed = urlparse(origin)
    return parsed.scheme in {"http", "https"} and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def create_http_server(
    tools: UETools,
    config: StreamableHTTPConfig,
) -> MCPHTTPServer:
    return MCPHTTPServer((config.host, config.port), StreamableHTTPHandler, tools=tools, config=config)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("UE_CONTEXT_HTTP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("UE_CONTEXT_HTTP_PORT", "8765")))
    parser.add_argument("--endpoint", default=os.getenv("UE_CONTEXT_HTTP_ENDPOINT", "/mcp"))
    parser.add_argument(
        "--allowed-origin",
        action="append",
        default=[origin for origin in os.getenv("UE_CONTEXT_HTTP_ALLOWED_ORIGINS", "").split(",") if origin],
    )
    args = parser.parse_args(argv)
    config = StreamableHTTPConfig(
        host=args.host,
        port=args.port,
        endpoint=args.endpoint,
        allowed_origins=tuple(args.allowed_origin),
    )
    server = create_http_server(UETools(create_runtime()), config)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 130
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
