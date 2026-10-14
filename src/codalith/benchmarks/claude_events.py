"""Pure adapters for native Claude Code stream-json output."""

from __future__ import annotations

import copy
import json
from typing import Any, cast

CLAUDE_USAGE_FIELDS = (
    "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens",
)


def _object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def _payload(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list):
        texts = [block.get("text") for block in value if isinstance(block, dict) and block.get("type") == "text"]
        if len(texts) != 1:
            return None
        value = texts[0]
    result = _object(value)
    if result is None or result.get("isError") or result.get("is_error"):
        return None
    structured = [result[key] for key in ("structuredContent", "structured_content") if result.get(key) is not None]
    if structured:
        if any(not isinstance(part, dict) or part != structured[0] for part in structured):
            return None
        return copy.deepcopy(cast(dict[str, Any], structured[0]))
    if "content" in result:
        return _payload(result["content"])
    return copy.deepcopy(result)


def normalize_claude_mcp_result(item: dict[str, Any]) -> dict[str, Any] | None:
    """Return a payload only after a matching successful MCP tool-result block."""
    if item.get("type") != "mcp_tool_call" or item.get("status") != "completed":
        return None
    result = item.get("tool_result")
    if not isinstance(result, dict) or result.get("is_error") or result.get("isError"):
        return None
    native = item.get("native_result")
    if native is not None:
        payload = _payload(native)
        if payload is not None:
            return payload
        if isinstance(native, dict) and (native.get("isError") or native.get("is_error")):
            return None
    return _payload(result.get("content"))


def _usage(result: dict[str, Any] | None) -> dict[str, Any]:
    raw = result.get("usage") if result else None
    usage = dict.fromkeys(CLAUDE_USAGE_FIELDS)
    if isinstance(raw, dict):
        usage.update(copy.deepcopy(raw))
    for field in CLAUDE_USAGE_FIELDS:
        value = usage[field]
        if type(value) is not int or value < 0:
            usage[field] = None
    inputs = [usage[field] for field in CLAUDE_USAGE_FIELDS[:3]]
    usage["total_input_tokens"] = sum(value for value in inputs if isinstance(value, int)) if all(value is not None for value in inputs) else None
    usage["total_tokens"] = (
        usage["total_input_tokens"] + usage["output_tokens"]
        if usage["total_input_tokens"] is not None and usage["output_tokens"] is not None else None
    )
    if result and result.get("subtype") in {"error_during_execution", "error_max_budget_usd"}:
        usage["total_input_tokens"] = None
        usage["total_tokens"] = None
    return usage


def parse_claude_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Bind unique tool-use IDs and preserve latest result totals without summing."""
    tools: dict[tuple[Any, str], dict[str, Any]] = {}
    messages: dict[tuple[Any, str], dict[str, Any]] = {}
    init: list[dict[str, Any]] = []
    result: dict[str, Any] | None = None
    result_events: list[dict[str, Any]] = []
    subagents: list[dict[str, Any]] = []
    retries: list[dict[str, Any]] = []
    denials: list[dict[str, Any]] = []
    compactions: list[dict[str, Any]] = []
    user_messages: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    errors: list[str] = []
    for number, original in enumerate(events):
        event = copy.deepcopy(original)
        kind = event.get("type")
        parent = event.get("parent_tool_use_id")
        if parent is not None and not isinstance(parent, str):
            errors.append(f"Event {number} has an invalid parent tool-use ID")
            other.append(event)
            continue
        if parent is not None and kind in {"assistant", "user", "result"}:
            subagents.append(event)
        if kind == "system" and event.get("subtype") == "init":
            init.append(event)
        elif kind == "system" and event.get("subtype") == "api_retry":
            retries.append(event)
        elif kind == "system" and event.get("subtype") == "permission_denied":
            denials.append(event)
        elif kind == "system" and event.get("subtype") in {"compact_boundary", "microcompact_boundary", "context_compaction"}:
            compactions.append(event)
        elif kind == "result":
            result_events.append(event)
            if parent is None:
                result = event
        elif kind in {"assistant", "user"}:
            message = event.get("message")
            if not isinstance(message, dict):
                errors.append(f"Event {number} has a non-object message")
                other.append(event)
                continue
            identifier = message.get("id")
            if kind == "assistant" and isinstance(identifier, str):
                messages[(parent, identifier)] = event
            if kind == "user":
                user_messages.append(event)
            content = message.get("content")
            if isinstance(content, str):
                continue
            if not isinstance(content, list):
                errors.append(f"Event {number} has invalid message content")
                continue
            results = [block for block in content if isinstance(block, dict) and block.get("type") == "tool_result"]
            for block in content:
                if not isinstance(block, dict) or block.get("type") not in {"tool_use", "tool_result"}:
                    continue
                use = block.get("type") == "tool_use"
                tool_id = block.get("id" if use else "tool_use_id")
                if not isinstance(tool_id, str) or not tool_id:
                    errors.append(f"Event {number} has a tool block without a valid ID")
                    other.append({"event": event, "block": block})
                    continue
                item = tools.setdefault((parent, tool_id), {"id": tool_id, "parent_tool_use_id": parent})
                if use:
                    name = block.get("name")
                    if "name" in item and (item["name"] != name or item.get("arguments") != block.get("input")):
                        errors.append(f"Conflicting tool-use ID {tool_id}")
                    item.update({"name": name, "arguments": block.get("input"), "tool_use": block})
                    if isinstance(name, str) and name.startswith("mcp__"):
                        parts = name.split("__", 2)
                        item.update({"type": "mcp_tool_call", "server": parts[1], "tool": parts[2] if len(parts) == 3 else None})
                    else:
                        item["type"] = "native_tool_call"
                    item.setdefault("status", "in_progress")
                else:
                    if "tool_result" in item and item["tool_result"] != block:
                        errors.append(f"Conflicting tool-result ID {tool_id}")
                    item["tool_result"] = block
                    item["status"] = "failed" if block.get("is_error") or block.get("isError") else "completed"
                    if len(results) == 1 and "tool_use_result" in event:
                        item["native_result"] = event["tool_use_result"]
        else:
            other.append(event)
    for item in tools.values():
        if "tool_use" not in item:
            errors.append(f"Unmatched tool result {item['id']}")
    structured = result.get("structured_output") if result else None
    text = result.get("result") if result else None
    raw_final = json.dumps(structured, ensure_ascii=False) if structured is not None else text if isinstance(text, str) else ""
    successful = bool(result and result.get("subtype") == "success" and not result.get("is_error") and raw_final.strip() and not errors)
    all_items = list(tools.values())
    return {
        "result": result, "result_events": result_events, "structured_output": structured,
        "raw_final_content": raw_final, "completed": successful, "failed": not successful,
        "usage": _usage(result), "usage_raw": copy.deepcopy(result.get("usage")) if result else None,
        "model_usage": copy.deepcopy(result.get("modelUsage", result.get("model_usage"))) if result else None,
        "duration_ms": result.get("duration_ms") if result else None,
        "duration_api_ms": result.get("duration_api_ms") if result else None,
        "total_cost_usd": result.get("total_cost_usd") if result else None,
        "num_turns": result.get("num_turns") if result else None,
        "init_messages": init, "tool_items": all_items,
        "mcp_items": [item for item in all_items if item.get("type") == "mcp_tool_call"],
        "read_items": [item for item in all_items if item.get("name") == "Read"],
        "skill_items": [item for item in all_items if item.get("name") == "Skill"],
        "other_items": [item for item in all_items if item.get("type") != "mcp_tool_call" and item.get("name") not in {"Read", "Skill"}],
        "assistant_messages": list(messages.values()), "user_messages": user_messages,
        "subagent_events": subagents, "api_retries": retries,
        "permission_denials": denials + (copy.deepcopy(result.get("permission_denials") or []) if result else []),
        "compact_events": compactions, "parse_errors": errors, "other_events": other,
    }
