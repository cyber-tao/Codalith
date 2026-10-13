from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import pytest

from codalith.benchmarks import runner
from codalith.benchmarks.models import BenchmarkCase


def test_symbol_recall_requires_case_sensitive_name_in_expected_file() -> None:
    case = BenchmarkCase(
        id="symbol",
        query="BeginPlay",
        expected_files=["Actor.h"],
        expected_symbols=["BeginPlay"],
    )
    wrong_file = {"path": "Widget.h", "symbol": "UWidget::BeginPlay"}
    wrong_case = {"path": "Actor.h", "symbol": "AActor::beginplay"}
    correct = {"path": "Actor.h", "symbol": "AActor::BeginPlay"}
    for hit in (wrong_file, wrong_case):
        assert runner._score_case(case, {}, [hit], 1.0, True).symbol_recall_at_5 == 0.0
    assert runner._score_case(case, {}, [correct], 1.0, True).symbol_recall_at_5 == 1.0


def test_symbol_recall_counts_path_and_name_pairs_before_cutoff() -> None:
    case = BenchmarkCase(
        id="symbol-cutoff",
        query="BeginPlay",
        expected_files=["Actor.h"],
        expected_symbols=["BeginPlay"],
    )
    hits = [{"path": f"Other{index}.h", "symbol": "BeginPlay"} for index in range(5)]
    hits.append({"path": "Actor.h", "symbol": "BeginPlay"})
    assert runner._score_case(case, {}, hits, 1.0, True).symbol_recall_at_5 == 0.0


def test_file_metrics_use_unique_files_and_binary_relevance() -> None:
    case = BenchmarkCase(
        id="file-metrics",
        query="file",
        expected_files=["one.h", "two.h"],
    )
    hits = [{"path": "one.h"}, {"path": "one.h"}, {"path": "other.h"}]
    row = runner._score_case(case, {}, hits, 1.0, True)
    assert row.file_recall_at_5 == 0.5
    assert row.reciprocal_rank == 1.0
    assert row.ndcg_at_10 == pytest.approx(0.6131471927654584)


@pytest.mark.parametrize("name", ["sample-smoke", "ue-regression", "ue-holdout", "ue-literal", "ue-semantic"])
def test_repository_datasets_have_valid_ground_truth_schema(name: str) -> None:
    path = Path(__file__).parents[1] / "benchmarks" / "datasets" / f"{name}.jsonl"
    assert runner.load_dataset(path)


def test_semantic_diagnostics_evaluate_file_retrieval_only() -> None:
    path = Path(__file__).parents[1] / "benchmarks" / "datasets" / "ue-semantic.jsonl"
    for case in runner.load_dataset(path):
        assert case.strategy == "semantic"
        assert case.expected_files
        assert not case.expected_symbols
        assert not any(Path(file).name in case.query for file in case.expected_files)


@pytest.mark.parametrize(
    "case",
    [
        BenchmarkCase(id="file-only", query="actor", expected_files=["Actor.h"]),
        BenchmarkCase(id="symbol-only", query="actor", expected_symbols=["AActor"]),
        BenchmarkCase(id="negative-only", query="missing", negative=True),
    ],
)
def test_acceptance_skips_only_inapplicable_quality_metrics(case: BenchmarkCase) -> None:
    hits = [] if case.negative else [{"path": "Actor.h", "symbol": "AActor"}]
    row = runner._score_case(case, {}, hits, 1.0, True)
    report = runner._aggregate("applicability", "http://unused/mcp", [row])
    assert runner.acceptance_failures(report) == []
    if not case.expected_files:
        assert report.file_recall_at_5 is report.mrr is report.ndcg_at_10 is None
    if not case.expected_symbols:
        assert report.symbol_recall_at_5 is None


def test_missing_applicable_aggregate_metric_is_not_treated_as_na() -> None:
    case = BenchmarkCase(id="symbol", query="actor", expected_symbols=["AActor"])
    row = runner._score_case(case, {}, [{"path": "Actor.h", "symbol": "AActor"}], 1.0, True)
    report = runner._aggregate("missing", "http://unused/mcp", [row])
    malformed = report.model_copy(update={"symbol_recall_at_5": None})
    assert runner.acceptance_failures(malformed) == ["symbol_recall@5 is unavailable"]


def test_positive_row_with_no_quality_scores_is_not_treated_as_na() -> None:
    case = BenchmarkCase(id="file", query="actor", expected_files=["Actor.h"])
    row = runner._score_case(case, {}, [{"path": "Actor.h"}], 1.0, True)
    unscored = row.model_copy(update={
        "file_recall_at_5": None, "reciprocal_rank": None, "ndcg_at_10": None,
    })
    report = runner._aggregate("unscored", "http://unused/mcp", [unscored])
    assert runner.acceptance_failures(report) == [
        "file_recall@5 is unavailable", "symbol_recall@5 is unavailable",
        "MRR is unavailable", "nDCG@10 is unavailable",
    ]


def _citation() -> tuple[dict[str, Any], dict[str, Any]]:
    hit = {
        "corpus_id": "ue-5.7.4",
        "revision": "5.7.4",
        "generation_id": "generation-one",
        "uri": "codalith://ue-5.7.4/Actor.h#L1-L2",
        "path": "Actor.h",
        "start_line": 1,
        "end_line": 2,
        "language": "cpp",
        "symbol": "AActor",
        "kind": "class",
        "score": 1.0,
        "backends": ["structure"],
        "snippet": "class AActor {};",
    }
    source = {
        key: hit[key]
        for key in (
            "corpus_id", "revision", "generation_id", "uri", "path", "start_line", "end_line"
        )
    }
    source.update(
        total_lines=2,
        text="class AActor {};\n",
        sha256="a" * 64,
        indexed_sha256="a" * 64,
        stale=False,
        truncated=False,
        decode_replacements=0,
    )
    return hit, source


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("path", "Other.h"),
        ("corpus_id", "other-corpus"),
        ("revision", "5.6"),
        ("generation_id", "generation-two"),
        ("start_line", 2),
        ("end_line", 3),
        ("stale", True),
        ("sha256", "b" * 64),
        ("sha256", "not-a-hash"),
    ],
)
def test_citation_validation_rejects_identity_and_content_drift(
    monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    hit, source = _citation()
    source[field] = value

    async def fake_call(*args: object) -> dict[str, Any]:
        return source

    monkeypatch.setattr(runner, "_call", fake_call)
    assert not anyio.run(runner._validate_citations, None, [hit])


def test_citation_validation_requires_complete_read_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hit, source = _citation()

    async def fake_call(*args: object) -> dict[str, Any]:
        return source

    monkeypatch.setattr(runner, "_call", fake_call)
    assert anyio.run(runner._validate_citations, None, [hit])
    source.pop("stale")
    assert not anyio.run(runner._validate_citations, None, [hit])


@pytest.mark.parametrize("failure", ["exception", "missing-degraded", "invalid-sixth-hit"])
def test_failed_or_malformed_query_contributes_zero_to_applicable_quality_metrics(
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    @asynccontextmanager
    async def fake_transport(*args: object) -> Any:
        yield None, None, None

    class FakeSession:
        def __init__(self, *args: object) -> None:
            pass

        async def __aenter__(self) -> FakeSession:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def initialize(self) -> None:
            pass

    async def fake_call(*args: object) -> dict[str, Any]:
        if failure == "exception":
            raise RuntimeError("query failed")
        hit, _ = _citation()
        payload: dict[str, Any] = {
            "query": "AActor", "target": "ue-5.7.4", "strategy": "auto",
            "degraded": False, "warnings": [], "hits": [hit] * 5,
        }
        if failure == "missing-degraded":
            payload.pop("degraded")
        else:
            payload["hits"].append({"path": "Actor.h"})
        return payload

    monkeypatch.setattr(runner, "streamable_http_client", fake_transport)
    monkeypatch.setattr(runner, "ClientSession", FakeSession)
    monkeypatch.setattr(runner, "_call", fake_call)
    case = BenchmarkCase(
        id="failed",
        query="AActor",
        expected_files=["Actor.h"],
        expected_symbols=["AActor"],
    )
    report = anyio.run(runner._run_mcp_benchmark, "http://unused/mcp", [case], "failed", False)
    assert report.errors == 1
    assert report.file_recall_at_5 == 0.0
    assert report.symbol_recall_at_5 == 0.0
    assert report.mrr == 0.0
    assert report.ndcg_at_10 == 0.0
