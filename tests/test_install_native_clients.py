import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from codalith.install.core import apply_plan, build_plan, select_targets

ENDPOINT = "http://127.0.0.1:9/mcp"
_REPO = Path(__file__).resolve().parents[1]


def _executable(name):
    executable = shutil.which(name)
    if executable is None:
        pytest.skip(f"Native {name} CLI is not installed")
    if os.name == "nt" and Path(executable).suffix.lower() in {".bat", ".cmd"}:
        pytest.skip(f"Native {name} validation requires an executable, not a Windows shell shim")
    return executable


@contextmanager
def _installed_home(agent):
    temporary_root = _REPO / ".local" / "native-install-tests"
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{agent}-", dir=temporary_root) as folder:
        root = Path(folder)
        home = root / "home"
        cwd = root / "workspace"
        cwd.mkdir()
        targets = select_targets([agent], home=home)
        apply_plan(build_plan(targets, endpoint=ENDPOINT))
        environment = {
            key: value for key, value in os.environ.items()
            if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "COMSPEC", "TEMP", "TMP", "LANG", "LC_ALL"}
        }
        environment.update({
            "HOME": str(home), "USERPROFILE": str(home), "CODEX_HOME": str(home / ".codex"),
            "APPDATA": str(home / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(home / "AppData" / "Local"),
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_TELEMETRY": "1",
            "DISABLE_ERROR_REPORTING": "1", "DISABLE_AUTOUPDATER": "1",
        })
        yield targets[0], cwd, environment


def _run(executable, arguments, cwd, environment):
    result = subprocess.run(
        [executable, *arguments], cwd=cwd, env=environment, capture_output=True,
        text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, "Native client rejected the generated configuration"
    return result.stdout


@contextmanager
def _stdio_client(executable, arguments, cwd, environment):
    process = subprocess.Popen(
        [executable, *arguments], cwd=cwd, env=environment, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
    )
    messages = queue.Queue()

    def read_output():
        for line in process.stdout:
            messages.put(line)
        messages.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()

    def exchange(payload, match=None):
        process.stdin.write(json.dumps(payload) + "\n")
        process.stdin.flush()
        if match is None:
            return None
        deadline = time.monotonic() + 30
        while True:
            try:
                line = messages.get(timeout=max(0.001, deadline - time.monotonic()))
            except queue.Empty:
                pytest.fail("Native client did not return its offline catalog within 30 seconds")
            assert line is not None, "Native client exited before returning its offline catalog"
            message = json.loads(line)
            assert message.get("type") not in {"assistant", "result"}, "Catalog probe must not run a model turn"
            if match(message):
                return message
            if time.monotonic() >= deadline:
                pytest.fail("Native client did not return the requested offline catalog")

    try:
        yield exchange
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        reader.join(timeout=5)
        process.stdout.close()


def test_installed_codex_recognizes_native_http_server():
    executable = _executable("codex")
    with _installed_home("codex") as (target, cwd, environment):
        listed = json.loads(_run(executable, ["mcp", "list", "--json"], cwd, environment))
        server = next(row for row in listed if row["name"] == "codalith")
        assert server["enabled"] is True
        assert server["transport"]["type"] == "streamable_http"
        assert server["transport"]["url"] == ENDPOINT
        fetched = json.loads(_run(executable, ["mcp", "get", "codalith", "--json"], cwd, environment))
        assert fetched["name"] == server["name"]
        assert fetched["enabled"] is server["enabled"]
        assert fetched["transport"] == server["transport"]


@pytest.mark.skipif(
    os.name == "nt",
    reason="Windows Codex uses the OS profile for user skills; HOME cannot isolate its user catalog",
)
def test_installed_codex_discovers_user_skill_in_isolated_home():
    executable = _executable("codex")
    with _installed_home("codex") as (target, cwd, environment):
        with _stdio_client(executable, ["app-server", "--stdio"], cwd, environment) as exchange:
            initialized = exchange({
                "id": 1, "method": "initialize", "params": {
                    "clientInfo": {"name": "codalith-install-test", "version": "1"},
                    "capabilities": {"experimentalApi": True},
                },
            }, lambda row: row.get("id") == 1)
            assert "result" in initialized and "error" not in initialized
            exchange({"method": "initialized"})
            listed_skills = exchange({
                "id": 2, "method": "skills/list", "params": {"cwds": [str(cwd)], "forceReload": True},
            }, lambda row: row.get("id") == 2)
        assert "error" not in listed_skills
        catalogs = listed_skills["result"]["data"]
        assert len(catalogs) == 1
        skill = next(row for row in catalogs[0]["skills"] if row["name"] == "codalith-source")
        assert skill["enabled"] is True
        assert Path(skill["path"]) == target.skill_dir / "SKILL.md"
        assert skill["scope"].lower() == "user"


def test_installed_claude_recognizes_native_http_server_and_source_skill():
    executable = _executable("claude")
    with _installed_home("claude-code") as (target, cwd, environment):
        settings = target.skill_dir.parents[1] / "settings.json"
        settings.write_text(json.dumps({
            "disableAllHooks": True, "disableBundledSkills": True, "autoMemoryEnabled": False,
            "disableClaudeAiConnectors": True, "syncClaudeAiPlugins": False,
            "enabledPlugins": {"cc-plugin-agents-md@builtin": False, "cc-plugin-plugin-authoring@builtin": False},
        }), encoding="utf-8")
        fetched = _run(executable, ["mcp", "get", "codalith"], cwd, environment)
        fields = dict(re.findall(r"^\s+(Scope|Type|URL): (.+)$", fetched, flags=re.MULTILINE))
        assert fields["Scope"] == "User config (available in all your projects)"
        assert fields["Type"] == "http"
        assert fields["URL"] == ENDPOINT
        with _stdio_client(executable, [
            "--print", "--input-format", "stream-json", "--output-format", "stream-json",
            "--verbose", "--no-session-persistence", "--setting-sources", "user,project",
        ], cwd, environment) as exchange:
            initialized = exchange({
                "type": "control_request", "request_id": "initialize",
                "request": {"subtype": "initialize"},
            }, lambda row: row.get("type") == "control_response" and row.get("response", {}).get("request_id") == "initialize")
            response = initialized["response"]
            assert response["subtype"] == "success"
            commands = response["response"]["commands"]
            skill = next(row for row in commands if row["name"] == "codalith-source")
            assert skill["description"].endswith("(user)")
            custom = [row for row in commands if not row.get("builtin")]
            assert {row["name"] for row in custom} == {"codalith-source"}
            status = exchange({
                "type": "control_request", "request_id": "mcp-status",
                "request": {"subtype": "mcp_status"},
            }, lambda row: row.get("type") == "control_response" and row.get("response", {}).get("request_id") == "mcp-status")
        assert status["response"]["subtype"] == "success"
        servers = status["response"]["response"]["mcpServers"]
        server = next(row for row in servers if row["name"] == "codalith")
        assert server["config"]["type"] == "http"
        assert server["config"]["url"] == ENDPOINT
