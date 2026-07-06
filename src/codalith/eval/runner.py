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
from codalith.eval.metrics import file_recall_at_k, module_accuracy


@dataclass(frozen=True, slots=True)
class EvalReport:
    count: int
    file_recall_at_5: float
    module_accuracy: float
    latency_p95_ms: float
    rows: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "file_recall@5": self.file_recall_at_5,
            "module_accuracy": self.module_accuracy,
            "latency_p95_ms": self.latency_p95_ms,
            "rows": self.rows,
        }


class EvalRunner:
    def __init__(self, compiler: ContextCompiler) -> None:
        self.compiler = compiler

    def run(self, dataset_path: str | Path, *, version: str = "5.7.4") -> EvalReport:
        rows: list[dict[str, Any]] = []
        latencies: list[float] = []
        for item in _read_jsonl(dataset_path):
            started = time.perf_counter()
            pack = self.compiler.compile(
                query=str(item["query"]),
                version=str(item.get("version", version)),
                mode=str(item.get("mode", "explain")),
                max_source_spans=5,
            ).as_dict()
            elapsed_ms = (time.perf_counter() - started) * 1000
            latencies.append(elapsed_ms)
            file_recall = file_recall_at_k(pack, [str(path) for path in item.get("expected_files", [])], k=5)
            module_score = module_accuracy(pack, [str(module) for module in item.get("expected_modules", [])])
            rows.append(
                {
                    "id": item.get("id"),
                    "query": item["query"],
                    "file_recall@5": file_recall,
                    "module_accuracy": module_score,
                    "latency_ms": elapsed_ms,
                }
            )
        count = len(rows)
        return EvalReport(
            count=count,
            file_recall_at_5=sum(row["file_recall@5"] for row in rows) / count if count else 0.0,
            module_accuracy=sum(row["module_accuracy"] for row in rows) / count if count else 0.0,
            latency_p95_ms=_p95(latencies),
            rows=rows,
        )


def write_reports(report: EvalReport, output_dir: str | Path) -> tuple[Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "eval_report.json"
    md_path = root / "eval_report.md"
    json_path.write_text(json.dumps(report.as_dict(), indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(_markdown(report), encoding="utf-8")
    return json_path, md_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default="configs/corpus_registry.yaml")
    parser.add_argument("--dataset", default="eval/datasets/ue50.jsonl")
    parser.add_argument("--output-dir", default="reports/eval")
    parser.add_argument("--version", default="5.7.4")
    args = parser.parse_args(argv)
    registry = CorpusRegistry.from_file(args.registry)
    adapter = CodeRAGAdapter(registry)
    compiler = ContextCompiler(registry, adapter)
    report = EvalRunner(compiler).run(args.dataset, version=args.version)
    write_reports(report, args.output_dir)
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    return 0


def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.95)))
    return ordered[index]


def _markdown(report: EvalReport) -> str:
    lines = [
        "# Codalith Eval Report",
        "",
        f"- count: {report.count}",
        f"- file_recall@5: {report.file_recall_at_5:.3f}",
        f"- module_accuracy: {report.module_accuracy:.3f}",
        f"- latency_p95_ms: {report.latency_p95_ms:.1f}",
        "",
        "| id | file_recall@5 | module_accuracy | latency_ms |",
        "| --- | ---: | ---: | ---: |",
    ]
    for row in report.rows:
        lines.append(
            f"| {row['id']} | {row['file_recall@5']:.3f} | "
            f"{row['module_accuracy']:.3f} | {row['latency_ms']:.1f} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
