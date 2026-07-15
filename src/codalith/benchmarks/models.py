"""Independent benchmark dataset and report models."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from codalith.query.models import StrictModel


class BenchmarkCase(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    query: str = Field(min_length=1, max_length=4096)
    target: str | None = None
    strategy: Literal["auto", "semantic", "text", "symbol"] = "auto"
    expected_files: list[str] = Field(default_factory=list)
    expected_symbols: list[str] = Field(default_factory=list)
    negative: bool = False
    language: Literal["en", "zh", "code", "mixed"] = "en"
    category: str = "general"


class BenchmarkRow(StrictModel):
    id: str
    latency_ms: float
    file_recall_at_5: float | None
    reciprocal_rank: float | None
    ndcg_at_10: float | None
    symbol_recall_at_5: float | None
    citation_valid: bool
    degraded: bool
    negative_passed: bool | None
    returned_files: list[str]
    returned_symbols: list[str]
    error: str | None = None


class BenchmarkReport(StrictModel):
    label: str
    endpoint: str
    count: int
    file_recall_at_5: float | None
    symbol_recall_at_5: float | None
    mrr: float | None
    ndcg_at_10: float | None
    citation_valid_rate: float
    degraded_rate: float
    negative_pass_rate: float | None
    latency_p50_ms: float
    latency_p95_ms: float
    errors: int
    rows: list[BenchmarkRow]
