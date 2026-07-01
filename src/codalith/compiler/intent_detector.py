"""Heuristic UE question intent detection."""

from __future__ import annotations


def detect_intent(query: str, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    lower = query.lower()
    if any(term in lower for term in ("compare", "diff", "changed", "变更", "对比")):
        return "compare"
    if any(term in lower for term in ("bug", "error", "crash", "报错", "崩溃")):
        return "debug"
    if any(term in lower for term in ("how to use", "example", "usage", "示例", "用法")):
        return "api_usage"
    if any(term in lower for term in ("trace", "trigger", "call path", "触发", "调用链")):
        return "trace"
    if any(term in lower for term in ("implement", "add", "modify", "实现", "修改")):
        return "implement"
    return "explain"
