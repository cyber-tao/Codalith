from __future__ import annotations

from codalith.coderag.adapter import RetrievalHit
from codalith.compiler.reranker import rerank


def _hit(source: str, path: str, score: float, module: str | None = None) -> RetrievalHit:
    return RetrievalHit(
        source=source,
        corpus_id="ue-5.7.4",
        uri=f"ue://5.7.4/source/{path}#L1-L10",
        path=path,
        start_line=1,
        end_line=10,
        title=path,
        snippet="UPROPERTY replication window",
        score=score,
        module=module,
    )


def test_source_prior_outranks_high_raw_local_scores():
    local = _hit("coderag-local", "Engine/Source/Runtime/Engine/Private/Big.cpp", 250.0)
    prior = _hit("ue-source-locator", "Engine/Source/Runtime/Engine/Classes/GameFramework/Actor.h", 12.0)

    ordered = rerank([local, prior], identifiers=[], max_hits=2)

    assert ordered[0] is prior


def test_base_scores_are_normalized_within_each_source():
    strong = _hit("coderag-local", "A.cpp", 90.0)
    weak = _hit("coderag-local", "B.cpp", 30.0)

    ordered = rerank([weak, strong], identifiers=[], max_hits=2)

    assert [hit.path for hit in ordered] == ["A.cpp", "B.cpp"]


def test_identifier_exact_match_beats_raw_score_within_source():
    plain = _hit("coderag-local", "Engine/Other.cpp", 80.0)
    matching = _hit("coderag-local", "Engine/Actor.cpp", 40.0)

    ordered = rerank([plain, matching], identifiers=["Actor"], max_hits=2)

    assert ordered[0] is matching


def test_rerank_caps_results_to_max_hits():
    hits = [_hit("coderag-local", f"File{i}.cpp", float(i)) for i in range(10)]

    assert len(rerank(hits, identifiers=[], max_hits=3)) == 3
