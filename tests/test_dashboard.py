from __future__ import annotations

from datetime import UTC, datetime

import pytest

from codalith.dashboard.telemetry import TelemetryStore
from codalith.query.models import CorpusStatus, StatusResponse


def _status() -> StatusResponse:
    return StatusResponse(
        target="sample",
        ready=True,
        corpora=[
            CorpusStatus(
                corpus_id="sample",
                revision="sample-v1",
                state="ready",
                generation_id="generation-1",
                semantic_available=True,
                files=12,
                symbols=48,
                references=96,
                module_dependencies=3,
            )
        ],
    )


def test_dashboard_snapshot_rolls_up_queries_tools_logs_and_resources() -> None:
    store = TelemetryStore()
    started_at = datetime.now(UTC)
    store.record_call(
        tool="codalith_search",
        arguments={"query": "Find UWorld", "target": "sample"},
        target="sample",
        started_at=started_at,
        duration_ms=125.0,
        result={"hits": [{"uri": "codalith://sample/source/world.py"}], "degraded": False},
        error=None,
    )
    store.record_call(
        tool="codalith_symbol",
        arguments={"query": "MissingType", "target": "sample"},
        target="sample",
        started_at=started_at,
        duration_ms=900.0,
        result=None,
        error=ValueError("No matching symbol"),
    )

    payload = store.snapshot(
        window_key="1h",
        target="sample",
        status=_status(),
        targets=[{"id": "sample", "label": "Sample", "kind": "corpus"}],
    )
    summary = payload["summary"]

    assert isinstance(summary, dict)
    assert summary["total_queries"] == 2
    assert summary["p50_latency_ms"] == 125.0
    assert summary["p95_latency_ms"] == 900.0
    assert summary["success_rate"] == 50.0
    assert float(summary["peak_memory_mb"]) > 0
    assert summary["error_count"] == 1
    assert [item["name"] for item in payload["tools"]] == [  # type: ignore[index]
        "codalith_search",
        "codalith_symbol",
    ]
    assert payload["recent_queries"][0]["status"] in {"success", "error"}  # type: ignore[index]
    assert {item["level"] for item in payload["logs"]} == {"INFO", "ERROR"}  # type: ignore[index]
    assert payload["corpora"][0]["symbols"] == 48  # type: ignore[index]
    assert payload["alerts"]


def test_dashboard_snapshot_rejects_unknown_range() -> None:
    with pytest.raises(ValueError, match="Unsupported dashboard range"):
        TelemetryStore().snapshot(
            window_key="forever",
            target="sample",
            status=_status(),
            targets=[],
        )
