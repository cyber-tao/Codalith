"""Shared helpers for the local and MCP eval runners."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from codalith.eval.metrics import (
    file_recall_at_k,
    missing_source_citation_rate,
    module_accuracy,
    symbol_recall,
    wrong_version_rate,
)


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
