from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Scope = Literal["user", "project"]
ConfigFormat = Literal["json", "jsonc", "toml"]
SKILL_NAME = "codalith-source"

_ALIASES = {
    "codex": "codex",
    "claude": "claude-code",
    "claude-code": "claude-code",
    "cursor": "cursor",
    "windsurf": "windsurf",
    "vscode": "vscode-copilot",
    "vscode-copilot": "vscode-copilot",
    "copilot": "vscode-copilot",
    "gemini": "gemini-cli",
    "gemini-cli": "gemini-cli",
    "opencode": "opencode",
}

_DOCS = {
    "codex": (
        "https://developers.openai.com/codex/mcp/",
        "https://developers.openai.com/codex/skills/",
    ),
    "claude-code": (
        "https://code.claude.com/docs/en/mcp",
        "https://code.claude.com/docs/en/skills",
        "https://code.claude.com/docs/en/settings",
    ),
    "cursor": ("https://cursor.com/docs/mcp", "https://cursor.com/docs/skills"),
    "windsurf": (
        "https://github.com/github/github-mcp-server/blob/main/docs/installation-guides/install-windsurf.md",
        "https://docs.devin.ai/desktop/cascade/mcp",
        "https://docs.devin.ai/desktop/cascade/skills",
    ),
    "vscode-copilot": (
        "https://code.visualstudio.com/docs/agent-customization/mcp-servers",
        "https://code.visualstudio.com/docs/agents/reference/mcp-configuration",
        "https://code.visualstudio.com/docs/agent-customization/agent-skills",
    ),
    "gemini-cli": (
        "https://geminicli.com/docs/tools/mcp-server/",
        "https://geminicli.com/docs/reference/configuration/",
        "https://geminicli.com/docs/cli/skills/",
        "https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/configuration.md",
    ),
    "opencode": (
        "https://opencode.ai/docs/config/",
        "https://opencode.ai/docs/mcp-servers/",
        "https://opencode.ai/docs/skills/",
        "https://github.com/anomalyco/opencode/blob/70881b2937fab1e425416659445875952730934a/packages/opencode/src/global/index.ts",
    ),
}


@dataclass(frozen=True)
class AgentTarget:
    agent: str
    scope: Scope
    config_path: Path
    config_format: ConfigFormat
    server_path: tuple[str, ...]
    skill_dir: Path
    command: str
    detection_dirs: tuple[Path, ...]
    notes: tuple[str, ...]
    docs: tuple[str, ...]


def agent_names() -> tuple[str, ...]:
    return tuple(_DOCS)


def canonical_agent(agent: str) -> str:
    try:
        return _ALIASES[agent.lower()]
    except KeyError:
        raise ValueError(f"Unsupported agent: {agent}. Choose from {', '.join(agent_names())}.") from None


def _environment_path(env: Mapping[str, str], key: str, default: Path, home: Path) -> Path:
    value = env.get(key)
    if not value:
        return default
    if value == "~":
        return home
    if value.startswith(("~/", "~\\")):
        return home / value[2:]
    return Path(value)


def _json_config(directory: Path, stem: str) -> Path:
    json_path = directory / f"{stem}.json"
    jsonc_path = directory / f"{stem}.jsonc"
    if json_path.exists() and jsonc_path.exists():
        raise ValueError(f"Both {json_path} and {jsonc_path} exist; consolidate before installing.")
    return jsonc_path if jsonc_path.exists() else json_path


def _vscode_profile(home: Path, env: Mapping[str, str], platform: str) -> Path:
    if platform == "win32":
        appdata = _environment_path(env, "APPDATA", home / "AppData" / "Roaming", home)
        return appdata / "Code" / "User"
    if platform == "darwin":
        return home / "Library" / "Application Support" / "Code" / "User"
    config = _environment_path(env, "XDG_CONFIG_HOME", home / ".config", home)
    return config / "Code" / "User"


def resolve_agent(
    agent: str,
    *,
    scope: Scope = "user",
    home: Path | None = None,
    project_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> AgentTarget:
    name = canonical_agent(agent)
    if scope not in ("user", "project"):
        raise ValueError(f"Unsupported install scope: {scope}")
    if scope == "project" and project_dir is None:
        raise ValueError("Project scope requires an explicit project directory.")
    if name == "windsurf" and scope == "project":
        raise ValueError("Windsurf/Cascade MCP supports user scope only; use --scope user.")
    task_home = Path.home() if home is None else home
    environment = os.environ if env is None else env
    host_platform = sys.platform if platform is None else platform
    base = project_dir if scope == "project" else task_home
    assert base is not None
    skill_dir = base / ".agents" / "skills" / SKILL_NAME
    config_format: ConfigFormat = "json"
    server_path = ("mcpServers",)
    detection_dirs: tuple[Path, ...]
    notes: tuple[str, ...] = ()

    if name == "codex":
        user_root = _environment_path(environment, "CODEX_HOME", task_home / ".codex", task_home)
        config_path = (base / ".codex" if scope == "project" else user_root) / "config.toml"
        config_format = "toml"
        server_path = ("mcp_servers",)
        command = "codex"
        detection_dirs = (user_root,)
        notes = ("Project MCP configuration loads only after the project is trusted.",)
    elif name == "claude-code":
        user_root = _environment_path(
            environment, "CLAUDE_CONFIG_DIR", task_home / ".claude", task_home
        )
        user_config = (
            user_root / ".claude.json"
            if environment.get("CLAUDE_CONFIG_DIR")
            else task_home / ".claude.json"
        )
        config_path = base / ".mcp.json" if scope == "project" else user_config
        skill_dir = (base / ".claude" if scope == "project" else user_root) / "skills" / SKILL_NAME
        command = "claude"
        detection_dirs = (user_root,)
        notes = ("Interactive sessions may request approval for project MCP servers.",)
    elif name == "cursor":
        config_path = base / ".cursor" / "mcp.json"
        command = "cursor"
        detection_dirs = (task_home / ".cursor",)
        notes = ("Reload Cursor or refresh its MCP and skill lists after installation.",)
    elif name == "windsurf":
        user_root = task_home / ".codeium" / "windsurf"
        config_path = user_root / "mcp_config.json"
        command = "windsurf"
        detection_dirs = (user_root,)
        notes = (
            "Targets legacy Windsurf/Cascade, not the Devin Local agent.",
            "Windsurf MCP is user-wide; refresh its MCP list or restart after installation.",
        )
    elif name == "vscode-copilot":
        user_root = _environment_path(environment, "COPILOT_HOME", task_home / ".copilot", task_home)
        profile_root = _vscode_profile(task_home, environment, host_platform)
        portable = base / ".mcp.json" if scope == "project" else user_root / "mcp-config.json"
        profile_config = (
            base / ".vscode" / "mcp.json" if scope == "project" else profile_root / "mcp.json"
        )
        config_path = portable
        if not portable.exists() and profile_config.exists():
            config_path = profile_config
            server_path = ("servers",)
        config_format = "jsonc"
        command = "code"
        detection_dirs = (user_root, profile_root, task_home / ".vscode")
        notes = (
            "New installs use the portable Copilot format; existing VS Code profile files stay in place.",
            "Trust or enable the MCP server in VS Code if prompted, then start a fresh chat.",
        )
    elif name == "gemini-cli":
        user_home = _environment_path(environment, "GEMINI_CLI_HOME", task_home, task_home)
        user_root = user_home / ".gemini"
        config_path = (base / ".gemini" if scope == "project" else user_root) / "settings.json"
        if scope == "user":
            skill_dir = user_home / ".agents" / "skills" / SKILL_NAME
        command = "gemini"
        detection_dirs = (user_root,)
        notes = ("Reload skills with /skills reload; Gemini may request consent when a skill activates.",)
    else:
        config_root = _environment_path(environment, "XDG_CONFIG_HOME", task_home / ".config", task_home)
        user_root = config_root / "opencode"
        config_path = _json_config(base if scope == "project" else user_root, "opencode")
        if scope == "user":
            config_path = _environment_path(environment, "OPENCODE_CONFIG", config_path, task_home)
        config_format = "jsonc"
        server_path = ("mcp",)
        command = "opencode"
        detection_dirs = (user_root,)
        notes = ("Uses stable OpenCode mcp.<name> entries, not the separate development-v2 format.",)

    return AgentTarget(
        agent=name,
        scope=scope,
        config_path=config_path,
        config_format=config_format,
        server_path=server_path,
        skill_dir=skill_dir,
        command=command,
        detection_dirs=detection_dirs,
        notes=notes,
        docs=_DOCS[name],
    )


def mcp_entry(agent: str, url: str) -> dict[str, object]:
    name = canonical_agent(agent)
    if name == "codex" or name == "cursor":
        return {"url": url}
    if name == "windsurf":
        return {"serverUrl": url}
    if name == "gemini-cli":
        return {"httpUrl": url}
    if name == "opencode":
        return {"type": "remote", "url": url, "enabled": True}
    return {"type": "http", "url": url}
