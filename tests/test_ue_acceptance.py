from __future__ import annotations

from pathlib import Path

import pytest

from ue_context.coderag.adapter import CodeRAGAdapter
from ue_context.corpus.registry import CorpusRegistry


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
