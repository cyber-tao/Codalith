"""Heuristic UE question intent detection."""

from __future__ import annotations

import re

_INTENT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("compare", ("compare", "diff", "changed", "变更", "对比")),
    ("debug", ("bug", "error", "crash", "报错", "崩溃")),
    ("api_usage", ("how to use", "example", "usage", "示例", "用法")),
    ("trace", ("trace", "trigger", "call path", "触发", "调用链")),
    ("implement", ("implement", "add", "modify", "实现", "修改")),
)

_ASCII_TERM = re.compile(r"^[a-z0-9_ ]+$")


def detect_intent(query: str, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    lower = query.lower()
    for intent, terms in _INTENT_RULES:
        if any(_term_matches(term, lower) for term in terms):
            return intent
    return "explain"


def _term_matches(term: str, lower: str) -> bool:
    # ASCII terms need word boundaries so "error" does not match "terror";
    # CJK terms have no word boundaries and keep substring semantics.
    if _ASCII_TERM.match(term):
        return re.search(rf"\b{re.escape(term)}\b", lower) is not None
    return term in lower
