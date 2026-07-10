"""Official MCP SDK Streamable HTTP entry point for Codalith."""

from __future__ import annotations

import argparse
import logging
import os
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import uvicorn
from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.middleware.bearer_auth import (
    AuthenticatedUser,
    RequireAuthMiddleware,
)
from mcp.server.auth.provider import AccessToken
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.authentication import (
    AuthCredentials,
    AuthenticationBackend,
)
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from codalith.gateway.auth import (
    AuthError,
    authenticate_http_headers,
    default_scopes,
)
from codalith.gateway.mcp_server import build_instructions
from codalith.gateway.sdk_server import create_sdk_server
from codalith.gateway.tools import CodalithTools, create_runtime

DEFAULT_MAX_REQUEST_BYTES = 1_048_576
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StreamableHTTPConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    endpoint: str = "/mcp"
    allowed_origins: tuple[str, ...] = ()
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES


class CodalithAuthenticationBackend(AuthenticationBackend):
    def __init__(self, tools: CodalithTools) -> None:
        self.tools = tools

    async def authenticate(
        self,
        conn: HTTPConnection,
    ) -> tuple[AuthCredentials, AuthenticatedUser] | None:
        try:
            auth = authenticate_http_headers(
                dict(conn.headers.items()),
                default_scopes(self.tools.runtime.registry),
            )
        except AuthError:
            return None
        access_token = AccessToken(
            token=conn.headers.get("authorization", "local"),
            client_id=auth.user_id,
            subject=auth.user_id,
            scopes=sorted(auth.scopes),
            claims={
                "client": auth.client,
                "session_id": auth.session_id,
                "iss": "codalith",
            },
        )
        return AuthCredentials(sorted(auth.scopes)), AuthenticatedUser(access_token)


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = {
                key.decode("latin-1").lower(): value.decode("latin-1")
                for key, value in scope.get("headers", [])
            }
            raw_length = headers.get("content-length")
            if raw_length and raw_length.isdigit() and int(raw_length) > self.max_bytes:
                response = JSONResponse(
                    {"error": "request_too_large"},
                    status_code=413,
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


class RequestMetadataLogMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not http_log_enabled():
            await self.app(scope, receive, send)
            return
        status = 500

        async def capture(message: Message) -> None:
            nonlocal status
            if message.get("type") == "http.response.start":
                raw_status = message.get("status")
                if isinstance(raw_status, int):
                    status = raw_status
            await send(message)

        await self.app(scope, receive, capture)
        _LOG.info(
            "MCP HTTP %s %s -> %s",
            scope.get("method", "?"),
            scope.get("path", "?"),
            status,
        )


class SessionManagerASGI:
    def __init__(self, manager: StreamableHTTPSessionManager) -> None:
        self.manager = manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.manager.handle_request(scope, receive, send)


def create_http_app(
    tools: CodalithTools,
    config: StreamableHTTPConfig,
) -> Starlette:
    sdk_server = create_sdk_server(
        tools,
        instructions=build_instructions(tools.runtime.registry),
    )
    allowed_origins = list(config.allowed_origins) or [
        "http://127.0.0.1",
        "http://127.0.0.1:*",
        "http://localhost",
        "http://localhost:*",
        "http://[::1]",
        "http://[::1]:*",
    ]
    allowed_hosts = [
        "127.0.0.1",
        "127.0.0.1:*",
        "localhost",
        "localhost:*",
        "[::1]",
        "[::1]:*",
        "0.0.0.0",
        "0.0.0.0:*",
        f"{config.host}",
        f"{config.host}:*",
        # Docker Desktop published-port and bridge clients.
        "host.docker.internal",
        "host.docker.internal:*",
        "*",
    ]
    manager = StreamableHTTPSessionManager(
        sdk_server,
        json_response=True,
        stateless=False,
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
            allowed_hosts=list(dict.fromkeys(allowed_hosts)),
            allowed_origins=allowed_origins,
        ),
        session_idle_timeout=1800,
    )
    endpoint = RequireAuthMiddleware(SessionManagerASGI(manager), required_scopes=[])

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        async with manager.run():
            yield

    return Starlette(
        routes=[
            Route(
                config.endpoint,
                endpoint=endpoint,
                methods=["GET", "POST", "DELETE"],
            )
        ],
        middleware=[
            Middleware(RequestMetadataLogMiddleware),
            Middleware(
                RequestBodyLimitMiddleware,
                max_bytes=config.max_request_bytes,
            ),
            Middleware(
                AuthenticationMiddleware,
                backend=CodalithAuthenticationBackend(tools),
            ),
            Middleware(AuthContextMiddleware),
        ],
        lifespan=lifespan,
    )


class MCPHTTPServer:
    """Small uvicorn lifecycle wrapper used by the CLI and integration tests."""

    def __init__(self, tools: CodalithTools, config: StreamableHTTPConfig) -> None:
        self.config = config
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind((config.host, config.port))
        self.socket.listen()
        address = self.socket.getsockname()
        self.server_address = (str(address[0]), int(address[1]))
        self._server = uvicorn.Server(
            uvicorn.Config(
                create_http_app(tools, config),
                log_level="info" if http_log_enabled() else "warning",
                access_log=http_log_enabled(),
            )
        )

    def serve_forever(self) -> None:
        self._server.run(sockets=[self.socket])

    def shutdown(self) -> None:
        self._server.should_exit = True

    def server_close(self) -> None:
        try:
            self.socket.close()
        except OSError:
            pass


def create_http_server(
    tools: CodalithTools,
    config: StreamableHTTPConfig,
) -> MCPHTTPServer:
    return MCPHTTPServer(tools, config)


def http_log_enabled() -> bool:
    value = os.getenv("CODALITH_HTTP_LOG", "").strip().lower()
    return value not in {"", "0", "false", "no", "off"}
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("CODALITH_HTTP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("CODALITH_HTTP_PORT", "8765")))
    parser.add_argument("--endpoint", default=os.getenv("CODALITH_HTTP_ENDPOINT", "/mcp"))
    parser.add_argument(
        "--allowed-origin",
        action="append",
        default=[
            origin
            for origin in os.getenv("CODALITH_HTTP_ALLOWED_ORIGINS", "").split(",")
            if origin
        ],
    )
    parser.add_argument(
        "--max-request-bytes",
        type=int,
        default=int(
            os.getenv(
                "CODALITH_HTTP_MAX_REQUEST_BYTES",
                str(DEFAULT_MAX_REQUEST_BYTES),
            )
        ),
    )
    args = parser.parse_args(argv)
    config = StreamableHTTPConfig(
        host=args.host,
        port=args.port,
        endpoint=args.endpoint,
        allowed_origins=tuple(args.allowed_origin),
        max_request_bytes=args.max_request_bytes,
    )
    # Bind through uvicorn directly so Docker Desktop published ports work on Windows.
    # MCPHTTPServer's pre-bound socket path remains for in-process tests (port=0).
    app = create_http_app(CodalithTools(create_runtime()), config)
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level="info" if http_log_enabled() else "warning",
        access_log=http_log_enabled(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
