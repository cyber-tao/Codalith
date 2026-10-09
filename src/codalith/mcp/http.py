"""Secure local-first Streamable HTTP transport."""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import anyio
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import (
    TransportSecurityMiddleware,
    TransportSecuritySettings,
)
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import HTTPConnection, Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from codalith.dashboard.routes import dashboard_routes
from codalith.dashboard.telemetry import TelemetryStore
from codalith.mcp.server import create_sdk_server
from codalith.query.service import QueryService

DEFAULT_MAX_REQUEST_BYTES = 1_048_576


@dataclass(frozen=True, slots=True)
class HTTPConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    endpoint: str = "/mcp"
    allowed_origins: tuple[str, ...] = ()
    allowed_hosts: tuple[str, ...] = ()
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES
    access_log: bool = False

    def validate(self) -> None:
        if not self.host.strip():
            raise ValueError("HTTP host cannot be blank")
        if not 0 <= self.port <= 65_535:
            raise ValueError("HTTP port must be between 0 and 65535")
        endpoint_parts = self.endpoint.split("/")[1:]
        if (
            not self.endpoint.startswith("/")
            or self.endpoint == "/"
            or "//" in self.endpoint
            or any(character in self.endpoint for character in "\\?#")
            or any(part in {"", ".", ".."} for part in endpoint_parts)
        ):
            raise ValueError("MCP endpoint must be an absolute non-root path")
        if self.max_request_bytes <= 0:
            raise ValueError("max_request_bytes must be positive")
        if any(not value.strip() for value in (*self.allowed_origins, *self.allowed_hosts)):
            raise ValueError("Allowed Origin and Host values cannot be blank")


class HostOriginGuardMiddleware:
    """Apply the MCP DNS-rebinding Host/Origin checks to every HTTP route."""

    def __init__(self, app: ASGIApp, *, settings: TransportSecuritySettings) -> None:
        self.app = app
        self.security = TransportSecurityMiddleware(settings)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rejection = await self.security.validate_request(HTTPConnection(scope))
        if rejection is not None:
            await rejection(scope, receive, send)
            return
        await self.app(scope, receive, send)


class RequestBodyLimitMiddleware:
    """Buffer at most one bounded request before the MCP application can respond."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        raw_length = headers.get("content-length")
        if raw_length:
            try:
                declared = int(raw_length)
            except ValueError:
                await _json_error(scope, receive, send, 400, "invalid_content_length")
                return
            if declared < 0:
                await _json_error(scope, receive, send, 400, "invalid_content_length")
                return
            if declared > self.max_bytes:
                await _json_error(scope, receive, send, 413, "request_too_large")
                return
        consumed = 0
        buffered: deque[Message] = deque()
        while True:
            message = await receive()
            buffered.append(message)
            if message.get("type") == "http.request":
                body = message.get("body", b"")
                if isinstance(body, bytes):
                    consumed += len(body)
                if consumed > self.max_bytes:
                    await _json_error(scope, receive, send, 413, "request_too_large")
                    return
                if not message.get("more_body", False):
                    break
            elif message.get("type") == "http.disconnect":
                break

        async def replay() -> Message:
            if buffered:
                return buffered.popleft()
            return await receive()

        await self.app(scope, replay, send)


class SessionManagerASGI:
    def __init__(self, manager: StreamableHTTPSessionManager) -> None:
        self.manager = manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.manager.handle_request(scope, receive, send)


def create_http_app(
    service: QueryService,
    config: HTTPConfig,
    *,
    telemetry: TelemetryStore | None = None,
    dashboard_dir: Path | None = None,
) -> Starlette:
    config.validate()
    telemetry_store = telemetry or TelemetryStore()
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts(config),
        allowed_origins=_allowed_origins(config),
    )
    manager = StreamableHTTPSessionManager(
        create_sdk_server(service, telemetry_store),
        json_response=True,
        stateless=False,
        security_settings=security,
        session_idle_timeout=1800,
    )
    endpoint = SessionManagerASGI(manager)

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def ready(_: Request) -> JSONResponse:
        response = await anyio.to_thread.run_sync(service.status)
        return JSONResponse(
            response.model_dump(mode="json"),
            status_code=200 if response.ready else 503,
        )

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        async with manager.run():
            try:
                yield
            finally:
                service.close()

    routes: list[BaseRoute] = [
        Route("/healthz", endpoint=health, methods=["GET"]),
        Route("/readyz", endpoint=ready, methods=["GET"]),
        Route(config.endpoint, endpoint=endpoint, methods=["GET", "POST", "DELETE"]),
        *dashboard_routes(service, telemetry_store, dashboard_dir=dashboard_dir),
    ]

    return Starlette(
        routes=routes,
        lifespan=lifespan,
        middleware=[
            Middleware(HostOriginGuardMiddleware, settings=security),
            Middleware(RequestBodyLimitMiddleware, max_bytes=config.max_request_bytes),
        ],
    )


def _allowed_hosts(config: HTTPConfig) -> list[str]:
    values = [
        "127.0.0.1",
        "127.0.0.1:*",
        "localhost",
        "localhost:*",
        "[::1]",
        "[::1]:*",
        *config.allowed_hosts,
    ]
    return list(dict.fromkeys(value.strip() for value in values))


def _allowed_origins(config: HTTPConfig) -> list[str]:
    values = [
        "http://127.0.0.1",
        "http://127.0.0.1:*",
        "http://localhost",
        "http://localhost:*",
        "http://[::1]",
        "http://[::1]:*",
        *config.allowed_origins,
    ]
    return list(dict.fromkeys(value.strip() for value in values))


async def _json_error(
    scope: Scope,
    receive: Receive,
    send: Send,
    status: int,
    code: str,
) -> None:
    response = JSONResponse({"error": {"code": code}}, status_code=status)
    await response(scope, receive, send)
