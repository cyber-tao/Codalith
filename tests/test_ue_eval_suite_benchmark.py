from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from codalith.coderag.adapter import CodeRAGAdapter
from codalith.compiler.context_compiler import ContextCompiler
from codalith.corpus.registry import CorpusRegistry
from codalith.corpus.source_policy import SourcePolicy
from codalith.corpus.source_reader import SourceReader
from codalith.corpus.uri_resolver import URIResolver
from codalith.eval.mcp_runner import run_mcp_eval
from codalith.eval.metrics import file_recall_at_k, module_accuracy
from codalith.gateway.audit import AuditLogger
from codalith.gateway.auth import AuthContext
from codalith.gateway.http_server import StreamableHTTPConfig, create_http_server
from codalith.gateway.tools import CodalithTools, ToolRuntime

EXPECTED_SUITE_SIZE = 80


def test_ue_eval_suite_passes_mcp_context_recall(
    ue_eval_tools: CodalithTools,
    eval_suite_rows: list[dict[str, Any]],
) -> None:
    failures: list[dict[str, object]] = []
    for row in eval_suite_rows:
        pack = ue_eval_tools.codalith_context(
            query=str(row["query"]),
            version=str(row.get("version", "5.7.4")),
            mode=str(row.get("mode", "explain")),
            max_source_spans=5,
            include_project_overlay=False,
        )
        expected_files = [str(path) for path in row.get("expected_files", [])]
        expected_modules = [str(module) for module in row.get("expected_modules", [])]
        file_score = file_recall_at_k(pack, expected_files, k=5)
        module_score = module_accuracy(pack, expected_modules)
        if file_score < 1.0 or module_score < 1.0:
            failures.append(
                {
                    "id": row.get("id"),
                    "file_recall@5": file_score,
                    "module_accuracy": module_score,
                    "expected_files": expected_files,
                    "expected_modules": expected_modules,
                    "source_paths": [
                        str(span.get("path", "")) for span in pack.get("source_spans", [])[:5]
                    ],
                    "modules": [str(module.get("name", "")) for module in pack.get("modules", [])],
                }
            )

    assert len(eval_suite_rows) == EXPECTED_SUITE_SIZE
    assert failures == []


def test_ue_eval_suite_passes_http_mcp_eval(
    ue_eval_tools: CodalithTools,
    eval_suite_dataset_path: Path,
) -> None:
    server = create_http_server(ue_eval_tools, StreamableHTTPConfig(port=0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address

    try:
        report = run_mcp_eval(
            endpoint=f"http://{host}:{port}/mcp",
            dataset_path=eval_suite_dataset_path,
            label="ue_eval_suite",
            version="5.7.4",
            max_source_spans=5,
            metric_k=5,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert report.count == EXPECTED_SUITE_SIZE
    assert report.file_recall_at_k == 1.0
    assert report.candidate_file_recall == 1.0
    assert report.module_accuracy == 1.0
    assert {row["failure_class"] for row in report.rows} == {"pass"}


def _ue_eval_tools(registry_path: Path, tmp_path: Path) -> CodalithTools:
    registry = CorpusRegistry.from_file(registry_path)
    resolver = URIResolver(registry)
    policy = SourcePolicy()
    source_reader = SourceReader(registry)
    adapter = CodeRAGAdapter(registry)
    compiler = ContextCompiler(registry, adapter, source_reader=source_reader)
    runtime = ToolRuntime(
        registry=registry,
        resolver=resolver,
        policy=policy,
        source_reader=source_reader,
        adapter=adapter,
        compiler=compiler,
        audit=AuditLogger(tmp_path / "ue-audit.jsonl"),
        identity=AuthContext(
            user_id="test-user",
            session_id="test-session",
            client="pytest",
            scopes=frozenset({"source:read", "index:status", "cards:read", "graph:read"}),
        ),
        semantic_store=None,
    )
    return CodalithTools(runtime)


@pytest.fixture()
def ue_eval_tools(ue_eval_registry_path: Path, tmp_path: Path) -> CodalithTools:
    return _ue_eval_tools(ue_eval_registry_path, tmp_path)
