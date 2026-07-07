"""MCP resource descriptors and template-backed reads."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from codalith.corpus.registry import CorpusRegistry
from codalith.errors import URIResolutionError
from codalith.gateway.auth import AuthError

if TYPE_CHECKING:
    from codalith.corpus.registry import Corpus
    from codalith.gateway.tools import CodalithTools

# Dots may separate name parts but cannot lead, trail, or repeat, so path
# segments like ".." can never escape the card root.
_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$")


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


def read_resource(uri: str, tools: CodalithTools) -> dict[str, Any]:
    registry = tools.runtime.registry
    for corpus in registry.engines.values():
        version = corpus.ue_version or corpus.corpus_id.removeprefix("ue-")
        base = f"ue://{version}"
        if uri != base and not uri.startswith(f"{base}/"):
            continue
        tools._require_corpus_access(corpus.corpus_id)
        if uri == base:
            return {
                "uri": uri,
                "corpus_id": corpus.corpus_id,
                "kind": "engine",
                "version": version,
                "source_commit": corpus.source_commit,
                "semantic": _semantic_status(tools, corpus.corpus_id),
            }
        if uri in {f"{base}/modules", f"{base}/plugins", f"{base}/cards", f"{base}/mechanisms"}:
            return {
                "uri": uri,
                "corpus_id": corpus.corpus_id,
                "kind": uri.rsplit("/", 1)[-1],
                "semantic": _semantic_status(tools, corpus.corpus_id),
                "caveat": (
                    "Use codalith_context, codalith_graph, codalith_examples, "
                    "or codalith_read_source for bounded evidence retrieval."
                ),
            }
        if uri.startswith(f"{base}/module/"):
            return _module_resource(tools, corpus, uri, uri.removeprefix(f"{base}/module/"))
        if uri.startswith(f"{base}/symbol/"):
            return _symbol_resource(tools, corpus, uri, uri.removeprefix(f"{base}/symbol/"))
        if uri.startswith(f"{base}/source/"):
            # Route through the tool so policy, rate limiting, and audit apply.
            return tools.codalith_read_source(uri=uri)
        if uri.startswith(f"{base}/card/"):
            return _card_resource(tools, corpus, uri, uri.removeprefix(f"{base}/card/"))
        raise URIResolutionError(f"Unknown resource URI: {uri}")
    for project_id, corpus in registry.projects.items():
        if uri == f"ue-project://{project_id}":
            tools._require_corpus_access(corpus.corpus_id)
            return {
                "uri": uri,
                "corpus_id": corpus.corpus_id,
                "kind": "project",
                "engine_corpus": corpus.engine_corpus,
                "semantic": _semantic_status(tools, corpus.corpus_id),
            }
    raise URIResolutionError(f"Unknown resource URI: {uri}")


def _semantic_status(tools: CodalithTools, corpus_id: str) -> dict[str, Any] | None:
    store = tools.runtime.semantic_store
    return store.semantic_status(corpus_id) if store is not None else None


def _module_resource(
    tools: CodalithTools,
    corpus: Corpus,
    uri: str,
    module_name: str,
) -> dict[str, Any]:
    store = tools.runtime.semantic_store
    if store is None:
        raise URIResolutionError(f"Semantic store is not configured; cannot resolve {uri}")
    module = store.get_module(corpus.corpus_id, module_name)
    if module is None:
        raise URIResolutionError(f"Unknown module resource: {uri}")
    return {
        "uri": uri,
        "corpus_id": corpus.corpus_id,
        "kind": "module",
        "module": module,
        "dependencies": store.list_module_deps(corpus.corpus_id, module_name),
    }


def _symbol_resource(
    tools: CodalithTools,
    corpus: Corpus,
    uri: str,
    symbol: str,
) -> dict[str, Any]:
    store = tools.runtime.semantic_store
    if store is None:
        raise URIResolutionError(f"Semantic store is not configured; cannot resolve {uri}")
    matches = store.find_symbols(corpus.corpus_id, symbol, limit=20)
    if not matches:
        raise URIResolutionError(f"Unknown symbol resource: {uri}")
    return {
        "uri": uri,
        "corpus_id": corpus.corpus_id,
        "kind": "symbol",
        "symbol": symbol,
        "matches": matches,
    }


def _card_resource(
    tools: CodalithTools,
    corpus: Corpus,
    uri: str,
    card_path: str,
) -> dict[str, Any]:
    if "cards:read" not in tools._scopes():
        raise AuthError("Missing required scope: cards:read")
    parts = card_path.split("/")
    if len(parts) != 2 or not all(_SAFE_SEGMENT_RE.fullmatch(part) for part in parts):
        raise URIResolutionError(f"Invalid card resource URI: {uri}")
    card_type, card_id = parts
    card_file = corpus.card_root / "UE_KNOWLEDGE" / card_type.title() / f"{card_id}.md"
    if not card_file.is_file():
        raise URIResolutionError(f"Unknown card resource: {uri}")
    return {
        "uri": uri,
        "corpus_id": corpus.corpus_id,
        "kind": "card",
        "card_type": card_type,
        "card_id": card_id,
        "markdown": card_file.read_text(encoding="utf-8"),
    }
