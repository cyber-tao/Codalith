from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from codalith.install.agents import Scope, agent_names
from codalith.install.core import (
    apply_plan,
    build_plan,
    select_targets,
    service_command,
    start_service,
)
from codalith.install.probe import DEFAULT_ENDPOINT, probe_endpoint


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--agent", action="append", default=[], help=f"Repeat agent names, or use auto (default) / all: {', '.join(agent_names())}")
    parser.add_argument("--scope", choices=["user", "project"], default="user")
    parser.add_argument("--project", type=Path, help="Project directory (defaults to cwd for project scope)")
    parser.add_argument("--home", type=Path, help="Use an isolated home; ignores host path overrides and executable detection")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Plan without writing files or starting services")
    mode.add_argument("--check", action="store_true", help="Verify installed skill/configuration without repairing")
    parser.add_argument("--replace", action="store_true", help="Back up and replace conflicting Codalith entries or modified skills")
    parser.add_argument("--no-probe", action="store_true", help="Configure offline without claiming MCP connectivity")
    parser.add_argument("--start-service", choices=["sample", "ue"], help="Explicitly start Docker service before client installation")


def run_from_args(args: argparse.Namespace) -> int:
    if args.start_service and (args.check or args.no_probe):
        raise ValueError("--start-service cannot be combined with --check or --no-probe.")
    if args.check and args.replace:
        raise ValueError("--check cannot be combined with --replace.")
    scope: Scope = args.scope
    project = (args.project or Path.cwd()).resolve() if scope == "project" else None
    if args.project and scope != "project":
        raise ValueError("--project requires --scope project.")
    home = args.home.expanduser().resolve() if args.home else None
    targets = select_targets(args.agent, scope=scope, home=home, project_dir=project)
    plan = build_plan(targets, endpoint=args.endpoint, replace=args.replace)
    result = plan.describe()
    command = service_command(args.start_service) if args.start_service else None
    result["service_command"] = command
    if args.dry_run:
        result.update({"status": "dry-run", "connectivity": {"connected": None, "reason": "Not probed during dry-run"}, "backups": []})
    else:
        connectivity = start_service(command, args.endpoint) if command else {"connected": None, "reason": "Offline configuration requested"} if args.no_probe else probe_endpoint(args.endpoint)
        result["connectivity"] = connectivity
        if args.check:
            result.update({"status": "needs-install" if plan.changes else "verified", "backups": []})
        else:
            result.update({"status": "installed" if plan.changes else "unchanged", "backups": apply_plan(plan)})
    print(json.dumps(result, indent=2))
    return 1 if args.check and plan.changes else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="codalith install-agents", description="Install the native source skill and merge Codalith MCP into selected agent configurations.")
    add_arguments(parser)
    args = parser.parse_args(argv)
    try:
        return run_from_args(args)
    except (OSError, ValueError) as exc:
        print(error_message(exc), file=sys.stderr)
        return 2


def error_message(exc: Exception) -> str:
    encoding = sys.stderr.encoding or "utf-8"
    return f"error: {exc}".encode(encoding, errors="backslashreplace").decode(encoding)


if __name__ == "__main__":
    raise SystemExit(main())
