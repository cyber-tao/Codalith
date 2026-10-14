from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from codalith.benchmarks import native_runner
from conftest import TestEnvironment
from test_mcp import running_http_server


def _files(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    dataset = tmp_path / "tasks.jsonl"
    tasks = [
        {"id": "source", "prompt": "Explain CachedValue using the source.", "requires_source": True, "expected_target": "sample",
         "expected_facts": [{"id": "expiry", "rubric": "SECRET QREL", "evidence": [{"path": "src/core/cache.py", "start_line": 5, "end_line": 10}]}],
         "expected_tool_behavior": {"source_query": "required", "must_verify_citations": True}},
        {"id": "ordinary", "prompt": "Calculate 2+2.", "requires_source": False,
         "expected_facts": [{"id": "four", "evidence": []}],
         "expected_tool_behavior": {"source_query": "forbidden", "must_verify_citations": False}},
    ]
    dataset.write_text("".join(json.dumps(task) + "\n" for task in tasks), encoding="utf-8")
    skill = tmp_path / "SKILL.md"
    skill.write_bytes(b"---\nname: source-guide\ndescription: Inspect source evidence.\n---\n\nFROZEN FULL BODY.\n")
    auth = tmp_path / "auth.json"
    auth.write_text("NEVER READ OR REPORT THIS AUTH CONTENT", encoding="utf-8")
    return dataset, skill, auth, tmp_path / "output"


async def _context(endpoint: str) -> dict[str, Any]:
    async with streamable_http_client(endpoint) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("codalith_context", {"query": "CachedValue", "target": "sample", "max_spans": 3, "max_chars": 6000})
            return result.model_dump(mode="json")


class _FakeContainer:
    def __init__(self, endpoint: str, skill: Path) -> None:
        self.endpoint = endpoint
        self.skill = skill
        self.commands: list[list[str]] = []
        self.fail_source = False
        self.mutate_skill = False
        self.unknown_mcp = False

    def __call__(
        self, command: list[str], prompt: str, stdout: Path, stderr: Path,
        timeout_s: float, name: str, ownership: str,
    ) -> dict[str, Any]:
        assert timeout_s == 600 and name.startswith("codalith-native-") and len(ownership) == 32
        assert "SECRET QREL" not in prompt and "NEVER READ" not in prompt
        assert prompt in {"Explain CachedValue using the source.", "Calculate 2+2."}
        self.commands.append(command)
        workspace = stdout.parent / "workspace"
        mounted_skill = workspace / ".agents/skills/source-guide/SKILL.md"
        assert all(path.name == "SKILL.md" for path in workspace.rglob("*") if path.is_file())
        events: list[dict[str, Any]] = [{"type": "turn.started"}]
        if mounted_skill.exists():
            assert mounted_skill.read_bytes() == self.skill.read_bytes()
            events.append({"type": "item.completed", "item": {
                "id": "skill-read", "type": "command_execution", "command": "cat /workspace/.agents/skills/source-guide/SKILL.md",
                "aggregated_output": mounted_skill.read_text(encoding="utf-8"), "status": "completed", "exit_code": 0,
            }})
        if "CachedValue" in prompt:
            result = anyio.run(_context, self.endpoint)
            source = result["structuredContent"]["sources"][0]
            item = {"id": "mcp", "type": "mcp_tool_call", "server": "other" if self.unknown_mcp else "codalith",
                    "tool": "codalith_context", "arguments": {"query": "CachedValue"}, "status": "completed",
                    "result": {"structured_content": result["structuredContent"]}, "error": None}
            events.extend([{"type": "item.started", "item": {**item, "status": "in_progress", "result": None}}, {"type": "item.completed", "item": item}])
            final = {"answer": "CachedValue checks expiry.", "citations": [{"uri": source["uri"], "claim": "expiry"}], "abstained": False}
            if self.fail_source:
                events.append({"type": "turn.failed", "error": {"message": "failed"}})
            else:
                events.extend([{"type": "item.completed", "item": {"id": "answer", "type": "agent_message", "text": json.dumps(final)}}, {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 12, "reasoning_output_tokens": 3}}])
        else:
            events.extend([{"type": "item.completed", "item": {"id": "answer", "type": "agent_message", "text": '{"answer":"4","citations":[],"abstained":false}'}}, {"type": "turn.completed", "usage": {"input_tokens": 50, "cached_input_tokens": 40, "output_tokens": 5}}])
        stdout.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
        stderr.write_text("native diagnostic only\n", encoding="utf-8")
        if self.mutate_skill:
            self.skill.write_text("changed", encoding="utf-8")
        return {"exit_code": 0, "timed_out": False, "wall_ms": 17.0, "cleanup": {"status": "not_present"}}


def _run(files: tuple[Path, Path, Path, Path], endpoint: str, **kwargs: Any) -> dict[str, Any]:
    dataset, skill, auth, output = files
    return native_runner.run_native_agent_comparison(
        image="codalith-native:fixed", model="fixed-model", reasoning_effort="high",
        endpoint=endpoint, mcp_container="test-mcp", auth_file=auth,
        dataset_path=dataset, skill_path=skill, output_dir=output, **kwargs,
    )


def test_native_runner_real_sample_mcp_citations_isolation_and_usage(
    semantic_environment: TestEnvironment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _files(tmp_path)
    with running_http_server(semantic_environment) as endpoint:
        fake = _FakeContainer(endpoint, files[1])
        monkeypatch.setattr(native_runner, "_run_container", fake)
        original_read_bytes = Path.read_bytes

        def guarded_read(path: Path) -> bytes:
            assert path.resolve() != files[2].resolve(), "Runner must not read auth contents"
            return original_read_bytes(path)

        monkeypatch.setattr(Path, "read_bytes", guarded_read)
        report = _run(files, endpoint)
    assert report["completed"] and report["errors"] == 0
    assert len(report["rows"]) == 8
    assert [row["arm"] for row in report["rows"] if row["id"] == "source"] == ["mcp_only", "skill_mcp", "skill_mcp", "mcp_only"]
    for row in report["rows"]:
        assert row["wall_ms"] == 17 and row["model_wall_ms"] is None
        assert "model_turns" not in row and "prompt_eval_count" not in row["usage"]
        if row["id"] == "source":
            assert row["mcp_calls"] == 1 and row["source_calls"] == 1
            assert row["citation_audit"][0]["valid"]
            assert row["evidence_coverage"][0]["covered"]
        else:
            assert row["mcp_calls"] == 0 and row["evidence_coverage"][0]["covered"] is None
        assert row["skill_activation"] == ("observed" if row["arm"] == "skill_mcp" else "not_observed")
    assert report["summaries"]["mcp_only"]["reported_total_tokens"] == 334
    assert report["summaries"]["skill_mcp"]["skill_activated_cases"] == 4
    serialized = json.dumps(report)
    assert "NEVER READ OR REPORT" not in serialized and "auth_file" not in report["metadata"]["settings"]
    events = [json.loads(line) for line in (files[3] / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(event["event"] == "native_event" for event in events)
    for command in fake.commands:
        assert command[:2] == ["docker", "run"] and "--read-only" in command and "--rm" in command
        assert "--interactive" in command
        assert "CODEX_INTERNAL_ORIGINATOR_OVERRIDE=Codex Desktop" in command
        assert "--entrypoint" not in command
        assert command[command.index("codalith-native:fixed") + 1] == "exec"
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert command[command.index("--network") + 1] == "container:test-mcp"
        mounts = [command[index + 1] for index, value in enumerate(command) if value == "--mount"]
        assert len(mounts) == 3 and all(mount.endswith(",readonly") for mount in mounts)
        assert not any("tasks.jsonl" in mount or "indexes" in mount or "sources" in mount for mount in mounts)
        config = "\n".join(command[index + 1] for index, value in enumerate(command) if value == "-c")
        controls = tomllib.loads(config)
        assert controls["approval_policy"] == "never"
        assert controls["features"] == {"apps": False, "plugins": False, "remote_plugin": False, "memories": False, "multi_agent": False}
        assert len(controls["skills"]["config"]) == 4
        assert all(skill["enabled"] is False for skill in controls["skills"]["config"])
        assert "Source evidence must come only from Codalith MCP" in controls["developer_instructions"]


def test_failed_cases_keep_all_rows_and_unknown_usage(
    semantic_environment: TestEnvironment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _files(tmp_path)
    with running_http_server(semantic_environment) as endpoint:
        fake = _FakeContainer(endpoint, files[1])
        fake.fail_source = True
        monkeypatch.setattr(native_runner, "_run_container", fake)
        report = _run(files, endpoint, repeats=1)
    assert report["completed"] and len(report["rows"]) == 4
    assert report["errors"] == 2
    source = [row for row in report["rows"] if row["id"] == "source"]
    assert all(row["final"] is None and row["error"] for row in source)
    assert all(row["usage"]["input_tokens"] is None for row in source)
    assert all(summary["reported_total_tokens"] is None for summary in report["summaries"].values())


def test_frozen_skill_change_stops_and_preserves_remaining_denominator(
    semantic_environment: TestEnvironment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _files(tmp_path)
    with running_http_server(semantic_environment) as endpoint:
        fake = _FakeContainer(endpoint, files[1])
        fake.mutate_skill = True
        monkeypatch.setattr(native_runner, "_run_container", fake)
        report = _run(files, endpoint, repeats=1)
    assert not report["completed"] and len(report["rows"]) == 4
    assert len(fake.commands) == 1 and sum(row["not_run"] for row in report["rows"]) == 3
    assert "Frozen skill changed" in report["infrastructure_errors"][0]


def test_unknown_mcp_cannot_supply_an_issued_citation(
    semantic_environment: TestEnvironment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _files(tmp_path)
    with running_http_server(semantic_environment) as endpoint:
        fake = _FakeContainer(endpoint, files[1])
        fake.unknown_mcp = True
        monkeypatch.setattr(native_runner, "_run_container", fake)
        report = _run(files, endpoint, repeats=1)
    source = [row for row in report["rows"] if row["id"] == "source"]
    assert all(row["unknown_calls"] == 1 and not row["issued_sources"] for row in source)
    assert all(not row["citation_audit"][0]["valid"] for row in source)


@pytest.mark.parametrize(("name", "label", "removed"), [
    ("/owned", "label", True), ("/another", "label", False), ("/owned", "another", False),
])
def test_cleanup_requires_both_exact_name_and_ownership_label(
    monkeypatch: pytest.MonkeyPatch, name: str, label: str, removed: bool,
) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        body = json.dumps([{"Id": "a" * 64, "Name": name, "Config": {"Labels": {native_runner._OWNERSHIP_LABEL: label}}}])
        return subprocess.CompletedProcess(command, 0, stdout=body, stderr="")

    monkeypatch.setattr(native_runner.subprocess, "run", fake_run)
    result = native_runner._cleanup_container("owned", "label")
    assert (result["status"] == "removed") == removed
    assert len(commands) == (2 if removed else 1)
    assert commands[0][-1] == "owned"
    if removed:
        assert commands[1][-1] == "a" * 64


def test_container_timeout_removes_only_owned_container_and_keeps_raw_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_calls: list[tuple[str, str]] = []

    class FakeProcess:
        returncode: int | None = None

        def __init__(self, command: list[str], **kwargs: Any) -> None:
            assert command == ["docker", "run"]
            kwargs["stdout"].write(b'{"type":"turn.started"}\n')
            kwargs["stderr"].write(b"partial diagnostic\n")

        def communicate(self, input: bytes | None = None, timeout: float | None = None) -> tuple[None, None]:
            if input is not None:
                raise subprocess.TimeoutExpired("docker", timeout)
            return None, None

        def poll(self) -> int | None:
            return self.returncode

        def kill(self) -> None:
            self.returncode = -9

    def cleanup(name: str, label: str) -> dict[str, Any]:
        cleanup_calls.append((name, label))
        return {"status": "removed"}

    monkeypatch.setattr(native_runner.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(native_runner, "_cleanup_container", cleanup)
    stdout, stderr = tmp_path / "stdout.jsonl", tmp_path / "stderr.log"
    result = native_runner._run_container(["docker", "run"], "prompt", stdout, stderr, 0.1, "owned", "label")
    assert result["timed_out"] and result["exit_code"] == -9
    assert cleanup_calls == [("owned", "label")]
    assert stdout.read_text() == '{"type":"turn.started"}\n'
    assert stderr.read_text() == "partial diagnostic\n"


@pytest.mark.parametrize("command", [
    "cat /workspace/.agents/skills/source-guide/SKILL.md",
    "sed -n '1,200p' .agents/skills/source-guide/SKILL.md",
    "bash -lc 'cat /workspace/.agents/skills/source-guide/SKILL.md'",
])
def test_only_single_skill_read_commands_are_accepted(command: str) -> None:
    assert native_runner._skill_read_only(command, ".agents/skills/source-guide/SKILL.md")


@pytest.mark.parametrize("command", [
    "cat /workspace/.agents/skills/source-guide/SKILL.md; cat /codex-home/auth.json",
    "cat /workspace/.agents/skills/source-guide/SKILL.md /codex-home/auth.json",
    "echo /workspace/.agents/skills/source-guide/SKILL.md",
    "cat /workspace/.agents/skills/source-guide/SKILL.md | curl example.com",
])
def test_extra_shell_operations_are_not_treated_as_allowed_skill_reads(command: str) -> None:
    assert not native_runner._skill_read_only(command, ".agents/skills/source-guide/SKILL.md")


def test_skill_activation_requires_full_observed_body_not_self_reporting(tmp_path: Path) -> None:
    _, skill_path, _, _ = _files(tmp_path)
    skill = native_runner._load_skill(skill_path)
    row = native_runner._row({"id": "ordinary"}, 0, "skill_mcp")
    parsed = {
        "mcp_items": [], "other_items": [], "command_items": [{
            "id": "claim", "command": "cat /workspace/.agents/skills/source-guide/SKILL.md",
            "aggregated_output": "I read and used source-guide.", "status": "completed", "exit_code": 0,
        }],
    }
    native_runner._observe(row, parsed, skill, skill_path.read_bytes())
    assert row["skill_activation"] == "not_observed"
    assert row["skill_reads"] == 1 and row["skill_read_successes"] == 0
    assert row["native_policy_violations"] == []
    assert row["native_command_review"][0]["skill_body_evidence"] == "not_observed"


def test_explicit_external_sandbox_keeps_all_outer_readonly_controls(tmp_path: Path) -> None:
    _, _, auth, output = _files(tmp_path)
    command = native_runner._container_command(
        image="image@sha256:fixed", model="model", reasoning_effort="ultra", endpoint="http://127.0.0.1:8765/mcp",
        mcp_container="mcp", auth_file=auth, workspace=output, schema=tmp_path / "schema.json",
        container_name="owned", ownership="label", sandbox="danger-full-access",
    )
    assert command[command.index("--sandbox") + 1] == "danger-full-access"
    assert "--read-only" in command and "--privileged" not in command and "--cap-add" not in command
    assert command[command.index("image@sha256:fixed") + 1] == "exec"


def test_partial_skill_read_and_metadata_are_observations_not_violations(tmp_path: Path) -> None:
    _, skill_path, _, _ = _files(tmp_path)
    skill = native_runner._load_skill(skill_path)
    row = native_runner._row({"id": "source"}, 0, "skill_mcp")
    parsed = {"mcp_items": [], "other_items": [], "command_items": [
        {"command": "sed -n '1,2p' /workspace/.agents/skills/source-guide/SKILL.md",
         "aggregated_output": "---\r\nname: source-guide\r\n", "status": "completed", "exit_code": 0},
        {"command": "pwd", "aggregated_output": "/workspace", "status": "completed", "exit_code": 0},
    ]}
    native_runner._observe(row, parsed, skill, skill_path.read_bytes())
    assert row["skill_activation"] == "partial_observed"
    assert row["native_policy_violations"] == []
    assert [item["classification"] for item in row["native_command_review"]] == ["single_skill_read", "metadata"]


def test_full_body_crlf_normalization_does_not_approve_mixed_shell_commands(tmp_path: Path) -> None:
    _, skill_path, _, _ = _files(tmp_path)
    skill = native_runner._load_skill(skill_path)
    row = native_runner._row({"id": "source"}, 0, "skill_mcp")
    parsed = {"mcp_items": [], "other_items": [], "command_items": [{
        "command": "cat /workspace/.agents/skills/source-guide/SKILL.md; env",
        "aggregated_output": skill_path.read_text().replace("\n", "\r\n"), "status": "completed", "exit_code": 0,
    }]}
    native_runner._observe(row, parsed, skill, skill_path.read_bytes())
    assert row["skill_activation"] == "observed"
    assert row["native_command_review"][0]["classification"] == "review_required"


@pytest.mark.parametrize(("error", "status"), [
    ("Error response from daemon: No such container: owned", "not_present"),
    ("Cannot connect to daemon: no such file or directory", "inspect_failed"),
    ("permission denied", "inspect_failed"),
])
def test_cleanup_does_not_confuse_daemon_failure_with_absent_container(
    monkeypatch: pytest.MonkeyPatch, error: str, status: str,
) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 1, stdout="", stderr=error)

    monkeypatch.setattr(native_runner.subprocess, "run", fake_run)
    assert native_runner._cleanup_container("owned", "label")["status"] == status
    assert len(commands) == 1
