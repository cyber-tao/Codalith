from __future__ import annotations

import json
import threading

from codalith.eval.mcp_runner import run_mcp_eval
from codalith.gateway.http_server import StreamableHTTPConfig, create_http_server


def test_mcp_eval_runner_calls_streamable_http(tools, tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "id": "case-1",
                "query": "CachedValue ttl expiration",
                "version": "sample",
                "expected_files": ["cache.py"],
                "expected_modules": ["core"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    server = create_http_server(tools, StreamableHTTPConfig(port=0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address

    try:
        report = run_mcp_eval(
            endpoint=f"http://{host}:{port}/mcp",
            dataset_path=dataset,
            label="test",
            max_source_spans=8,
            metric_k=5,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert report.count == 1
    assert report.file_recall_at_k == 1.0
    assert report.rows[0]["failure_class"] == "pass"
