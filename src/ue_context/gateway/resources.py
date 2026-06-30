"""MCP resource descriptors for v0."""

from __future__ import annotations


def resource_templates() -> list[dict[str, str]]:
    return [
        {
            "uriTemplate": "ue://{version}/source/{path}",
            "name": "UE source file",
            "description": "Version-pinned Unreal Engine source file.",
        }
    ]
