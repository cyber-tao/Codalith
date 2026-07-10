"""Real CodeRAG acceptance job for Codalith."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from codalith.cards.generator import write_cards
from codalith.cards.verifier import KnowledgeCardVerifier
from codalith.cli.common import load_seed_cards
from codalith.coderag import CodeRAGAdapter
from codalith.compiler.context_compiler import ContextCompiler
from codalith.corpus.registry import Corpus, CorpusRegistry
from codalith.corpus.uri_resolver import URIResolver
from codalith.eval.runner import EvalRunner, write_reports

DEFAULT_CODERAG_EMBEDDING_BATCH_SIZE = "32"
DEFAULT_CODERAG_EMBEDDING_MODEL = "Qwen3-Embedding-8B"
DEFAULT_CODERAG_WORKERS = "4"
DEFAULT_PYPI_INDEX = "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple/"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default="configs/sample/registry.json")
    parser.add_argument("--dataset", default="eval/datasets/sample_eval_suite.jsonl")
    parser.add_argument(
        "--corpus", default=None, help="Base corpus id or version alias"
    )
    parser.add_argument("--output-dir", default="reports/coderag")
    parser.add_argument("--provider", default=os.getenv("CODALITH_CODERAG_PROVIDER", "fake"))
    parser.add_argument("--index-path")
    parser.add_argument("--min-files", type=int)
    parser.add_argument("--min-chunks", type=int)
    parser.add_argument("--min-cards-verified", type=int)
    parser.add_argument("--min-file-recall-at-5", type=float, default=0.70)
    parser.add_argument("--max-p95-ms", type=float, default=30_000.0)
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args(argv)
    min_files, min_chunks = acceptance_minimums(args.index_path, args.min_files, args.min_chunks)

    configure_coderag_runtime_env(args.provider)
    ensure_coderag_installed(args.provider)
    configure_openai_batch_limit(args.provider)
    registry = CorpusRegistry.from_file(args.registry)
    corpus = registry.get_base(args.corpus)
    prepare_indexed_root(corpus)
    resolver = URIResolver(registry)
    card_adapter = CodeRAGAdapter(registry)
    seed_cards = load_seed_cards(corpus, resolver, card_adapter)
    verifier = KnowledgeCardVerifier(resolver, card_adapter)
    verified_cards = []
    card_results = []
    for card in seed_cards:
        result = verifier.verify(card)
        card_results.append(result.as_dict() | {"card_id": card.card_id})
        if result.ok:
            verified_cards.append(result.verified_card(card))
    write_cards(verified_cards, corpus.card_root)

    os.environ["CODALITH_USE_NATIVE_CODERAG"] = "1"
    os.environ["CODALITH_NATIVE_CODERAG_STRICT"] = "1"
    os.environ.setdefault("CODERAG_GITIGNORE", "0")

    adapter = CodeRAGAdapter(registry, prefer_native=True)
    started = time.perf_counter()
    index_stats = adapter.reindex(corpus.corpus_id, path=args.index_path, full=args.full)
    index_seconds = time.perf_counter() - started
    status = adapter.status(corpus.corpus_id)

    compiler = ContextCompiler(registry, adapter)
    report = EvalRunner(compiler).run(args.dataset, corpus=args.corpus)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_reports(report, output_dir)

    acceptance = {
        "provider": args.provider,
        "indexed_root": str(corpus.indexed_root),
        "index_seconds": index_seconds,
        "index_stats": index_stats,
        "status": status,
        "cards_verified": sum(1 for item in card_results if item["ok"]),
        "cards_total": len(card_results),
        "card_results": card_results,
        "eval": report.as_dict(),
    }
    (output_dir / "coderag_acceptance.json").write_text(
        json.dumps(acceptance, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(acceptance, indent=2, sort_keys=True))
    enforce_acceptance(
        acceptance,
        min_files=min_files,
        min_chunks=min_chunks,
        min_cards_verified=args.min_cards_verified,
        min_file_recall_at_5=args.min_file_recall_at_5,
        max_p95_ms=args.max_p95_ms,
    )
    return 0


def configure_coderag_runtime_env(provider: str) -> None:
    os.environ["CODERAG_PROVIDER"] = provider
    os.environ["CODERAG_INDEX_ALL_TEXT"] = "1"
    os.environ["CODERAG_WORKERS"] = os.getenv(
        "CODALITH_CODERAG_WORKERS", DEFAULT_CODERAG_WORKERS
    )
    if provider.lower() != "openai":
        return
    os.environ["CODERAG_OPENAI_MODEL"] = os.getenv(
        "CODALITH_CODERAG_EMBEDDING_MODEL", DEFAULT_CODERAG_EMBEDDING_MODEL
    )
    os.environ["CODERAG_OPENAI_BATCH"] = os.getenv(
        "CODALITH_CODERAG_EMBEDDING_BATCH_SIZE",
        DEFAULT_CODERAG_EMBEDDING_BATCH_SIZE,
    )


def acceptance_minimums(
    index_path: str | None,
    min_files: int | None,
    min_chunks: int | None,
) -> tuple[int, int]:
    default_minimum = 1 if index_path else 1000
    return (
        min_files if min_files is not None else default_minimum,
        min_chunks if min_chunks is not None else default_minimum,
    )


def ensure_coderag_installed(provider: str) -> None:
    mirror = os.getenv("CODALITH_PYPI_INDEX", DEFAULT_PYPI_INDEX)
    os.environ.setdefault("UV_DEFAULT_INDEX", mirror)
    os.environ.setdefault("PIP_INDEX_URL", mirror)
    try:
        import coderag  # noqa: F401

        return
    except ImportError:
        pass

    source = Path("external/CodeRAG")
    if not source.exists():
        if os.getenv("CODALITH_CODERAG_ALLOW_AUTO_CLONE", "").lower() not in {"1", "true", "yes"}:
            raise RuntimeError(
                "CodeRAG submodule is missing. Run "
                "`git submodule update --init --recursive external/CodeRAG` "
                "or set CODALITH_CODERAG_ALLOW_AUTO_CLONE=1 for a temporary /tmp clone."
            )
        source = Path("/tmp/CodeRAG")
        if not source.exists():
            subprocess.run(
                ["git", "clone", "--depth", "1", "https://github.com/Neverdecel/CodeRAG.git", str(source)],
                check=True,
            )
    uv = shutil.which("uv")
    dependencies = minimal_coderag_dependencies(provider)
    if uv:
        subprocess.run([uv, "pip", "install", "--no-deps", str(source)], check=True)
        subprocess.run([uv, "pip", "install", *dependencies], check=True)
    else:
        subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", str(source)], check=True)
        subprocess.run(
            [sys.executable, "-m", "pip", "install", *dependencies],
            check=True,
        )
    import coderag  # noqa: F401


def minimal_coderag_dependencies(provider: str) -> list[str]:
    dependencies = [
        "lancedb>=0.33,<1",
        "pylance>=0.10",
        "pyarrow>=16,<25",
        "numpy>=2.4.6,<3",
        "python-dotenv>=1.2.2,<2",
        "tenacity>=9.1.4,<10",
        "pathspec>=0.12,<2",
        "tree-sitter>=0.25.2,<0.26",
        "tree-sitter-python>=0.25.0,<0.26",
        "tree-sitter-javascript>=0.25.0,<0.26",
        "tree-sitter-typescript>=0.23.2,<0.26",
        "tree-sitter-go>=0.25.0,<0.26",
        "tree-sitter-rust>=0.24.2,<0.26",
        "tree-sitter-java>=0.23.5,<0.26",
    ]
    if provider.lower() == "openai":
        dependencies.append("openai>=2.41.1,<3")
    return dependencies


def configure_openai_batch_limit(provider: str) -> None:
    if provider.lower() != "openai":
        return
    import coderag.embeddings.openai_provider as openai_provider

    openai_provider._BATCH = int(os.getenv("CODERAG_OPENAI_BATCH", "10"))


def prepare_indexed_root(corpus: Corpus) -> None:
    if corpus.indexed_root.exists() or corpus.source_root.exists():
        return
    raise FileNotFoundError(
        f"Neither indexed_root nor source_root exists for corpus {corpus.corpus_id}: "
        f"{corpus.indexed_root} / {corpus.source_root}"
    )


def enforce_acceptance(
    acceptance: dict[str, Any],
    *,
    min_files: int,
    min_chunks: int,
    min_cards_verified: int | None,
    min_file_recall_at_5: float,
    max_p95_ms: float,
) -> None:
    failures: list[str] = []
    status = acceptance["status"]
    eval_report = acceptance["eval"]
    if int(status["total_files"]) < min_files:
        failures.append(f"total_files {status['total_files']} < {min_files}")
    if int(status["total_chunks"]) < min_chunks:
        failures.append(f"total_chunks {status['total_chunks']} < {min_chunks}")
    required_cards = min_cards_verified if min_cards_verified is not None else acceptance["cards_total"]
    if int(acceptance["cards_verified"]) < int(required_cards):
        failures.append(f"cards_verified {acceptance['cards_verified']} < {required_cards}")
    if float(eval_report["file_recall@k"]) < min_file_recall_at_5:
        failures.append(
            f"file_recall@k {eval_report['file_recall@k']:.3f} < {min_file_recall_at_5:.3f}"
        )
    if float(eval_report["latency_p95_ms"]) > max_p95_ms:
        failures.append(f"latency_p95_ms {eval_report['latency_p95_ms']:.1f} > {max_p95_ms:.1f}")
    if failures:
        raise SystemExit("; ".join(failures))


if __name__ == "__main__":
    raise SystemExit(main())
