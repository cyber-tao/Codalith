"""Secure local-first Streamable HTTP transport."""

from __future__ import annotations

import logging
import os
import socket
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import BaseRoute, Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from codalith.dashboard.telemetry import TelemetryStore
from codalith.errors import CodalithError
from codalith.mcp.server import create_sdk_server
from codalith.query.service import QueryService

DEFAULT_MAX_REQUEST_BYTES = 1_048_576
_LOG = logging.getLogger(__name__)


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


class RequestLogMiddleware:
    def __init__(self, app: ASGIApp, *, enabled: bool) -> None:
        self.app = app
        self.enabled = enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.enabled:
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
            "Codalith HTTP %s %s -> %s",
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
    service: QueryService,
    config: HTTPConfig,
    *,
    telemetry: TelemetryStore | None = None,
    dashboard_dir: Path | None = None,
) -> Starlette:
    config.validate()
    telemetry_store = telemetry or TelemetryStore()
    manager = StreamableHTTPSessionManager(
        create_sdk_server(service, telemetry_store),
        json_response=True,
        stateless=False,
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=_allowed_hosts(config),
            allowed_origins=_allowed_origins(config),
        ),
        session_idle_timeout=1800,
    )
    endpoint = SessionManagerASGI(manager)

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def ready(_: Request) -> JSONResponse:
        response = service.status()
        return JSONResponse(
            response.model_dump(mode="json"),
            status_code=200 if response.ready else 503,
        )

    async def dashboard_snapshot(request: Request) -> JSONResponse:
        target = request.query_params.get("target") or service.registry.default_target
        window_key = request.query_params.get("range", "1h")
        try:
            response = service.status(target=target)
            payload = telemetry_store.snapshot(
                window_key=window_key,
                target=response.target,
                status=response,
                targets=_dashboard_targets(service),
            )
        except (CodalithError, ValueError) as exc:
            return JSONResponse(
                {"error": {"code": "invalid_dashboard_query", "message": str(exc)}},
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    resolved_dashboard_dir = _dashboard_dir(dashboard_dir)

    async def dashboard_root(_: Request) -> JSONResponse | RedirectResponse:
        if resolved_dashboard_dir is None:
            return JSONResponse(
                {
                    "error": {
                        "code": "dashboard_not_built",
                        "message": "Build the dashboard with `bun --cwd dashboard run build`.",
                    }
                },
                status_code=503,
            )
        return RedirectResponse("/dashboard/", status_code=307)

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
        Route("/api/dashboard/snapshot", endpoint=dashboard_snapshot, methods=["GET"]),
        Route("/dashboard", endpoint=dashboard_root, methods=["GET"]),
        Route(config.endpoint, endpoint=endpoint, methods=["GET", "POST", "DELETE"]),
    ]
    if resolved_dashboard_dir is not None:
        routes.append(
            Mount(
                "/dashboard",
                app=StaticFiles(directory=resolved_dashboard_dir, html=True),
                name="dashboard",
            )
        )

    return Starlette(
        routes=routes,
        lifespan=lifespan,
        middleware=[
            Middleware(RequestLogMiddleware, enabled=config.access_log),
            Middleware(RequestBodyLimitMiddleware, max_bytes=config.max_request_bytes),
        ],
    )


class MCPHTTPServer:
    """Uvicorn wrapper used by integration tests and the CLI."""

    def __init__(self, service: QueryService, config: HTTPConfig) -> None:
        config.validate()
        self.config = config
        self.socket = _bind_socket(config.host, config.port)
        address = self.socket.getsockname()
        self.server_address = (str(address[0]), int(address[1]))
        self._server = uvicorn.Server(
            uvicorn.Config(
                create_http_app(service, config),
                log_level="info" if config.access_log else "warning",
                access_log=config.access_log,
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


def _bind_socket(host: str, port: int) -> socket.socket:
    addresses = socket.getaddrinfo(
        host,
        port,
        type=socket.SOCK_STREAM,
        flags=socket.AI_PASSIVE,
    )
    last_error: OSError | None = None
    for family, socktype, proto, _, address in addresses:
        candidate = socket.socket(family, socktype, proto)
        try:
            candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if family == socket.AF_INET6:
                candidate.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            candidate.bind(address)
            candidate.listen()
            return candidate
        except OSError as exc:
            last_error = exc
            candidate.close()
    raise OSError(f"Cannot bind {host}:{port}: {last_error}")


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


def _dashboard_targets(service: QueryService) -> list[dict[str, str]]:
    targets = [
        {
            "id": corpus.corpus_id,
            "label": corpus.display_name,
            "kind": "corpus",
        }
        for corpus in service.registry.corpora.values()
    ]
    targets.extend(
        {
            "id": workspace.workspace_id,
            "label": workspace.workspace_id,
            "kind": "workspace",
        }
        for workspace in service.registry.workspaces.values()
    )
    return targets


def _dashboard_dir(configured: Path | None) -> Path | None:
    candidates: list[Path] = []
    if configured is not None:
        candidates.append(configured)
    environment = os.getenv("CODALITH_DASHBOARD_DIR")
    if environment:
        candidates.append(Path(environment))
    project_root = Path(__file__).resolve().parents[3]
    candidates.extend(
        (
            project_root / "dashboard" / "dist",
            Path(__file__).resolve().parents[1] / "dashboard_dist",
        )
    )
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if (resolved / "index.html").is_file():
            return resolved
    return None


async def _json_error(
    scope: Scope,
    receive: Receive,
    send: Send,
    status: int,
    code: str,
) -> None:
    response = JSONResponse({"error": {"code": code}}, status_code=status)
    await response(scope, receive, send)
