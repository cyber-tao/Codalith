from __future__ import annotations

import importlib.util
import os
import threading
from pathlib import Path
from typing import Any

import pytest

from codalith.eval.mcp_runner import run_mcp_eval
from codalith.gateway.http_server import StreamableHTTPConfig, create_http_server
from codalith.gateway.tools import CodalithTools, create_runtime

EXPECTED_SUITE_SIZE = 80


def test_ue_eval_suite_has_strict_source_expectations(
    eval_suite_rows: list[dict[str, Any]],
) -> None:
    assert len(eval_suite_rows) == EXPECTED_SUITE_SIZE
    assert len({str(row["id"]) for row in eval_suite_rows}) == EXPECTED_SUITE_SIZE
    symbol_rows = 0
    for row in eval_suite_rows:
        expected_files = [str(path) for path in row.get("expected_files", [])]
        assert row.get("query")
        assert row.get("version") == "5.7.4"
        assert expected_files
        assert all("/" in path and not Path(path).is_absolute() for path in expected_files)
        verified_paths = {
            str(source).rsplit(":", 1)[0]
            for source in row.get("verified_sources", [])
        }
        assert verified_paths <= set(expected_files)
        if row.get("expected_symbols"):
            symbol_rows += 1
    assert symbol_rows > 0


@pytest.mark.ue_acceptance
def test_ue_eval_suite_passes_real_native_mcp(
    eval_suite_dataset_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    if os.getenv("CODALITH_RUN_UE_ACCEPTANCE") != "1":
        pytest.skip("set CODALITH_RUN_UE_ACCEPTANCE=1 for real UE acceptance")
    if importlib.util.find_spec("coderag") is None:
        pytest.skip("native CodeRAG dependencies are not installed")
    required_paths = {
        "CODALITH_UE_SOURCE_ROOT": os.getenv("CODALITH_UE_SOURCE_ROOT"),
        "CODALITH_UE_INDEXED_ROOT": os.getenv("CODALITH_UE_INDEXED_ROOT"),
        "CODALITH_UE_CODERAG_STORE_DIR": os.getenv(
            "CODALITH_UE_CODERAG_STORE_DIR"
        ),
    }
    missing = [
        name
        for name, value in required_paths.items()
        if not value or not Path(value).exists()
    ]
    if missing:
        pytest.skip(f"real UE paths are unavailable: {', '.join(missing)}")
    monkeypatch.setenv(
        "CODALITH_CORPUS_REGISTRY",
        "configs/corpora/ue-5.7.4/registry.json",
    )
    monkeypatch.setenv("CODALITH_USE_NATIVE_CODERAG", "1")
    monkeypatch.setenv("CODALITH_NATIVE_CODERAG_STRICT", "1")
    monkeypatch.delenv("CODALITH_SEMANTIC_DSN", raising=False)
    monkeypatch.delenv("CODALITH_SEMANTIC_DB", raising=False)
    runtime = create_runtime(audit_log=str(tmp_path / "ue-audit.jsonl"))
    server = create_http_server(
        CodalithTools(runtime),
        StreamableHTTPConfig(port=0),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address

    try:
        report = run_mcp_eval(
            endpoint=f"http://{host}:{port}/mcp",
            dataset_path=eval_suite_dataset_path,
            label="ue_eval_suite",
            corpus="5.7.4",
            max_source_spans=20,
            metric_k=5,
            expected_count=EXPECTED_SUITE_SIZE,
            require_native=True,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        if runtime.semantic_store is not None:
            runtime.semantic_store.close()

    assert report.all_passed, report.gate_failures
    assert report.file_recall_at_k == 1.0
    assert report.candidate_file_recall == 1.0
    assert report.module_accuracy == 1.0
    assert report.missing_source_citation_rate == 0.0
    assert report.wrong_version_rate == 0.0
