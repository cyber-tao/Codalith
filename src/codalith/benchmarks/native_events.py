"""Pure adapters for Codex exec JSONL events and native MCP results."""

from __future__ import annotations

import copy
import json
from typing import Any

_ITEM_EVENTS = frozenset({"item.started", "item.updated", "item.completed"})
_USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)


def normalize_native_mcp_result(item: dict[str, Any]) -> dict[str, Any] | None:
    """Extract an object payload only from a successfully completed MCP call."""
    if (
        item.get("type") != "mcp_tool_call"
        or item.get("status") != "completed"
        or item.get("error") is not None
    ):
        return None
    result = item.get("result")
    if not isinstance(result, dict):
        return None
    if result.get("isError") or result.get("is_error"):
        return None
    structured = [
        result[key]
        for key in ("structured_content", "structuredContent")
        if result.get(key) is not None
    ]
    if structured:
        if any(not isinstance(value, dict) for value in structured):
            return None
        if any(value != structured[0] for value in structured[1:]):
            return None
        payload: dict[str, Any] = structured[0]
        return copy.deepcopy(payload)
    content = result.get("content")
    if not isinstance(content, list):
        return None
    texts = [
        block["text"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]
    if len(texts) != 1:
        return None
    try:
        parsed: object = json.loads(texts[0])
    except ValueError:
        return None
    return copy.deepcopy(parsed) if isinstance(parsed, dict) else None


def parse_native_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge item snapshots and retain latest outer-turn usage without summing it."""
    items: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    final_message: dict[str, Any] | None = None
    usage: dict[str, Any] = dict.fromkeys(_USAGE_FIELDS)
    outer_turns = {"started": 0, "completed": 0, "failed": 0}
    latest_turn_event: str | None = None
    errors: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    other_events: list[dict[str, Any]] = []
    parse_errors: list[str] = []

    for index, event in enumerate(events):
        event_type = event.get("type")
        if not isinstance(event_type, str):
            other_events.append(copy.deepcopy(event))
            parse_errors.append(f"Event {index} has a non-string type")
            continue
        if event_type in _ITEM_EVENTS:
            item = event.get("item")
            if not isinstance(item, dict):
                other_events.append(copy.deepcopy(event))
                parse_errors.append(f"Event {index} has a non-object item")
                continue
            identifier = item.get("id")
            if not isinstance(identifier, str) or not identifier:
                items.append(copy.deepcopy(item))
                parse_errors.append(f"Event {index} has an item without a valid id")
                continue
            if identifier not in positions:
                positions[identifier] = len(items)
                items.append({})
            merged = items[positions[identifier]]
            merged.update(copy.deepcopy(item))
            if event_type == "item.completed" and merged.get("type") == "agent_message":
                if isinstance(merged.get("text"), str):
                    final_message = copy.deepcopy(merged)
                else:
                    final_message = None
                    parse_errors.append(f"Event {index} has an agent message without text")
        elif event_type in {"turn.started", "turn.completed", "turn.failed"}:
            kind = event_type.removeprefix("turn.")
            outer_turns[kind] += 1
            latest_turn_event = event_type
            if event_type == "turn.started":
                final_message = None
            elif event_type == "turn.completed":
                raw_usage = event.get("usage")
                usage = dict.fromkeys(_USAGE_FIELDS)
                if isinstance(raw_usage, dict):
                    usage.update(copy.deepcopy(raw_usage))
                elif raw_usage is not None:
                    parse_errors.append(f"Event {index} has non-object usage")
            elif event_type == "turn.failed":
                failures.append(copy.deepcopy(event))
        elif event_type == "error":
            errors.append(copy.deepcopy(event))
        else:
            other_events.append(copy.deepcopy(event))

    mcp_items = [item for item in items if item.get("type") == "mcp_tool_call"]
    commands = [item for item in items if item.get("type") == "command_execution"]
    other_items = [
        item for item in items
        if item.get("type") not in ("mcp_tool_call", "command_execution")
    ]
    raw_final_content = final_message["text"] if final_message is not None else ""
    final_present = bool(raw_final_content.strip())
    completed = (
        latest_turn_event == "turn.completed"
        and final_present
        and not errors
        and not failures
        and not parse_errors
    )
    return {
        "agent_message": final_message,
        "raw_final_content": raw_final_content,
        "final_present": final_present,
        "mcp_items": mcp_items,
        "command_items": commands,
        "other_items": other_items,
        "usage": usage,
        "outer_turns_started": outer_turns["started"],
        "outer_turns_completed": outer_turns["completed"],
        "outer_turns_failed": outer_turns["failed"],
        "turn_completed": latest_turn_event == "turn.completed",
        "turn_failed": bool(failures),
        "error_events": errors,
        "failure_events": failures,
        "parse_errors": parse_errors,
        "other_events": other_events,
        "completed": completed,
        "failed": not completed,
    }
