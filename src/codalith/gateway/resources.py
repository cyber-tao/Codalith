"""MCP resource descriptors and template-backed reads."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from codalith.cards.repository import FileCardRepository
from codalith.corpus.registry import CorpusRegistry
from codalith.corpus.uris import card_uri, corpus_uri
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
    for corpus in registry.corpora.values():
        base = corpus_uri(corpus.corpus_id)
        label = corpus.label
        items.extend(
            [
                {
                    "uri": base,
                    "name": label,
                    "description": corpus.description or "Version-pinned corpus summary.",
                    "mimeType": "application/json",
                },
                {
                    "uri": f"{base}/modules",
                    "name": f"{label} modules",
                    "description": "Module names and dependency graph summary.",
                    "mimeType": "application/json",
                },
                {
                    "uri": f"{base}/cards",
                    "name": f"{label} knowledge cards",
                    "description": "Verified knowledge card collection summary.",
                    "mimeType": "application/json",
                },
            ]
        )
    for project_id in registry.projects:
        items.append(
            {
                "uri": corpus_uri(project_id),
                "name": f"Project overlay {project_id}",
                "description": "Project overlay corpus summary.",
                "mimeType": "application/json",
            }
        )
    return items


def resource_templates() -> list[dict[str, str]]:
    return [
        {
            "uriTemplate": "codalith://{corpus}/module/{module}",
            "name": "Module",
            "description": "Version-pinned module within an indexed corpus.",
        },
        {
            "uriTemplate": "codalith://{corpus}/symbol/{symbol}",
            "name": "Symbol",
            "description": "Version-pinned source symbol.",
        },
        {
            "uriTemplate": "codalith://{corpus}/source/{path}",
            "name": "Source file",
            "description": "Version-pinned source file within an indexed corpus.",
        },
        {
            "uriTemplate": "codalith://{corpus}/card/{card_type}/{card_id}",
            "name": "Knowledge card",
            "description": "Verified source-backed knowledge card.",
        },
    ]


def read_resource(uri: str, tools: CodalithTools) -> dict[str, Any]:
    registry = tools.runtime.registry
    for corpus in registry.corpora.values():
        base = corpus_uri(corpus.corpus_id)
        if uri != base and not uri.startswith(f"{base}/"):
            continue
        tools.require_corpus_access(corpus.corpus_id)
        if uri == base:
            return {
                "uri": uri,
                "corpus_id": corpus.corpus_id,
                "kind": "corpus",
                "version": corpus.version_label,
                "source_revision": corpus.source_revision,
                "semantic": _semantic_status(tools, corpus.corpus_id),
            }
        if uri == f"{base}/modules":
            return {
                "uri": uri,
                "corpus_id": corpus.corpus_id,
                "kind": "modules",
                "semantic": _semantic_status(tools, corpus.corpus_id),
                "caveat": (
                    "Use codalith_context, codalith_graph, codalith_examples, "
                    "or codalith_read_source for bounded evidence retrieval."
                ),
            }
        if uri == f"{base}/cards":
            return _cards_collection_resource(tools, corpus, uri)
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
        if uri == corpus_uri(project_id):
            tools.require_corpus_access(corpus.corpus_id)
            return {
                "uri": uri,
                "corpus_id": corpus.corpus_id,
                "kind": "project",
                "base_corpus": corpus.base_corpus,
                "semantic": _semantic_status(tools, corpus.corpus_id),
            }
    raise URIResolutionError(f"Unknown resource URI: {uri}")


def _semantic_status(tools: CodalithTools, corpus_id: str) -> dict[str, Any] | None:
    store = tools.runtime.semantic_store
    return store.semantic_status(corpus_id) if store is not None else None


def _cards_collection_resource(
    tools: CodalithTools,
    corpus: Corpus,
    uri: str,
) -> dict[str, Any]:
    if "cards:read" not in tools.scopes():
        raise AuthError("Missing required scope: cards:read")
    cards: list[dict[str, str]] = []
    for document in FileCardRepository(tools.runtime.registry).list_documents(corpus.corpus_id):
        card = document.card
        cards.append(
            {
                "uri": document.uri,
                "card_type": card.card_type,
                "card_id": card.card_id,
                "title": card.title,
                "path": str(document.path.relative_to(corpus.card_root)).replace("\\", "/"),
            }
        )
    return {
        "uri": uri,
        "corpus_id": corpus.corpus_id,
        "kind": "cards",
        "count": len(cards),
        "cards": cards,
        "semantic": _semantic_status(tools, corpus.corpus_id),
        "caveat": (
            "Read individual cards via codalith://{corpus}/card/{card_type}/{card_id}, "
            "or use codalith_context for bounded evidence retrieval."
        ),
    }


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
    if "cards:read" not in tools.scopes():
        raise AuthError("Missing required scope: cards:read")
    parts = card_path.split("/")
    if len(parts) != 2 or not all(_SAFE_SEGMENT_RE.fullmatch(part) for part in parts):
        raise URIResolutionError(f"Invalid card resource URI: {uri}")
    card_type, card_id = parts
    document = FileCardRepository(tools.runtime.registry).get_document(
        corpus.corpus_id,
        card_type,
        card_id,
    )
    if document is None:
        raise URIResolutionError(f"Unknown card resource: {uri}")
    return {
        "uri": card_uri(corpus.corpus_id, card_type, card_id),
        "corpus_id": corpus.corpus_id,
        "kind": "card",
        "card_type": card_type,
        "card_id": card_id,
        "markdown": document.markdown,
    }
