"""Evaluation metrics for Codalith packs."""

from __future__ import annotations

from typing import Any


def file_recall_at_k(pack: dict[str, Any], expected_files: list[str], k: int = 5) -> float:
    if not expected_files:
        return 1.0
    spans = pack.get("source_spans", [])[:k]
    found = {str(span.get("path", "")) for span in spans}
    hits = sum(1 for expected in expected_files if any(path.endswith(expected) for path in found))
    return hits / len(expected_files)


def module_accuracy(pack: dict[str, Any], expected_modules: list[str]) -> float:
    if not expected_modules:
        return 1.0
    modules = {str(item.get("name", "")) for item in pack.get("modules", [])}
    hits = sum(1 for expected in expected_modules if expected in modules)
    return hits / len(expected_modules)
