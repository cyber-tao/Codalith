"""Plan and safely apply native skill/MCP configuration without runtime dependencies."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codalith.install.agents import AgentTarget, Scope, agent_names, resolve_agent
from codalith.install.agents import mcp_entry as agent_entry
from codalith.install.configs import merge_server
from codalith.install.probe import DEFAULT_ENDPOINT, probe_endpoint, validate_endpoint

SERVER_NAME = "codalith"
SKILL_MARKER = ".codalith-install.json"
MAX_CONFIG_BYTES = 10 * 1024 * 1024
_HOME_ENV = (
    "CODEX_HOME", "CLAUDE_CONFIG_DIR", "COPILOT_HOME", "GEMINI_CLI_HOME",
    "OPENCODE_CONFIG", "XDG_CONFIG_HOME", "APPDATA", "LOCALAPPDATA",
)


@dataclass(frozen=True)
class FileChange:
    path: Path
    original: bytes | None
    content: bytes
    kind: str


@dataclass(frozen=True)
class InstallPlan:
    targets: tuple[AgentTarget, ...]
    changes: tuple[FileChange, ...]
    endpoint: str

    def describe(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "agents": [
                {"agent": target.agent, "scope": target.scope, "config": str(target.config_path),
                 "skill": str(target.skill_dir), "notes": list(target.notes)}
                for target in self.targets
            ],
            "changes": [{"path": str(item.path), "kind": item.kind, "action": "create" if item.original is None else "update"} for item in self.changes],
        }


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def skill_bytes() -> bytes:
    candidates = (
        repository_root() / "skills" / "codalith-source" / "SKILL.md",
        Path(sys.prefix) / "share" / "codalith" / "skills" / "codalith-source" / "SKILL.md",
    )
    for path in candidates:
        if path.is_file():
            return path.read_bytes()
    raise ValueError("The packaged codalith-source skill is missing; reinstall Codalith or run from its checkout.")


def _read(path: Path) -> bytes | None:
    if path.is_symlink():
        raise ValueError(f"Refusing to replace a symlink: {path}. Install into a regular configuration path.")
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError(f"Installation target is not a regular file: {path}")
    if path.stat().st_size > MAX_CONFIG_BYTES:
        raise ValueError(f"Installation target exceeds the configuration size limit: {path}")
    return path.read_bytes()


def _decode(data: bytes, path: Path) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeError:
        raise ValueError(f"Configuration must be UTF-8: {path}") from None


def select_targets(
    agents: Sequence[str], *, scope: Scope = "user", home: Path | None = None,
    project_dir: Path | None = None, env: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> tuple[AgentTarget, ...]:
    environment = dict(os.environ if env is None else env)
    if home is not None:
        for key in _HOME_ENV:
            environment.pop(key, None)
    selected = list(agents) or ["auto"]
    if any(name in {"all", "auto"} for name in selected) and len(selected) != 1:
        raise ValueError("Use --agent all or --agent auto alone, or repeat explicit agent names.")
    targets: dict[str, AgentTarget] = {}
    automatic = selected == ["auto"]
    candidates = agent_names() if selected[0] in {"auto", "all"} else selected
    for name in candidates:
        if automatic and scope == "project" and name == "windsurf":
            continue
        if selected == ["all"] and scope == "project" and name == "windsurf":
            continue
        target = resolve_agent(name, scope=scope, home=home, project_dir=project_dir, env=environment, platform=platform)
        detected = target.config_path.exists() or any(path.is_dir() for path in target.detection_dirs)
        if automatic and not detected and (home is not None or not shutil.which(target.command)):
            continue
        targets[target.agent] = target
    if not targets:
        raise ValueError("No supported agents detected. Specify --agent codex, --agent claude-code, or --agent all explicitly.")
    return tuple(targets.values())


def build_plan(
    targets: Sequence[AgentTarget], *, endpoint: str = DEFAULT_ENDPOINT,
    replace: bool = False, source: bytes | None = None,
) -> InstallPlan:
    validate_endpoint(endpoint)
    data = skill_bytes() if source is None else source
    digest = hashlib.sha256(data).hexdigest()
    marker_data = (json.dumps({"schema_version": 1, "skill": "codalith-source", "sha256": digest}, indent=2) + "\n").encode("utf-8")
    changes: dict[Path, FileChange] = {}

    def add(path: Path, content: bytes, kind: str) -> None:
        original = _read(path)
        if content == original:
            return
        if path in changes and changes[path].content != content:
            raise ValueError(f"Selected agents require incompatible changes to {path}")
        changes[path] = FileChange(path, original, content, kind)

    for target in targets:
        path = target.config_path
        previous = changes.get(path)
        original = previous.original if previous else _read(path)
        current = previous.content if previous else original
        text = _decode(current, path) if current is not None else "" if target.config_format == "toml" else "{}\n"
        try:
            merged = merge_server(text, format=target.config_format, server_path=target.server_path, name=SERVER_NAME, entry=agent_entry(target.agent, endpoint), replace=replace)
        except ValueError as exc:
            raise ValueError(f"Cannot configure {target.agent} at {path}: {exc}") from None
        content = merged.encode("utf-8")
        if original is not None and original.startswith(b"\xef\xbb\xbf"):
            content = b"\xef\xbb\xbf" + content
        if content != original:
            changes[path] = FileChange(path, original, content, "mcp-config")
        skill_path = target.skill_dir / "SKILL.md"
        marker_path = target.skill_dir / SKILL_MARKER
        installed = _read(skill_path)
        marker_raw = _read(marker_path)
        managed = False
        if marker_raw is not None:
            try:
                marker = json.loads(marker_raw)
                managed = bool(isinstance(marker, dict) and marker.get("schema_version") == 1 and marker.get("skill") == "codalith-source" and installed is not None and marker.get("sha256") == hashlib.sha256(installed).hexdigest())
            except (ValueError, UnicodeError):
                raise ValueError(f"Invalid skill ownership marker: {marker_path}") from None
        if installed is not None and installed != data and not managed and not replace:
            raise ValueError(f"Existing skill is unowned or locally modified: {skill_path}. Review it, then use --replace to back it up and update.")
        add(skill_path, data, "skill")
        add(marker_path, marker_data, "skill-marker")
    return InstallPlan(tuple(targets), tuple(changes.values()), endpoint)


def _atomic_write(path: Path, content: bytes, mode: int) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{path.name}.codalith-", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def apply_plan(plan: InstallPlan) -> list[dict[str, str]]:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    backups: list[dict[str, str]] = []
    applied: list[tuple[FileChange, int]] = []
    for change in plan.changes:
        if _read(change.path) != change.original:
            raise ValueError(f"Configuration changed since planning: {change.path}. Run the installer again.")
    try:
        for change in plan.changes:
            path = change.path
            path.parent.mkdir(parents=True, exist_ok=True)
            if _read(path) != change.original:
                raise ValueError(f"Configuration changed during installation: {path}")
            mode = stat.S_IMODE(path.stat().st_mode) if change.original is not None else 0o600
            if change.original is not None:
                backup = path.with_name(f"{path.name}.codalith-backup-{stamp}")
                descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(change.original)
                    stream.flush()
                    os.fsync(stream.fileno())
                backup.chmod(mode)
                backups.append({"path": str(path), "backup": str(backup)})
            applied.append((change, mode))
            _atomic_write(path, change.content, mode)
    except (OSError, ValueError, KeyboardInterrupt) as exc:
        unresolved = []
        for change, mode in reversed(applied):
            try:
                current = _read(change.path)
                if current == change.original:
                    continue
                if current != change.content:
                    unresolved.append(str(change.path))
                elif change.original is None:
                    change.path.unlink()
                else:
                    _atomic_write(change.path, change.original, mode)
            except (OSError, ValueError):
                unresolved.append(str(change.path))
        if unresolved:
            raise ValueError("Installation failed; concurrent edits or I/O errors prevented rollback at: " + ", ".join(unresolved) + ". Original backups remain beside the files.") from None
        raise ValueError("Installation failed and completed file changes were rolled back. Original backups remain beside existing files.") from exc
    return backups


def service_command(profile: str, *, root: Path | None = None) -> list[str]:
    repo = repository_root() if root is None else root
    if profile not in {"sample", "ue"}:
        raise ValueError("Service profile must be sample or ue.")
    if not (repo / "docker-compose.yml").is_file():
        raise ValueError("--start-service requires a Codalith checkout containing docker-compose.yml.")
    command = ["docker", "compose", "--project-directory", str(repo), "--file", str(repo / "docker-compose.yml")]
    if profile == "ue":
        command.extend(["--profile", "ue"])
    command.extend(["up", "-d", "--build"])
    if profile == "ue":
        command.append("--no-deps")
    return [*command, "mcp-http-ue" if profile == "ue" else "mcp-http"]


def start_service(command: Sequence[str], endpoint: str, *, wait_seconds: float = 120) -> dict[str, Any]:
    if not shutil.which("docker"):
        raise ValueError("Docker with Compose is required for --start-service.")
    print("Building and starting the selected Docker service; this can take several minutes.", file=sys.stderr)
    try:
        completed = subprocess.run(list(command), capture_output=True, check=False, timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("Docker Compose could not finish; inspect the service with docker compose ps/logs. No client files were changed.") from None
    if completed.returncode:
        raise ValueError(f"Docker Compose failed (exit {completed.returncode}); inspect docker compose logs. No client files were changed.")
    print("Waiting for the MCP handshake and default index readiness.", file=sys.stderr)
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            return probe_endpoint(endpoint, timeout=5)
        except ValueError:
            if time.monotonic() >= deadline:
                raise ValueError("Service started but the MCP handshake did not become ready. Check Docker logs and --endpoint; no client files were changed.") from None
            time.sleep(1)
