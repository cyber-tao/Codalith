"""Gateway error formatting."""

from __future__ import annotations


def tool_error(message: str, *, code: str = "codalith_error") -> dict[str, object]:
    return {"error": {"code": code, "message": message}}
