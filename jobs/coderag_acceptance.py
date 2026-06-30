"""Real CodeRAG acceptance job for UE Context Engine."""

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

from ue_context.cards.generator import built_in_cards, write_cards
from ue_context.cards.verifier import KnowledgeCardVerifier
from ue_context.coderag.adapter import CodeRAGAdapter
from ue_context.compiler.context_compiler import ContextCompiler
from ue_context.corpus.registry import Corpus, CorpusRegistry
from ue_context.corpus.uri_resolver import URIResolver
from ue_context.eval.runner import EvalRunner, write_reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default="configs/corpus_registry.yaml")
    parser.add_argument("--dataset", default="eval/datasets/ue50.jsonl")
    parser.add_argument("--version", default="5.7.4")
    parser.add_argument("--output-dir", default="reports/coderag")
    parser.add_argument("--provider", default=os.getenv("CODERAG_PROVIDER", "fake"))
    parser.add_argument("--min-files", type=int, default=1000)
    parser.add_argument("--min-chunks", type=int, default=1000)
    parser.add_argument("--min-file-recall-at-5", type=float, default=0.70)
    parser.add_argument("--max-p95-ms", type=float, default=30_000.0)
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args(argv)

    configure_openai_compatible_env()
    ensure_coderag_installed(args.provider)
    registry = CorpusRegistry.from_file(args.registry)
    corpus = registry.get_engine(args.version)
    prepare_indexed_root(corpus)
    cards = [card.verified() for card in built_in_cards(corpus_id=corpus.corpus_id, version=corpus.ue_version or args.version)]
    write_cards(cards, corpus.card_root)
    write_cards(cards, corpus.indexed_root)

    os.environ["UE_CONTEXT_USE_NATIVE_CODERAG"] = "1"
    os.environ["UE_CONTEXT_NATIVE_CODERAG_STRICT"] = "1"
    os.environ["CODERAG_PROVIDER"] = args.provider
    os.environ["CODERAG_INDEX_ALL_TEXT"] = "1"
    os.environ.setdefault("CODERAG_GITIGNORE", "0")
    os.environ.setdefault("CODERAG_WORKERS", "4")

    adapter = CodeRAGAdapter(registry, prefer_native=True)
    started = time.perf_counter()
    index_stats = adapter.reindex(corpus.corpus_id, full=args.full)
    index_seconds = time.perf_counter() - started
    status = adapter.status(corpus.corpus_id)

    verifier = KnowledgeCardVerifier(URIResolver(registry), adapter)
    card_results = [verifier.verify(card).as_dict() | {"card_id": card.card_id} for card in cards]

    compiler = ContextCompiler(registry, adapter)
    report = EvalRunner(compiler).run(args.dataset, version=args.version)
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
        "eval": report.as_dict(),
    }
    (output_dir / "coderag_acceptance.json").write_text(
        json.dumps(acceptance, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(acceptance, indent=2, sort_keys=True))
    enforce_acceptance(
        acceptance,
        min_files=args.min_files,
        min_chunks=args.min_chunks,
        min_file_recall_at_5=args.min_file_recall_at_5,
        max_p95_ms=args.max_p95_ms,
    )
    return 0


def configure_openai_compatible_env() -> None:
    mappings = {
        "API_KEY": "OPENAI_API_KEY",
        "BASE_URL": "OPENAI_BASE_URL",
        "MODEL": "CODERAG_OPENAI_MODEL",
    }
    for source, target in mappings.items():
        if not os.getenv(target) and os.getenv(source):
            os.environ[target] = os.environ[source]
    if not os.getenv("CODERAG_CHAT_MODEL") and os.getenv("MODEL"):
        os.environ["CODERAG_CHAT_MODEL"] = os.environ["MODEL"]


def ensure_coderag_installed(provider: str) -> None:
    mirror = "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple/"
    os.environ.setdefault("UV_DEFAULT_INDEX", mirror)
    os.environ.setdefault("PIP_INDEX_URL", mirror)
    try:
        import coderag  # noqa: F401

        return
    except ImportError:
        pass

    source = Path("external/CodeRAG")
    if not source.exists():
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


def prepare_indexed_root(corpus: Corpus) -> None:
    corpus.indexed_root.mkdir(parents=True, exist_ok=True)
    engine_dir = corpus.indexed_root / "Engine"
    if not engine_dir.exists():
        source_engine = corpus.source_root / "Engine"
        if not source_engine.exists():
            raise FileNotFoundError(f"UE Engine source directory is missing: {source_engine}")
        try:
            engine_dir.symlink_to(source_engine, target_is_directory=True)
        except OSError as exc:
            # Docker compose mounts Engine directly for the real acceptance profile.
            raise FileNotFoundError(
                f"{engine_dir} is missing. Mount UE Engine there or run with an indexed root."
            ) from exc
    if engine_dir.is_symlink():
        raise RuntimeError(
            f"{engine_dir} is a symlink; CodeRAG's walker does not follow symlinked directories. "
            "Mount the Engine directory directly into indexed_root/Engine."
        )


def enforce_acceptance(
    acceptance: dict[str, Any],
    *,
    min_files: int,
    min_chunks: int,
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
    if acceptance["cards_verified"] != acceptance["cards_total"]:
        failures.append(f"cards_verified {acceptance['cards_verified']} != {acceptance['cards_total']}")
    if float(eval_report["file_recall@5"]) < min_file_recall_at_5:
        failures.append(
            f"file_recall@5 {eval_report['file_recall@5']:.3f} < {min_file_recall_at_5:.3f}"
        )
    if float(eval_report["latency_p95_ms"]) > max_p95_ms:
        failures.append(f"latency_p95_ms {eval_report['latency_p95_ms']:.1f} > {max_p95_ms:.1f}")
    if failures:
        raise SystemExit("; ".join(failures))


if __name__ == "__main__":
    raise SystemExit(main())
