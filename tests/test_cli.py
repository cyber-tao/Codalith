from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from codalith.cli.main import main
from conftest import TestEnvironment


def test_cli_imports_without_posix_only_modules() -> None:
    code = "import sys; sys.modules['resource'] = None; import codalith.cli.main"
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_version_and_http_client_config_do_not_require_local_config(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODALITH_HTTP_PORT", "")
    monkeypatch.setenv("CODALITH_HTTP_MAX_REQUEST_BYTES", "")
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "0.2.0"
    assert main(["client-config", "--client", "claude", "--transport", "http"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mcpServers"]["codalith"]["url"] == "http://127.0.0.1:8765/mcp"


def test_stdio_client_config_is_independent_of_client_working_directory(
    semantic_environment: TestEnvironment,
    capsys,
) -> None:
    assert (
        main(
            [
                "--registry",
                str(semantic_environment.registry_path),
                "--policy",
                str(semantic_environment.policy_path),
                "client-config",
                "--client",
                "claude",
                "--transport",
                "stdio",
                "--project-dir",
                str(Path(__file__).parents[1]),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    args = payload["mcpServers"]["codalith"]["args"]
    assert args[:3] == ["run", "--directory", str(Path(__file__).parents[1].resolve())]
    assert str(semantic_environment.registry_path.resolve()) in args


def test_deep_doctor_verifies_generation_and_semantic_store(
    semantic_environment: TestEnvironment,
    capsys,
) -> None:
    assert (
        main(
            [
                "--registry",
                str(semantic_environment.registry_path),
                "--policy",
                str(semantic_environment.policy_path),
                "doctor",
                "--target",
                "sample",
                "--deep",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is True
    assert payload["deep_checks"] == [
        {
            "corpus_id": "sample",
            "artifacts_valid": True,
            "semantic_fingerprint_valid": True,
        }
    ]


@pytest.mark.parametrize("completed,errors,exit_code", [(True, 0, 0), (True, 1, 1), (False, 0, 1)])
def test_agent_benchmark_does_not_require_registry_and_reports_run_failures(
    tmp_path: Path,
    monkeypatch,
    capsys,
    completed: bool,
    errors: int,
    exit_code: int,
) -> None:
    from codalith.benchmarks import agent_runner

    monkeypatch.chdir(tmp_path)
    received = {}

    def compare(**kwargs):
        received.update(kwargs)
        return {"completed": completed, "errors": errors, "summaries": {}}

    monkeypatch.setattr(agent_runner, "run_agent_comparison", compare)
    assert main([
        "agent-benchmark",
        "--model", "local-model",
        "--dataset", "tasks.jsonl",
        "--skill", "SKILL.md",
        "--output-dir", "results",
    ]) == exit_code
    payload = json.loads(capsys.readouterr().out)
    assert payload["completed"] is completed
    assert payload["errors"] == errors
    assert payload["output_dir"] == str(tmp_path / "results")
    assert received["model"] == "local-model"
    assert received["think"] is False


def test_agent_benchmark_passes_explicit_reasoning_and_output_budget(tmp_path: Path, monkeypatch) -> None:
    from codalith.benchmarks import agent_runner

    monkeypatch.chdir(tmp_path)
    received = {}

    def compare(**kwargs):
        received.update(kwargs)
        return {"completed": True, "errors": 0, "summaries": {}}

    monkeypatch.setattr(agent_runner, "run_agent_comparison", compare)
    assert main([
        "agent-benchmark", "--model", "local-model", "--dataset", "tasks.jsonl",
        "--skill", "SKILL.md", "--output-dir", "results", "--think", "--output-tokens", "3072",
    ]) == 0
    assert received["think"] is True
    assert received["output_tokens"] == 3072


@pytest.mark.parametrize("completed,errors,exit_code", [(True, 0, 0), (True, 1, 1), (False, 0, 1)])
def test_native_agent_benchmark_does_not_require_registry(
    tmp_path: Path, monkeypatch, capsys, completed: bool, errors: int, exit_code: int,
) -> None:
    from codalith.benchmarks import native_runner

    monkeypatch.chdir(tmp_path)
    received = {}

    def compare(**kwargs):
        received.update(kwargs)
        return {"completed": completed, "errors": errors, "summaries": {}}

    monkeypatch.setattr(native_runner, "run_native_agent_comparison", compare)
    assert main([
        "native-agent-benchmark", "--image", "sha256:test", "--mcp-container", "test-mcp",
        "--model", "test-model", "--reasoning-effort", "ultra", "--auth-file", "existing-auth.json",
        "--dataset", "tasks.jsonl", "--skill", "SKILL.md", "--output-dir", "results",
    ]) == exit_code
    payload = json.loads(capsys.readouterr().out)
    assert payload["completed"] is completed and payload["errors"] == errors
    assert received["reasoning_effort"] == "ultra"
    assert received["auth_file"] == Path("existing-auth.json")
    assert received["repeats"] == 2 and received["case_timeout_s"] == 600
    assert received["sandbox"] == "read-only"


@pytest.mark.parametrize("completed,errors,exit_code", [(True, 0, 0), (True, 1, 1), (False, 0, 1)])
def test_claude_agent_benchmark_does_not_require_registry(
    tmp_path: Path, monkeypatch, capsys, completed: bool, errors: int, exit_code: int,
) -> None:
    from codalith.benchmarks import claude_runner

    monkeypatch.chdir(tmp_path)
    received = {}

    def compare(**kwargs):
        received.update(kwargs)
        return {"completed": completed, "errors": errors, "summaries": {}}

    monkeypatch.setattr(claude_runner, "run_claude_agent_comparison", compare)
    assert main([
        "claude-agent-benchmark", "--image", "sha256:test", "--mcp-container", "test-mcp",
        "--model", "glm-5.3[1M]", "--settings-file", "existing-settings.json",
        "--dataset", "tasks.jsonl", "--skill", "SKILL.md", "--output-dir", "results",
    ]) == exit_code
    assert json.loads(capsys.readouterr().out)["errors"] == errors
    assert received["model"] == "glm-5.3[1M]" and received["effort"] == "max"
    assert received["settings_file"] == Path("existing-settings.json")
    assert received["repeats"] == 2 and received["case_timeout_s"] == 600
