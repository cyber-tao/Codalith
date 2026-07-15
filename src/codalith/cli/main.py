"""Unified Codalith command-line interface."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import anyio
import uvicorn

from codalith import __version__
from codalith.benchmarks.runner import (
    acceptance_failures,
    run_mcp_benchmark,
    write_report,
)
from codalith.corpus.registry import CorpusRegistry
from codalith.corpus.source_policy import SourcePolicy
from codalith.corpus.store_manifest import GenerationRepository
from codalith.errors import CodalithError
from codalith.indexing.coderag.backend import store_fingerprint
from codalith.indexing.structure.builder import StructureBuilder
from codalith.mcp.http import DEFAULT_MAX_REQUEST_BYTES, HTTPConfig, create_http_app
from codalith.mcp.stdio import serve_stdio
from codalith.query.service import QueryService

_DEFAULT_REGISTRY = os.getenv("CODALITH_REGISTRY") or "configs/registry.toml"
_DEFAULT_POLICY = os.getenv("CODALITH_SOURCE_POLICY") or "configs/source-policy.toml"


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.version:
        print(__version__)
        return 0
    try:
        return _dispatch(args, parser)
    except (CodalithError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _dispatch(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.command is None:
        parser.print_help()
        return 2
    if args.command == "benchmark":
        return _benchmark(args)
    if args.command == "client-config":
        return _client_config(args)
    registry = CorpusRegistry.from_file(args.registry)
    policy = SourcePolicy.from_file(args.policy)
    if args.command == "serve":
        return _serve(args, registry, policy)
    if args.command == "index":
        return _index(args, registry, policy)
    if args.command == "doctor":
        return _doctor(args, registry, policy)
    raise ValueError(f"Unknown command: {args.command}")


def _serve(
    args: argparse.Namespace,
    registry: CorpusRegistry,
    policy: SourcePolicy,
) -> int:
    service = QueryService(registry, policy)
    if args.transport == "stdio":
        try:
            anyio.run(serve_stdio, service)
        finally:
            service.close()
        return 0
    config = HTTPConfig(
        host=args.host,
        port=args.port,
        endpoint=args.endpoint,
        allowed_origins=tuple(args.allowed_origin),
        allowed_hosts=tuple(args.allowed_host),
        max_request_bytes=args.max_request_bytes,
        access_log=args.access_log,
    )
    uvicorn.run(
        create_http_app(service, config),
        host=config.host,
        port=config.port,
        log_level="info" if config.access_log else "warning",
        access_log=config.access_log,
    )
    return 0


def _index(
    args: argparse.Namespace,
    registry: CorpusRegistry,
    policy: SourcePolicy,
) -> int:
    if args.index_command == "build":
        corpus = registry.get_corpus(args.corpus)
        report = StructureBuilder(policy).build(
            corpus,
            semantic_mode=args.semantic,
            allow_external_rebuild=args.allow_external_rebuild,
            progress=lambda message: print(message, file=sys.stderr, flush=True),
        )
        _print_json(report.to_dict())
        return 0
    if args.index_command == "status":
        service = QueryService(registry, policy)
        try:
            _print_json(service.status(target=args.target).model_dump(mode="json"))
        finally:
            service.close()
        return 0
    raise ValueError("index requires build or status")


def _doctor(
    args: argparse.Namespace,
    registry: CorpusRegistry,
    policy: SourcePolicy,
) -> int:
    service = QueryService(registry, policy)
    failures: list[str] = []
    try:
        status = service.status(target=args.target)
        payload = status.model_dump(mode="json")
        if args.deep:
            target = registry.resolve(args.target)
            checks: list[dict[str, object]] = []
            repository = GenerationRepository()
            for corpus in target.corpora:
                try:
                    generation = repository.active(corpus, verify_artifacts=True)
                    semantic_match = True
                    if generation.manifest.semantic_available:
                        semantic_match = (
                            store_fingerprint(generation.coderag_path)
                            == generation.manifest.coderag_store_fingerprint
                        )
                        if not semantic_match:
                            failures.append(
                                f"CodeRAG fingerprint mismatch for {corpus.corpus_id}"
                            )
                    checks.append(
                        {
                            "corpus_id": corpus.corpus_id,
                            "artifacts_valid": True,
                            "semantic_fingerprint_valid": semantic_match,
                        }
                    )
                except CodalithError as exc:
                    failures.append(str(exc))
                    checks.append(
                        {
                            "corpus_id": corpus.corpus_id,
                            "artifacts_valid": False,
                            "error": str(exc),
                        }
                    )
            payload["deep_checks"] = checks
        payload["failures"] = failures
        _print_json(payload)
        return 1 if failures or not status.ready else 0
    finally:
        service.close()


def _benchmark(args: argparse.Namespace) -> int:
    report = run_mcp_benchmark(
        endpoint=args.endpoint_url,
        dataset_path=args.dataset,
        label=args.label,
        require_ready=not args.allow_degraded,
    )
    if args.output:
        write_report(report, args.output)
    _print_json(report.model_dump(mode="json"))
    failures = acceptance_failures(
        report,
        min_file_recall=args.min_file_recall,
        min_symbol_recall=args.min_symbol_recall,
        min_mrr=args.min_mrr,
        min_ndcg=args.min_ndcg,
        max_p95_ms=args.max_p95_ms,
    )
    if failures:
        print("Acceptance failures:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    return 0


def _client_config(args: argparse.Namespace) -> int:
    if args.transport == "http":
        if args.client == "codex":
            print("[mcp_servers.codalith]")
            print(f'url = "{args.url}"')
        else:
            print(
                json.dumps(
                    {"mcpServers": {"codalith": {"type": "http", "url": args.url}}},
                    indent=2,
                )
            )
        return 0
    command = [
        "uv",
        "run",
        "--directory",
        str(Path(args.project_dir).resolve()),
        "--frozen",
        "codalith",
        "--registry",
        str(Path(args.registry).resolve()),
        "--policy",
        str(Path(args.policy).resolve()),
        "serve",
        "--transport",
        "stdio",
    ]
    if args.client == "codex":
        print("[mcp_servers.codalith]")
        print(f"command = {json.dumps(command[0])}")
        print(f"args = {json.dumps(command[1:])}")
    else:
        print(
            json.dumps(
                {
                    "mcpServers": {
                        "codalith": {"command": command[0], "args": command[1:]}
                    }
                },
                indent=2,
            )
        )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codalith")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--registry", default=_DEFAULT_REGISTRY)
    parser.add_argument("--policy", default=_DEFAULT_POLICY)
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="Run the MCP server")
    serve.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    serve.add_argument("--host", default=os.getenv("CODALITH_HTTP_HOST", "127.0.0.1"))
    serve.add_argument(
        "--port",
        type=int,
        default=os.getenv("CODALITH_HTTP_PORT") or "8765",
    )
    serve.add_argument("--endpoint", default=os.getenv("CODALITH_HTTP_ENDPOINT", "/mcp"))
    serve.add_argument(
        "--allowed-origin",
        action="append",
        default=_csv_env("CODALITH_HTTP_ALLOWED_ORIGINS"),
    )
    serve.add_argument(
        "--allowed-host",
        action="append",
        default=_csv_env("CODALITH_HTTP_ALLOWED_HOSTS"),
    )
    serve.add_argument(
        "--max-request-bytes",
        type=int,
        default=os.getenv("CODALITH_HTTP_MAX_REQUEST_BYTES")
        or str(DEFAULT_MAX_REQUEST_BYTES),
    )
    serve.add_argument("--access-log", action="store_true")

    index = subparsers.add_parser("index", help="Build or inspect index generations")
    index_commands = index.add_subparsers(dest="index_command", required=True)
    build = index_commands.add_parser("build")
    build.add_argument("--corpus", required=True)
    build.add_argument("--semantic", choices=("none", "build", "adopt"), default="none")
    build.add_argument("--allow-external-rebuild", action="store_true")
    status = index_commands.add_parser("status")
    status.add_argument("--target")

    doctor = subparsers.add_parser("doctor", help="Validate index provenance")
    doctor.add_argument("--target")
    doctor.add_argument("--deep", action="store_true")

    benchmark = subparsers.add_parser("benchmark", help="Run a real MCP benchmark")
    benchmark.add_argument("--endpoint-url", default="http://127.0.0.1:8765/mcp")
    benchmark.add_argument("--dataset", required=True)
    benchmark.add_argument("--label", default="local")
    benchmark.add_argument("--output")
    benchmark.add_argument("--allow-degraded", action="store_true")
    benchmark.add_argument("--min-file-recall", type=float, default=0.85)
    benchmark.add_argument("--min-symbol-recall", type=float, default=0.80)
    benchmark.add_argument("--min-mrr", type=float, default=0.65)
    benchmark.add_argument("--min-ndcg", type=float, default=0.75)
    benchmark.add_argument("--max-p95-ms", type=float, default=2_000)

    client = subparsers.add_parser("client-config", help="Print client configuration")
    client.add_argument("--client", choices=("codex", "claude"), required=True)
    client.add_argument("--transport", choices=("stdio", "http"), default="http")
    client.add_argument("--url", default="http://127.0.0.1:8765/mcp")
    client.add_argument("--project-dir", default=_project_root())
    return parser


def _csv_env(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _project_root() -> str:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").is_file():
            return str(candidate)
    return str(Path.cwd().resolve())


if __name__ == "__main__":
    raise SystemExit(main())
