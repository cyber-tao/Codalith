"""Run Codalith eval through the Streamable HTTP MCP endpoint."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from codalith.eval.common import (
    average,
    expected_strings,
    p95,
    pack_metrics,
    read_jsonl,
    write_report_files,
)
from codalith.eval.metrics import file_recall_at_k

PROTOCOL_VERSION = "2025-11-25"


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


class MCPClient:
    def __init__(
        self,
        endpoint: str,
        *,
        timeout_seconds: float = 120.0,
        bearer_token: str | None = None,
    ) -> None:
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("MCP endpoint must start with http:// or https://")
        if parsed.scheme == "https":
            raise ValueError("HTTPS MCP eval is not supported by the stdlib client")
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or 80
        self.path = parsed.path or "/mcp"
        self.timeout_seconds = timeout_seconds
        self.bearer_token = bearer_token
        self.session_id: str | None = None
        self._next_id = 1

    def initialize(self) -> None:
        response, payload = self.post(
            {"method": "initialize", "params": {"protocolVersion": PROTOCOL_VERSION}},
            require_session=False,
        )
        session_id = response.getheader("MCP-Session-Id")
        if not session_id:
            raise RuntimeError(f"MCP initialize did not return a session: {payload}")
        self.session_id = session_id

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.session_id is None:
            self.initialize()
        _, payload = self.post(
            {
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        if "error" in payload:
            raise RuntimeError(json.dumps(payload["error"], ensure_ascii=False))
        result = payload.get("result", {})
        if not isinstance(result, dict):
            raise RuntimeError(f"MCP result must be an object: {payload}")
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            raise RuntimeError(f"MCP result missing structuredContent: {payload}")
        return structured

    def post(
        self,
        payload: dict[str, Any],
        *,
        require_session: bool = True,
    ) -> tuple[http.client.HTTPResponse, dict[str, Any]]:
        request = {"jsonrpc": "2.0", "id": self._next_id, **payload}
        self._next_id += 1
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Origin": "http://127.0.0.1",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if require_session and self.session_id:
            headers["MCP-Session-Id"] = self.session_id
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        connection = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout_seconds)
        connection.request("POST", self.path, body=json.dumps(request), headers=headers)
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        connection.close()
        parsed = json.loads(body) if body else {}
        if response.status >= 400:
            raise RuntimeError(f"MCP HTTP {response.status}: {parsed}")
        return response, parsed


def run_mcp_eval(
    *,
    endpoint: str,
    dataset_path: str | Path,
    label: str,
    version: str | None = None,
    max_source_spans: int = 20,
    metric_k: int = 5,
    timeout_seconds: float = 120.0,
) -> MCPEvalReport:
    client = MCPClient(
        endpoint,
        timeout_seconds=timeout_seconds,
        bearer_token=os.getenv("CODALITH_HTTP_BEARER_TOKEN") or None,
    )
    client.initialize()
    rows: list[dict[str, Any]] = []
    latencies: list[float] = []
    for item in read_jsonl(dataset_path):
        expected_files = expected_strings(item, "expected_files")
        expected_version = str(item["version"]) if item.get("version") else version
        arguments: dict[str, Any] = {
            "query": str(item["query"]),
            "mode": str(item.get("mode", "explain")),
            "max_source_spans": max_source_spans,
        }
        if expected_version:
            arguments["version"] = expected_version
        started = time.perf_counter()
        pack = client.call_tool("codalith_context", arguments)
        elapsed_ms = (time.perf_counter() - started) * 1000
        latencies.append(elapsed_ms)
        metrics = pack_metrics(pack, item, k=metric_k, default_version=version)
        candidate_recall = file_recall_at_k(pack, expected_files, k=max_source_spans)
        source_spans = pack.get("source_spans", [])
        rows.append(
            {
                "id": item.get("id"),
                "query": item["query"],
                **metrics,
                f"file_recall@{max_source_spans}": candidate_recall,
                "latency_ms": elapsed_ms,
                "failure_class": _failure_class(
                    metrics[f"file_recall@{metric_k}"],
                    candidate_recall,
                    metrics["module_accuracy"],
                ),
                "expected_files": expected_files,
                "expected_modules": expected_strings(item, "expected_modules"),
                "source_paths": [str(span.get("path", "")) for span in source_spans],
                "modules": [str(module.get("name", "")) for module in pack.get("modules", [])],
            }
        )
    count = len(rows)
    return MCPEvalReport(
        label=label,
        endpoint=endpoint,
        metric_k=metric_k,
        max_source_spans=max_source_spans,
        count=count,
        file_recall_at_k=average(rows, f"file_recall@{metric_k}"),
        candidate_file_recall=average(rows, f"file_recall@{max_source_spans}"),
        module_accuracy=average(rows, "module_accuracy"),
        symbol_recall=average(rows, "symbol_recall"),
        missing_source_citation_rate=average(rows, "missing_source_citation_rate"),
        wrong_version_rate=average(rows, "wrong_version_rate"),
        latency_p95_ms=p95(latencies),
        rows=rows,
    )


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
    parser.add_argument("--metric-k", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
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
