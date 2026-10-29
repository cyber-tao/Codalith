from __future__ import annotations

import pytest

from codalith.corpus.generations import GenerationRepository
from conftest import build_environment


@pytest.mark.parametrize("name", ["with # marker", "with %23 marker", "source space \u8def\u5f84"])
@pytest.mark.parametrize("semantic", [False, True])
def test_build_and_deep_verify_support_uri_characters(tmp_path, name, semantic):
    environment = build_environment(tmp_path / name, semantic=semantic)
    generation = GenerationRepository(environment.policy).active(
        environment.corpus, verify_artifacts=True,
    )
    assert generation.manifest.counts["files"] == 2
    service = environment.service()
    try:
        assert service.read("codalith://sample/source/src/core/cache.py#L1-L2").text
    finally:
        service.close()
