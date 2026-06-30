from __future__ import annotations

from jobs.extract_semantic import extract_semantic_summary


def test_extract_semantic_summary_counts_fixture(fake_engine_root):
    summary = extract_semantic_summary(fake_engine_root)
    assert summary["modules"] >= 2
    assert summary["module_dependencies"] >= 4
    assert summary["reflection_entities"] >= 3
