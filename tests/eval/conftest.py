from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

EVAL_SUITE_DATASET = (
    Path(__file__).resolve().parents[2]
    / "eval"
    / "datasets"
    / "ue_eval_suite.jsonl"
)


@pytest.fixture()
def eval_suite_rows() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in EVAL_SUITE_DATASET.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.fixture()
def eval_suite_dataset_path() -> Path:
    return EVAL_SUITE_DATASET
