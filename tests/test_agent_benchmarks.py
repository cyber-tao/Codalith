from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from codalith.benchmarks import agent_runner
from conftest import TestEnvironment
from test_mcp import running_http_server


def _task(identifier: str, *, source: bool = True, abstain: bool = False) -> dict[str, Any]:
    return {
        "id": identifier, "prompt": identifier, "category": "source" if source else "ordinary",
        "requires_source": source, "expected_target": "sample" if source else None,
        "expected_files": ["src/core/cache.py"] if source else [],
        "expected_facts": [{"id": "hidden-fact", "rubric": "SECRET RUBRIC", "evidence": [{"path": "src/core/cache.py", "start_line": 5, "end_line": 10}]}] if source and not abstain else [],
        "expected_abstention": {"required": abstain, "scope": "full" if abstain else "none", "reason": "SECRET REASON"},
        "expected_tool_behavior": {"source_query": "required" if source and not abstain else "optional" if source else "forbidden", "must_verify_citations": source},
    }


def _files(tmp_path: Path, tasks: list[dict[str, Any]]) -> tuple[Path, Path, Path]:
    dataset = tmp_path / "tasks.jsonl"
    dataset.write_text("".join(json.dumps(task) + "\n" for task in tasks), encoding="utf-8")
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: source-guide\ndescription: Retrieve relevant source evidence.\n---\n\nFROZEN BODY SENT ONLY ON DEMAND.\n", encoding="utf-8")
    return dataset, skill, tmp_path / "output"


class _MockModel:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.overflow_rejected = True
        self.fail_prompt: str | None = None
        self.malformed_prompt: str | None = None
        self.invalid_prompt: str | None = None
        self.endless = False
        self.expected_think = False
        self.expected_output_tokens = 1536
        self.capabilities = ["completion", "tools"]

    def __call__(self, origin: str, route: str, payload: dict[str, Any] | None, timeout_s: float) -> dict[str, Any]:
        assert origin == "http://127.0.0.1:11434"
        assert 0 < timeout_s <= 180
        if route == "/api/tags":
            return {"models": [{"name": "mock:latest", "model": "mock:latest", "digest": "a" * 64}]}
        if route == "/api/version":
            return {"version": "test-version"}
        if route == "/api/show":
            return {"capabilities": self.capabilities, "model_info": {"general.architecture": "mock"}}
        if route == "/api/ps":
            return {"models": [{"name": "mock:latest", "size_vram": 16, "context_length": 32768}]}
        assert route == "/api/chat" and payload is not None
        self.requests.append(copy.deepcopy(payload))
        assert payload["think"] is self.expected_think and payload["stream"] is False
        assert payload["truncate"] is False and payload["shift"] is False
        assert payload["options"]["num_ctx"] == 32768
        assert payload["options"]["seed"] == 42 and payload["options"]["temperature"] == 0
        if not payload["tools"]:
            if self.overflow_rejected:
                raise agent_runner._ModelError("Model API HTTP 400: context overflow", {"error": "request exceeds context length"})
            return {"message": {"role": "assistant", "content": "silently truncated"}}
        assert payload["options"]["num_predict"] == self.expected_output_tokens
        prompt = payload["messages"][1]["content"]
        if prompt == self.fail_prompt:
            raise agent_runner._ModelError("Model API HTTP 500", {"error": "failed", "prompt_eval_count": 71, "eval_count": 3})
        if prompt == self.malformed_prompt:
            return self._response({"role": "assistant", "content": "not json"}, usage=False)
        tools = [item["function"]["name"] for item in payload["tools"]]
        tool_messages = [item for item in payload["messages"] if item["role"] == "tool"]
        if self.endless:
            return self._tool("codalith_status", {})
        if prompt == self.invalid_prompt:
            if not tool_messages:
                return self._response({"role": "assistant", "content": "", "tool_calls": [
                    {"function": {"name": "not_a_tool", "arguments": {}}},
                    {"function": {"name": "codalith_search", "arguments": {"query": "CachedValue", "limit": 999}}},
                ]})
            assert len(tool_messages) == 2
            assert all("error" in item["content"].lower() for item in tool_messages)
            return self._final("unsupported citation", [{"uri": "codalith://sample/source/src/core/cache.py#L5-L10", "claim": "invented"}])
        if prompt == "ordinary":
            assert not tool_messages
            return self._final("4", [])
        if prompt == "absent":
            if not tool_messages:
                return self._tool("codalith_status", {"target": "sample"})
            return self._final("The requested source API is unavailable.", [], abstain=True)
        if "read_skill" in tools and not tool_messages:
            assert "FROZEN BODY" not in payload["messages"][0]["content"]
            return self._tool("read_skill", {"name": "source-guide"})
        if not tool_messages or tool_messages[-1]["tool_name"] == "read_skill":
            if tool_messages:
                assert "FROZEN BODY" in tool_messages[-1]["content"]
            return self._tool("codalith_context", {"query": "CachedValue", "target": "sample", "max_spans": 3, "max_chars": 6000})
        source = json.loads(tool_messages[-1]["content"])["sources"][0]
        return self._final("CachedValue stores expiry and checks the time.", [{"uri": source["uri"], "claim": "expiry check"}])

    @staticmethod
    def _response(message: dict[str, Any], *, usage: bool = True) -> dict[str, Any]:
        response: dict[str, Any] = {"message": message, "done": True, "done_reason": "stop", "load_duration": 123, "prompt_eval_cached_count": 7}
        if usage:
            response.update({"prompt_eval_count": 100, "eval_count": 12})
        return response

    def _tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._response({"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "function": {"name": name, "arguments": arguments}}]})

    def _final(self, answer: str, citations: list[dict[str, str]], *, abstain: bool = False) -> dict[str, Any]:
        return self._response({"role": "assistant", "content": json.dumps({"answer": answer, "citations": citations, "abstained": abstain})})


def _run(environment: TestEnvironment, files: tuple[Path, Path, Path], **kwargs: Any) -> dict[str, Any]:
    dataset, skill, output = files
    with running_http_server(environment) as endpoint:
        return agent_runner.run_agent_comparison(endpoint=endpoint, model_url="http://127.0.0.1:11434", model="mock:latest", dataset_path=dataset, skill_path=skill, output_dir=output, **kwargs)


def test_agent_comparison_real_mcp_tool_loop_and_lazy_skill(
    semantic_environment: TestEnvironment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _MockModel()
    monkeypatch.setattr(agent_runner, "_api_request", model)
    files = _files(tmp_path, [_task("source"), _task("ordinary", source=False), _task("absent", abstain=True)])
    report = _run(semantic_environment, files)
    assert report["completed"] is True and report["errors"] == 0
    assert len(report["rows"]) == 12
    assert [row["arm"] for row in report["rows"] if row["id"] == "source"] == ["mcp_only", "skill_mcp", "skill_mcp", "mcp_only"]
    for arm in ("mcp_only", "skill_mcp"):
        summary = report["summaries"][arm]
        assert summary["planned_cases"] == 6
        assert summary["evidence_coverage"] == 1
        assert summary["citation_validity"] == 1
        assert summary["source_required_queried"] == 2
        assert summary["source_forbidden_cases"] == 2 and summary["source_forbidden_violations"] == 0
        assert summary["abstention_flag_match_rate"] == 1
        assert summary["mcp_calls"] == 4  # verifier and startup status calls do not consume agent quota
    assert report["summaries"]["mcp_only"]["skill_reads"] == 0
    assert report["summaries"]["skill_mcp"]["skill_reads"] == 2
    source_rows = [row for row in report["rows"] if row["id"] == "source"]
    assert all(row["citation_audit"][0]["valid"] for row in source_rows)
    formal = [request for request in model.requests if request["tools"]]
    assert all("SECRET" not in json.dumps(request) for request in formal)
    assert all(len(request["tools"]) == (9 if "Available skill:" in request["messages"][0]["content"] else 8) for request in formal)
    trace = [json.loads(line) for line in (files[2] / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(item["event"] == "model_response" and item["response"]["prompt_eval_cached_count"] == 7 for item in trace)
    assert any(item["event"] == "citation_audit" for item in trace)
    assert json.loads((files[2] / "report.json").read_text(encoding="utf-8"))["errors"] == 0
    assert report["metadata"]["model_preflight"]["context_overflow_rejected"] is True


def test_unknown_and_invalid_tools_receive_errors_and_fabricated_uri_fails(
    semantic_environment: TestEnvironment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _MockModel()
    model.invalid_prompt = "invalid"
    monkeypatch.setattr(agent_runner, "_api_request", model)
    report = _run(semantic_environment, _files(tmp_path, [_task("invalid")]), repeats=1)
    assert report["completed"] is True and report["errors"] == 0
    for row in report["rows"]:
        assert row["mcp_calls"] == 2 and row["tool_errors"] == 2
        assert row["unknown_calls"] == 1 and row["source_calls"] == 1
        assert row["citation_audit"][0]["valid"] is False
        assert "not issued" in row["citation_audit"][0]["error"]
        assert row["evidence_coverage"] == [{"id": "hidden-fact", "applicable": True, "covered": False}]


def test_failed_cases_keep_denominator_partial_usage_and_missing_counts(
    semantic_environment: TestEnvironment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _MockModel()
    model.fail_prompt = "api_failure"
    model.malformed_prompt = "malformed"
    monkeypatch.setattr(agent_runner, "_api_request", model)
    files = _files(tmp_path, [_task("api_failure"), _task("malformed")])
    report = _run(semantic_environment, files, repeats=1)
    assert report["completed"] is True and report["errors"] == 4
    for arm in ("mcp_only", "skill_mcp"):
        summary = report["summaries"][arm]
        assert summary["planned_cases"] == 2 and summary["failed_cases"] == 2
        assert summary["expected_facts"] == 2 and summary["evidence_coverage"] == 0
        assert summary["prompt_eval_count_known_sum"] == 71
        assert summary["eval_count_known_sum"] == 3
        assert summary["prompt_eval_count_missing_requests"] == 1
    assert all(row["final"] is None and row["error"] for row in report["rows"])
    assert next(row for row in report["rows"] if row["id"] == "malformed")["usage"] == [{"prompt_eval_count": None, "eval_count": None}]


@pytest.mark.parametrize(("max_turns", "max_mcp_calls", "expected"), [(1, 10, "turn budget"), (8, 1, "MCP call budget")])
def test_agent_hard_budgets(
    semantic_environment: TestEnvironment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    max_turns: int, max_mcp_calls: int, expected: str,
) -> None:
    model = _MockModel()
    model.endless = True
    monkeypatch.setattr(agent_runner, "_api_request", model)
    report = _run(semantic_environment, _files(tmp_path, [_task("ordinary", source=False)]), repeats=1, max_turns=max_turns, max_mcp_calls=max_mcp_calls)
    assert report["errors"] == 2
    assert all(expected in row["error"] for row in report["rows"])
    assert all(row["mcp_calls"] <= max_mcp_calls and row["model_turns"] <= max_turns for row in report["rows"])
    assert all(summary["source_forbidden_violations"] == 1 for summary in report["summaries"].values())


def test_preflight_overflow_acceptance_blocks_all_formal_cases(
    semantic_environment: TestEnvironment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _MockModel()
    model.overflow_rejected = False
    monkeypatch.setattr(agent_runner, "_api_request", model)
    report = _run(semantic_environment, _files(tmp_path, [_task("source")]), repeats=1)
    assert report["completed"] is False
    assert "silently accepted overflow" in report["infrastructure_errors"][0]
    assert all(row["not_run"] for row in report["rows"])
    assert all(summary["planned_cases"] == 1 and summary["evidence_coverage"] == 0 for summary in report["summaries"].values())
    assert len(model.requests) == 1 and model.requests[0]["tools"] == []


@pytest.mark.parametrize("origin", ["http://example.com:11434", "http://192.168.1.1:11434", "http://127.0.0.1:11434/api", "http://user:password@127.0.0.1:11434", "http://127.0.0.1:11434/?token=secret"])
def test_model_transport_rejects_non_loopback_or_non_origin_urls(origin: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        agent_runner._local_model_url(origin)


@pytest.mark.parametrize("content", ["```json\n{}\n```", '{"answer":"x","citations":[],"abstained":0}', '{"answer":"x","citations":[{"uri":"x","claim":3}],"abstained":false}', '{"answer":"x","citations":[],"abstained":false,"extra":1}'])
def test_final_output_schema_is_strict(content: str) -> None:
    with pytest.raises(ValueError):
        agent_runner._parse_final(content)


def test_output_in_repository_must_be_ignored() -> None:
    with pytest.raises(ValueError, match="git-ignored"):
        agent_runner._output_path(Path(__file__).parents[1] / "unsafe-agent-output")


def test_graph_evidence_uri_is_an_issued_and_valid_final_citation(
    semantic_environment: TestEnvironment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class GraphModel(_MockModel):
        def __call__(self, origin: str, route: str, payload: dict[str, Any] | None, timeout_s: float) -> dict[str, Any]:
            if route != "/api/chat" or payload is None or not payload["tools"]:
                return super().__call__(origin, route, payload, timeout_s)
            self.requests.append(copy.deepcopy(payload))
            messages = [item for item in payload["messages"] if item["role"] == "tool"]
            if not messages:
                return self._tool("codalith_symbol", {"query": "cache_value", "target": "sample"})
            result = json.loads(messages[-1]["content"])
            if messages[-1]["tool_name"] == "codalith_symbol":
                return self._tool("codalith_graph", {"root_uri": result["definitions"][0]["uri"], "direction": "outgoing"})
            edge = next(edge for edge in result["edges"] if edge["target_name"] == "CachedValue")
            return self._final("cache_value calls CachedValue.", [{"uri": edge["evidence_uri"], "claim": "constructor call"}])

    monkeypatch.setattr(agent_runner, "_api_request", GraphModel())
    task = _task("graph")
    task["expected_facts"][0]["evidence"][0].update(start_line=13, end_line=13)
    report = _run(semantic_environment, _files(tmp_path, [task]), repeats=1)
    assert report["errors"] == 0 and report["completed"] is True
    for row in report["rows"]:
        assert row["mcp_calls"] == 2 and row["citation_audit"][0]["valid"] is True
        assert row["citation_audit"][0]["uri"].endswith("#L13-L13")
        assert row["evidence_coverage"][0]["covered"] is True
        assert row["model_wall_ms"] > 0 and row["mcp_wall_ms"] > 0 and row["audit_wall_ms"] > 0


def test_empty_evidence_is_not_a_coverage_denominator_or_failure(
    semantic_environment: TestEnvironment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _MockModel()
    monkeypatch.setattr(agent_runner, "_api_request", model)
    task = _task("ordinary", source=False)
    task["expected_facts"] = [{"id": "non-source-fact", "rubric": "Arithmetic", "evidence": []}]
    report = _run(semantic_environment, _files(tmp_path, [task]), repeats=1)
    for row in report["rows"]:
        assert row["evidence_coverage"] == [{"id": "non-source-fact", "applicable": False, "covered": None}]
    for summary in report["summaries"].values():
        assert summary["expected_facts"] == 1 and summary["expected_evidence_facts"] == 0
        assert summary["evidence_coverage"] is None
        assert summary["source_forbidden_skill_activations"] == 0


def test_thinking_mode_is_explicit_in_preflight_and_formal_requests(
    semantic_environment: TestEnvironment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _MockModel()
    model.expected_think = True
    model.expected_output_tokens = 3072
    model.capabilities.append("thinking")
    monkeypatch.setattr(agent_runner, "_api_request", model)
    report = _run(semantic_environment, _files(tmp_path, [_task("source")]), repeats=1, think=True, output_tokens=3072)
    assert report["completed"] is True and report["errors"] == 0
    assert report["metadata"]["settings"]["think"] is True
    assert report["metadata"]["model_preflight"]["request_controls"]["think"] is True
    assert all(request["think"] is True for request in model.requests)
    assert all(request["options"]["num_predict"] == 3072 for request in model.requests if request["tools"])
    assert all(row["citation_audit"][0]["valid"] for row in report["rows"])


def test_thinking_mode_requires_model_capability_before_any_chat_request(
    semantic_environment: TestEnvironment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _MockModel()
    model.expected_think = True
    monkeypatch.setattr(agent_runner, "_api_request", model)
    report = _run(semantic_environment, _files(tmp_path, [_task("source")]), repeats=1, think=True)
    assert report["completed"] is False
    assert "thinking capability" in report["infrastructure_errors"][0]
    assert all(row["not_run"] for row in report["rows"])
    assert model.requests == []
