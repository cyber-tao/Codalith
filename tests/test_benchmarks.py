from __future__ import annotations

import json
from pathlib import Path

import pytest

from codalith.benchmarks.models import BenchmarkReport
from codalith.benchmarks.runner import (
    acceptance_failures,
    load_dataset,
    run_mcp_benchmark,
)
from conftest import TestEnvironment
from test_mcp import running_http_server


def test_dataset_schema_is_strict_and_ids_are_unique(tmp_path: Path) -> None:
    row = {
        "id": "one",
        "query": "CachedValue",
        "target": "sample",
        "strategy": "symbol",
        "expected_files": ["src/core/cache.py"],
        "expected_symbols": ["CachedValue"],
        "negative": False,
        "language": "code",
        "category": "symbol",
    }
    path = tmp_path / "dataset.jsonl"
    path.write_text(
        json.dumps(row) + "\n" + json.dumps(row) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Duplicate benchmark id"):
        load_dataset(path)
    row["legacy_mode"] = "trace"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="legacy_mode"):
        load_dataset(path)


def test_acceptance_requires_every_metric() -> None:
    report = BenchmarkReport(
        label="empty",
        endpoint="http://localhost/mcp",
        count=1,
        file_recall_at_5=None,
        symbol_recall_at_5=None,
        mrr=None,
        ndcg_at_10=None,
        citation_valid_rate=1.0,
        degraded_rate=0.0,
        negative_pass_rate=None,
        latency_p50_ms=1,
        latency_p95_ms=1,
        errors=0,
        rows=[],
    )
    failures = acceptance_failures(report)
    assert failures == [
        "file_recall@5 is unavailable",
        "symbol_recall@5 is unavailable",
        "MRR is unavailable",
        "nDCG@10 is unavailable",
    ]


def test_sample_benchmark_runs_through_real_mcp_endpoint(
    semantic_environment: TestEnvironment,
) -> None:
    dataset = Path(__file__).parents[1] / "benchmarks" / "datasets" / "sample-smoke.jsonl"
    with running_http_server(semantic_environment) as endpoint:
        report = run_mcp_benchmark(
            endpoint=endpoint,
            dataset_path=dataset,
            label="pytest",
        )
    assert report.count == 10
    assert report.file_recall_at_5 == 1.0
    assert report.symbol_recall_at_5 == 1.0
    assert report.citation_valid_rate == 1.0
    assert report.degraded_rate == 0.0
    assert report.negative_pass_rate == 1.0
    assert report.errors == 0
