"""Local management commands using the same durable catalog and worker locks."""

from __future__ import annotations

import argparse
import json
from typing import Any

from codalith.corpus.registry import CorpusRegistry
from codalith.corpus.source_policy import SourcePolicy
from codalith.errors import IndexBuildError
from codalith.management.config import ManagementConfig
from codalith.management.service import ManagementService


def add_arguments(subparsers: argparse._SubParsersAction[Any]) -> None:
    parser = subparsers.add_parser("management", help="Manage shared version databases locally")
    commands = parser.add_subparsers(dest="management_command", required=True)
    commands.add_parser("config")
    repositories = commands.add_parser("repositories")
    repositories.add_argument("--path")
    repositories.add_argument("--root-id")
    tags = commands.add_parser("tags")
    tags.add_argument("repo_id")
    tags.add_argument("--query", default="")
    tags.add_argument("--cursor", default="0")
    tags.add_argument("--limit", type=int, default=100)
    databases = commands.add_parser("databases")
    databases.add_argument("--name")
    databases.add_argument("--profile-id")
    databases.add_argument("--repo-id")
    plan = commands.add_parser("plan")
    plan.add_argument("--database-id", required=True)
    plan.add_argument("--repo-id")
    plan.add_argument("--tag", action="append", required=True)
    plan.add_argument("--legacy-generation")
    build = commands.add_parser("build")
    build.add_argument("--plan-id", required=True)
    build.add_argument("--idempotency-key", required=True)
    build.add_argument("--submit-only", action="store_true")
    jobs = commands.add_parser("jobs")
    jobs.add_argument("--job-id")
    for name in ("cancel", "resume", "retry"):
        action = commands.add_parser(name)
        action.add_argument("job_id")
    space = commands.add_parser("space")
    space.add_argument("--database-id")
    space.add_argument("--refresh", action="store_true")
    commands.add_parser("upgrade-storage", help="Back up and explicitly upgrade the shared catalog")
    gc = commands.add_parser("gc")
    gc.add_argument("--dry-run", action="store_true", required=True)


def run(args: argparse.Namespace, registry: CorpusRegistry, policy: SourcePolicy) -> int:
    result: Any
    command = args.management_command
    config = ManagementConfig.from_registry(registry.path)
    if command == "upgrade-storage":
        from codalith.indexing.shared import SharedPool

        if not config.enabled:
            raise ValueError("Management is disabled")
        root = config.storage_root / "shared"
        if not (root / "catalog.sqlite").is_file():
            raise IndexBuildError("No shared catalog exists to upgrade")
        result = SharedPool(root, policy).upgrade()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    service = ManagementService(registry, policy, config)
    if command == "config":
        result = service.public_config()
    elif command == "repositories":
        result = (
            service.add_repository(args.path, args.root_id)
            if args.path
            else {"items": service.catalog.list("repository")}
        )
    elif command == "tags":
        result = service.tags(args.repo_id, q=args.query, cursor=args.cursor, limit=args.limit)
    elif command == "databases":
        if args.name and not args.profile_id:
            raise ValueError("Creating a database requires --profile-id")
        result = (
            service.create_database(args.name, args.profile_id, args.repo_id)
            if args.name
            else {"items": service.catalog.list("database")}
        )
    elif command == "plan":
        result = service.create_plan(
            args.database_id, args.tag, args.repo_id, args.legacy_generation
        )
    elif command == "build":
        job = service.create_job(args.plan_id, args.idempotency_key)
        if not args.submit_only:
            try:
                while service.catalog.get("job", job["job_id"])["status"] == "queued":
                    if not service.run_pending_once():
                        break
            except IndexBuildError:
                pass
        result = service.catalog.get("job", job["job_id"])
    elif command == "jobs":
        result = (
            service.catalog.get("job", args.job_id)
            if args.job_id
            else {"items": service.catalog.list("job")}
        )
    elif command == "cancel":
        result = service.cancel(args.job_id)
    elif command in {"resume", "retry"}:
        result = service.resume(args.job_id)
    elif command == "space":
        result = service.space(args.database_id, refresh=args.refresh)
    elif command == "gc":
        from codalith.indexing.shared import SharedPool

        pool = SharedPool(service.config.storage_root / "shared", policy)
        result = pool.gc(dry_run=True)
    else:
        raise ValueError("Unknown management command")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return (
        1
        if isinstance(result, dict) and result.get("status") in {"failed", "partial", "interrupted"}
        else 0
    )
