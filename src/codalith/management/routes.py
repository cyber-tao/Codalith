"""Authenticated management HTTP endpoints, separate from read-only MCP tools."""

from __future__ import annotations

import hmac
from collections.abc import Callable
from typing import Any

import anyio
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from codalith.errors import CodalithError
from codalith.management.service import ManagementService


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RepositoryInput(_Input):
    path: str = Field(max_length=4096)
    root_id: str | None = None


class DatabaseInput(_Input):
    name: str = Field(min_length=1, max_length=128)
    profile_id: str = Field(min_length=1, max_length=64)
    repo_id: str | None = None


class PlanInput(_Input):
    database_id: str
    repo_id: str | None = None
    tags: list[str] = Field(min_length=1, max_length=1000)
    legacy_generation: str | None = None
    legacy_generation_id: str | None = None


class JobInput(_Input):
    plan_id: str
    idempotency_key: str = Field(min_length=1, max_length=200)


def management_routes(service: ManagementService) -> list[BaseRoute]:
    if not service.config.enabled:
        return []

    async def dispatch(request: Request) -> JSONResponse:
        expected = "Bearer " + service.config.admin_token
        supplied = request.headers.get("authorization", "")
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            return JSONResponse(
                {
                    "error": {
                        "code": "admin_unauthorized",
                        "message": "Administrator token required",
                    }
                },
                status_code=401,
                headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
            )
        try:
            payload = await handle(request)
            return JSONResponse(
                payload,
                headers={"Cache-Control": "no-store"},
                status_code=202
                if request.method == "POST" and request.url.path.endswith("/jobs")
                else 200,
            )
        except KeyError as exc:
            return _error("admin_not_found", str(exc), 404)
        except (ValidationError, ValueError, CodalithError, OSError) as exc:
            return _error("admin_invalid_request", str(exc), 400)

    async def body(request: Request, model: type[_Input]) -> Any:
        if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
            raise ValueError("Management writes require application/json")
        return model.model_validate(await request.json())

    async def call(function: Callable[[], Any]) -> Any:
        return await anyio.to_thread.run_sync(function)

    async def handle(request: Request) -> Any:
        path = request.url.path.removeprefix("/api/admin/")
        method = request.method
        parameters = request.path_params
        if path == "config":
            return service.public_config()
        if path == "source-roots":
            return {"items": service.source_roots()}
        if path == "legacy-generations":
            return {"items": service.legacy_generations()}
        if path == "directories":
            return await call(
                lambda: service.directories(
                    request.query_params.get("root_id", "0"), request.query_params.get("path", "")
                )
            )
        if path == "repositories":
            if method == "GET":
                return {"items": service.catalog.list("repository")}
            data = await body(request, RepositoryInput)
            return await call(lambda: service.add_repository(data.path, data.root_id))
        if "repo_id" in parameters:
            return await call(
                lambda: service.tags(
                    parameters["repo_id"],
                    q=request.query_params.get("q", ""),
                    cursor=request.query_params.get("cursor", "0"),
                    limit=int(request.query_params.get("limit", "100")),
                )
            )
        if path == "databases":
            if method == "GET":
                return {"items": service.catalog.list("database")}
            data = await body(request, DatabaseInput)
            return await call(
                lambda: service.create_database(data.name, data.profile_id, data.repo_id)
            )
        if "database_id" in parameters:
            return await call(lambda: service.space(parameters["database_id"]))
        if path in {"plans", "build-plans"}:
            data = await body(request, PlanInput)
            legacy = data.legacy_generation_id or data.legacy_generation
            return await call(
                lambda: service.create_plan(data.database_id, data.tags, data.repo_id, legacy)
            )
        if path == "jobs":
            if method == "GET":
                return {"items": service.catalog.list("job")}
            data = await body(request, JobInput)
            return await call(lambda: service.create_job(data.plan_id, data.idempotency_key))
        job_id = parameters["job_id"]
        if path.endswith("/events"):
            return {
                "items": service.catalog.events(
                    job_id, int(request.query_params.get("after_seq", "0"))
                )
            }
        if path.endswith("/cancel"):
            return service.cancel(job_id)
        if path.endswith(("/resume", "/retry")):
            return service.resume(job_id)
        return service.catalog.get("job", job_id)

    definitions = [
        ("config", ["GET"]),
        ("source-roots", ["GET"]),
        ("directories", ["GET"]),
        ("legacy-generations", ["GET"]),
        ("repositories", ["GET", "POST"]),
        ("repositories/{repo_id}/tags", ["GET"]),
        ("databases", ["GET", "POST"]),
        ("databases/{database_id}/space", ["GET"]),
        ("plans", ["POST"]),
        ("build-plans", ["POST"]),
        ("jobs", ["GET", "POST"]),
        ("jobs/{job_id}", ["GET"]),
        ("jobs/{job_id}/events", ["GET"]),
        ("jobs/{job_id}/cancel", ["POST"]),
        ("jobs/{job_id}/resume", ["POST"]),
        ("jobs/{job_id}/retry", ["POST"]),
    ]
    return [Route("/api/admin/" + path, dispatch, methods=methods) for path, methods in definitions]


def _error(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}},
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )
