"""Generation-bound semantic plane: build, adopt, verify, and query one store per generation."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from pathlib import Path

from codalith.corpus.generations import ActiveGeneration
from codalith.corpus.registry import Corpus
from codalith.corpus.source_policy import SourcePolicy
from codalith.errors import (
    CodalithError,
    IndexBuildError,
    IndexInvalidError,
    IndexUnavailableError,
    RetrievalError,
)
from codalith.indexing.leases import GenerationLeases
from codalith.semantic.config import Config
from codalith.semantic.engine import SemanticEngine
from codalith.semantic.indexer import has_indexable_text
from codalith.semantic.store import SemanticStoreError, StoreProvenance, read_provenance


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


class SemanticBackend:
    def __init__(self, policy: SourcePolicy) -> None:
        self.policy = policy
        self._engines: GenerationLeases[SemanticEngine] = GenerationLeases(_close_engine)

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
                hits = engine.search(query, limit)
            except Exception as exc:
                raise RetrievalError(
                    f"Semantic search failed for {corpus.corpus_id}: {type(exc).__name__}: {exc}"
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
    ) -> AbstractContextManager[SemanticEngine]:
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

    def _open_engine(self, corpus: Corpus, generation: ActiveGeneration) -> SemanticEngine:
        try:
            generation_store_provenance(generation)
        except IndexUnavailableError as exc:
            raise RetrievalError(str(exc)) from exc
        return _open_engine(
            engine_config(
                corpus,
                generation.semantic_path,
                self.policy,
                provider=generation.manifest.embedding_provider,
                model=generation.manifest.embedding_model,
            ),
            error=RetrievalError,
        )


def _close_engine(engine: SemanticEngine) -> None:
    engine.close()


def generation_store_provenance(generation: ActiveGeneration) -> StoreProvenance:
    """Verify the store a semantic generation points at without loading a model."""

    manifest = generation.manifest
    if manifest.embedding_dimension is None:
        raise IndexInvalidError(
            f"Generation {manifest.generation_id} has no semantic embedding dimension"
        )
    expected = StoreProvenance(manifest.embedding_model, manifest.embedding_dimension)
    try:
        recorded = read_provenance(generation.semantic_path)
    except SemanticStoreError as exc:
        raise IndexInvalidError(str(exc)) from exc
    if recorded != expected:
        raise IndexInvalidError(
            f"Semantic store {generation.semantic_path} records {recorded.describe()} but "
            f"generation {manifest.generation_id} expects {expected.describe()}"
        )
    return expected


def _create_engine(config: Config) -> SemanticEngine:
    try:
        return SemanticEngine.create(config)
    except Exception as exc:
        raise IndexBuildError(
            f"Cannot create semantic store {config.store_dir}: {type(exc).__name__}: {exc}"
        ) from exc


def _open_engine(config: Config, *, error: type[CodalithError]) -> SemanticEngine:
    """Open an existing store read-only; the engine refuses another embedding model."""

    try:
        return SemanticEngine.open(config)
    except SemanticStoreError as exc:
        raise error(str(exc)) from exc
    except Exception as exc:
        raise error(
            f"Cannot open semantic store {config.store_dir} with provider "
            f"{config.provider!r}: {type(exc).__name__}: {exc}"
        ) from exc


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
    config = engine_config(corpus, store_dir, policy)
    source_view: tempfile.TemporaryDirectory[str] | None = None
    try:
        if mode == "build":
            source_view = tempfile.TemporaryDirectory(
                prefix=".codalith-source-",
                dir=_filesystem_path(corpus.index_root),
            )
            view_root = Path(source_view.name)
            _materialize_source_view(corpus.source_root, view_root, tuple(indexed_files))
            engine = _create_engine(config)
        else:
            engine = _open_engine(config, error=IndexBuildError)
        try:
            if mode == "build":
                engine.index(view_root)
            provenance = engine.provenance
            stats = engine.stats()
            stored = engine.file_hashes()
        except Exception as exc:
            raise IndexBuildError(
                f"Cannot {mode} semantic store for {corpus.corpus_id}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        finally:
            engine.close()
    finally:
        if source_view is not None:
            source_view.cleanup()
    if stats.total_files <= 0 or stats.total_chunks <= 0:
        raise IndexBuildError("Semantic store is empty")
    if mode == "build":
        covered_files = _verify_built_store(corpus.source_root, stored, indexed_files)
        extra_files = 0
    else:
        covered_files, extra_files = _verify_adopted_store(stored, indexed_files)
    return SemanticBuildInfo(
        provider=config.provider,
        model=provenance.model,
        dimension=provenance.dimension,
        total_files=stats.total_files,
        total_chunks=stats.total_chunks,
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

    _validate_semantic_request(corpus, mode=mode, store_dir=corpus.semantic_store)
    if mode != "adopt" or corpus.semantic_store is None:
        return
    engine = _open_engine(
        engine_config(corpus, corpus.semantic_store, policy),
        error=IndexBuildError,
    )
    try:
        stats = engine.stats()
    except Exception as exc:
        raise IndexBuildError(
            f"Cannot preflight semantic store for {corpus.corpus_id}: {type(exc).__name__}: {exc}"
        ) from exc
    finally:
        engine.close()
    if stats.total_files <= 0 or stats.total_chunks <= 0:
        raise IndexBuildError("Semantic store is empty")


def _validate_semantic_request(
    corpus: Corpus,
    *,
    mode: str,
    store_dir: Path | None,
) -> None:
    if mode not in {"build", "adopt"}:
        raise ValueError("Semantic mode must be build or adopt")
    if mode == "adopt":
        if store_dir is None:
            raise IndexBuildError("Semantic adopt mode requires corpus.semantic_store")
        if not store_dir.is_dir():
            raise IndexBuildError(f"Semantic store does not exist: {store_dir}")


def _verify_built_store(
    source_root: Path,
    stored: Mapping[str, str],
    indexed_files: Mapping[str, str],
) -> int:
    outside = sorted(set(stored) - set(indexed_files))
    if outside:
        raise IndexBuildError(
            f"The semantic store holds {len(outside)} file(s) outside the structural snapshot: "
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
            f"The semantic index skipped {len(skipped)} indexable source file(s): "
            f"{', '.join(skipped[:5])}"
        )
    return len(stored)


def _verify_adopted_store(
    stored: Mapping[str, str],
    indexed_files: Mapping[str, str],
) -> tuple[int, int]:
    common = stored.keys() & indexed_files.keys()
    if not common:
        raise IndexBuildError("Adopted semantic store covers no file in the structural snapshot")
    drifted = sorted(path for path in common if stored[path] != indexed_files[path])
    if drifted:
        raise IndexBuildError(
            f"Adopted semantic store content differs from the structural snapshot for "
            f"{len(drifted)} file(s): {', '.join(drifted[:5])}"
        )
    return len(common), len(stored) - len(common)


def _has_indexable_text(path: Path) -> bool:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise IndexBuildError(f"Indexed source file disappeared: {path}") from exc
    return has_indexable_text(data)


def engine_config(
    corpus: Corpus,
    store_dir: Path,
    policy: SourcePolicy,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> Config:
    model = corpus.embedding_model if model is None else model
    return Config(
        store_dir=store_dir,
        provider=provider or corpus.embedding_provider,
        model=model or None,
        openai_api_key=os.getenv("CODALITH_EMBEDDING_API_KEY"),
        openai_base_url=os.getenv("CODALITH_EMBEDDING_BASE_URL"),
        max_file_bytes=policy.max_file_bytes,
    )


def store_fingerprint(store_dir: Path) -> str:
    """Hash every semantic store byte so provenance detects same-size corruption."""

    if not store_dir.is_dir():
        raise IndexBuildError(f"Semantic store does not exist: {store_dir}")
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


def _materialize_source_view(
    source_root: Path,
    view_root: Path,
    indexed_paths: tuple[str, ...],
) -> None:
    """Create a temporary link tree containing exactly one structural generation."""

    resolved_root = _filesystem_path(source_root).resolve()
    for relative in indexed_paths:
        source = (resolved_root / relative).resolve()
        if resolved_root not in source.parents:
            raise IndexBuildError(f"Indexed path escapes source root: {relative}")
        source_path = _filesystem_path(source)
        if not source_path.is_file():
            raise IndexBuildError(f"Indexed source file disappeared: {relative}")
        destination = _filesystem_path(view_root / relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source_path, destination)
        except OSError:
            try:
                destination.symlink_to(source_path)
            except OSError:
                try:
                    shutil.copy2(source_path, destination)
                except OSError as exc:
                    raise IndexBuildError(
                        f"Cannot materialize source for semantic indexing: {relative}"
                    ) from exc


def _filesystem_path(path: Path) -> Path:
    if os.name != "nt":
        return path
    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)
