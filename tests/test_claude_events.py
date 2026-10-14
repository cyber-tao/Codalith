from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from codalith.benchmarks.claude_events import normalize_claude_mcp_result, parse_claude_events


def _use(identifier: str = "call", name: str = "mcp__codalith__codalith_read", parent: str | None = None) -> dict[str, Any]:
    return {"type": "assistant", "parent_tool_use_id": parent, "message": {"id": "message", "model": "glm-5.3", "usage": {"input_tokens": 10, "output_tokens": 1}, "content": [{"type": "tool_use", "id": identifier, "name": name, "input": {"uri": "source"}}]}}


def _result(content: Any, identifier: str = "call", failed: bool = False, parent: str | None = None) -> dict[str, Any]:
    return {"type": "user", "parent_tool_use_id": parent, "message": {"content": [{"type": "tool_result", "tool_use_id": identifier, "content": content, "is_error": failed}]}}


def _final(**kwargs: Any) -> dict[str, Any]:
    return {"type": "result", "subtype": "success", "is_error": False, "structured_output": {"answer": "done", "citations": [], "abstained": False}, **kwargs}


def test_unique_tool_ids_bind_results_even_before_the_call_and_do_not_mutate_events() -> None:
    events = [_result('{"sources":[{"uri":"source"}]}'), _use(), _use(), _final()]
    original = copy.deepcopy(events)
    parsed = parse_claude_events(events)
    assert events == original and parsed["completed"]
    assert len(parsed["mcp_items"]) == 1
    assert normalize_claude_mcp_result(parsed["mcp_items"][0]) == {"sources": [{"uri": "source"}]}
    assert len(parsed["assistant_messages"]) == 1


@pytest.mark.parametrize("content", [
    '{"sources":[{"uri":"source"}]}',
    [{"type": "text", "text": '{"sources":[{"uri":"source"}]}'}],
    {"structuredContent": {"sources": [{"uri": "source"}]}},
    {"structured_content": {"sources": [{"uri": "source"}]}},
    {"content": [{"type": "text", "text": '{"sources":[{"uri":"source"}]}'}]},
])
def test_mcp_payload_formats(content: Any) -> None:
    parsed = parse_claude_events([_use(), _result(content)])
    assert normalize_claude_mcp_result(parsed["mcp_items"][0]) == {"sources": [{"uri": "source"}]}


@pytest.mark.parametrize("content", ["not JSON", "[]", [], {"isError": True, "structuredContent": {"uri": "source"}}, {"structuredContent": {}, "structured_content": {"uri": "source"}}])
def test_malformed_results_cannot_issue_source(content: Any) -> None:
    parsed = parse_claude_events([_use(), _result(content)])
    assert normalize_claude_mcp_result(parsed["mcp_items"][0]) is None


def test_failed_and_incomplete_calls_retained_without_issuing_uri() -> None:
    for events in ([_use()], [_use(), _result('{"uri":"source"}', failed=True)]):
        parsed = parse_claude_events(events)
        assert len(parsed["mcp_items"]) == 1
        assert normalize_claude_mcp_result(parsed["mcp_items"][0]) is None


def test_raw_native_tool_use_result_takes_precedence_but_cannot_override_failure() -> None:
    event = _result("human readable")
    event["tool_use_result"] = {"structuredContent": {"uri": "source"}}
    item = parse_claude_events([_use(), event])["mcp_items"][0]
    assert normalize_claude_mcp_result(item) == {"uri": "source"}
    event["message"]["content"][0]["is_error"] = True
    assert normalize_claude_mcp_result(parse_claude_events([_use(), event])["mcp_items"][0]) is None


def test_latest_result_usage_uncached_and_cache_are_separate_not_duplicate_totals() -> None:
    final = _final(usage={"input_tokens": 10, "cache_read_input_tokens": 70, "cache_creation_input_tokens": 20, "output_tokens": 6, "cache_creation": {"ephemeral_5m_input_tokens": 20}}, modelUsage={"glm-5.3": {"inputTokens": 100, "outputTokens": 6}}, duration_ms=30, duration_api_ms=20, total_cost_usd=0.123, num_turns=3)
    parsed = parse_claude_events([_final(usage={"input_tokens": 999}), _use(), _use(), final])
    assert parsed["usage"]["input_tokens"] == 10
    assert parsed["usage"]["total_input_tokens"] == 100 and parsed["usage"]["total_tokens"] == 106
    assert parsed["usage_raw"] == final["usage"]
    assert parsed["model_usage"] == final["modelUsage"] and parsed["duration_api_ms"] == 20
    assert parsed["usage"]["cache_creation"] == {"ephemeral_5m_input_tokens": 20}


def test_missing_usage_does_not_become_zero_or_assistant_placeholder_output() -> None:
    parsed = parse_claude_events([_use(), _final(usage={"input_tokens": 10, "output_tokens": 6})])
    assert parsed["usage"]["cache_read_input_tokens"] is None
    assert parsed["usage"]["total_input_tokens"] is None and parsed["usage"]["total_tokens"] is None
    parsed = parse_claude_events([_use()])
    assert parsed["usage_raw"] is None and all(value is None for value in parsed["usage"].values())


@pytest.mark.parametrize("subtype", ["error_during_execution", "error_max_budget_usd"])
def test_crash_or_budget_underreported_usage_is_preserved_without_claiming_a_total(subtype: str) -> None:
    raw = {"input_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0, "output_tokens": 0}
    parsed = parse_claude_events([_final(subtype=subtype, is_error=True, usage=raw)])
    assert parsed["usage_raw"] == raw and parsed["usage"]["input_tokens"] == 0
    assert parsed["usage"]["total_input_tokens"] is None and parsed["usage"]["total_tokens"] is None


def test_final_structured_output_precedes_text_and_error_result_does_not_complete() -> None:
    event = _final(result="a diagnostic string")
    parsed = parse_claude_events([event])
    assert json.loads(parsed["raw_final_content"]) == event["structured_output"]
    assert not parse_claude_events([_final(subtype="error_during_execution", is_error=True)])["completed"]
    assert not parse_claude_events([{"type": "result", "subtype": "success", "result": ""}])["completed"]


def test_native_tools_and_subagent_ids_are_preserved_without_parent_collision() -> None:
    parsed = parse_claude_events([_use(), _result('{"uri":"main"}'), _use(parent="spawn"), _result('{"uri":"child"}', parent="spawn"), _use("read", "Read"), _use("skill", "Skill"), _use("bash", "Bash"), _final()])
    assert len(parsed["mcp_items"]) == 2 and len(parsed["subagent_events"]) == 2
    assert len(parsed["read_items"]) == len(parsed["skill_items"]) == len(parsed["other_items"]) == 1
    assert {normalize_claude_mcp_result(item)["uri"] for item in parsed["mcp_items"]} == {"main", "child"}


def test_unknown_retry_denial_compaction_and_unmatched_result_are_retained() -> None:
    parsed = parse_claude_events([
        {"type": "system", "subtype": "init", "model": "glm-5.3"},
        {"type": "system", "subtype": "api_retry", "error_status": 403},
        {"type": "system", "subtype": "permission_denied", "tool_name": "Read"},
        {"type": "system", "subtype": "compact_boundary"}, {"type": "future", "anything": 1},
        _result("orphan"), _final(permission_denials=[{"tool_name": "Skill"}]),
    ])
    assert not parsed["completed"] and parsed["parse_errors"] == ["Unmatched tool result call"]
    assert len(parsed["init_messages"]) == len(parsed["api_retries"]) == len(parsed["compact_events"]) == len(parsed["other_events"]) == 1
    assert len(parsed["permission_denials"]) == 2
