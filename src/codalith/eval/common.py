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
    _validate_dataset_rows(rows, Path(path))
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
) -> dict[str, float | None]:
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


def average(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [
        float(row[key])
        for row in rows
        if row.get(key) is not None
    ]
    return sum(values) / len(values) if values else None


def evaluate_dataset(
    dataset_path: str | Path,
    run_pack: Callable[[dict[str, Any], str | None], dict[str, Any]],
    *,
    version: str | None = None,
    metric_k: int = DEFAULT_METRIC_K,
    row_extras: Callable[
        [dict[str, Any], dict[str, Any], dict[str, float | None]],
        dict[str, Any],
    ]
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
) -> dict[str, float | None]:
    """Aggregate the shared per-item metrics into report-level numbers."""
    return {
        "file_recall_at_k": average(rows, f"file_recall@{metric_k}"),
        "module_accuracy": average(rows, "module_accuracy"),
        "symbol_recall": average(rows, "symbol_recall"),
        "missing_source_citation_rate": average(rows, "missing_source_citation_rate"),
        "wrong_version_rate": average(rows, "wrong_version_rate"),
        "latency_p95_ms": p95(latencies),
    }


def metric_coverage(rows: list[dict[str, Any]], key: str) -> int:
    return sum(1 for row in rows if row.get(key) is not None)


def classify_failure(
    metrics: dict[str, float | None],
    *,
    metric_k: int,
    candidate_recall: float | None,
) -> str:
    file_recall = metrics.get(f"file_recall@{metric_k}")
    if file_recall is None:
        return "missing_file_expectation"
    if file_recall < 1.0:
        if candidate_recall is not None and candidate_recall > file_recall:
            return "expected_file_below_top_k"
        return "expected_file_not_retrieved"
    module_score = metrics.get("module_accuracy")
    if module_score is not None and module_score < 1.0:
        return "module_mismatch"
    symbol_score = metrics.get("symbol_recall")
    if symbol_score is not None and symbol_score < 1.0:
        return "symbol_mismatch"
    if float(metrics.get("missing_source_citation_rate") or 0.0) > 0.0:
        return "missing_source_citation"
    if float(metrics.get("wrong_version_rate") or 0.0) > 0.0:
        return "wrong_version"
    return "pass"


def _validate_dataset_rows(rows: list[dict[str, Any]], path: Path) -> None:
    seen_ids: set[str] = set()
    for index, row in enumerate(rows, start=1):
        case_id = str(row.get("id") or "")
        if not case_id or case_id in seen_ids:
            raise ValueError(f"{path}:{index} has a missing or duplicate id")
        seen_ids.add(case_id)
        if not isinstance(row.get("query"), str) or not str(row["query"]).strip():
            raise ValueError(f"{path}:{index} must define a non-empty query")
        expected_files = row.get("expected_files")
        if not isinstance(expected_files, list) or not expected_files:
            raise ValueError(f"{path}:{index} must define expected_files")
        normalized_files = {str(item).replace("\\", "/") for item in expected_files}
        if any("/" not in item or item.startswith("/") for item in normalized_files):
            raise ValueError(
                f"{path}:{index} expected_files must use corpus-relative paths"
            )
        verified_paths = {
            str(source).rsplit(":", 1)[0].replace("\\", "/")
            for source in row.get("verified_sources", [])
        }
        if not verified_paths <= normalized_files:
            raise ValueError(
                f"{path}:{index} verified_sources must be represented in expected_files"
            )


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
