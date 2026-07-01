from __future__ import annotations

import os
from pathlib import Path

import pytest

from ue_context.coderag.adapter import CodeRAGAdapter
from ue_context.corpus.registry import CorpusRegistry
from ue_context.semantic.db import SemanticStore


@pytest.mark.ue_acceptance
def test_ue57_source_mount_can_read_actor_header():
    root = Path("/srv/ue/5.7.4")
    if not (root / "Engine/Source").exists():
        pytest.skip("UE 5.7 source is not mounted at /srv/ue/5.7.4")
    assert (root / "Engine/Source").exists()
    registry = CorpusRegistry.from_file("configs/corpus_registry.yaml")
    adapter = CodeRAGAdapter(registry)
    content = adapter.get_file(
        "ue-5.7.4",
        "Engine/Source/Runtime/Engine/Classes/GameFramework/Actor.h",
        1,
        5,
    )
    assert "AActor" in content or "#pragma once" in content


@pytest.mark.ue_acceptance
def test_ue57_semantic_status_meets_v0_floors():
    semantic_db = Path(os.getenv("UE_CONTEXT_SEMANTIC_DB", "/tmp/ue-context-semantic.sqlite"))
    if not semantic_db.exists():
        pytest.skip(f"Semantic DB is not available: {semantic_db}")
    store = SemanticStore(semantic_db)
    status = store.semantic_status("ue-5.7.4")

    assert status["module_dependencies"] >= 100
    assert status["reflection_entities"] >= 100
    assert status["cpp_symbols"] >= 100
    assert status["compile_guards"] > 0
