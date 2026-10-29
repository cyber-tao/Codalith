"""Assemble the same query backends for serving and local diagnostics."""

from __future__ import annotations

from codalith.corpus.registry import CorpusRegistry
from codalith.corpus.source_policy import SourcePolicy
from codalith.management.config import ManagementConfig
from codalith.query.service import QueryService


def create_query_service(
    registry: CorpusRegistry,
    policy: SourcePolicy,
    *,
    management_config: ManagementConfig | None = None,
    initialize_shared: bool = False,
) -> QueryService:
    config = management_config or ManagementConfig.from_registry(
        registry.path, require_admin_token=False
    )
    if not config.enabled:
        return QueryService(registry, policy)
    from codalith.corpus.managed_registry import ManagedRegistry
    from codalith.indexing.shared import SharedPool, SharedQueryService

    pool = SharedPool(config.storage_root / "shared", policy, readonly=not initialize_shared)
    managed_registry = ManagedRegistry(registry, pool.published_corpora)
    return SharedQueryService(managed_registry, policy, pool)
