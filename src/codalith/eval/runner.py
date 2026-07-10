"""In-process eval runner for Codalith."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codalith.coderag import CodeRAGAdapter
from codalith.compiler.context_compiler import ContextCompiler
from codalith.corpus.registry import CorpusRegistry
from codalith.eval.common import (
    DEFAULT_METRIC_K,
    aggregate_rows,
    average,
    classify_failure,
    evaluate_dataset,
    expected_strings,
    metric_coverage,
    write_report_files,
)
from codalith.eval.metrics import file_recall_at_k
from codalith.semantic.store import SemanticStore


@dataclass(frozen=True, slots=True)
class EvalReport:
    metric_k: int
    max_source_spans: int
    count: int
    file_recall_at_k: float | None
    candidate_file_recall: float | None
    module_accuracy: float | None
    symbol_recall: float | None
    missing_source_citation_rate: float | None
    wrong_version_rate: float | None
    latency_p95_ms: float
    metric_coverage: dict[str, int]
    rows: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric_k": self.metric_k,
            "max_source_spans": self.max_source_spans,
            "count": self.count,
            "file_recall@k": self.file_recall_at_k,
            "candidate_file_recall": self.candidate_file_recall,
            "module_accuracy": self.module_accuracy,
            "symbol_recall": self.symbol_recall,
            "missing_source_citation_rate": self.missing_source_citation_rate,
            "wrong_version_rate": self.wrong_version_rate,
            "latency_p95_ms": self.latency_p95_ms,
            "metric_coverage": self.metric_coverage,
            "rows": self.rows,
        }

    @property
    def all_passed(self) -> bool:
        return all(row.get("failure_class") == "pass" for row in self.rows)


class EvalRunner:
    def __init__(self, compiler: ContextCompiler) -> None:
        self.compiler = compiler

    def run(
        self,
        dataset_path: str | Path,
        *,
        version: str | None = None,
        max_source_spans: int = 8,
        metric_k: int = DEFAULT_METRIC_K,
    ) -> EvalReport:
        def run_pack(item: dict[str, Any], item_version: str | None) -> dict[str, Any]:
            return self.compiler.compile(
                query=str(item["query"]),
                corpus=item_version,
                mode=str(item.get("mode", "explain")),
                max_source_spans=max_source_spans,
            ).as_dict()

        def row_extras(
            item: dict[str, Any],
            pack: dict[str, Any],
            metrics: dict[str, float | None],
        ) -> dict[str, Any]:
            expected_files = expected_strings(item, "expected_files")
            candidate_recall = file_recall_at_k(
                pack,
                expected_files,
                k=max_source_spans,
            )
            return {
                f"file_recall@{max_source_spans}": candidate_recall,
                "failure_class": classify_failure(
                    metrics,
                    metric_k=metric_k,
                    candidate_recall=candidate_recall,
                ),
            }

        rows, latencies = evaluate_dataset(
            dataset_path,
            run_pack,
            version=version,
            metric_k=metric_k,
            row_extras=row_extras,
        )
        aggregates = aggregate_rows(rows, latencies, metric_k=metric_k)
        return EvalReport(
            metric_k=metric_k,
            max_source_spans=max_source_spans,
            count=len(rows),
            file_recall_at_k=aggregates["file_recall_at_k"],
            candidate_file_recall=average(
                rows,
                f"file_recall@{max_source_spans}",
            ),
            module_accuracy=aggregates["module_accuracy"],
            symbol_recall=aggregates["symbol_recall"],
            missing_source_citation_rate=aggregates[
                "missing_source_citation_rate"
            ],
            wrong_version_rate=aggregates["wrong_version_rate"],
            latency_p95_ms=float(aggregates["latency_p95_ms"] or 0.0),
            metric_coverage={
                key: metric_coverage(rows, key)
                for key in (
                    f"file_recall@{metric_k}",
                    "module_accuracy",
                    "symbol_recall",
                )
            },
            rows=rows,
        )


def write_reports(report: EvalReport, output_dir: str | Path) -> tuple[Path, Path]:
    root = Path(output_dir)
    return write_report_files(
        report.as_dict(),
        _markdown(report),
        json_path=root / "eval_report.json",
        md_path=root / "eval_report.md",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default="configs/corpus_registry.json")
    parser.add_argument("--dataset", default="eval/datasets/sample_eval_suite.jsonl")
    parser.add_argument("--output-dir", default="reports/eval")
    parser.add_argument(
        "--version", default=None, help="Corpus version (defaults to the registry default corpus)"
    )
    parser.add_argument(
        "--max-source-spans",
        type=int,
        default=8,
        help="Source span budget per pack (matches the MCP tool default)",
    )
    parser.add_argument("--metric-k", type=int, default=DEFAULT_METRIC_K)
    parser.add_argument(
        "--require-pass",
        action="store_true",
        help="Exit non-zero unless every evaluated row passes all applicable metrics",
    )
    parser.add_argument(
        "--semantic-db",
        default=None,
        help="Optional semantic store path/DSN so eval packs include symbols/graph/guards",
    )
    args = parser.parse_args(argv)
    registry = CorpusRegistry.from_file(args.registry)
    adapter = CodeRAGAdapter(registry)
    semantic_store = SemanticStore(args.semantic_db) if args.semantic_db else None
    try:
        compiler = ContextCompiler(registry, adapter, semantic_store=semantic_store)
        report = EvalRunner(compiler).run(
            args.dataset,
            version=args.version,
            max_source_spans=args.max_source_spans,
            metric_k=args.metric_k,
        )
        write_reports(report, args.output_dir)
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    finally:
        if semantic_store is not None:
            semantic_store.close()
    return 1 if args.require_pass and not report.all_passed else 0


def _markdown(report: EvalReport) -> str:
    k = report.metric_k
    lines = [
        "# Codalith Eval Report",
        "",
        f"- metric_k: {k}",
        f"- count: {report.count}",
        f"- file_recall@{k}: {_metric(report.file_recall_at_k)}",
        f"- candidate_file_recall: {_metric(report.candidate_file_recall)}",
        f"- module_accuracy: {_metric(report.module_accuracy)}",
        f"- symbol_recall: {_metric(report.symbol_recall)}",
        f"- missing_source_citation_rate: {_metric(report.missing_source_citation_rate)}",
        f"- wrong_version_rate: {_metric(report.wrong_version_rate)}",
        f"- latency_p95_ms: {report.latency_p95_ms:.1f}",
        "",
        f"| id | file_recall@{k} | module_accuracy | symbol_recall | missing_citation | wrong_version | latency_ms |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in report.rows:
        lines.append(
            f"| {row['id']} | {_metric(row[f'file_recall@{k}'])} | "
            f"{_metric(row['module_accuracy'])} | {_metric(row['symbol_recall'])} | "
            f"{_metric(row['missing_source_citation_rate'])} | "
            f"{_metric(row['wrong_version_rate'])} | "
            f"{row['latency_ms']:.1f} |"
        )
    return "\n".join(lines) + "\n"


def _metric(value: object) -> str:
    return "N/A" if value is None else f"{float(str(value)):.3f}"


if __name__ == "__main__":
    raise SystemExit(main())
