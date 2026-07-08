"""Shared helpers for the local and MCP eval runners."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from codalith.eval.metrics import (
    file_recall_at_k,
    missing_source_citation_rate,
    module_accuracy,
    symbol_recall,
    wrong_version_rate,
)

DEFAULT_METRIC_K = 5


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def p95(values: list[float]) -> float:
    """Nearest-rank p95: the ceil(0.95 * n)-th smallest value (1-indexed)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = math.ceil(0.95 * len(ordered))
    return ordered[rank - 1]


def expected_strings(item: dict[str, Any], key: str) -> list[str]:
    return [str(value) for value in item.get(key, [])]


def pack_metrics(
    pack: dict[str, Any],
    item: dict[str, Any],
    *,
    k: int,
    default_version: str | None = None,
) -> dict[str, float]:
    """Compute the per-item metric set shared by both eval runners.

    Without an expected version (dataset item or runner default) the pack's
    own resolved version is treated as expected, so wrong_version_rate only
    checks span/corpus consistency.
    """
    expected_version = str(item.get("version") or default_version or pack.get("version", ""))
    return {
        f"file_recall@{k}": file_recall_at_k(pack, expected_strings(item, "expected_files"), k=k),
        "module_accuracy": module_accuracy(pack, expected_strings(item, "expected_modules")),
        "symbol_recall": symbol_recall(pack, expected_strings(item, "expected_symbols")),
        "missing_source_citation_rate": missing_source_citation_rate(pack),
        "wrong_version_rate": wrong_version_rate(pack, expected_version),
    }


def average(rows: list[dict[str, Any]], key: str) -> float:
    return sum(float(row[key]) for row in rows) / len(rows) if rows else 0.0


def evaluate_dataset(
    dataset_path: str | Path,
    run_pack: Callable[[dict[str, Any], str | None], dict[str, Any]],
    *,
    version: str | None = None,
    metric_k: int = DEFAULT_METRIC_K,
    row_extras: Callable[[dict[str, Any], dict[str, Any], dict[str, float]], dict[str, Any]]
    | None = None,
) -> tuple[list[dict[str, Any]], list[float]]:
    """Run every dataset item through ``run_pack`` and collect metric rows.

    ``run_pack`` receives the dataset item and its effective version and must
    return a Context Pack dict. ``row_extras`` can append runner-specific
    columns computed from the item, pack, and base metrics.
    """
    rows: list[dict[str, Any]] = []
    latencies: list[float] = []
    for item in read_jsonl(dataset_path):
        item_version = str(item["version"]) if item.get("version") else version
        started = time.perf_counter()
        pack = run_pack(item, item_version)
        elapsed_ms = (time.perf_counter() - started) * 1000
        latencies.append(elapsed_ms)
        metrics = pack_metrics(pack, item, k=metric_k, default_version=version)
        row: dict[str, Any] = {
            "id": item.get("id"),
            "query": item["query"],
            **metrics,
            "latency_ms": elapsed_ms,
        }
        if row_extras is not None:
            row.update(row_extras(item, pack, metrics))
        rows.append(row)
    return rows, latencies


def aggregate_rows(
    rows: list[dict[str, Any]],
    latencies: list[float],
    *,
    metric_k: int,
) -> dict[str, float]:
    """Aggregate the shared per-item metrics into report-level numbers."""
    return {
        "file_recall_at_k": average(rows, f"file_recall@{metric_k}"),
        "module_accuracy": average(rows, "module_accuracy"),
        "symbol_recall": average(rows, "symbol_recall"),
        "missing_source_citation_rate": average(rows, "missing_source_citation_rate"),
        "wrong_version_rate": average(rows, "wrong_version_rate"),
        "latency_p95_ms": p95(latencies),
    }


def write_report_files(
    payload: dict[str, Any],
    markdown: str,
    *,
    json_path: Path,
    md_path: Path,
) -> tuple[Path, Path]:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")
    return json_path, md_path
