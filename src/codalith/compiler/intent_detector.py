"""Heuristic UE question intent detection."""

from __future__ import annotations

from codalith.text import contains_word

_INTENT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("compare", ("compare", "diff", "changed", "变更", "对比")),
    ("debug", ("bug", "error", "crash", "报错", "崩溃")),
    ("api_usage", ("how to use", "example", "usage", "示例", "用法")),
    ("trace", ("trace", "trigger", "call path", "触发", "调用链")),
    ("implement", ("implement", "add", "modify", "实现", "修改")),
)


def detect_intent(query: str, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    lower = query.lower()
    for intent, terms in _INTENT_RULES:
        if any(contains_word(term, lower) for term in terms):
            return intent
    return "explain"
