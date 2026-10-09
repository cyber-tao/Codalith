"""Immutable index-generation manifests and atomic activation."""

from __future__ import annotations

import functools
import hashlib
import importlib.metadata
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codalith.corpus.globs import SOURCE_SELECTION_VERSION
from codalith.corpus.registry import Corpus
from codalith.corpus.source_policy import SourcePolicy
from codalith.errors import IndexBuildError, IndexInvalidError, IndexMissingError

MANIFEST_SCHEMA_VERSION = 3
STRUCTURE_SCHEMA_VERSION = 1
GENERATION_STORE_DIR = "coderag"
_GENERATION_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$")
_FileStamp = tuple[int, int, int]
_REPLACE_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class Artifact:
    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class IndexManifest:
    schema_version: int
    generation_id: str
    corpus_id: str
    source_revision: str
    source_fingerprint: str
    created_at: str
    coderag_version: str
    embedding_provider: str
    embedding_model: str
    embedding_dimension: int | None
    coderag_store_fingerprint: str | None
    coderag_store: str | None
    coderag_covered_files: int | None
    coderag_extra_files: int | None
    source_selection_hash: str
    adapter: str
    adapter_version: int
    structure_schema_version: int
    files: int
    symbols: int
    references: int
    module_dependencies: int
    semantic_available: bool
    artifacts: tuple[Artifact, ...]

    @classmethod
    def load(cls, path: Path) -> IndexManifest:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise IndexInvalidError(f"Cannot read index manifest {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise IndexInvalidError(f"Index manifest {path} must be an object")
        if raw.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            raise IndexInvalidError(
                f"Unsupported index manifest schema {raw.get('schema_version')!r} in {path}; "
                "rebuild the generation"
            )
        payload = dict(raw)
        try:
            raw_artifacts = payload.pop("artifacts")
            if not isinstance(raw_artifacts, list):
                raise TypeError("artifacts must be an array")
            artifacts = tuple(Artifact(**item) for item in raw_artifacts)
            manifest = cls(artifacts=artifacts, **payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise IndexInvalidError(f"Invalid index manifest {path}: {exc}") from exc
        manifest.validate()
        return manifest

    def validate(self) -> None:
        if self.schema_version != MANIFEST_SCHEMA_VERSION:
            raise IndexInvalidError(
                f"Unsupported index manifest schema: {self.schema_version}"
            )
        if self.structure_schema_version != STRUCTURE_SCHEMA_VERSION:
            raise IndexInvalidError(
                f"Unsupported structure schema: {self.structure_schema_version}"
            )
        if not _GENERATION_ID.fullmatch(self.generation_id):
            raise IndexInvalidError(f"Invalid generation id: {self.generation_id}")
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in self.counts.values()
        ):
            raise IndexInvalidError("Index manifest counts cannot be negative")
        if not self.corpus_id or not self.source_revision or not self.source_fingerprint:
            raise IndexInvalidError("Index manifest provenance cannot be blank")
        if not re.fullmatch(r"[0-9a-f]{64}", self.source_fingerprint):
            raise IndexInvalidError("Invalid source fingerprint")
        try:
            created_at = datetime.fromisoformat(self.created_at)
        except (TypeError, ValueError) as exc:
            raise IndexInvalidError("Invalid manifest creation timestamp") from exc
        if created_at.utcoffset() is None:
            raise IndexInvalidError("Manifest creation timestamp must include a timezone")
        if not isinstance(self.adapter_version, int) or isinstance(self.adapter_version, bool):
            raise IndexInvalidError("Invalid adapter version")
        if self.adapter_version < 1:
            raise IndexInvalidError("Invalid adapter version")
        if self.semantic_available:
            if self.embedding_dimension is None or self.embedding_dimension <= 0:
                raise IndexInvalidError("Semantic index has no embedding dimension")
            if not self.coderag_store_fingerprint or not re.fullmatch(
                r"[0-9a-f]{64}", self.coderag_store_fingerprint
            ):
                raise IndexInvalidError("Semantic index has no valid store fingerprint")
            if not self.coderag_store or (
                self.coderag_store != GENERATION_STORE_DIR
                and not Path(self.coderag_store).is_absolute()
            ):
                raise IndexInvalidError("Semantic index has no valid store location")
            covered = self.coderag_covered_files
            extra = self.coderag_extra_files
            if (
                not isinstance(covered, int)
                or isinstance(covered, bool)
                or not 1 <= covered <= self.files
                or not isinstance(extra, int)
                or isinstance(extra, bool)
                or extra < 0
            ):
                raise IndexInvalidError("Semantic index has invalid coverage counts")
        elif any(
            value is not None
            for value in (
                self.embedding_dimension,
                self.coderag_store_fingerprint,
                self.coderag_store,
                self.coderag_covered_files,
                self.coderag_extra_files,
            )
        ):
            raise IndexInvalidError("Non-semantic generation contains semantic provenance")
        if not self.artifacts:
            raise IndexInvalidError("Index manifest has no artifacts")
        seen_artifacts: set[str] = set()
        for artifact in self.artifacts:
            artifact_path = Path(artifact.path)
            if (
                artifact_path.is_absolute()
                or not artifact.path
                or any(part in {"", ".", ".."} for part in artifact_path.parts)
                or "\\" in artifact.path
            ):
                raise IndexInvalidError(f"Unsafe artifact path: {artifact.path}")
            if artifact.path in seen_artifacts:
                raise IndexInvalidError(f"Duplicate artifact path: {artifact.path}")
            seen_artifacts.add(artifact.path)
            if not re.fullmatch(r"[0-9a-f]{64}", artifact.sha256):
                raise IndexInvalidError(f"Invalid artifact hash: {artifact.path}")
            if (
                not isinstance(artifact.size_bytes, int)
                or isinstance(artifact.size_bytes, bool)
                or artifact.size_bytes < 0
            ):
                raise IndexInvalidError(f"Invalid artifact size: {artifact.path}")
        if "structure.sqlite" not in seen_artifacts:
            raise IndexInvalidError("Index manifest has no structure.sqlite artifact")
        if self.semantic_available and "coderag-meta.json" not in seen_artifacts:
            raise IndexInvalidError("Semantic manifest has no coderag-meta.json artifact")

    @property
    def counts(self) -> dict[str, int]:
        return {
            "files": self.files,
            "symbols": self.symbols,
            "references": self.references,
            "module_dependencies": self.module_dependencies,
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ActiveGeneration:
    root: Path
    manifest_path: Path
    structure_path: Path
    coderag_path: Path
    manifest: IndexManifest


class GenerationRepository:
    def __init__(self, policy: SourcePolicy) -> None:
        self.policy = policy
        self._resolved: dict[Corpus, tuple[_FileStamp, _FileStamp, ActiveGeneration]] = {}

    def active(self, corpus: Corpus, *, verify_artifacts: bool = False) -> ActiveGeneration:
        pointer_stamp = _file_stamp(corpus.index_root / "current.json")
        cached = self._resolved.get(corpus)
        if (
            cached is not None
            and pointer_stamp is not None
            and cached[0] == pointer_stamp
            and cached[1] == _file_stamp(cached[2].manifest_path)
        ):
            generation = cached[2]
        else:
            generation = self._resolve(corpus)
            manifest_stamp = _file_stamp(generation.manifest_path)
            if pointer_stamp is not None and manifest_stamp is not None:
                self._resolved[corpus] = (pointer_stamp, manifest_stamp, generation)
        self._check_artifacts(corpus, generation)
        if verify_artifacts:
            self.verify(generation)
        return generation

    def _resolve(self, corpus: Corpus) -> ActiveGeneration:
        pointer_path = corpus.index_root / "current.json"
        try:
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            generation_id = pointer["generation_id"]
        except FileNotFoundError as exc:
            raise IndexMissingError(
                f"No active index generation for {corpus.corpus_id}; build one first"
            ) from exc
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise IndexInvalidError(
                f"Unreadable active generation pointer for {corpus.corpus_id}: {exc}"
            ) from exc
        if not isinstance(generation_id, str) or not _GENERATION_ID.fullmatch(generation_id):
            raise IndexInvalidError(f"Invalid active generation for {corpus.corpus_id}")
        root = corpus.index_root / "generations" / generation_id
        manifest_path = root / "manifest.json"
        manifest = IndexManifest.load(manifest_path)
        if manifest.generation_id != generation_id:
            raise IndexInvalidError("Generation pointer and manifest disagree")
        if manifest.corpus_id != corpus.corpus_id:
            raise IndexInvalidError("Index manifest belongs to another corpus")
        if manifest.source_revision != corpus.revision:
            raise IndexInvalidError(
                f"Index revision {manifest.source_revision!r} does not match "
                f"configured revision {corpus.revision!r}"
            )
        from codalith.languages import create_adapter

        adapter = create_adapter(corpus.adapter)
        if manifest.adapter != adapter.adapter_id or manifest.adapter_version != adapter.version:
            raise IndexInvalidError(
                f"Index adapter {manifest.adapter}@{manifest.adapter_version} does not match "
                f"configured adapter {adapter.adapter_id}@{adapter.version}"
            )
        if manifest.source_selection_hash != source_selection_hash(corpus, self.policy):
            raise IndexInvalidError(
                "Index source selection does not match the corpus configuration and "
                "source policy; rebuild the generation"
            )
        if manifest.embedding_provider != corpus.embedding_provider:
            raise IndexInvalidError(
                "Index embedding provider does not match corpus configuration"
            )
        if corpus.embedding_model and manifest.embedding_model != corpus.embedding_model:
            raise IndexInvalidError("Index embedding model does not match corpus configuration")
        if manifest.semantic_available:
            installed = installed_coderag_version()
            if manifest.coderag_version != installed:
                raise IndexInvalidError(
                    f"Semantic index was built with CodeRAG {manifest.coderag_version}; "
                    f"installed CodeRAG is {installed}"
                )
        return ActiveGeneration(
            root=root,
            manifest_path=manifest_path,
            structure_path=root / "structure.sqlite",
            coderag_path=(
                Path(manifest.coderag_store)
                if manifest.coderag_store and manifest.coderag_store != GENERATION_STORE_DIR
                else root / GENERATION_STORE_DIR
            ),
            manifest=manifest,
        )

    def _check_artifacts(self, corpus: Corpus, generation: ActiveGeneration) -> None:
        if not generation.structure_path.is_file():
            raise IndexInvalidError(f"Missing structure index: {generation.structure_path}")
        for artifact in generation.manifest.artifacts:
            artifact_path = generation.root / artifact.path
            try:
                size = artifact_path.stat().st_size
            except OSError as exc:
                raise IndexInvalidError(f"Missing index artifact: {artifact_path}") from exc
            if (
                artifact_path.is_symlink()
                or not artifact_path.is_file()
                or size != artifact.size_bytes
            ):
                raise IndexInvalidError(f"Index artifact size mismatch: {artifact_path}")
        if generation.manifest.semantic_available and not generation.coderag_path.is_dir():
            raise IndexInvalidError(
                f"Missing CodeRAG store for {corpus.corpus_id}: {generation.coderag_path}"
            )

    def verify(self, generation: ActiveGeneration) -> None:
        for artifact in generation.manifest.artifacts:
            path = generation.root / artifact.path
            if not path.is_file():
                raise IndexInvalidError(f"Missing index artifact: {path}")
            if path.stat().st_size != artifact.size_bytes:
                raise IndexInvalidError(f"Index artifact size mismatch: {path}")
            if sha256_file(path) != artifact.sha256:
                raise IndexInvalidError(f"Index artifact hash mismatch: {path}")
        try:
            connection = sqlite3.connect(
                f"file:{generation.structure_path.as_posix()}?mode=ro",
                uri=True,
            )
            try:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()
                if integrity is None or integrity[0] != "ok":
                    raise IndexInvalidError(
                        f"Structure index integrity check failed: {integrity}"
                    )
                counts: dict[str, int] = {}
                for table in (
                    "files",
                    "symbols",
                    "references",
                    "module_dependencies",
                ):
                    quoted = '"references"' if table == "references" else table
                    row = connection.execute(f"SELECT count(*) FROM {quoted}").fetchone()
                    if row is None:
                        raise IndexInvalidError(
                            f"Cannot count structure index table: {table}"
                        )
                    counts[table] = int(row[0])
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise IndexInvalidError(
                f"Cannot verify structure index {generation.structure_path}: {exc}"
            ) from exc
        if counts != generation.manifest.counts:
            raise IndexInvalidError(
                "Structure index count snapshot does not match the generation manifest"
            )

    def publish(self, corpus: Corpus, generation_root: Path, manifest: IndexManifest) -> None:
        if generation_root.name != manifest.generation_id:
            raise IndexBuildError("Generation directory and manifest id disagree")
        corpus.index_root.mkdir(parents=True, exist_ok=True)
        pointer = corpus.index_root / "current.json"
        temporary = corpus.index_root / f".current.json.{manifest.generation_id}.tmp"
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps({"generation_id": manifest.generation_id}, indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            _replace_with_retry(temporary, pointer)
        finally:
            temporary.unlink(missing_ok=True)
        fsync_directory(corpus.index_root)


@functools.cache
def installed_coderag_version() -> str:
    try:
        return importlib.metadata.version("coderag")
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def _file_stamp(path: Path) -> _FileStamp | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_ino, stat.st_mtime_ns, stat.st_size


def _replace_with_retry(source: Path, destination: Path) -> None:
    """Windows refuses to replace a file that a reader holds open, so wait out brief reads."""

    deadline = time.monotonic() + _REPLACE_TIMEOUT_SECONDS
    delay = 0.005
    while True:
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.1)


def fsync_directory(path: Path) -> None:
    if sys.platform == "win32":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def new_generation_id(seed: bytes) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{hashlib.sha256(seed).hexdigest()[:12]}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_selection_hash(corpus: Corpus, policy: SourcePolicy) -> str:
    payload = json.dumps(
        {
            "adapter": corpus.adapter,
            "extensions": corpus.include_extensions,
            "exclude_globs": corpus.exclude_globs,
            "deny_globs": policy.deny_globs,
            "max_file_bytes": policy.max_file_bytes,
            "selection_semantics": SOURCE_SELECTION_VERSION,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
