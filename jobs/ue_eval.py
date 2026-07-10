"""Cross-platform real UE corpus MCP acceptance runner."""

from __future__ import annotations

import argparse
import json
import os
import threading
from pathlib import Path

from codalith.eval.mcp_runner import run_mcp_eval, write_reports
from codalith.gateway.http_server import StreamableHTTPConfig, create_http_server
from codalith.gateway.tools import CodalithTools, create_runtime
from jobs.coderag_acceptance import (
    configure_coderag_runtime_env,
    ensure_coderag_installed,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-root",
        default=os.getenv("CODALITH_UE_SOURCE_ROOT"),
    )
    parser.add_argument(
        "--indexed-root",
        default=os.getenv("CODALITH_UE_INDEXED_ROOT"),
    )
    parser.add_argument(
        "--store-dir",
        default=os.getenv("CODALITH_UE_CODERAG_STORE_DIR"),
    )
    parser.add_argument(
        "--registry",
        default=os.getenv(
            "CODALITH_UE_CORPUS_REGISTRY",
            "configs/ue_5_7_4_registry.json",
        ),
    )
    parser.add_argument(
        "--dataset",
        default="eval/datasets/ue_eval_suite.jsonl",
    )
    parser.add_argument(
        "--output-dir",
        default="reports/mcp-eval/ue_eval_suite",
    )
    parser.add_argument("--version", default="5.7.4")
    parser.add_argument("--expected-count", type=int, default=80)
    parser.add_argument("--max-source-spans", type=int, default=20)
    parser.add_argument("--metric-k", type=int, default=5)
    args = parser.parse_args(argv)
    source_root = _required_directory(parser, "--source-root", args.source_root)
    indexed_root = _required_directory(
        parser,
        "--indexed-root",
        args.indexed_root or source_root,
    )
    store_dir = _required_directory(parser, "--store-dir", args.store_dir)
    os.environ.update(
        {
            "CODALITH_UE_SOURCE_ROOT": str(source_root),
            "CODALITH_UE_INDEXED_ROOT": str(indexed_root),
            "CODALITH_UE_CODERAG_STORE_DIR": str(store_dir),
            "CODALITH_CORPUS_REGISTRY": str(args.registry),
            "CODALITH_USE_NATIVE_CODERAG": "1",
            "CODALITH_NATIVE_CODERAG_STRICT": "1",
        }
    )
    configure_coderag_runtime_env("openai")
    ensure_coderag_installed("openai")
    runtime = create_runtime()
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
            dataset_path=args.dataset,
            label="ue_eval_suite",
            version=args.version,
            max_source_spans=args.max_source_spans,
            metric_k=args.metric_k,
            expected_count=args.expected_count,
            require_native=True,
        )
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()
        if runtime.semantic_store is not None:
            runtime.semantic_store.close()
    write_reports(report, args.output_dir)
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    return 0 if report.all_passed else 1


def _required_directory(
    parser: argparse.ArgumentParser,
    option: str,
    value: str | Path | None,
) -> Path:
    if not value:
        parser.error(f"{option} is required")
    path = Path(value)
    if not path.is_dir():
        parser.error(f"{option} does not exist or is not a directory: {path}")
    return path


if __name__ == "__main__":
    raise SystemExit(main())
