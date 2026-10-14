from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from codalith.benchmarks.native_events import (
    normalize_native_mcp_result,
    parse_native_events,
)


def _mcp(**values: Any) -> dict[str, Any]:
    return {
        "id": "mcp-1", "type": "mcp_tool_call", "server": "codalith",
        "tool": "codalith_read", "arguments": {"uri": "codalith://sample/source/x#L1-L1"},
        "status": "completed", "error": None,
        "result": {"structured_content": {"uri": "codalith://sample/source/x#L1-L1"}},
        **values,
    }


def _event(item: dict[str, Any], event_type: str = "item.completed") -> dict[str, Any]:
    return {"type": event_type, "item": item}


def _final(text: str = '{"answer":"ok","citations":[],"abstained":false}') -> dict[str, Any]:
    return _event({"id": "final", "type": "agent_message", "text": text})


def test_item_lifecycle_merges_without_double_counting_or_mutation() -> None:
    events = [
        {"type": "turn.started"},
        _event(_mcp(status="in_progress", result=None), "item.started"),
        _event({"id": "mcp-1", "status": "in_progress"}, "item.updated"),
        _event({"id": "mcp-1", "status": "completed", "result": _mcp()["result"]}),
        _final(),
        {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 7}},
    ]
    original = copy.deepcopy(events)
    parsed = parse_native_events(events)
    assert len(parsed["mcp_items"]) == 1
    item = parsed["mcp_items"][0]
    assert item["server"] == "codalith" and item["tool"] == "codalith_read"
    assert item["arguments"] == _mcp()["arguments"]
    assert item["result"] == _mcp()["result"]
    assert normalize_native_mcp_result(item) == _mcp()["result"]["structured_content"]
    assert parsed["completed"] and not parsed["failed"]
    assert parsed["outer_turns_started"] == parsed["outer_turns_completed"] == 1
    assert events == original
    item["result"]["structured_content"]["uri"] = "modified"
    assert events == original


@pytest.mark.parametrize("key", ["structured_content", "structuredContent"])
def test_structured_result_spelling_and_detached_payload(key: str) -> None:
    item = _mcp(result={key: {"sources": [{"uri": "source"}]}})
    payload = normalize_native_mcp_result(item)
    assert payload == {"sources": [{"uri": "source"}]}
    assert payload is not None
    payload["sources"][0]["uri"] = "changed"
    assert item["result"][key]["sources"][0]["uri"] == "source"


def test_single_text_json_object_is_normalized() -> None:
    payload = {"sources": [{"uri": "source"}], "query": "x"}
    item = _mcp(result={"structured_content": None, "content": [{"type": "text", "text": json.dumps(payload)}]})
    assert normalize_native_mcp_result(item) == payload


@pytest.mark.parametrize("changes", [
    {"status": "failed"},
    {"status": "in_progress"},
    {"status": None},
    {"error": {"message": "failed"}},
    {"error": {}},
    {"result": {"isError": True, "structuredContent": {"uri": "not-issued"}}},
    {"result": {"is_error": True, "structured_content": {"uri": "not-issued"}}},
])
def test_failed_or_unfinished_result_cannot_issue_a_source(changes: dict[str, Any]) -> None:
    assert normalize_native_mcp_result(_mcp(**changes)) is None


def test_failed_and_unfinished_mcp_attempts_are_kept() -> None:
    events = [
        _event(_mcp(status="in_progress", result=None), "item.started"),
        _event(_mcp(id="mcp-2", status="failed", error={"message": "bad"})),
        {"type": "turn.failed", "error": {"message": "deadline"}},
    ]
    parsed = parse_native_events(events)
    assert [item["id"] for item in parsed["mcp_items"]] == ["mcp-1", "mcp-2"]
    assert all(normalize_native_mcp_result(item) is None for item in parsed["mcp_items"])
    assert parsed["turn_failed"] and parsed["outer_turns_failed"] == 1
    assert parsed["failure_events"] == events[-1:]
    assert parsed["failed"] and not parsed["final_present"]


@pytest.mark.parametrize("result", [
    None,
    "not an MCP envelope",
    [],
    {},
    {"structured_content": ["invalid"], "content": [{"type": "text", "text": "{}"}]},
    {"structured_content": {}, "structuredContent": {"uri": "conflicting"}},
    {"content": [{"type": "text", "text": "not json"}]},
    {"content": [{"type": "text", "text": "[]"}]},
    {"content": [{"type": "text", "text": "null"}]},
    {"content": [{"type": "text", "text": "{}"}, {"type": "text", "text": "{}"}]},
])
def test_malformed_or_ambiguous_results_are_not_payloads(result: object) -> None:
    assert normalize_native_mcp_result(_mcp(result=result)) is None


def test_latest_usage_replaces_prior_usage_and_keeps_cache_as_a_subset() -> None:
    parsed = parse_native_events([
        {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 10, "reasoning_output_tokens": 3}},
        {"type": "turn.started"},
        _final(),
        {"type": "turn.completed", "usage": {"input_tokens": 200, "cached_input_tokens": 150, "output_tokens": 20, "reasoning_output_tokens": 8, "provider_detail": "retained"}},
    ])
    assert parsed["usage"] == {
        "input_tokens": 200, "cached_input_tokens": 150, "output_tokens": 20,
        "reasoning_output_tokens": 8, "provider_detail": "retained",
    }
    assert parsed["usage"]["input_tokens"] + parsed["usage"]["output_tokens"] == 220
    assert parsed["outer_turns_completed"] == 2


def test_latest_missing_usage_does_not_reuse_earlier_values_or_fill_zero() -> None:
    parsed = parse_native_events([
        {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 10}},
        _final(),
        {"type": "turn.completed", "usage": {"output_tokens": 3}},
    ])
    assert parsed["usage"] == {
        "input_tokens": None, "cached_input_tokens": None,
        "output_tokens": 3, "reasoning_output_tokens": None,
    }
    assert all(value is None for value in parse_native_events([])["usage"].values())


def test_unknown_items_commands_and_events_are_preserved() -> None:
    command = {"id": "cmd", "type": "command_execution", "command": "read skill", "status": "completed"}
    unknown_item = {"id": "future", "type": "future_tool", "result": {"secret": "retained"}}
    future_event = {"type": "future.event", "extra": [1, 2]}
    parsed = parse_native_events([
        _event(command, "item.started"), _event(command),
        _event(unknown_item), future_event, _final(), {"type": "turn.completed"},
    ])
    assert parsed["command_items"] == [command]
    assert unknown_item in parsed["other_items"]
    assert parsed["other_events"] == [future_event]
    assert parsed["completed"]


@pytest.mark.parametrize("text", ["", " \n\t"])
def test_empty_completed_final_is_unsuccessful(text: str) -> None:
    parsed = parse_native_events([_final(text), {"type": "turn.completed"}])
    assert parsed["raw_final_content"] == text
    assert not parsed["final_present"] and parsed["failed"]


def test_unfinished_agent_message_is_not_a_final_answer() -> None:
    parsed = parse_native_events([
        _event({"id": "partial", "type": "agent_message", "text": "unfinished"}, "item.started"),
        {"type": "turn.completed"},
    ])
    assert parsed["agent_message"] is None
    assert parsed["raw_final_content"] == "" and parsed["failed"]


def test_new_outer_turn_cannot_reuse_the_previous_turns_final_message() -> None:
    parsed = parse_native_events([
        {"type": "turn.started"}, _final("previous turn"), {"type": "turn.completed"},
        {"type": "turn.started"}, {"type": "turn.completed"},
    ])
    assert parsed["agent_message"] is None
    assert parsed["raw_final_content"] == "" and parsed["failed"]


def test_latest_completed_agent_message_keeps_raw_contract_errors() -> None:
    parsed = parse_native_events([
        _final("intermediate"),
        _event({"id": "last", "type": "agent_message", "text": "```json\n{}\n```"}),
        {"type": "turn.completed"},
    ])
    assert parsed["agent_message"]["id"] == "last"
    assert parsed["raw_final_content"] == "```json\n{}\n```"
    assert parsed["completed"]  # The caller validates the frozen final-output schema.


def test_error_and_failed_turn_cannot_be_hidden_by_later_completion() -> None:
    failure = {"type": "turn.failed", "error": {"message": "bad"}}
    error = {"type": "error", "message": "transport failed"}
    parsed = parse_native_events([failure, error, _final(), {"type": "turn.completed"}])
    assert parsed["error_events"] == [error]
    assert parsed["turn_failed"] and parsed["turn_completed"]
    assert parsed["failed"] and not parsed["completed"]


def test_malformed_items_and_usage_are_observable_not_dropped() -> None:
    no_id = _mcp()
    del no_id["id"]
    events = [
        {"type": "item.started", "item": None}, _event(no_id),
        _final(), {"type": "turn.completed", "usage": "invalid"},
    ]
    parsed = parse_native_events(events)
    assert parsed["mcp_items"] == [no_id]
    assert events[0] in parsed["other_events"]
    assert len(parsed["parse_errors"]) == 3
    assert parsed["failed"]


def test_malformed_event_and_unknown_item_types_do_not_crash_the_parser() -> None:
    malformed_event = {"type": ["future"]}
    unknown_item = {"id": "unknown", "type": {"future": "item"}}
    parsed = parse_native_events([
        malformed_event, _event(unknown_item), _final(), {"type": "turn.completed"},
    ])
    assert parsed["other_events"] == [malformed_event]
    assert unknown_item in parsed["other_items"]
    assert parsed["parse_errors"] == ["Event 0 has a non-string type"]
    assert parsed["failed"]
