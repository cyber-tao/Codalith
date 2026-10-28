from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from codalith.install import core
from codalith.install.__main__ import main
from codalith.install.agents import agent_names, resolve_agent
from codalith.install.core import apply_plan, build_plan, select_targets
from codalith.install.probe import _decode_result, probe_endpoint, validate_endpoint
from test_mcp import running_http_server


def test_all_agents_share_one_skill_and_install_is_idempotent(tmp_path):
    targets = select_targets(["all"], home=tmp_path)
    assert len(targets) == len(agent_names()) == 7
    plan = build_plan(targets)
    assert len([change for change in plan.changes if change.kind == "skill"]) == 2
    assert not list(tmp_path.iterdir())
    assert apply_plan(plan) == []
    assert build_plan(targets).changes == ()
    for target in targets:
        assert (target.skill_dir / "SKILL.md").read_bytes() == core.skill_bytes()
    assert tomllib.loads((tmp_path / ".codex/config.toml").read_text()) == {"mcp_servers": {"codalith": {"url": "http://127.0.0.1:8765/mcp"}}}


def test_backups_preserve_original_bytes_bom_and_unrelated_secret_settings(tmp_path):
    target = resolve_agent("claude", home=tmp_path, env={})
    original = b'\xef\xbb\xbf{\r\n  "someSecret": "local-value",\r\n  "mcpServers": {"other": {"command": "other"}}\r\n}\r\n'
    target.config_path.write_bytes(original)
    plan = build_plan([target])
    backups = apply_plan(plan)
    assert len(backups) == 1 and Path(backups[0]["backup"]).read_bytes() == original
    installed = target.config_path.read_bytes()
    assert installed.startswith(b"\xef\xbb\xbf") and b'"someSecret": "local-value"' in installed
    assert b'"other": {"command": "other"}' in installed
    assert build_plan([target]).changes == ()


def test_owned_skill_upgrades_but_local_changes_require_explicit_replace(tmp_path):
    target = resolve_agent("codex", home=tmp_path, env={})
    apply_plan(build_plan([target], source=b"first skill"))
    changed = build_plan([target], source=b"updated skill")
    assert len(changed.changes) == 2
    apply_plan(changed)
    assert build_plan([target], source=b"updated skill").changes == ()
    skill = target.skill_dir / "SKILL.md"
    skill.write_bytes(b"user changes")
    with pytest.raises(ValueError, match="locally modified"):
        build_plan([target], source=b"updated skill")
    plan = build_plan([target], source=b"updated skill", replace=True)
    backups = apply_plan(plan)
    assert any(Path(backup["backup"]).read_bytes() == b"user changes" for backup in backups)


def test_conflict_fails_before_any_other_target_is_written(tmp_path):
    targets = select_targets(["codex", "claude"], home=tmp_path)
    targets[1].config_path.write_text('{"mcpServers":{"codalith":{"command":"private-old"}}}', encoding="utf-8")
    with pytest.raises(ValueError):
        build_plan(targets)
    assert not targets[0].config_path.exists()
    assert not targets[0].skill_dir.exists()


def test_concurrent_edit_after_planning_is_not_overwritten(tmp_path):
    target = resolve_agent("claude", home=tmp_path, env={})
    plan = build_plan([target])
    target.config_path.write_text('{"user":"concurrent"}', encoding="utf-8")
    with pytest.raises(ValueError, match="changed since planning"):
        apply_plan(plan)
    assert target.config_path.read_text() == '{"user":"concurrent"}'
    assert not target.skill_dir.exists()


def test_write_failure_rolls_back_existing_config_and_retains_backup(tmp_path, monkeypatch):
    target = resolve_agent("claude", home=tmp_path, env={})
    original = b'{"other":"keep"}\n'
    target.config_path.write_bytes(original)
    plan = build_plan([target])
    real_write = core._atomic_write

    def fail_skill(path, content, mode):
        if path.name == "SKILL.md":
            raise OSError("simulated disk failure")
        real_write(path, content, mode)

    monkeypatch.setattr(core, "_atomic_write", fail_skill)
    with pytest.raises(ValueError, match="rolled back"):
        apply_plan(plan)
    assert target.config_path.read_bytes() == original
    assert not (target.skill_dir / "SKILL.md").exists()
    assert [p.read_bytes() for p in tmp_path.glob(".claude.json.codalith-backup-*")] == [original]


def test_interrupt_immediately_after_atomic_replace_rolls_back_that_file(tmp_path, monkeypatch):
    target = resolve_agent("claude", home=tmp_path, env={})
    original = b'{"user":"keep"}\n'
    target.config_path.write_bytes(original)
    plan = build_plan([target])
    real_write = core._atomic_write
    interrupted = False

    def interrupt_after_replace(path, content, mode):
        nonlocal interrupted
        real_write(path, content, mode)
        if not interrupted:
            interrupted = True
            raise KeyboardInterrupt

    monkeypatch.setattr(core, "_atomic_write", interrupt_after_replace)
    with pytest.raises(ValueError, match="rolled back"):
        apply_plan(plan)
    assert target.config_path.read_bytes() == original
    assert not (target.skill_dir / "SKILL.md").exists()


def test_auto_detection_does_not_escape_an_isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "outside"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "outside"))
    monkeypatch.setattr(core.shutil, "which", lambda name: "/host/bin/" + name)
    home = tmp_path / "isolated"
    with pytest.raises(ValueError, match="No supported agents"):
        select_targets([], home=home)
    (home / ".codex").mkdir(parents=True)
    targets = select_targets([], home=home)
    assert [target.agent for target in targets] == ["codex"]
    assert targets[0].config_path == home / ".codex/config.toml"
    assert not (tmp_path / "outside").exists()


def test_dry_run_and_check_never_write_and_offline_status_is_explicit(tmp_path, capsys):
    arguments = ["--agent", "all", "--home", str(tmp_path), "--no-probe"]
    assert main([*arguments, "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "dry-run"
    assert not list(tmp_path.iterdir())
    assert main([*arguments, "--check"]) == 1
    assert not list(tmp_path.iterdir())
    capsys.readouterr()
    assert main(arguments) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["connectivity"]["connected"] is None and result["status"] == "installed"
    assert main([*arguments, "--check"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "verified"


def test_failed_endpoint_probe_leaves_client_files_untouched(tmp_path, monkeypatch, capsys):
    def fail_probe(url):
        raise ValueError("offline endpoint")

    monkeypatch.setattr("codalith.install.__main__.probe_endpoint", fail_probe)
    assert main(["--agent", "all", "--home", str(tmp_path)]) == 2
    assert not list(tmp_path.iterdir()) and "offline endpoint" in capsys.readouterr().err


def test_install_bootstrap_works_without_site_packages(tmp_path):
    script = Path(__file__).parents[1] / "scripts/install_agents.py"
    result = subprocess.run([sys.executable, "-S", str(script), "--agent", "all", "--home", str(tmp_path), "--dry-run"], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert len(json.loads(result.stdout)["agents"]) == 7
    assert not list(tmp_path.iterdir())


def test_standalone_json_and_errors_handle_unicode_under_ascii_terminal(tmp_path):
    home = tmp_path / "user-\U0001f4e6"
    script = Path(__file__).parents[1] / "scripts/install_agents.py"
    command = [sys.executable, "-S", str(script), "--agent", "codex", "--home", str(home), "--no-probe"]
    env = {**os.environ, "PYTHONIOENCODING": "ascii"}
    completed = subprocess.run(command, capture_output=True, text=True, env=env, check=False)
    assert completed.returncode == 0 and json.loads(completed.stdout)["status"] == "installed"
    (home / ".agents/skills/codalith-source/SKILL.md").write_text("user changes", encoding="utf-8")
    completed = subprocess.run(command, capture_output=True, text=True, env=env, check=False)
    assert completed.returncode == 2 and "locally modified" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_installer_probe_uses_real_mcp_and_ready_default_target(semantic_environment):
    with running_http_server(semantic_environment) as endpoint:
        result = probe_endpoint(endpoint)
    assert result["connected"] is True and result["ready"] is True
    assert result["target"] == "sample"
    assert len(result["tools"]) == 8 and result["corpora"][0]["generation_id"]


def test_probe_rejects_degraded_index_without_building_semantics(environment_factory):
    environment = environment_factory(semantic=False)
    with running_http_server(environment) as endpoint:
        with pytest.raises(ValueError, match="no usable index"):
            probe_endpoint(endpoint)


@pytest.mark.parametrize("url", ["ftp://host/mcp", "http://user:secret@host/mcp", "http://host/mcp?token=secret", "http://host/mcp#secret", "http://host:wrong/mcp", "http://host/mcp\n"])
def test_endpoint_validation_rejects_unsafe_or_invalid_settings_without_echoing_them(url):
    with pytest.raises(ValueError) as error:
        validate_endpoint(url)
    assert "secret" not in str(error.value)


def test_sse_decoder_handles_notifications_and_multiline_results():
    stream = io.BytesIO(b': ping\n\ndata: {"jsonrpc":"2.0","method":"notify"}\n\ndata: {"jsonrpc":"2.0",\ndata: "id":2,"result":{"tools":[]}}\n\n')
    stream.headers = {"Content-Type": "text/event-stream"}
    assert _decode_result(stream, 2) == {"tools": []}


def test_ue_service_command_never_schedules_index_build(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services: {}", encoding="utf-8")
    command = core.service_command("ue", root=tmp_path)
    assert command[-1] == "mcp-http-ue" and "--no-deps" in command
    assert "index-ue" not in command and "--profile" in command


def test_service_start_is_planned_without_running_docker(tmp_path, monkeypatch, capsys):
    def unexpected(*args, **kwargs):
        raise AssertionError("dry-run must not start service")

    monkeypatch.setattr("codalith.install.__main__.start_service", unexpected)
    assert main(["--agent", "codex", "--home", str(tmp_path), "--start-service", "ue", "--dry-run"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["service_command"][-1] == "mcp-http-ue"
    assert not list(tmp_path.iterdir())
