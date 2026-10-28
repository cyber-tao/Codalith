"""Local project detection and atomic, version-bound MCP installation."""

from __future__ import annotations

import argparse
import configparser
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import Request, build_opener

from codalith.install.core import FileChange, InstallPlan, apply_plan, build_plan, select_targets
from codalith.install.probe import MAX_RESPONSE_BYTES, _NoRedirect, validate_endpoint

BINDING_PATH = Path(".codalith/project.json")
_VERSION = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?$")
_TARGET = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_MAX_PROJECT_BYTES = 1024 * 1024


def catalog_url(server: str) -> str:
    parsed = urlsplit(validate_endpoint(server))
    return urlunsplit((parsed.scheme, parsed.netloc, "/api/catalog", "", ""))


def bound_endpoint(server: str, target: str) -> str:
    if not _TARGET.fullmatch(target):
        raise ValueError("Invalid target id")
    parsed = urlsplit(validate_endpoint(server))
    path = parsed.path.rstrip("/")
    if "/targets/" in path:
        path = path.split("/targets/", 1)[0]
    if not path:
        path = "/mcp"
    return urlunsplit((parsed.scheme, parsed.netloc, f"{path}/targets/{quote(target)}", "", ""))


def fetch_catalog(server: str) -> list[dict[str, Any]]:
    request = Request(catalog_url(server), headers={"Accept": "application/json"})
    with build_opener(_NoRedirect()).open(request, timeout=15) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Version catalog exceeds the response size limit")
    payload = json.loads(raw)
    rows = payload.get("versions", payload.get("targets", [])) if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("Server returned an invalid version catalog")
    return rows


def _json_file(path: Path) -> dict[str, Any]:
    if path.stat().st_size > _MAX_PROJECT_BYTES:
        raise ValueError(f"Project metadata exceeds the size limit: {path.name}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"Project metadata must be an object: {path.name}")
    return payload


def read_binding(project: Path) -> dict[str, Any] | None:
    path = project / BINDING_PATH
    if path.is_symlink():
        raise ValueError("Project binding must not be a symlink")
    if not path.exists():
        return None
    binding = _json_file(path)
    if binding.get("schema_version") != 1:
        raise ValueError("Unsupported project binding schema")
    for key in ("server", "target", "source_commit", "project_file"):
        if not isinstance(binding.get(key), str):
            raise ValueError(f"Invalid project binding field: {key}")
    validate_endpoint(binding["server"])
    if not _TARGET.fullmatch(binding["target"]):
        raise ValueError("Invalid bound target")
    return binding


def _registered_engine(association: str) -> Path | None:
    if sys.platform == "win32":
        import winreg

        lookups = [
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Epic Games\Unreal Engine\Builds", association),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Epic Games\UnrealEngine\Builds", association),
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\EpicGames\Unreal Engine\{association}", "InstalledDirectory"),
        ]
        for hive, key, name in lookups:
            try:
                with winreg.OpenKey(hive, key) as handle:
                    value, _ = winreg.QueryValueEx(handle, name)
                if isinstance(value, str):
                    return Path(value)
            except OSError:
                continue
        return None
    ini = (
        Path.home() / "Library/Application Support/Epic/UnrealEngine/Install.ini"
        if sys.platform == "darwin"
        else Path.home() / ".config/Epic/UnrealEngine/Install.ini"
    )
    if ini.is_file():
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(ini, encoding="utf-8")
        if parser.has_option("Installations", association):
            return Path(parser.get("Installations", association))
    return None


def _engine_version(root: Path) -> str:
    payload = _json_file(root / "Engine/Build/Build.version")
    components = [payload.get(key) for key in ("MajorVersion", "MinorVersion", "PatchVersion")]
    if any(type(value) is not int or value < 0 for value in components):
        raise ValueError("Invalid Engine/Build/Build.version")
    return ".".join(str(value) for value in components)


def _engine_commit(root: Path) -> str | None:
    if not (root / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "--no-replace-objects", "--no-lazy-fetch", "-C", str(root), "rev-parse", "--verify", "HEAD"],
            capture_output=True, check=False, timeout=10,
            env={**{key: value for key, value in os.environ.items() if not key.startswith("GIT_")}, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode == 0:
        value = result.stdout.decode("ascii").strip()
        if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
            return value
    return None


def detect_project(
    project: Path,
    *,
    server: str | None = None,
    project_file: Path | None = None,
    engine_root: Path | None = None,
    target: str | None = None,
    catalog: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    root = project.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Project must be a directory")
    binding = read_binding(root)
    chosen_file = project_file or (Path(binding["project_file"]) if binding else None)
    files = [(root / chosen_file).resolve(strict=True)] if chosen_file else sorted(root.glob("*.uproject"))
    base: dict[str, Any] = {"status": "unknown", "candidates": [], "project_files": [str(p) for p in files]}
    if len(files) != 1:
        base.update(status="ambiguous" if files else "unknown", reason="Select exactly one .uproject with --project-file")
        return base
    selected = files[0]
    if not selected.is_relative_to(root) or selected.suffix != ".uproject":
        raise ValueError("Selected .uproject must be inside the project directory")
    metadata = _json_file(selected)
    association = metadata.get("EngineAssociation", "")
    if not isinstance(association, str):
        raise ValueError("EngineAssociation must be a string")
    inferred = association if _VERSION.fullmatch(association) else None
    engine = engine_root or (Path(binding["engine_root"]) if binding and binding.get("engine_root") else None)
    if engine is None and association:
        engine = _registered_engine(association)
    if engine is None:
        engine = next((p for p in (root, *root.parents) if (p / "Engine/Build/Build.version").is_file()), None)
    commit = None
    if engine is not None:
        engine = engine.expanduser().resolve(strict=True)
        inferred = _engine_version(engine)
        if _VERSION.fullmatch(association) and not (inferred == association or inferred.startswith(association + ".")):
            base.update(status="conflict", reason="EngineAssociation disagrees with the selected engine")
            return base
        commit = _engine_commit(engine)
    address = server or (binding["server"] if binding else None)
    if address is None:
        raise ValueError("--server is required for an unbound project")
    rows = fetch_catalog(address) if catalog is None else catalog
    rows = [row for row in rows if row.get("ready", row.get("state") == "ready")]
    wanted = target or (binding["target"] if binding else None)
    if wanted:
        candidates = [row for row in rows if row.get("target_id", row.get("target")) == wanted]
    elif inferred:
        candidates = [row for row in rows if str(row.get("engine_version", "")) == inferred or (inferred.count(".") == 1 and str(row.get("engine_version", "")).startswith(inferred + "."))]
        if commit:
            candidates = [row for row in candidates if row.get("source_commit", row.get("revision")) == commit]
        elif association and not _VERSION.fullmatch(association):
            candidates = []
    else:
        candidates = []
    base.update(
        server=address, project_file=selected.relative_to(root).as_posix(),
        engine_association=association, engine_version=inferred, engine_root=str(engine) if engine else None,
        source_commit=commit, candidates=candidates,
    )
    if len(candidates) != 1:
        base.update(status="ambiguous" if len(candidates) > 1 else "unavailable", reason="No unique ready target matches the project; select an exact target")
        return base
    match = candidates[0]
    actual_version = str(match.get("engine_version", ""))
    revision = str(match.get("source_commit", match.get("revision", "")))
    if inferred and actual_version and not (actual_version == inferred or (inferred.count(".") == 1 and actual_version.startswith(inferred + "."))):
        base.update(status="conflict", reason="Bound target does not match the detected engine version")
        return base
    if commit and revision != commit:
        base.update(status="conflict", reason="Target source commit does not match the local engine")
        return base
    if binding and not target and revision != binding["source_commit"]:
        base.update(status="conflict", reason="Bound target source revision has changed")
        return base
    base.update(status="resolved", target=match.get("target_id", match.get("target")), resolved_version=actual_version, resolved_commit=revision)
    return base


def bind_project(
    project: Path, detection: dict[str, Any], *, agents: list[str], home: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    if detection.get("status") != "resolved":
        raise ValueError("Project binding requires an unambiguous matching version")
    root = project.expanduser().resolve(strict=True)
    endpoint = bound_endpoint(detection["server"], detection["target"])
    targets = select_targets(agents, scope="project", home=home, project_dir=root)
    installation = build_plan(targets, endpoint=endpoint, replace=True)
    binding = {
        "schema_version": 1, "server": detection["server"], "target": detection["target"],
        "source_commit": detection["resolved_commit"], "engine_version": detection["resolved_version"],
        "project_file": detection["project_file"], "engine_root": detection.get("engine_root"),
    }
    path = root / BINDING_PATH
    if path.is_symlink():
        raise ValueError("Project binding must not be a symlink")
    original = path.read_bytes() if path.exists() else None
    content = (json.dumps(binding, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    changes = installation.changes
    if original != content:
        changes += (FileChange(path, original, content, "project-binding"),)
    plan = InstallPlan(installation.targets, changes, endpoint)
    result = plan.describe()
    result.update(status="dry-run" if dry_run else "bound", binding=binding)
    result["backups"] = [] if dry_run else apply_plan(plan)
    return result


def add_arguments(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="project_command", required=True)
    for command in ("detect", "bind"):
        child = commands.add_parser(command)
        child.add_argument("--project", type=Path, default=Path.cwd())
        child.add_argument("--project-file", type=Path)
        child.add_argument("--engine-root", type=Path)
        child.add_argument("--server")
        child.add_argument("--target")
        if command == "bind":
            child.add_argument("--agent", action="append", default=[])
            child.add_argument("--home", type=Path)
            child.add_argument("--dry-run", action="store_true")


def run(args: argparse.Namespace) -> int:
    result = detect_project(args.project, server=args.server, project_file=args.project_file, engine_root=args.engine_root, target=args.target)
    if args.project_command == "bind":
        result = bind_project(args.project, result, agents=args.agent, home=args.home, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"resolved", "bound", "dry-run"} else 1
