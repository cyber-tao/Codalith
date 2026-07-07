"""Eval runner v0 for Codalith."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codalith.coderag.adapter import CodeRAGAdapter
from codalith.compiler.context_compiler import ContextCompiler
from codalith.corpus.registry import CorpusRegistry
from codalith.eval.common import average, p95, pack_metrics, read_jsonl, write_report_files

METRIC_K = 5


@dataclass(frozen=True, slots=True)
class EvalReport:
    count: int
    file_recall_at_5: float
    module_accuracy: float
    symbol_recall: float
    missing_source_citation_rate: float
    wrong_version_rate: float
    latency_p95_ms: float
    rows: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "file_recall@5": self.file_recall_at_5,
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
    ) -> EvalReport:
        rows: list[dict[str, Any]] = []
        latencies: list[float] = []
        for item in read_jsonl(dataset_path):
            item_version = str(item["version"]) if item.get("version") else version
            started = time.perf_counter()
            pack = self.compiler.compile(
                query=str(item["query"]),
                version=item_version,
                mode=str(item.get("mode", "explain")),
                max_source_spans=max_source_spans,
            ).as_dict()
            elapsed_ms = (time.perf_counter() - started) * 1000
            latencies.append(elapsed_ms)
            metrics = pack_metrics(pack, item, k=METRIC_K, default_version=version)
            rows.append(
                {
                    "id": item.get("id"),
                    "query": item["query"],
                    **metrics,
                    "latency_ms": elapsed_ms,
                }
            )
        return EvalReport(
            count=len(rows),
            file_recall_at_5=average(rows, f"file_recall@{METRIC_K}"),
            module_accuracy=average(rows, "module_accuracy"),
            symbol_recall=average(rows, "symbol_recall"),
            missing_source_citation_rate=average(rows, "missing_source_citation_rate"),
            wrong_version_rate=average(rows, "wrong_version_rate"),
            latency_p95_ms=p95(latencies),
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
    parser.add_argument("--dataset", default="eval/datasets/ue_eval_suite.jsonl")
    parser.add_argument("--output-dir", default="reports/eval")
    parser.add_argument(
        "--version", default=None, help="Engine version (defaults to the registry default engine)"
    )
    parser.add_argument(
        "--max-source-spans",
        type=int,
        default=8,
        help="Source span budget per pack (matches the MCP tool default)",
    )
    args = parser.parse_args(argv)
    registry = CorpusRegistry.from_file(args.registry)
    adapter = CodeRAGAdapter(registry)
    compiler = ContextCompiler(registry, adapter)
    report = EvalRunner(compiler).run(
        args.dataset,
        version=args.version,
        max_source_spans=args.max_source_spans,
    )
    write_reports(report, args.output_dir)
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    return 0


def _markdown(report: EvalReport) -> str:
    lines = [
        "# Codalith Eval Report",
        "",
        f"- count: {report.count}",
        f"- file_recall@5: {report.file_recall_at_5:.3f}",
        f"- module_accuracy: {report.module_accuracy:.3f}",
        f"- symbol_recall: {report.symbol_recall:.3f}",
        f"- missing_source_citation_rate: {report.missing_source_citation_rate:.3f}",
        f"- wrong_version_rate: {report.wrong_version_rate:.3f}",
        f"- latency_p95_ms: {report.latency_p95_ms:.1f}",
        "",
        "| id | file_recall@5 | module_accuracy | symbol_recall | missing_citation | wrong_version | latency_ms |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in report.rows:
        lines.append(
            f"| {row['id']} | {row['file_recall@5']:.3f} | "
            f"{row['module_accuracy']:.3f} | {row['symbol_recall']:.3f} | "
            f"{row['missing_source_citation_rate']:.3f} | {row['wrong_version_rate']:.3f} | "
            f"{row['latency_ms']:.1f} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
