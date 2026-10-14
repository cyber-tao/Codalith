from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import pytest

from codalith.benchmarks import claude_runner
from conftest import TestEnvironment
from test_mcp import running_http_server
from test_native_benchmarks import _context, _files


def _tool(identifier: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"type": "assistant", "parent_tool_use_id": None, "message": {"id": "message-" + identifier, "model": "glm-5.3", "content": [{"type": "tool_use", "id": identifier, "name": name, "input": arguments}]}}


def _result(identifier: str, content: Any, native: Any = None, failed: bool = False) -> dict[str, Any]:
    result = {"type": "user", "parent_tool_use_id": None, "message": {"content": [{"type": "tool_result", "tool_use_id": identifier, "content": content, "is_error": failed}]}}
    if native is not None:
        result["tool_use_result"] = native
    return result


class _FakeClaude:
    def __init__(self, endpoint: str, skill: Path) -> None:
        self.endpoint = endpoint
        self.skill = skill
        self.commands: list[list[str]] = []
        self.timeout = False
        self.wrong_model = False
        self.extra_skill = False
        self.auth_failure = False
        self.contract_failure = False
        self.unknown_mcp = False
        self.mutate_skill = False
        self.skill_method = "read"
        self.cli_task_failure = False
        self.provider_to_mutate: Path | None = None

    def __call__(
        self, command: list[str], prompt: str, stdout: Path, stderr: Path,
        timeout_s: float, name: str, ownership: str,
    ) -> dict[str, Any]:
        assert timeout_s == 600 and name.startswith("codalith-claude-") and len(ownership) == 32
        assert prompt in {"Explain CachedValue using the source.", "Calculate 2+2."}
        assert "SECRET QREL" not in prompt and "NEVER READ" not in prompt
        self.commands.append(command)
        workspace = stdout.parent / "workspace"
        candidate = workspace / ".claude/skills/source-guide/SKILL.md"
        assert all(path.name == "SKILL.md" for path in workspace.rglob("*") if path.is_file())
        events: list[dict[str, Any]] = [{
            "type": "system", "subtype": "init", "model": "other-model" if self.wrong_model else "glm-5.3[1M]",
            "tools": ["Read", "Skill", "StructuredOutput", *sorted(f"mcp__codalith__{tool}" for tool in claude_runner._MCP_TOOLS)],
            "mcp_servers": [{"name": "codalith", "status": "connected"}], "plugins": [],
            "skills": (["source-guide"] if candidate.exists() else []) + (["unexpected-skill"] if self.extra_skill else []),
        }]
        if candidate.exists():
            assert candidate.read_bytes() == self.skill.read_bytes()
            body = candidate.read_text(encoding="utf-8")
            if self.skill_method == "read":
                numbered = "\n".join(f"{number:6d}→{line}" for number, line in enumerate(body.splitlines(), 1)) + "\n"
                events.extend([_tool("read", "Read", {"file_path": "/workspace/.claude/skills/source-guide/SKILL.md"}), _result("read", numbered, {"file": {"content": body}})])
            elif self.skill_method == "partial":
                events.extend([_tool("read", "Read", {"file_path": "/workspace/.claude/skills/source-guide/SKILL.md", "limit": 2}), _result("read", "---\nname: source-guide\n")])
            elif self.skill_method == "skill":
                expanded = body.split("---", 2)[2].strip()
                events.extend([_tool("skill", "Skill", {"skill": "source-guide"}), _result("skill", "Launching skill: source-guide", {"success": True, "commandName": "source-guide"}), {"type": "user", "parent_tool_use_id": None, "message": {"content": "Base directory for this skill: /workspace/.claude/skills/source-guide\n\n" + expanded}}])
        if "CachedValue" in prompt:
            native = anyio.run(_context, self.endpoint)
            source = native["structuredContent"]["sources"][0]
            use = _tool("context", "mcp__other__codalith_context" if self.unknown_mcp else "mcp__codalith__codalith_context", {"query": "CachedValue", "target": "sample"})
            events.extend([use, use, _result("context", native["content"], native)])
            final = {"answer": "CachedValue checks expiry.", "citations": [{"uri": source["uri"], "claim": "expiry"}], "abstained": False}
        else:
            final = {"answer": "4", "citations": [], "abstained": False}
        if self.auth_failure:
            events.append({"type": "system", "subtype": "api_retry", "error_status": 403, "error": "authentication_failed"})
        if not self.timeout:
            event = {
                "type": "result", "subtype": "success", "is_error": False,
                "structured_output": final, "usage": {"input_tokens": 10, "cache_read_input_tokens": 70, "cache_creation_input_tokens": 20, "output_tokens": 6},
                "modelUsage": {"glm-5.3": {"inputTokens": 10, "cacheReadInputTokens": 70, "cacheCreationInputTokens": 20, "outputTokens": 6}},
                "duration_ms": 15, "duration_api_ms": 9, "total_cost_usd": 0.01, "num_turns": 2,
            }
            if self.contract_failure and "CachedValue" in prompt:
                event.pop("structured_output")
                event["result"] = json.dumps(final)
            if self.cli_task_failure:
                event.pop("structured_output", None)
                event.pop("usage")
                event.update({"subtype": "error_max_turns", "is_error": True})
            events.append(event)
        stdout.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
        stderr.write_text("Claude diagnostic only\n", encoding="utf-8")
        if self.mutate_skill:
            self.skill.write_text("changed", encoding="utf-8")
        if self.provider_to_mutate:
            self.provider_to_mutate.write_text("changed provider metadata", encoding="utf-8")
        return {"exit_code": 1 if self.cli_task_failure else 0, "timed_out": self.timeout, "wall_ms": 17.0, "cleanup": {"status": "not_present"}}


def _run(files: tuple[Path, Path, Path, Path], endpoint: str, **kwargs: Any) -> dict[str, Any]:
    dataset, skill, settings, output = files
    return claude_runner.run_claude_agent_comparison(
        image="claude-native:fixed", model="glm-5.3[1M]", effort="max", endpoint=endpoint,
        mcp_container="test-mcp", settings_file=settings, dataset_path=dataset,
        skill_path=skill, output_dir=output, **kwargs,
    )


def test_claude_runner_real_sample_mcp_evidence_isolation_and_usage(
    semantic_environment: TestEnvironment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _files(tmp_path)
    with running_http_server(semantic_environment) as endpoint:
        fake = _FakeClaude(endpoint, files[1])
        monkeypatch.setattr(claude_runner, "_run_container", fake)
        original_bytes, original_text = Path.read_bytes, Path.read_text

        def guarded_bytes(path: Path) -> bytes:
            assert path.resolve() != files[2].resolve(), "Runner must not read provider settings"
            return original_bytes(path)

        def guarded_text(path: Path, *args: Any, **kwargs: Any) -> str:
            assert path.resolve() != files[2].resolve(), "Runner must not read provider settings"
            return original_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_bytes", guarded_bytes)
        monkeypatch.setattr(Path, "read_text", guarded_text)
        report = _run(files, endpoint)
    assert report["completed"] and report["errors"] == 0 and len(report["rows"]) == 8
    assert [row["arm"] for row in report["rows"] if row["id"] == "source"] == ["mcp_only", "skill_mcp", "skill_mcp", "mcp_only"]
    for row in report["rows"]:
        assert row["wall_ms"] == 17 and row["model_wall_ms"] is None and "outer_turns_started" not in row
        assert row["usage"]["total_input_tokens"] == 100 and row["usage"]["total_tokens"] == 106
        assert row["claude_num_turns"] == 2 and row["duration_api_ms"] == 9
        assert row["skill_activation"] == ("observed" if row["arm"] == "skill_mcp" else "not_observed")
        if row["id"] == "source":
            assert row["mcp_calls"] == row["source_calls"] == 1
            assert row["citation_audit"][0]["valid"] and row["evidence_coverage"][0]["covered"]
        else:
            assert row["mcp_calls"] == 0 and row["evidence_coverage"][0]["covered"] is None
    assert all(summary["reported_total_tokens"] == 424 for summary in report["summaries"].values())
    assert "NEVER READ OR REPORT" not in json.dumps(report)
    assert "settings_file" not in report["metadata"]["settings"]
    for command in fake.commands:
        assert "--read-only" in command and "--interactive" in command and "--privileged" not in command
        assert "--restricted" not in command and command[command.index("--setting-sources") + 1] == "project"
        assert command[command.index("claude-native:fixed") + 1] == "--tools"
        assert command.count("--settings") == 1 and command[command.index("--tools") + 1] == "Read,Skill"
        assert command[command.index("--network") + 1] == "container:test-mcp"
        assert command[command.index("--permission-mode") + 1] == "dontAsk"
        assert command[command.index("--permission-prompts") + 1] == "none"
        assert command[command.index("--append-system-prompt") + 1] == claude_runner._CLAUDE_SYSTEM
        mounts = [command[index + 1] for index, arg in enumerate(command) if arg == "--mount"]
        assert len(mounts) == 2 and all(mount.endswith(",readonly") for mount in mounts)
        assert not any("tasks.jsonl" in mount or "reports" in mount or "schema.json" in mount for mount in mounts)
        controls = json.loads(command[command.index("--settings") + 1])
        assert controls == claude_runner.claude_config_overrides()
        assert controls["permissions"]["blockReadsOutsideWorkingDirectories"]


@pytest.mark.parametrize("failure", ["wrong_model", "auth_failure", "extra_skill"])
def test_infrastructure_failure_stops_and_retains_all_planned_rows(
    failure: str, semantic_environment: TestEnvironment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _files(tmp_path)
    with running_http_server(semantic_environment) as endpoint:
        fake = _FakeClaude(endpoint, files[1])
        setattr(fake, failure, True)
        monkeypatch.setattr(claude_runner, "_run_container", fake)
        report = _run(files, endpoint, repeats=1)
    assert not report["completed"] and len(report["rows"]) == 4 and len(fake.commands) == 1
    assert report["rows"][0]["infrastructure_failure"] and sum(row["not_run"] for row in report["rows"]) == 3
    assert all(summary["reported_total_tokens"] is None for summary in report["summaries"].values())


@pytest.mark.parametrize("failure", ["timeout", "cli_task_failure"])
def test_per_task_failure_keeps_schedule_running_and_unknown_usage(
    failure: str, semantic_environment: TestEnvironment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _files(tmp_path)
    with running_http_server(semantic_environment) as endpoint:
        fake = _FakeClaude(endpoint, files[1])
        setattr(fake, failure, True)
        monkeypatch.setattr(claude_runner, "_run_container", fake)
        report = _run(files, endpoint, repeats=1)
    assert report["completed"] and len(report["rows"]) == len(fake.commands) == 4
    assert all(row["error"] and not row["not_run"] and not row["infrastructure_failure"] for row in report["rows"])
    assert all(row["usage"]["total_tokens"] is None for row in report["rows"])
    assert all(summary["reported_total_tokens"] is None for summary in report["summaries"].values())


def test_provider_settings_metadata_changes_stop_without_reading_secret_values(
    semantic_environment: TestEnvironment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _files(tmp_path)
    original = claude_runner._provider_stat(files[2])
    with running_http_server(semantic_environment) as endpoint:
        fake = _FakeClaude(endpoint, files[1])
        fake.provider_to_mutate = files[2]
        monkeypatch.setattr(claude_runner, "_run_container", fake)
        report = _run(files, endpoint, repeats=1)
    assert not report["completed"] and len(fake.commands) == 1 and len(report["rows"]) == 4
    assert report["metadata"]["provider_settings_stat"] == original
    assert "provider settings file metadata changed" in report["rows"][0]["error"]


def test_output_contract_failure_keeps_diagnostic_raw_and_does_not_rerun(
    semantic_environment: TestEnvironment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _files(tmp_path)
    with running_http_server(semantic_environment) as endpoint:
        fake = _FakeClaude(endpoint, files[1])
        fake.contract_failure = True
        monkeypatch.setattr(claude_runner, "_run_container", fake)
        report = _run(files, endpoint, repeats=1)
    assert report["completed"] and len(fake.commands) == len(report["rows"]) == 4
    source = [row for row in report["rows"] if row["id"] == "source"]
    assert all(row["final"] is None and row["error"] and row["raw_final_content"] for row in source)


def test_unknown_mcp_cannot_issue_citation(
    semantic_environment: TestEnvironment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _files(tmp_path)
    with running_http_server(semantic_environment) as endpoint:
        fake = _FakeClaude(endpoint, files[1])
        fake.unknown_mcp = True
        monkeypatch.setattr(claude_runner, "_run_container", fake)
        report = _run(files, endpoint, repeats=1)
    source = [row for row in report["rows"] if row["id"] == "source"]
    assert all(row["unknown_calls"] == 1 and not row["issued_sources"] and not row["citation_audit"][0]["valid"] for row in source)


@pytest.mark.parametrize("method,activation", [("skill", "observed"), ("partial", "partial_observed"), ("self_claim", "not_observed")])
def test_skill_activation_requires_native_body_evidence(
    method: str, activation: str, semantic_environment: TestEnvironment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _files(tmp_path)
    with running_http_server(semantic_environment) as endpoint:
        fake = _FakeClaude(endpoint, files[1])
        fake.skill_method = method
        monkeypatch.setattr(claude_runner, "_run_container", fake)
        report = _run(files, endpoint, repeats=1)
    assert report["completed"]
    assert all(row["skill_activation"] == activation for row in report["rows"] if row["arm"] == "skill_mcp")


def test_frozen_skill_mutation_retains_remaining_denominator(
    semantic_environment: TestEnvironment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _files(tmp_path)
    with running_http_server(semantic_environment) as endpoint:
        fake = _FakeClaude(endpoint, files[1])
        fake.mutate_skill = True
        monkeypatch.setattr(claude_runner, "_run_container", fake)
        report = _run(files, endpoint, repeats=1)
    assert not report["completed"] and len(report["rows"]) == 4 and sum(row["not_run"] for row in report["rows"]) == 3
    assert "Frozen skill changed" in report["infrastructure_errors"][0]
