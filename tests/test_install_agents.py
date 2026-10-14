from pathlib import Path

import pytest

from codalith.install.agents import agent_names, canonical_agent, mcp_entry, resolve_agent

ENDPOINT = "http://127.0.0.1:8765/mcp"


@pytest.mark.parametrize(
    ("agent", "relative_config", "config_format", "server_path", "entry"),
    [
        ("codex", ".codex/config.toml", "toml", ("mcp_servers",), {"url": ENDPOINT}),
        (
            "claude-code", ".claude.json", "json", ("mcpServers",),
            {"type": "http", "url": ENDPOINT},
        ),
        ("cursor", ".cursor/mcp.json", "json", ("mcpServers",), {"url": ENDPOINT}),
        (
            "windsurf", ".codeium/windsurf/mcp_config.json", "json", ("mcpServers",),
            {"serverUrl": ENDPOINT},
        ),
        (
            "vscode-copilot", ".copilot/mcp-config.json", "jsonc", ("mcpServers",),
            {"type": "http", "url": ENDPOINT},
        ),
        (
            "gemini-cli", ".gemini/settings.json", "json", ("mcpServers",),
            {"httpUrl": ENDPOINT},
        ),
        (
            "opencode", ".config/opencode/opencode.json", "jsonc", ("mcp",),
            {"type": "remote", "url": ENDPOINT, "enabled": True},
        ),
    ],
)
@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_user_targets_match_documented_client_formats(
    tmp_path, agent, relative_config, config_format, server_path, entry, platform
):
    home = tmp_path / "user home"
    target = resolve_agent(agent, home=home, env={}, platform=platform)
    assert target.config_path == home / relative_config
    assert target.config_format == config_format
    assert target.server_path == server_path
    assert mcp_entry(agent, ENDPOINT) == entry
    expected_skill_root = home / ".claude" if agent == "claude-code" else home / ".agents"
    assert target.skill_dir == expected_skill_root / "skills" / "codalith-source"
    assert home not in target.detection_dirs
    assert target.docs and all(url.startswith("https://") for url in target.docs)
    assert not home.exists()


@pytest.mark.parametrize(
    ("agent", "relative_config", "server_path"),
    [
        ("codex", ".codex/config.toml", ("mcp_servers",)),
        ("claude-code", ".mcp.json", ("mcpServers",)),
        ("cursor", ".cursor/mcp.json", ("mcpServers",)),
        ("vscode-copilot", ".mcp.json", ("mcpServers",)),
        ("gemini-cli", ".gemini/settings.json", ("mcpServers",)),
        ("opencode", "opencode.json", ("mcp",)),
    ],
)
def test_project_targets_do_not_redirect_to_user_environment(
    tmp_path, agent, relative_config, server_path
):
    project = tmp_path / "project"
    redirected = tmp_path / "user redirect"
    env = dict.fromkeys(
        ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "COPILOT_HOME", "GEMINI_CLI_HOME", "OPENCODE_CONFIG"),
        str(redirected),
    )
    target = resolve_agent(agent, scope="project", home=tmp_path, project_dir=project, env=env)
    assert target.config_path == project / relative_config
    assert target.server_path == server_path
    skill_root = project / ".claude" if agent == "claude-code" else project / ".agents"
    assert target.skill_dir == skill_root / "skills" / "codalith-source"


@pytest.mark.parametrize(
    ("alias", "canonical"),
    [
        ("Claude", "claude-code"), ("vscode", "vscode-copilot"),
        ("copilot", "vscode-copilot"), ("gemini", "gemini-cli"),
    ],
)
def test_client_aliases_are_consistent(tmp_path, alias, canonical):
    assert canonical_agent(alias) == canonical
    assert resolve_agent(alias, home=tmp_path, env={}).agent == canonical
    assert mcp_entry(alias, ENDPOINT) == mcp_entry(canonical, ENDPOINT)


def test_invalid_agents_and_unsupported_project_scope_are_explicit(tmp_path):
    with pytest.raises(ValueError, match="Unsupported agent"):
        resolve_agent("imaginary-agent", home=tmp_path, env={})
    with pytest.raises(ValueError, match="explicit project"):
        resolve_agent("codex", scope="project", home=tmp_path, env={})
    with pytest.raises(ValueError, match="user scope only"):
        resolve_agent("windsurf", scope="project", home=tmp_path, project_dir=tmp_path, env={})


def test_codex_and_claude_configuration_redirects_do_not_move_shared_skills(tmp_path):
    custom = tmp_path / "custom"
    codex = resolve_agent("codex", home=tmp_path, env={"CODEX_HOME": str(custom)})
    assert codex.config_path == custom / "config.toml"
    assert codex.skill_dir == tmp_path / ".agents" / "skills" / "codalith-source"
    claude = resolve_agent("claude", home=tmp_path, env={"CLAUDE_CONFIG_DIR": str(custom)})
    assert claude.config_path == custom / ".claude.json"
    assert claude.skill_dir == custom / "skills" / "codalith-source"


def test_gemini_home_is_a_root_containing_dot_gemini_not_the_configuration_directory(tmp_path):
    custom = tmp_path / "isolated home"
    target = resolve_agent("gemini", home=tmp_path, env={"GEMINI_CLI_HOME": str(custom)})
    assert target.config_path == custom / ".gemini" / "settings.json"
    assert target.skill_dir == custom / ".agents" / "skills" / "codalith-source"
    assert target.detection_dirs == (custom / ".gemini",)


def test_copilot_portable_home_override(tmp_path):
    custom = tmp_path / "copilot"
    target = resolve_agent("vscode", home=tmp_path, env={"COPILOT_HOME": str(custom)})
    assert target.config_path == custom / "mcp-config.json"
    assert target.server_path == ("mcpServers",)


@pytest.mark.parametrize(
    ("platform", "relative_profile", "variable"),
    [
        ("win32", "roaming/Code/User", "APPDATA"),
        ("darwin", "Library/Application Support/Code/User", None),
        ("linux", "config/Code/User", "XDG_CONFIG_HOME"),
    ],
)
def test_existing_vscode_profile_uses_its_own_servers_shape(
    tmp_path, platform, relative_profile, variable
):
    profile = tmp_path / relative_profile
    profile.mkdir(parents=True)
    (profile / "mcp.json").write_text("{ // Existing profile\n }", encoding="utf-8")
    env = {variable: str(tmp_path / relative_profile.split("/")[0])} if variable else {}
    target = resolve_agent("vscode", home=tmp_path, env=env, platform=platform)
    assert target.config_path == profile / "mcp.json"
    assert target.server_path == ("servers",)
    portable = tmp_path / ".copilot" / "mcp-config.json"
    portable.parent.mkdir()
    portable.write_text("{}", encoding="utf-8")
    assert resolve_agent("vscode", home=tmp_path, env=env, platform=platform).config_path == portable


def test_project_vscode_preserves_existing_legacy_configuration_location(tmp_path):
    legacy = tmp_path / ".vscode" / "mcp.json"
    legacy.parent.mkdir()
    legacy.write_text("{}", encoding="utf-8")
    target = resolve_agent("vscode", scope="project", project_dir=tmp_path, env={})
    assert target.config_path == legacy
    assert target.server_path == ("servers",)


def test_opencode_xdg_jsonc_and_explicit_file_override(tmp_path):
    root = tmp_path / "xdg" / "opencode"
    root.mkdir(parents=True)
    config = root / "opencode.jsonc"
    config.write_text("{}", encoding="utf-8")
    env = {"XDG_CONFIG_HOME": str(root.parent)}
    target = resolve_agent("opencode", home=tmp_path, env=env)
    assert target.config_path == config
    assert target.config_format == "jsonc"
    custom = tmp_path / "custom.jsonc"
    assert resolve_agent(
        "opencode", home=tmp_path, env=env | {"OPENCODE_CONFIG": str(custom)}
    ).config_path == custom


def test_opencode_rejects_ambiguous_two_file_configuration(tmp_path):
    root = tmp_path / ".config" / "opencode"
    root.mkdir(parents=True)
    for name in ("opencode.json", "opencode.jsonc"):
        (root / name).write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="Both"):
        resolve_agent("opencode", home=tmp_path, env={})


def test_resolving_targets_does_not_read_user_config_contents(tmp_path, monkeypatch):
    def reject_read(*args, **kwargs):
        raise AssertionError("Resolving targets must not read configuration contents")

    monkeypatch.setattr(Path, "read_text", reject_read)
    for name in agent_names():
        resolve_agent(name, home=tmp_path, env={})


def test_environment_tilde_paths_use_the_supplied_home(tmp_path):
    target = resolve_agent("codex", home=tmp_path, env={"CODEX_HOME": "~/custom-codex"})
    assert target.config_path == tmp_path / "custom-codex" / "config.toml"
