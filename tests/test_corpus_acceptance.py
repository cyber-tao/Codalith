from __future__ import annotations

import os
from pathlib import Path

import pytest

from codalith.coderag import CodeRAGAdapter
from codalith.corpus.registry import CorpusRegistry
from codalith.semantic.store import SemanticStore


@pytest.mark.corpus_acceptance
def test_sample_source_mount_can_read_cache_file():
    registry = CorpusRegistry.from_file("configs/corpus_registry.json")
    corpus = registry.get_base()
    if not corpus.source_root.exists():
        pytest.skip(f"Sample source root is not available: {corpus.source_root}")
    adapter = CodeRAGAdapter(registry)
    content = adapter.get_file(corpus.corpus_id, "src/core/cache.py", 1, 20)
    assert "CachedValue" in content


@pytest.mark.corpus_acceptance
def test_sample_semantic_status_is_queryable():
    semantic_target = os.getenv("CODALITH_SEMANTIC_DSN") or os.getenv(
        "CODALITH_SEMANTIC_DB",
        "/tmp/codalith-semantic.sqlite",
    )
    if not semantic_target.startswith(("postgresql://", "postgres://")) and not Path(semantic_target).exists():
        pytest.skip(f"Semantic DB is not available: {semantic_target}")
    store = SemanticStore(semantic_target)
    corpus_id = CorpusRegistry.from_file("configs/corpus_registry.json").get_base().corpus_id
    status = store.semantic_status(corpus_id)
    assert status["corpus_id"] == corpus_id
