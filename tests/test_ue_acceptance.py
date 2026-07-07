from __future__ import annotations

import os
from pathlib import Path

import pytest

from codalith.coderag.adapter import CodeRAGAdapter
from codalith.corpus.registry import CorpusRegistry
from codalith.semantic.store import SemanticStore


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
    semantic_target = os.getenv("CODALITH_SEMANTIC_DSN") or os.getenv(
        "CODALITH_SEMANTIC_DB",
        "/tmp/codalith-semantic.sqlite",
    )
    if not semantic_target.startswith(("postgresql://", "postgres://")) and not Path(semantic_target).exists():
        pytest.skip(f"Semantic DB is not available: {semantic_target}")
    store = SemanticStore(semantic_target)
    status = store.semantic_status("ue-5.7.4")
    expect_ready = os.getenv("CODALITH_EXPECT_SEMANTIC_READY", "").lower() in {"1", "true", "yes"}
    if not expect_ready and status["module_dependencies"] == 0:
        pytest.skip("Semantic DB is configured but has not been populated for this test service")

    assert status["module_dependencies"] >= 100
    assert status["reflection_entities"] >= 100
    assert status["cpp_symbols"] >= 100
    assert status["compile_guards"] > 0
