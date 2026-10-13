"""Build and atomically publish index generations."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from codalith.corpus.generations import (
    GENERATION_STORE_DIR,
    MANIFEST_SCHEMA_VERSION,
    SEMANTIC_METADATA_ARTIFACT,
    STRUCTURE_SCHEMA_VERSION,
    Artifact,
    GenerationRepository,
    IndexManifest,
    fsync_directory,
    new_generation_id,
    sha256_file,
    source_selection_hash,
)
from codalith.corpus.registry import Corpus
from codalith.corpus.source_policy import SourcePolicy
from codalith.errors import IndexBuildError
from codalith.indexing.semantic import (
    preflight_semantic_index,
    prepare_semantic_index,
    write_semantic_metadata,
)
from codalith.indexing.structure.writer import write_structure
from codalith.languages.catalog import ADAPTERS
from codalith.semantic import ENGINE_VERSION


@dataclass(frozen=True, slots=True)
class BuildReport:
    corpus_id: str
    generation_id: str
    files: int
    symbols: int
    references: int
    module_dependencies: int
    warnings: tuple[str, ...]
    source_fingerprint: str
    semantic_available: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class GenerationBuilder:
    """Build the structural and semantic planes of one generation, then publish it."""

    def __init__(self, policy: SourcePolicy) -> None:
        self.policy = policy
        self.generations = GenerationRepository(policy)

    def build(
        self,
        corpus: Corpus,
        *,
        semantic_mode: str = "none",
        progress: Callable[[str], None] | None = None,
    ) -> BuildReport:
        if semantic_mode not in {"none", "build", "adopt"}:
            raise IndexBuildError("semantic_mode must be none, build, or adopt")
        if not corpus.source_root.is_dir():
            raise IndexBuildError(f"Corpus source root does not exist: {corpus.source_root}")
        with build_lock(corpus.index_root):
            return self._build(corpus, semantic_mode=semantic_mode, progress=progress)

    def _build(
        self,
        corpus: Corpus,
        *,
        semantic_mode: str,
        progress: Callable[[str], None] | None,
    ) -> BuildReport:
        if semantic_mode != "none":
            _report(progress, f"Preflighting semantic index in {semantic_mode} mode")
            preflight_semantic_index(corpus, self.policy, mode=semantic_mode)
        seed = f"{corpus.corpus_id}\0{corpus.revision}\0{time.time_ns()}".encode()
        generation_id = new_generation_id(seed)
        generations_root = corpus.index_root / "generations"
        generations_root.mkdir(parents=True, exist_ok=True)
        staging = generations_root / f".build-{generation_id}"
        final = generations_root / generation_id
        if staging.exists() or final.exists():
            raise IndexBuildError(f"Generation already exists: {generation_id}")
        staging.mkdir()
        database = staging / "structure.sqlite"
        try:
            _report(progress, f"Scanning structural source for {corpus.corpus_id}")
            structure = write_structure(corpus, self.policy, database, progress=progress)
            counts = structure.counts
            _report(
                progress,
                f"Structural index complete: {counts['files']} files, "
                f"{counts['symbols']} symbols, {counts['references']} references",
            )
            artifacts = [Artifact(
                path="structure.sqlite",
                sha256=sha256_file(database),
                size_bytes=database.stat().st_size,
            )]
            semantic_info = None
            recorded_store: str | None = None
            if semantic_mode != "none":
                _report(progress, f"Preparing semantic index in {semantic_mode} mode")
                if semantic_mode == "build":
                    semantic_store = staging / GENERATION_STORE_DIR
                    recorded_store = GENERATION_STORE_DIR
                elif corpus.semantic_store is not None:
                    semantic_store = corpus.semantic_store.resolve()
                    recorded_store = str(semantic_store)
                else:
                    raise IndexBuildError("Semantic adopt mode requires corpus.semantic_store")
                semantic_info = prepare_semantic_index(
                    corpus,
                    semantic_store,
                    self.policy,
                    mode=semantic_mode,
                    indexed_files=_indexed_files(database),
                )
                _report(progress, "Semantic provenance verified")
                semantic_metadata = staging / SEMANTIC_METADATA_ARTIFACT
                write_semantic_metadata(semantic_metadata, semantic_info)
                artifacts.append(
                    Artifact(
                        path=SEMANTIC_METADATA_ARTIFACT,
                        sha256=sha256_file(semantic_metadata),
                        size_bytes=semantic_metadata.stat().st_size,
                    )
                )
            _verify_indexed_sources(corpus, database)
            _report(progress, "Indexed source hashes verified")
            manifest = IndexManifest(
                schema_version=MANIFEST_SCHEMA_VERSION,
                generation_id=generation_id,
                corpus_id=corpus.corpus_id,
                source_revision=corpus.revision,
                source_fingerprint=structure.source_fingerprint,
                created_at=datetime.now(UTC).isoformat(),
                semantic_engine_version=ENGINE_VERSION,
                embedding_provider=(
                    semantic_info.provider if semantic_info else corpus.embedding_provider
                ),
                embedding_model=(
                    semantic_info.model if semantic_info else corpus.embedding_model
                ),
                embedding_dimension=semantic_info.dimension if semantic_info else None,
                semantic_store_fingerprint=(
                    semantic_info.store_fingerprint if semantic_info else None
                ),
                semantic_store=recorded_store,
                semantic_covered_files=semantic_info.covered_files if semantic_info else None,
                semantic_extra_files=semantic_info.extra_files if semantic_info else None,
                source_selection_hash=source_selection_hash(corpus, self.policy),
                adapter=corpus.adapter,
                adapter_version=ADAPTERS[corpus.adapter].version,
                structure_schema_version=STRUCTURE_SCHEMA_VERSION,
                files=counts["files"],
                symbols=counts["symbols"],
                references=counts["references"],
                module_dependencies=counts["module_dependencies"],
                semantic_available=semantic_info is not None,
                artifacts=tuple(artifacts),
            )
            manifest.validate()
            (staging / "manifest.json").write_text(
                json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            _fsync_tree(staging)
            os.replace(staging, final)
            fsync_directory(generations_root)
            self.generations.publish(corpus, final, manifest)
        except BaseException:
            if staging.exists():
                shutil.rmtree(staging)
            raise
        return BuildReport(
            corpus_id=corpus.corpus_id,
            generation_id=generation_id,
            files=counts["files"],
            symbols=counts["symbols"],
            references=counts["references"],
            module_dependencies=counts["module_dependencies"],
            warnings=structure.warnings,
            source_fingerprint=structure.source_fingerprint,
            semantic_available=semantic_info is not None,
        )


@contextmanager
def build_lock(index_root: Path) -> Iterator[None]:
    """Allow one build per index root; the OS releases the lock if the process dies."""

    index_root.mkdir(parents=True, exist_ok=True)
    lock_path = index_root / ".build.lock"
    with lock_path.open("a+b") as handle:
        try:
            _lock_file(handle.fileno())
        except OSError as exc:
            raise IndexBuildError(f"Another index build holds {lock_path}") from exc
        try:
            yield
        finally:
            _unlock_file(handle.fileno())


if sys.platform == "win32":
    import msvcrt

    def _lock_file(descriptor: int) -> None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)

    def _unlock_file(descriptor: int) -> None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock_file(descriptor: int) -> None:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_file(descriptor: int) -> None:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


def _fsync_tree(root: Path) -> None:
    for directory, _, filenames in os.walk(root):
        for filename in filenames:
            descriptor = os.open(Path(directory, filename), os.O_RDWR)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        fsync_directory(Path(directory))


def _indexed_files(database: Path) -> dict[str, str]:
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        return {
            str(path): str(digest)
            for path, digest in connection.execute(
                "SELECT path, sha256 FROM files ORDER BY path"
            )
        }
    finally:
        connection.close()


def _verify_indexed_sources(corpus: Corpus, database: Path) -> None:
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        expected = {
            str(path): str(digest)
            for path, digest in connection.execute("SELECT path, sha256 FROM files")
        }
    finally:
        connection.close()
    for relative, digest in expected.items():
        source = corpus.source_root / relative
        try:
            current = hashlib.sha256(source.read_bytes()).hexdigest()
        except OSError as exc:
            raise IndexBuildError(
                f"Source changed while building the index: {relative}"
            ) from exc
        if current != digest:
            raise IndexBuildError(f"Source changed while building the index: {relative}")


def _report(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)
