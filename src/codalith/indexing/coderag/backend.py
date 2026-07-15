"""CodeRAG integration using only its public Config and CodeRAG APIs."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import shutil
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from coderag import CodeRAG, Config

from codalith.corpus.registry import Corpus
from codalith.corpus.source_policy import SourcePolicy
from codalith.corpus.store_manifest import ActiveGeneration
from codalith.errors import IndexBuildError, RetrievalError


@dataclass(frozen=True, slots=True)
class SemanticHit:
    path: str
    symbol: str | None
    kind: str
    language: str
    start_line: int
    end_line: int
    snippet: str
    score: float
    similarity: float


@dataclass(frozen=True, slots=True)
class SemanticBuildInfo:
    provider: str
    model: str
    dimension: int
    total_files: int
    total_chunks: int
    store_fingerprint: str

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "dimension": self.dimension,
            "total_files": self.total_files,
            "total_chunks": self.total_chunks,
            "store_fingerprint": self.store_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class TextHit:
    path: str
    line: int
    text: str


class CodeRAGBackend:
    def __init__(self, policy: SourcePolicy) -> None:
        self.policy = policy
        self._engines: dict[tuple[str, str], CodeRAG] = {}
        self._lock = threading.RLock()

    def search(
        self,
        corpus: Corpus,
        generation: ActiveGeneration,
        query: str,
        *,
        limit: int,
    ) -> list[SemanticHit]:
        engine = self._engine(corpus, generation)
        try:
            hits = engine.search(query, top_k=limit)
        except Exception as exc:
            raise RetrievalError(
                f"CodeRAG search failed for {corpus.corpus_id}: {type(exc).__name__}: {exc}"
            ) from exc
        return [
            SemanticHit(
                path=hit.path.replace("\\", "/"),
                symbol=hit.symbol,
                kind=hit.kind,
                language=hit.language,
                start_line=hit.start_line,
                end_line=hit.end_line,
                snippet=hit.text,
                score=float(hit.score),
                similarity=float(hit.similarity),
            )
            for hit in hits
        ]

    def text_search(
        self,
        corpus: Corpus,
        generation: ActiveGeneration,
        query: str,
        *,
        limit: int,
    ) -> list[TextHit]:
        engine = self._engine(corpus, generation)
        try:
            result = engine.search_files(
                re.escape(query),
                target="content",
                output_mode="content",
                limit=limit,
                ignore_case=True,
                max_file_bytes=self.policy.max_file_bytes,
                redact=True,
            )
        except Exception as exc:
            raise RetrievalError(
                f"Text search failed for {corpus.corpus_id}: {type(exc).__name__}: {exc}"
            ) from exc
        if "error" in result:
            raise RetrievalError(str(result["error"]))
        rows = result.get("results", [])
        if not isinstance(rows, list):
            raise RetrievalError("CodeRAG text search returned invalid results")
        hits: list[TextHit] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            path = row.get("path")
            line = row.get("line_number")
            text = row.get("line")
            if isinstance(path, str) and isinstance(line, int) and isinstance(text, str):
                hits.append(TextHit(path.replace("\\", "/"), line, text))
        return hits

    def close(self) -> None:
        with self._lock:
            engines = list(self._engines.values())
            self._engines.clear()
        for engine in engines:
            engine.close()

    def _engine(self, corpus: Corpus, generation: ActiveGeneration) -> CodeRAG:
        if not generation.manifest.semantic_available:
            raise RetrievalError(
                f"Semantic index is unavailable for {corpus.corpus_id} generation "
                f"{generation.manifest.generation_id}"
            )
        key = (corpus.corpus_id, generation.manifest.generation_id)
        with self._lock:
            engine = self._engines.get(key)
            if engine is None:
                obsolete = [item for item in self._engines if item[0] == corpus.corpus_id]
                for old_key in obsolete:
                    self._engines.pop(old_key).close()
                engine = CodeRAG(
                    coderag_config(corpus, generation.coderag_path, self.policy)
                )
                self._engines[key] = engine
            return engine


def prepare_semantic_index(
    corpus: Corpus,
    store_dir: Path,
    policy: SourcePolicy,
    *,
    mode: str,
    indexed_paths: tuple[str, ...] = (),
    allow_external_rebuild: bool = False,
) -> SemanticBuildInfo:
    _validate_semantic_request(
        corpus,
        store_dir,
        mode=mode,
        allow_external_rebuild=allow_external_rebuild,
    )
    source_view: tempfile.TemporaryDirectory[str] | None = None
    watched_root = corpus.source_root
    if mode == "build":
        if not indexed_paths:
            raise IndexBuildError("Cannot build a semantic index without indexed source files")
        source_view = tempfile.TemporaryDirectory(
            prefix=".codalith-source-",
            dir=corpus.index_root,
        )
        watched_root = Path(source_view.name)
        try:
            _materialize_source_view(corpus.source_root, watched_root, indexed_paths)
        except Exception:
            source_view.cleanup()
            raise
    engine = CodeRAG(
        coderag_config(corpus, store_dir, policy, watched_root=watched_root)
    )
    try:
        if mode == "build":
            engine.index(full=True)
        status = engine.status()
    except Exception as exc:
        raise IndexBuildError(
            f"Cannot {mode} CodeRAG store for {corpus.corpus_id}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    finally:
        engine.close()
        if source_view is not None:
            source_view.cleanup()
    total_files = _status_int(status, "total_files")
    total_chunks = _status_int(status, "total_chunks")
    if total_files <= 0 or total_chunks <= 0:
        raise IndexBuildError("CodeRAG store is empty")
    return SemanticBuildInfo(
        provider=str(status.get("provider", corpus.embedding_provider)),
        model=str(status.get("model", corpus.embedding_model)),
        dimension=_status_int(status, "embedding_dim"),
        total_files=total_files,
        total_chunks=total_chunks,
        store_fingerprint=store_fingerprint(store_dir),
    )


def preflight_semantic_index(
    corpus: Corpus,
    policy: SourcePolicy,
    *,
    mode: str,
    allow_external_rebuild: bool = False,
) -> None:
    """Fail fast before an expensive structural scan when semantic setup is invalid."""

    store_dir = corpus.coderag_store
    if mode == "adopt" and store_dir is None:
        raise IndexBuildError("Semantic adopt mode requires corpus.coderag_store")
    effective_store = store_dir or corpus.index_root / ".semantic-preflight"
    _validate_semantic_request(
        corpus,
        effective_store,
        mode=mode,
        allow_external_rebuild=allow_external_rebuild,
    )
    if mode != "adopt":
        return
    engine = CodeRAG(coderag_config(corpus, effective_store, policy))
    try:
        status = engine.status()
    except Exception as exc:
        raise IndexBuildError(
            f"Cannot preflight CodeRAG store for {corpus.corpus_id}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    finally:
        engine.close()
    if _status_int(status, "total_files") <= 0 or _status_int(status, "total_chunks") <= 0:
        raise IndexBuildError("CodeRAG store is empty")
    if _status_int(status, "embedding_dim") <= 0:
        raise IndexBuildError("CodeRAG store has no embedding dimension")


def _validate_semantic_request(
    corpus: Corpus,
    store_dir: Path,
    *,
    mode: str,
    allow_external_rebuild: bool,
) -> None:
    if mode not in {"build", "adopt"}:
        raise ValueError("Semantic mode must be build or adopt")
    if corpus.embedding_provider == "openai":
        try:
            importlib.import_module("openai")
        except ModuleNotFoundError as exc:
            raise IndexBuildError(
                "OpenAI embeddings require the coderag[openai] package extra"
            ) from exc
    if mode == "adopt" and not store_dir.is_dir():
        raise IndexBuildError(f"CodeRAG store does not exist: {store_dir}")
    if (
        mode == "build"
        and store_dir.exists()
        and corpus.coderag_store is not None
        and not allow_external_rebuild
    ):
        raise IndexBuildError(
            "Refusing to rebuild an external CodeRAG store without explicit permission"
        )


def coderag_config(
    corpus: Corpus,
    store_dir: Path,
    policy: SourcePolicy,
    *,
    watched_root: Path | None = None,
) -> Config:
    defaults = Config()
    model = corpus.embedding_model
    return Config(
        provider=corpus.embedding_provider,
        model=model if corpus.embedding_provider == "fastembed" and model else defaults.model,
        openai_model=(
            model if corpus.embedding_provider == "openai" and model else defaults.openai_model
        ),
        openai_api_key=os.getenv("CODALITH_EMBEDDING_API_KEY"),
        openai_base_url=os.getenv("CODALITH_EMBEDDING_BASE_URL"),
        cache_dir=defaults.cache_dir,
        watched_dir=watched_root or corpus.source_root,
        store_dir=store_dir,
        languages=_coderag_languages(corpus, defaults.languages),
        ignore_globs=tuple(
            dict.fromkeys([*defaults.ignore_globs, *corpus.exclude_globs])
        ),
        use_gitignore=True,
        index_all_text=False,
        max_file_bytes=policy.max_file_bytes,
        max_chunk_lines=defaults.max_chunk_lines,
        window_lines=defaults.window_lines,
        window_overlap=defaults.window_overlap,
        top_k=defaults.top_k,
        fetch_k=defaults.fetch_k,
        rrf_k=defaults.rrf_k,
        dense_weight=defaults.dense_weight,
        lexical_weight=defaults.lexical_weight,
        adaptive_fusion=True,
        graph_expansion=False,
    )


def store_fingerprint(store_dir: Path) -> str:
    """Hash every CodeRAG store byte so provenance detects same-size corruption."""

    if not store_dir.is_dir():
        raise IndexBuildError(f"CodeRAG store does not exist: {store_dir}")
    digest = hashlib.sha256()
    files = sorted(path for path in store_dir.rglob("*") if path.is_file())
    for path in files:
        relative = path.relative_to(store_dir).as_posix()
        stat = path.stat()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def write_semantic_metadata(path: Path, info: SemanticBuildInfo) -> None:
    path.write_text(json.dumps(info.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _status_int(status: dict[str, Any], key: str) -> int:
    value = status.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise IndexBuildError(f"CodeRAG status field {key!r} is invalid")
    return value


def _coderag_languages(corpus: Corpus, defaults: tuple[str, ...]) -> tuple[str, ...]:
    if corpus.adapter == "python":
        return ("python",)
    if corpus.adapter == "csharp":
        return ("csharp",)
    if corpus.adapter == "cpp-ue":
        return ("c", "cpp", "csharp")
    return defaults


def _materialize_source_view(
    source_root: Path,
    view_root: Path,
    indexed_paths: tuple[str, ...],
) -> None:
    """Create a temporary link tree containing exactly one structural generation."""

    resolved_root = source_root.resolve()
    for relative in indexed_paths:
        source = (resolved_root / relative).resolve()
        if resolved_root not in source.parents:
            raise IndexBuildError(f"Indexed path escapes source root: {relative}")
        if not source.is_file():
            raise IndexBuildError(f"Indexed source file disappeared: {relative}")
        destination = view_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source, destination)
        except OSError:
            try:
                destination.symlink_to(source)
            except OSError:
                try:
                    shutil.copy2(source, destination)
                except OSError as exc:
                    raise IndexBuildError(
                        "Cannot materialize source for semantic indexing: "
                        f"{relative}"
                    ) from exc
