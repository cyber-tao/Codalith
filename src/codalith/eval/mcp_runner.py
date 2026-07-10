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
    classify_failure,
    expected_strings,
    metric_coverage,
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
    file_recall_at_k: float | None
    candidate_file_recall: float | None
    module_accuracy: float | None
    symbol_recall: float | None
    missing_source_citation_rate: float | None
    wrong_version_rate: float | None
    latency_p95_ms: float
    metric_coverage: dict[str, int]
    retrieval_status: dict[str, Any]
    gate_failures: list[str]
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
            "metric_coverage": self.metric_coverage,
            "retrieval_status": self.retrieval_status,
            "gate_failures": self.gate_failures,
            "rows": self.rows,
        }

    @property
    def all_passed(self) -> bool:
        return not self.gate_failures and all(
            row.get("failure_class") == "pass" for row in self.rows
        )


def run_mcp_eval(
    *,
    endpoint: str,
    dataset_path: str | Path,
    label: str,
    corpus: str | None = None,
    max_source_spans: int = 20,
    metric_k: int = DEFAULT_METRIC_K,
    timeout_seconds: float = 120.0,
    expected_count: int | None = None,
    require_native: bool = False,
) -> MCPEvalReport:
    if not endpoint.startswith(("http://", "https://")):
        raise ValueError("MCP endpoint must start with http:// or https://")
    return anyio.run(
        _run_mcp_eval_async,
        endpoint,
        Path(dataset_path),
        label,
        corpus,
        max_source_spans,
        metric_k,
        timeout_seconds,
        expected_count,
        require_native,
    )


async def _run_mcp_eval_async(
    endpoint: str,
    dataset_path: Path,
    label: str,
    corpus: str | None,
    max_source_spans: int,
    metric_k: int,
    timeout_seconds: float,
    expected_count: int | None,
    require_native: bool,
) -> MCPEvalReport:
    headers = {"Origin": "http://127.0.0.1"}
    bearer_token = os.getenv("CODALITH_HTTP_BEARER_TOKEN")
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    rows: list[dict[str, Any]] = []
    latencies: list[float] = []
    retrieval_status: dict[str, Any] = {}
    async with httpx.AsyncClient(
        headers=headers,
        timeout=timeout_seconds,
        trust_env=False,
    ) as http_client:
        async with streamable_http_client(
            endpoint,
            http_client=http_client,
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                status_arguments = {"corpus": corpus} if corpus else {}
                retrieval_status = _structured_tool_result(
                    await session.call_tool(
                        "codalith_index_status",
                        status_arguments,
                    )
                )
                for item in read_jsonl(dataset_path):
                    item_corpus = str(
                        item.get("corpus")
                        or item.get("version")
                        or corpus
                        or ""
                    )
                    arguments: dict[str, Any] = {
                        "query": str(item["query"]),
                        "mode": str(item.get("mode", "explain")),
                        "max_source_spans": max_source_spans,
                    }
                    if item_corpus:
                        arguments["corpus"] = item_corpus
                    started = time.perf_counter()
                    result = await session.call_tool("codalith_context", arguments)
                    latencies.append((time.perf_counter() - started) * 1000)
                    pack = _structured_tool_result(result)
                    metrics = pack_metrics(
                        pack,
                        item,
                        k=metric_k,
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
                            "failure_class": classify_failure(
                                metrics,
                                metric_k=metric_k,
                                candidate_recall=candidate_recall,
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
    aggregates = aggregate_rows(rows, latencies, metric_k=metric_k)
    gate_failures = _retrieval_gate_failures(
        retrieval_status,
        count=len(rows),
        expected_count=expected_count,
        require_native=require_native,
    )
    return MCPEvalReport(
        label=label,
        endpoint=endpoint,
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
        retrieval_status=retrieval_status,
        gate_failures=gate_failures,
        rows=rows,
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
    parser.add_argument("--corpus", default=None, help="Base corpus id or version alias")
    parser.add_argument("--max-source-spans", type=int, default=20)
    parser.add_argument("--metric-k", type=int, default=DEFAULT_METRIC_K)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--expected-count", type=int)
    parser.add_argument(
        "--require-native",
        action="store_true",
        help="Require a validated native store with zero fallbacks",
    )
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
        corpus=args.corpus,
        max_source_spans=args.max_source_spans,
        metric_k=args.metric_k,
        timeout_seconds=args.timeout_seconds,
        expected_count=args.expected_count,
        require_native=args.require_native,
    )
    write_reports(report, args.output_dir)
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    if args.require_pass and not report.all_passed:
        failures = [row["id"] for row in report.rows if row.get("failure_class") != "pass"]
        print(f"require-pass failed for rows: {', '.join(str(item) for item in failures)}")
        return 1
    return 0


def _retrieval_gate_failures(
    status: dict[str, Any],
    *,
    count: int,
    expected_count: int | None,
    require_native: bool,
) -> list[str]:
    failures: list[str] = []
    if expected_count is not None and count != expected_count:
        failures.append(f"count {count} != {expected_count}")
    if not require_native:
        return failures
    base = status.get("base")
    if not isinstance(base, dict):
        failures.append("base retrieval status is missing")
        return failures
    if base.get("backend") != "native":
        failures.append(f"backend {base.get('backend')!r} is not native")
    if int(base.get("native_fallbacks", 0)) != 0:
        failures.append(f"native_fallbacks is {base.get('native_fallbacks')}")
    manifest = base.get("store_manifest")
    if not isinstance(manifest, dict) or manifest.get("validated") is not True:
        failures.append("store manifest is not validated")
    return failures


def _markdown(report: MCPEvalReport) -> str:
    lines = [
        f"# Codalith MCP Eval Report: {report.label}",
        "",
        f"- endpoint: {report.endpoint}",
        f"- count: {report.count}",
        f"- file_recall@k: {_metric(report.file_recall_at_k)}",
        f"- candidate_file_recall: {_metric(report.candidate_file_recall)}",
        f"- module_accuracy: {_metric(report.module_accuracy)}",
        f"- symbol_recall: {_metric(report.symbol_recall)}",
        f"- missing_source_citation_rate: {_metric(report.missing_source_citation_rate)}",
        f"- wrong_version_rate: {_metric(report.wrong_version_rate)}",
        f"- latency_p95_ms: {report.latency_p95_ms:.1f}",
        f"- gate_failures: {report.gate_failures}",
        "",
        "| id | file_recall@k | candidate_recall | module_accuracy | latency_ms | failure_class |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in report.rows:
        lines.append(
            f"| {row['id']} | {_metric(row[f'file_recall@{report.metric_k}'])} | "
            f"{_metric(row[f'file_recall@{report.max_source_spans}'])} | "
            f"{_metric(row['module_accuracy'])} | "
            f"{row['latency_ms']:.1f} | {row['failure_class']} |"
        )
    return "\n".join(lines) + "\n"


def _metric(value: object) -> str:
    return "N/A" if value is None else f"{float(str(value)):.3f}"


if __name__ == "__main__":
    raise SystemExit(main())
