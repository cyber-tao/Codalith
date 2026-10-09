"""HTTP routes for the local operations dashboard."""

from __future__ import annotations

import os
from pathlib import Path

import anyio
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import BaseRoute, Mount, Route
from starlette.staticfiles import StaticFiles

from codalith.dashboard.telemetry import TelemetryStore
from codalith.errors import CodalithError
from codalith.query.service import QueryService

_NO_STORE = {"Cache-Control": "no-store"}


def dashboard_routes(
    service: QueryService,
    telemetry: TelemetryStore,
    *,
    dashboard_dir: Path | None = None,
) -> list[BaseRoute]:
    assets = _dashboard_assets(dashboard_dir)

    def build_snapshot(target: str, window_key: str) -> dict[str, object]:
        status = service.status(target=target)
        return telemetry.snapshot(
            window_key=window_key,
            target=status.target,
            status=status,
            targets=_dashboard_targets(service),
        )

    async def snapshot(request: Request) -> JSONResponse:
        target = request.query_params.get("target") or service.registry.default_target
        window_key = request.query_params.get("range", "1h")
        try:
            payload = await anyio.to_thread.run_sync(build_snapshot, target, window_key)
        except (CodalithError, ValueError) as exc:
            return JSONResponse(
                {"error": {"code": "invalid_dashboard_query", "message": str(exc)}},
                status_code=400,
                headers=_NO_STORE,
            )
        return JSONResponse(payload, headers=_NO_STORE)

    async def root(_: Request) -> JSONResponse | RedirectResponse:
        if assets is None:
            return JSONResponse(
                {
                    "error": {
                        "code": "dashboard_not_built",
                        "message": (
                            "Dashboard assets are missing; run `bun install --cwd dashboard "
                            "--frozen-lockfile && bun run --cwd dashboard build` or set "
                            "CODALITH_DASHBOARD_DIR."
                        ),
                    }
                },
                status_code=503,
            )
        return RedirectResponse("/dashboard/", status_code=307)

    routes: list[BaseRoute] = [
        Route("/api/dashboard/snapshot", endpoint=snapshot, methods=["GET"]),
        Route("/dashboard", endpoint=root, methods=["GET"]),
    ]
    if assets is not None:
        routes.append(
            Mount("/dashboard", app=StaticFiles(directory=assets, html=True), name="dashboard")
        )
    return routes


def _dashboard_targets(service: QueryService) -> list[dict[str, str]]:
    targets = [
        {"id": corpus.corpus_id, "label": corpus.display_name, "kind": "corpus"}
        for corpus in service.registry.corpora.values()
    ]
    targets.extend(
        {"id": workspace.workspace_id, "label": workspace.workspace_id, "kind": "workspace"}
        for workspace in service.registry.workspaces.values()
    )
    return targets


def _dashboard_assets(configured: Path | None) -> Path | None:
    if configured is None:
        environment = os.getenv("CODALITH_DASHBOARD_DIR")
        configured = (
            Path(environment)
            if environment
            else Path(__file__).resolve().parents[3] / "dashboard" / "dist"
        )
    resolved = configured.expanduser().resolve()
    return resolved if (resolved / "index.html").is_file() else None
