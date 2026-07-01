"""MCP resource descriptors for v0."""

from __future__ import annotations

from typing import Any

from codalith.corpus.registry import CorpusRegistry


def resources(registry: CorpusRegistry) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for corpus in registry.engines.values():
        version = corpus.ue_version or corpus.corpus_id.removeprefix("ue-")
        base = f"ue://{version}"
        items.extend(
            [
                {
                    "uri": base,
                    "name": f"Unreal Engine {version}",
                    "description": "Version-pinned Unreal Engine corpus summary.",
                    "mimeType": "application/json",
                },
                {
                    "uri": f"{base}/modules",
                    "name": f"Unreal Engine {version} modules",
                    "description": "Module names and dependency graph summary.",
                    "mimeType": "application/json",
                },
                {
                    "uri": f"{base}/plugins",
                    "name": f"Unreal Engine {version} plugins",
                    "description": "Plugin index summary for the corpus.",
                    "mimeType": "application/json",
                },
                {
                    "uri": f"{base}/cards",
                    "name": f"Unreal Engine {version} knowledge cards",
                    "description": "Verified UE knowledge card collection summary.",
                    "mimeType": "application/json",
                },
                {
                    "uri": f"{base}/mechanisms",
                    "name": f"Unreal Engine {version} mechanisms",
                    "description": "Curated mechanism summaries backed by cards and source evidence.",
                    "mimeType": "application/json",
                },
            ]
        )
    for project_id in registry.projects:
        items.append(
            {
                "uri": f"ue-project://{project_id}",
                "name": f"UE project {project_id}",
                "description": "Project overlay corpus summary.",
                "mimeType": "application/json",
            }
        )
    return items


def resource_templates() -> list[dict[str, str]]:
    return [
        {
            "uriTemplate": "ue://{version}/module/{module}",
            "name": "UE module",
            "description": "Version-pinned Unreal Engine module.",
        },
        {
            "uriTemplate": "ue://{version}/symbol/{symbol}",
            "name": "UE symbol",
            "description": "Version-pinned UE C++ or reflection symbol.",
        },
        {
            "uriTemplate": "ue://{version}/source/{path}",
            "name": "UE source file",
            "description": "Version-pinned Unreal Engine source file.",
        },
        {
            "uriTemplate": "ue://{version}/card/{card_type}/{card_id}",
            "name": "UE knowledge card",
            "description": "Verified source-backed UE knowledge card.",
        },
    ]


def read_resource(uri: str, registry: CorpusRegistry, semantic_status: dict[str, Any] | None) -> dict[str, Any]:
    for corpus in registry.engines.values():
        version = corpus.ue_version or corpus.corpus_id.removeprefix("ue-")
        base = f"ue://{version}"
        if uri == base:
            return {
                "uri": uri,
                "corpus_id": corpus.corpus_id,
                "kind": "engine",
                "version": version,
                "source_commit": corpus.source_commit,
                "semantic": semantic_status,
            }
        if uri in {f"{base}/modules", f"{base}/plugins", f"{base}/cards", f"{base}/mechanisms"}:
            return {
                "uri": uri,
                "corpus_id": corpus.corpus_id,
                "kind": uri.rsplit("/", 1)[-1],
                "semantic": semantic_status,
                "caveat": (
                    "Use codalith_context, codalith_graph, codalith_examples, "
                    "or codalith_read_source for bounded evidence retrieval."
                ),
            }
    for project_id, corpus in registry.projects.items():
        if uri == f"ue-project://{project_id}":
            return {
                "uri": uri,
                "corpus_id": corpus.corpus_id,
                "kind": "project",
                "engine_corpus": corpus.engine_corpus,
                "semantic": semantic_status,
            }
    raise ValueError(f"Unknown resource URI: {uri}")
