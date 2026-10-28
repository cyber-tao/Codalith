"""Bounded version discovery without loading models or touching source trees."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from codalith.query.models import CatalogResponse, CatalogVersion

if TYPE_CHECKING:
    from codalith.query.service import QueryService

def version_catalog(service: QueryService, target: str | None = None) -> CatalogResponse:
    refresh = getattr(service.registry, "refresh", None)
    if refresh is not None:
        refresh()
    corpora = service.registry.resolve(target).corpora if target is not None else tuple(service.registry.corpora.values())
    pool = getattr(service, "pool", None)
    managed: dict[str, Any] = {}
    if pool is not None:
        managed = {row["target_id"]: row for row in pool.catalog_entries()}
    versions = []
    for corpus in corpora:
        status = service.status(target=corpus.corpus_id)
        state = status.corpora[0] if status.corpora else None
        metadata = managed.get(corpus.corpus_id, {})
        engine_version = metadata.get("engine_version")
        capabilities = ["read", "text", "symbol", "graph", "compare"] if state and state.generation_id else []
        if state and state.semantic_available:
            capabilities.append("semantic")
        versions.append(CatalogVersion(
            target_id=corpus.corpus_id, display_name=corpus.display_name,
            engine_version=engine_version, source_commit=corpus.revision,
            snapshot_id=state.generation_id if state else None,
            ready=status.ready, capabilities=capabilities,
        ))
    return CatalogResponse(versions=versions)
