"""CodeRAG integration using only its public Config and CodeRAG APIs."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import tempfile
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from coderag import CodeRAG, Config

from codalith.corpus.registry import Corpus
from codalith.corpus.source_policy import SourcePolicy
from codalith.corpus.store_manifest import ActiveGeneration
from codalith.errors import CodalithError, IndexBuildError, IndexUnavailableError, RetrievalError
from codalith.indexing.leases import GenerationLeases

_STORE_PROVENANCE_FILE = "meta.json"
_CODERAG_BINARY_SNIFF_BYTES = 8192


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
    covered_files: int
    extra_files: int
    store_fingerprint: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class StoreProvenance:
    model: str
    dimension: int

    def describe(self) -> str:
        return f"{self.model} ({self.dimension} dimensions)"


class CodeRAGBackend:
    def __init__(self, policy: SourcePolicy) -> None:
        self.policy = policy
        self._engines: GenerationLeases[CodeRAG] = GenerationLeases(_close_engine)

    def search(
        self,
        corpus: Corpus,
        generation: ActiveGeneration,
        query: str,
        *,
        limit: int,
    ) -> list[SemanticHit]:
        with self._engine(corpus, generation) as engine:
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

    def close(self) -> None:
        self._engines.close()

    def _engine(
        self,
        corpus: Corpus,
        generation: ActiveGeneration,
    ) -> AbstractContextManager[CodeRAG]:
        manifest = generation.manifest
        if not manifest.semantic_available:
            raise RetrievalError(
                f"Semantic index is unavailable for {corpus.corpus_id} generation "
                f"{manifest.generation_id}"
            )
        return self._engines.lease(
            corpus.corpus_id,
            manifest.generation_id,
            lambda: self._open_engine(corpus, generation),
        )

    def _open_engine(self, corpus: Corpus, generation: ActiveGeneration) -> CodeRAG:
        try:
            expected = generation_store_provenance(generation)
        except IndexUnavailableError as exc:
            raise RetrievalError(str(exc)) from exc
        return _open_verified_engine(
            coderag_config(
                corpus,
                generation.coderag_path,
                self.policy,
                provider=generation.manifest.embedding_provider,
                model=generation.manifest.embedding_model,
            ),
            error=RetrievalError,
            expected=expected,
        )


def _close_engine(engine: CodeRAG) -> None:
    engine.close()


def read_store_provenance(store_dir: Path) -> StoreProvenance:
    """Read the embedding provenance CodeRAG compares before it clears a store."""

    path = store_dir / _STORE_PROVENANCE_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise IndexUnavailableError(
            f"CodeRAG store has no readable provenance {path}: {exc}"
        ) from exc
    model = raw.get("embed_model") if isinstance(raw, dict) else None
    dimension = raw.get("embed_dim") if isinstance(raw, dict) else None
    if (
        not isinstance(model, str)
        or not model
        or not isinstance(dimension, int)
        or isinstance(dimension, bool)
        or dimension <= 0
    ):
        raise IndexUnavailableError(f"CodeRAG store provenance is invalid: {path}")
    return StoreProvenance(model, dimension)


def generation_store_provenance(generation: ActiveGeneration) -> StoreProvenance:
    """Verify the store a semantic generation points at without loading a model."""

    manifest = generation.manifest
    if manifest.embedding_dimension is None:
        raise IndexUnavailableError(
            f"Generation {manifest.generation_id} has no semantic embedding dimension"
        )
    expected = StoreProvenance(manifest.embedding_model, manifest.embedding_dimension)
    recorded = read_store_provenance(generation.coderag_path)
    if recorded != expected:
        raise IndexUnavailableError(
            f"CodeRAG store {generation.coderag_path} records {recorded.describe()} but "
            f"generation {manifest.generation_id} expects {expected.describe()}"
        )
    return expected


def _open_verified_engine(
    config: Config,
    *,
    error: type[CodalithError],
    expected: StoreProvenance | None = None,
) -> CodeRAG:
    """Open an existing store only when CodeRAG's own bootstrap cannot clear it."""

    store_dir = Path(config.store_dir)
    try:
        recorded = read_store_provenance(store_dir)
    except IndexUnavailableError as exc:
        raise error(str(exc)) from exc
    if expected is not None and recorded != expected:
        raise error(
            f"CodeRAG store {store_dir} records {recorded.describe()} but "
            f"{expected.describe()} was expected"
        )
    engine = CodeRAG(config)
    try:
        provider = engine.provider
        configured = StoreProvenance(str(provider.model_id), int(provider.dim))
    except Exception as exc:
        engine.close()
        raise error(
            f"Cannot initialize embedding provider {config.provider!r}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if configured != recorded:
        engine.close()
        raise error(
            f"CodeRAG store {store_dir} was built with {recorded.describe()} but the "
            f"configured provider is {configured.describe()}; refusing to open it "
            "because CodeRAG would clear the store"
        )
    return engine


def prepare_semantic_index(
    corpus: Corpus,
    store_dir: Path,
    policy: SourcePolicy,
    *,
    mode: str,
    indexed_files: Mapping[str, str],
) -> SemanticBuildInfo:
    """Build a fresh store or adopt an external one for exactly one structural snapshot.

    ``indexed_files`` maps every snapshot path to its SHA-256.
    """

    _validate_semantic_request(corpus, mode=mode, store_dir=store_dir)
    if not indexed_files:
        raise IndexBuildError("Cannot prepare a semantic index without indexed source files")
    if mode == "build" and store_dir.exists():
        raise IndexBuildError(f"Semantic build target already exists: {store_dir}")
    source_view: tempfile.TemporaryDirectory[str] | None = None
    try:
        if mode == "build":
            source_view = tempfile.TemporaryDirectory(
                prefix=".codalith-source-",
                dir=corpus.index_root,
            )
            view_root = Path(source_view.name)
            _materialize_source_view(corpus.source_root, view_root, tuple(indexed_files))
            engine = CodeRAG(coderag_config(corpus, store_dir, policy, source_view=view_root))
        else:
            engine = _open_verified_engine(
                coderag_config(corpus, store_dir, policy),
                error=IndexBuildError,
            )
        try:
            if mode == "build":
                engine.index(full=True)
            status = engine.status()
            stored = _stored_file_hashes(engine)
        except Exception as exc:
            raise IndexBuildError(
                f"Cannot {mode} CodeRAG store for {corpus.corpus_id}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        finally:
            engine.close()
    finally:
        if source_view is not None:
            source_view.cleanup()
    total_files = _status_int(status, "total_files")
    total_chunks = _status_int(status, "total_chunks")
    if total_files <= 0 or total_chunks <= 0:
        raise IndexBuildError("CodeRAG store is empty")
    if mode == "build":
        covered_files = _verify_built_store(corpus.source_root, stored, indexed_files)
        extra_files = 0
    else:
        covered_files, extra_files = _verify_adopted_store(stored, indexed_files)
    return SemanticBuildInfo(
        provider=str(status.get("provider", corpus.embedding_provider)),
        model=str(status.get("model", corpus.embedding_model)),
        dimension=_status_int(status, "embedding_dim"),
        total_files=total_files,
        total_chunks=total_chunks,
        covered_files=covered_files,
        extra_files=extra_files,
        store_fingerprint=store_fingerprint(store_dir),
    )


def preflight_semantic_index(
    corpus: Corpus,
    policy: SourcePolicy,
    *,
    mode: str,
) -> None:
    """Fail fast before an expensive structural scan when semantic setup is invalid."""

    _validate_semantic_request(corpus, mode=mode, store_dir=corpus.coderag_store)
    if mode != "adopt" or corpus.coderag_store is None:
        return
    engine = _open_verified_engine(
        coderag_config(corpus, corpus.coderag_store, policy),
        error=IndexBuildError,
    )
    try:
        status = engine.status()
    except Exception as exc:
        raise IndexBuildError(
            f"Cannot preflight CodeRAG store for {corpus.corpus_id}: {type(exc).__name__}: {exc}"
        ) from exc
    finally:
        engine.close()
    if _status_int(status, "total_files") <= 0 or _status_int(status, "total_chunks") <= 0:
        raise IndexBuildError("CodeRAG store is empty")
    if _status_int(status, "embedding_dim") <= 0:
        raise IndexBuildError("CodeRAG store has no embedding dimension")


def _validate_semantic_request(
    corpus: Corpus,
    *,
    mode: str,
    store_dir: Path | None,
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
    if mode == "adopt":
        if store_dir is None:
            raise IndexBuildError("Semantic adopt mode requires corpus.coderag_store")
        if not store_dir.is_dir():
            raise IndexBuildError(f"CodeRAG store does not exist: {store_dir}")


def _stored_file_hashes(engine: CodeRAG) -> dict[str, str]:
    metas = engine.store.all_file_metas()
    return {
        str(path).replace("\\", "/"): str(meta.get("content_hash") or "")
        for path, meta in metas.items()
    }


def _verify_built_store(
    source_root: Path,
    stored: Mapping[str, str],
    indexed_files: Mapping[str, str],
) -> int:
    outside = sorted(set(stored) - set(indexed_files))
    if outside:
        raise IndexBuildError(
            f"CodeRAG indexed {len(outside)} file(s) outside the structural snapshot: "
            f"{', '.join(outside[:5])}"
        )
    changed = sorted(path for path, digest in stored.items() if indexed_files[path] != digest)
    if changed:
        raise IndexBuildError(
            f"Source changed while building the semantic index: {', '.join(changed[:5])}"
        )
    skipped = sorted(
        path
        for path in set(indexed_files) - set(stored)
        if _has_indexable_text(source_root / path)
    )
    if skipped:
        raise IndexBuildError(
            f"CodeRAG skipped {len(skipped)} indexable source file(s): {', '.join(skipped[:5])}"
        )
    return len(stored)


def _verify_adopted_store(
    stored: Mapping[str, str],
    indexed_files: Mapping[str, str],
) -> tuple[int, int]:
    common = stored.keys() & indexed_files.keys()
    if not common:
        raise IndexBuildError("Adopted CodeRAG store covers no file in the structural snapshot")
    drifted = sorted(path for path in common if stored[path] != indexed_files[path])
    if drifted:
        raise IndexBuildError(
            f"Adopted CodeRAG store content differs from the structural snapshot for "
            f"{len(drifted)} file(s): {', '.join(drifted[:5])}"
        )
    return len(common), len(stored) - len(common)


def _has_indexable_text(path: Path) -> bool:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise IndexBuildError(f"Indexed source file disappeared: {path}") from exc
    return bool(data.strip()) and b"\x00" not in data[:_CODERAG_BINARY_SNIFF_BYTES]


def coderag_config(
    corpus: Corpus,
    store_dir: Path,
    policy: SourcePolicy,
    *,
    provider: str | None = None,
    model: str | None = None,
    source_view: Path | None = None,
) -> Config:
    """``source_view`` is a materialized snapshot that CodeRAG must index without filtering."""

    defaults = Config()
    provider = provider or corpus.embedding_provider
    model = corpus.embedding_model if model is None else model
    return Config(
        provider=provider,
        model=model if provider == "fastembed" and model else defaults.model,
        openai_model=(model if provider == "openai" and model else defaults.openai_model),
        openai_api_key=os.getenv("CODALITH_EMBEDDING_API_KEY"),
        openai_base_url=os.getenv("CODALITH_EMBEDDING_BASE_URL"),
        cache_dir=defaults.cache_dir,
        watched_dir=source_view or corpus.source_root,
        store_dir=store_dir,
        languages=_coderag_languages(corpus, defaults.languages),
        ignore_globs=(
            ()
            if source_view is not None
            else tuple(dict.fromkeys([*defaults.ignore_globs, *corpus.exclude_globs]))
        ),
        use_gitignore=source_view is None,
        index_all_text=source_view is not None,
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
                        f"Cannot materialize source for semantic indexing: {relative}"
                    ) from exc
