"""Evaluation metrics for Codalith packs."""

from __future__ import annotations

from typing import Any


def file_recall_at_k(pack: dict[str, Any], expected_files: list[str], k: int = 5) -> float:
    if not expected_files:
        return 1.0
    spans = pack.get("source_spans", [])[:k]
    found = {str(span.get("path", "")) for span in spans if isinstance(span, dict)}
    hits = sum(
        1
        for expected in expected_files
        if any(_path_matches(path, expected) for path in found)
    )
    return hits / len(expected_files)


def module_accuracy(pack: dict[str, Any], expected_modules: list[str]) -> float:
    if not expected_modules:
        return 1.0
    modules = {str(item.get("name", "")) for item in pack.get("modules", [])}
    hits = sum(1 for expected in expected_modules if expected in modules)
    return hits / len(expected_modules)


def symbol_recall(pack: dict[str, Any], expected_symbols: list[str]) -> float:
    if not expected_symbols:
        return 1.0
    symbols = {
        str(item.get("name", ""))
        for item in pack.get("symbols", [])
        if isinstance(item, dict)
    }
    symbols.update(
        str(item.get("qualified_name", "")).split("::")[-1]
        for item in pack.get("symbols", [])
        if isinstance(item, dict)
    )
    hits = sum(1 for expected in expected_symbols if expected in symbols)
    return hits / len(expected_symbols)


def missing_source_citation_rate(pack: dict[str, Any]) -> float:
    """Fraction of spans without a citation; an empty pack counts as 1.0."""
    spans = pack.get("source_spans", [])
    if not spans:
        return 1.0
    missing = 0
    for span in spans:
        if not isinstance(span, dict) or not span.get("uri") or not span.get("path"):
            missing += 1
    return missing / len(spans)


def wrong_version_rate(pack: dict[str, Any], expected_version: str) -> float:
    """Fraction of spans not anchored to the expected version.

    Mirrors missing_source_citation_rate for empty packs: no spans means the
    pack cannot demonstrate version anchoring, so it counts as 1.0.
    """
    spans = pack.get("source_spans", [])
    if not spans:
        return 1.0
    wrong = 0
    expected_prefix = f"ue://{expected_version}/"
    for span in spans:
        if not isinstance(span, dict):
            wrong += 1
            continue
        uri = str(span.get("uri", ""))
        if uri.startswith("ue://") and not uri.startswith(expected_prefix):
            wrong += 1
    return wrong / len(spans)


def _path_matches(path: str, expected: str) -> bool:
    # Segment-aligned suffix match so "Actor.h" cannot hit "MyActor.h".
    normalized_path = path.replace("\\", "/")
    normalized_expected = expected.replace("\\", "/").lstrip("/")
    if not normalized_expected:
        return False
    return normalized_path == normalized_expected or normalized_path.endswith(
        "/" + normalized_expected
    )
