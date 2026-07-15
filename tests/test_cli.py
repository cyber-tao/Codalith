from __future__ import annotations

import json
from pathlib import Path

from codalith.cli.main import main
from conftest import TestEnvironment


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
