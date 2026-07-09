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
    evaluate_dataset,
    write_report_files,
)
from codalith.semantic.store import SemanticStore


@dataclass(frozen=True, slots=True)
class EvalReport:
    metric_k: int
    count: int
    file_recall_at_k: float
    module_accuracy: float
    symbol_recall: float
    missing_source_citation_rate: float
    wrong_version_rate: float
    latency_p95_ms: float
    rows: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric_k": self.metric_k,
            "count": self.count,
            "file_recall@k": self.file_recall_at_k,
            "module_accuracy": self.module_accuracy,
            "symbol_recall": self.symbol_recall,
            "missing_source_citation_rate": self.missing_source_citation_rate,
            "wrong_version_rate": self.wrong_version_rate,
            "latency_p95_ms": self.latency_p95_ms,
            "rows": self.rows,
        }


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
                version=item_version,
                mode=str(item.get("mode", "explain")),
                max_source_spans=max_source_spans,
            ).as_dict()

        rows, latencies = evaluate_dataset(
            dataset_path,
            run_pack,
            version=version,
            metric_k=metric_k,
        )
        return EvalReport(
            metric_k=metric_k,
            count=len(rows),
            rows=rows,
            **aggregate_rows(rows, latencies, metric_k=metric_k),
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
    return 0


def _markdown(report: EvalReport) -> str:
    k = report.metric_k
    lines = [
        "# Codalith Eval Report",
        "",
        f"- metric_k: {k}",
        f"- count: {report.count}",
        f"- file_recall@{k}: {report.file_recall_at_k:.3f}",
        f"- module_accuracy: {report.module_accuracy:.3f}",
        f"- symbol_recall: {report.symbol_recall:.3f}",
        f"- missing_source_citation_rate: {report.missing_source_citation_rate:.3f}",
        f"- wrong_version_rate: {report.wrong_version_rate:.3f}",
        f"- latency_p95_ms: {report.latency_p95_ms:.1f}",
        "",
        f"| id | file_recall@{k} | module_accuracy | symbol_recall | missing_citation | wrong_version | latency_ms |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in report.rows:
        lines.append(
            f"| {row['id']} | {row[f'file_recall@{k}']:.3f} | "
            f"{row['module_accuracy']:.3f} | {row['symbol_recall']:.3f} | "
            f"{row['missing_source_citation_rate']:.3f} | {row['wrong_version_rate']:.3f} | "
            f"{row['latency_ms']:.1f} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
