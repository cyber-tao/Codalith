"""Run Codalith eval through the Streamable HTTP MCP endpoint."""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent

from codalith.eval.common import (
    DEFAULT_METRIC_K,
    aggregate_rows,
    average,
    expected_strings,
    pack_metrics,
    read_jsonl,
    write_report_files,
)
from codalith.eval.metrics import file_recall_at_k


@dataclass(frozen=True, slots=True)
class MCPEvalReport:
    label: str
    endpoint: str
    metric_k: int
    max_source_spans: int
    count: int
    file_recall_at_k: float
    candidate_file_recall: float
    module_accuracy: float
    symbol_recall: float
    missing_source_citation_rate: float
    wrong_version_rate: float
    latency_p95_ms: float
    rows: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "endpoint": self.endpoint,
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
            "rows": self.rows,
        }

    @property
    def all_passed(self) -> bool:
        return all(row.get("failure_class") == "pass" for row in self.rows)


def run_mcp_eval(
    *,
    endpoint: str,
    dataset_path: str | Path,
    label: str,
    version: str | None = None,
    max_source_spans: int = 20,
    metric_k: int = DEFAULT_METRIC_K,
    timeout_seconds: float = 120.0,
) -> MCPEvalReport:
    if not endpoint.startswith(("http://", "https://")):
        raise ValueError("MCP endpoint must start with http:// or https://")
    return anyio.run(
        _run_mcp_eval_async,
        endpoint,
        Path(dataset_path),
        label,
        version,
        max_source_spans,
        metric_k,
        timeout_seconds,
    )


async def _run_mcp_eval_async(
    endpoint: str,
    dataset_path: Path,
    label: str,
    version: str | None,
    max_source_spans: int,
    metric_k: int,
    timeout_seconds: float,
) -> MCPEvalReport:
    headers = {"Origin": "http://127.0.0.1"}
    bearer_token = os.getenv("CODALITH_HTTP_BEARER_TOKEN")
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    rows: list[dict[str, Any]] = []
    latencies: list[float] = []
    async with httpx.AsyncClient(headers=headers, timeout=timeout_seconds) as http_client:
        async with streamable_http_client(
            endpoint,
            http_client=http_client,
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                for item in read_jsonl(dataset_path):
                    item_version = str(item["version"]) if item.get("version") else version
                    arguments: dict[str, Any] = {
                        "query": str(item["query"]),
                        "mode": str(item.get("mode", "explain")),
                        "max_source_spans": max_source_spans,
                    }
                    if item_version:
                        arguments["corpus"] = item_version
                    started = time.perf_counter()
                    result = await session.call_tool("codalith_context", arguments)
                    latencies.append((time.perf_counter() - started) * 1000)
                    pack = _structured_tool_result(result)
                    metrics = pack_metrics(
                        pack,
                        item,
                        k=metric_k,
                        default_version=version,
                    )
                    expected_files = expected_strings(item, "expected_files")
                    candidate_recall = file_recall_at_k(
                        pack,
                        expected_files,
                        k=max_source_spans,
                    )
                    source_spans = pack.get("source_spans", [])
                    rows.append(
                        {
                            "id": item.get("id"),
                            "query": item["query"],
                            **metrics,
                            "latency_ms": latencies[-1],
                            f"file_recall@{max_source_spans}": candidate_recall,
                            "failure_class": _failure_class(
                                metrics[f"file_recall@{metric_k}"],
                                candidate_recall,
                                metrics["module_accuracy"],
                            ),
                            "expected_files": expected_files,
                            "expected_modules": expected_strings(
                                item,
                                "expected_modules",
                            ),
                            "source_paths": [
                                str(span.get("path", "")) for span in source_spans
                            ],
                            "modules": [
                                str(module.get("name", ""))
                                for module in pack.get("modules", [])
                            ],
                        }
                    )
    return MCPEvalReport(
        label=label,
        endpoint=endpoint,
        metric_k=metric_k,
        max_source_spans=max_source_spans,
        count=len(rows),
        candidate_file_recall=average(rows, f"file_recall@{max_source_spans}"),
        rows=rows,
        **aggregate_rows(rows, latencies, metric_k=metric_k),
    )


def _structured_tool_result(result: CallToolResult) -> dict[str, Any]:
    if result.isError:
        message = "\n".join(
            block.text for block in result.content if isinstance(block, TextContent)
        )
        raise RuntimeError(message or "MCP tool call failed")
    if not isinstance(result.structuredContent, dict):
        raise RuntimeError(f"MCP result missing structuredContent: {result}")
    return result.structuredContent


def write_reports(report: MCPEvalReport, output_dir: str | Path) -> tuple[Path, Path]:
    root = Path(output_dir)
    return write_report_files(
        report.as_dict(),
        _markdown(report),
        json_path=root / f"{report.label}_mcp_eval.json",
        md_path=root / f"{report.label}_mcp_eval.md",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://127.0.0.1:8765/mcp")
    parser.add_argument("--dataset", default="eval/datasets/sample_eval_suite.jsonl")
    parser.add_argument("--output-dir", default="reports/mcp-eval")
    parser.add_argument("--label", default="baseline")
    parser.add_argument(
        "--version", default=None, help="Corpus version (defaults to the endpoint default corpus)"
    )
    parser.add_argument("--max-source-spans", type=int, default=20)
    parser.add_argument("--metric-k", type=int, default=DEFAULT_METRIC_K)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument(
        "--require-pass",
        action="store_true",
        help="Exit non-zero unless every row's failure_class is 'pass'",
    )
    args = parser.parse_args(argv)
    report = run_mcp_eval(
        endpoint=args.endpoint,
        dataset_path=args.dataset,
        label=args.label,
        version=args.version,
        max_source_spans=args.max_source_spans,
        metric_k=args.metric_k,
        timeout_seconds=args.timeout_seconds,
    )
    write_reports(report, args.output_dir)
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    if args.require_pass and not report.all_passed:
        failures = [row["id"] for row in report.rows if row.get("failure_class") != "pass"]
        print(f"require-pass failed for rows: {', '.join(str(item) for item in failures)}")
        return 1
    return 0


def _failure_class(file_recall: float, candidate_recall: float, module_score: float) -> str:
    if file_recall < 1.0:
        if candidate_recall > file_recall:
            return "expected_file_below_top_k"
        return "expected_file_not_retrieved"
    if module_score < 1.0:
        return "module_mismatch"
    return "pass"


def _markdown(report: MCPEvalReport) -> str:
    lines = [
        f"# Codalith MCP Eval Report: {report.label}",
        "",
        f"- endpoint: {report.endpoint}",
        f"- count: {report.count}",
        f"- file_recall@k: {report.file_recall_at_k:.3f}",
        f"- candidate_file_recall: {report.candidate_file_recall:.3f}",
        f"- module_accuracy: {report.module_accuracy:.3f}",
        f"- symbol_recall: {report.symbol_recall:.3f}",
        f"- missing_source_citation_rate: {report.missing_source_citation_rate:.3f}",
        f"- wrong_version_rate: {report.wrong_version_rate:.3f}",
        f"- latency_p95_ms: {report.latency_p95_ms:.1f}",
        "",
        "| id | file_recall@k | candidate_recall | module_accuracy | latency_ms | failure_class |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in report.rows:
        lines.append(
            f"| {row['id']} | {row[f'file_recall@{report.metric_k}']:.3f} | "
            f"{row[f'file_recall@{report.max_source_spans}']:.3f} | {row['module_accuracy']:.3f} | "
            f"{row['latency_ms']:.1f} | {row['failure_class']} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
