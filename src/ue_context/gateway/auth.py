"""Scope helpers for local MCP execution."""

from __future__ import annotations

import os


def scopes_from_env() -> set[str]:
    raw = os.getenv("UE_CONTEXT_SCOPES", "source:read,index:status,cards:read,graph:read,ue:5.7")
    return {item.strip() for item in raw.split(",") if item.strip()}
