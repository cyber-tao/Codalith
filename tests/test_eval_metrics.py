from __future__ import annotations

from codalith.eval.common import p95
from codalith.eval.metrics import (
    file_recall_at_k,
    missing_source_citation_rate,
    wrong_version_rate,
)


def _pack(
    paths: list[str],
    *,
    version: str = "5.7.4",
    corpus_id: str = "ue-5.7.4",
) -> dict[str, object]:
    return {
        "version": version,
        "corpus_id": corpus_id,
        "source_spans": [
            {
                "path": path,
                "uri": f"codalith://{corpus_id}/source/{path}#L1-L5",
                "corpus_id": corpus_id,
                "corpus_kind": "engine",
            }
            for path in paths
        ],
    }


def test_file_recall_requires_segment_aligned_suffix():
    pack = _pack(["Engine/Source/Runtime/Engine/Classes/GameFramework/MyActor.h"])
    assert file_recall_at_k(pack, ["Actor.h"]) == 0.0

    pack = _pack(["Engine/Source/Runtime/Engine/Classes/GameFramework/Actor.h"])
    assert file_recall_at_k(pack, ["Actor.h"]) == 1.0
    assert file_recall_at_k(pack, ["GameFramework/Actor.h"]) == 1.0
    assert file_recall_at_k(pack, ["Framework/Actor.h"]) == 0.0


def test_file_recall_normalizes_backslashes_and_k_window():
    pack = _pack(["A.h", "B.h", "C.h"])
    assert file_recall_at_k(pack, ["C.h"], k=2) == 0.0
    assert file_recall_at_k({"source_spans": [{"path": "Sub\\Dir\\D.h"}]}, ["Dir/D.h"]) == 1.0


def test_empty_packs_count_as_worst_case_for_citation_and_version():
    assert missing_source_citation_rate({}) == 1.0
    assert wrong_version_rate({}, "5.7.4") == 1.0


def test_citation_and_version_rates_on_populated_packs():
    good = _pack(["Actor.h"])
    assert missing_source_citation_rate(good) == 0.0
    assert wrong_version_rate(good, "5.7.4") == 0.0
    assert wrong_version_rate(good, "5.7.5") == 1.0

    uncited = {"source_spans": [{"path": "Actor.h"}]}
    assert missing_source_citation_rate(uncited) == 1.0

    project_span = {
        "version": "5.7.4",
        "corpus_id": "ue-5.7.4",
        "source_spans": [
            {
                "path": "A.h",
                "uri": "codalith://ProjectA/source/A.h#L1-L2",
                "corpus_id": "ProjectA",
                "corpus_kind": "project",
            }
        ],
    }
    assert wrong_version_rate(project_span, "5.7.4") == 0.0


def test_wrong_version_rate_flags_engine_spans_from_other_corpora():
    leaked = _pack(["Actor.h"])
    spans = leaked["source_spans"]
    assert isinstance(spans, list)
    spans.append(
        {
            "path": "Other.h",
            "uri": "codalith://ue-5.7.5/source/Other.h#L1-L5",
            "corpus_id": "ue-5.7.5",
            "corpus_kind": "engine",
        }
    )
    assert wrong_version_rate(leaked, "5.7.4") == 0.5


def test_p95_uses_nearest_rank_definition():
    assert p95([]) == 0.0
    assert p95([5.0]) == 5.0
    assert p95([1.0, 2.0, 3.0, 4.0]) == 4.0
    assert p95([float(value) for value in range(1, 101)]) == 95.0
