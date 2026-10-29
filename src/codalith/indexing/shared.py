"""Shared source objects, structural facts and immutable multi-version snapshots."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
from collections.abc import Callable, Generator, Iterator
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, Protocol, cast
from uuid import uuid4

import numpy as np

from codalith.corpus.generations import (
    MANIFEST_SCHEMA_VERSION,
    STRUCTURE_SCHEMA_VERSION,
    ActiveGeneration,
    Artifact,
    GenerationRepository,
    IndexManifest,
    fsync_directory,
    sha256_file,
    source_selection_hash,
)
from codalith.corpus.registry import Corpus, CorpusRegistry
from codalith.corpus.source_policy import SourcePolicy
from codalith.corpus.source_reader import FileCatalog, SourceReader, SourceSlice
from codalith.corpus.uris import source_uri
from codalith.errors import IndexBuildError, IndexInvalidError, RetrievalError, SourceReadError
from codalith.indexing.builder import build_lock
from codalith.indexing.leases import GenerationLeases
from codalith.indexing.semantic import SemanticBackend, SemanticHit, engine_config
from codalith.indexing.structure.store import StructureIndex, _escape_like
from codalith.indexing.structure.writer import write_structure
from codalith.indexing.text_search import TextHit
from codalith.languages import create_adapter
from codalith.languages.base import (
    ExtractionResult,
    ModuleDependencyObservation,
    ReferenceObservation,
    SymbolObservation,
)
from codalith.languages.catalog import ADAPTERS, retrieval_profile
from codalith.query.service import QueryService
from codalith.semantic import ENGINE_VERSION
from codalith.semantic.chunking import chunk_file
from codalith.semantic.config import Config
from codalith.semantic.embeddings import EmbeddingProvider, get_provider
from codalith.semantic.indexer import has_indexable_text
from codalith.semantic.shared import SharedVectors, SharedVectorSnapshot
from codalith.semantic.types import Chunk

_TABLES = ("files", "symbols", "references", "module_dependencies")
_SCHEMA_VERSION = 2
_VECTOR_FLUSH_ROWS = 1_024
_SQL_BATCH_ROWS = 400
_ANALYZE_ROWS = 1_000
Progress = Callable[[dict[str, object]], None]


class GitSource(Protocol):
    def files(self, commit: str) -> list[Any]: ...
    def read_blob(self, oid: str) -> bytes: ...


class SharedBuildCancelled(IndexBuildError):
    pass


@dataclass(frozen=True, slots=True)
class SharedBuildResult:
    snapshot_id: str
    target_id: str
    generation_id: str
    corpus: Corpus
    counts: dict[str, int]
    reused_vectors: int
    new_vectors: int
    statistics: dict[str, int]


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: object) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _git(
    root: Path,
    *args: str,
    data: bytes | None = None,
    stdin: IO[bytes] | None = None,
) -> bytes:
    env = {
        **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
    }
    command = ["git", "--no-replace-objects", "--git-dir", str(root), *args]
    result = subprocess.run(
        command, input=data, stdin=stdin, capture_output=True, env=env, timeout=120, check=False
    )
    if result.returncode:
        raise IndexBuildError(result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout


def _quoted_git_path(path: str) -> bytes:
    result = bytearray(b'"')
    for byte in path.encode("utf-8"):
        if byte in (34, 92):
            result.extend(b"\\" + bytes([byte]))
        elif byte < 32 or byte >= 127:
            result.extend(f"\\{byte:03o}".encode())
        else:
            result.append(byte)
    return bytes(result) + b'"'


class SharedPool:
    def __init__(self, root: Path, policy: SourcePolicy) -> None:
        self.root = root.resolve()
        self.policy = policy
        self.catalog = self.root / "catalog.sqlite"
        self.source_git = self.root / "sources.git"
        self.root.mkdir(parents=True, exist_ok=True)
        if self.catalog.is_file():
            _ = self.schema_version
            return
        with build_lock(self.root):
            if self.catalog.is_file():
                _ = self.schema_version
                return
            with tempfile.TemporaryDirectory(prefix=".catalog-init-", dir=self.root) as temporary:
                staged_catalog = Path(temporary) / "catalog.sqlite"
                self._initialize(staged_catalog)
                os.replace(staged_catalog, self.catalog)
                fsync_directory(self.root)

    def _initialize(self, catalog: Path) -> None:
        with closing(sqlite3.connect(catalog)) as connection, connection:
            connection.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS pool_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS snapshots(
                    snapshot_id TEXT NOT NULL,target_id TEXT PRIMARY KEY,
                    metadata TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS snapshots_by_id ON snapshots(snapshot_id);
                CREATE TABLE IF NOT EXISTS source_objects(
                    sha256 TEXT PRIMARY KEY,git_oid TEXT NOT NULL,size_bytes INTEGER NOT NULL);
                CREATE VIRTUAL TABLE IF NOT EXISTS source_text_search USING fts5(
                    text,content='',tokenize='trigram',columnsize=0);
                CREATE TABLE IF NOT EXISTS source_text_indexed(sha256 TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS snapshot_sources(
                    snapshot_id TEXT NOT NULL,path TEXT NOT NULL,sha256 TEXT NOT NULL,
                    PRIMARY KEY(snapshot_id,path));
                CREATE TABLE IF NOT EXISTS extraction_cache(key TEXT PRIMARY KEY,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS extraction_objects(key TEXT PRIMARY KEY,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS chunk_cache(key TEXT PRIMARY KEY,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS chunks(
                    id INTEGER PRIMARY KEY,namespace TEXT NOT NULL,input_hash TEXT NOT NULL,
                    stored INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(namespace,input_hash));
                CREATE TABLE IF NOT EXISTS occurrences(
                    snapshot_id TEXT NOT NULL,chunk_id INTEGER NOT NULL,path TEXT NOT NULL,
                    start_line INTEGER NOT NULL,end_line INTEGER NOT NULL,symbol TEXT,
                    kind TEXT NOT NULL,language TEXT NOT NULL,
                    PRIMARY KEY(snapshot_id,chunk_id,path,start_line,end_line));
                CREATE INDEX IF NOT EXISTS occurrences_lookup
                    ON occurrences(snapshot_id,chunk_id);
            """)
            self._upgrade_schema(connection)
            connection.execute(
                "INSERT INTO pool_metadata VALUES('schema',?)", (str(_SCHEMA_VERSION),)
            )

    @contextmanager
    def _connect(self, *, readonly: bool = False) -> Iterator[sqlite3.Connection]:
        location = self.catalog.as_uri() + "?mode=ro" if readonly else str(self.catalog)
        connection = sqlite3.connect(
            location, uri=readonly, timeout=60, check_same_thread=False
        )
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @property
    def schema_version(self) -> int:
        with self._connect(readonly=True) as connection:
            row = connection.execute(
                "SELECT value FROM pool_metadata WHERE key='schema'"
            ).fetchone()
        if row is None or row[0] not in {"1", str(_SCHEMA_VERSION)}:
            raise IndexInvalidError("Unsupported shared pool schema")
        return int(row[0])

    def upgrade(self) -> dict[str, object]:
        with build_lock(self.root):
            previous = self.schema_version
            if previous == _SCHEMA_VERSION:
                return {
                    "previous_version": previous,
                    "schema_version": _SCHEMA_VERSION,
                    "backup_path": None,
                }
            backups = self.root / "backups"
            backups.mkdir(exist_ok=True)
            backup = backups / f"catalog-v{previous}-{uuid4().hex}.sqlite"
            with self._connect() as connection:
                with closing(sqlite3.connect(backup)) as destination:
                    connection.backup(destination)
                connection.execute("BEGIN IMMEDIATE")
                self._upgrade_schema(connection)
                connection.execute(
                    "UPDATE pool_metadata SET value=? WHERE key='schema'",
                    (str(_SCHEMA_VERSION),),
                )
                connection.execute("ANALYZE")
            return {
                "previous_version": previous,
                "schema_version": _SCHEMA_VERSION,
                "backup_path": str(backup),
            }

    @staticmethod
    def _upgrade_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TABLE source_blob_map("
            "object_format TEXT NOT NULL,source_oid TEXT NOT NULL,"
            "sha256 TEXT NOT NULL,git_oid TEXT NOT NULL,size_bytes INTEGER NOT NULL,"
            "PRIMARY KEY(object_format,source_oid))"
        )
        connection.execute(
            "INSERT INTO source_blob_map "
            "SELECT 'sha1',git_oid,sha256,git_oid,size_bytes FROM source_objects"
        )
        for table in ("files", "symbols"):
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (f"facts_{table}",),
            ).fetchone():
                connection.execute(
                    f"CREATE INDEX facts_{table}_path_binary ON facts_{table}(path COLLATE BINARY)"
                )

    @staticmethod
    def _analyze(connection: sqlite3.Connection) -> None:
        connection.execute(f"PRAGMA analysis_limit={_ANALYZE_ROWS}")
        connection.execute("ANALYZE")

    def storage_stats(self) -> dict[str, int]:
        with self._connect() as connection:
            unique_source = int(
                connection.execute(
                    "SELECT COALESCE(sum(size_bytes),0) FROM source_objects"
                ).fetchone()[0]
            )
            logical_source = int(
                connection.execute(
                    "SELECT COALESCE(sum(o.size_bytes),0) FROM snapshot_sources s "
                    "JOIN source_objects o USING(sha256) "
                    "WHERE EXISTS(SELECT 1 FROM snapshots p WHERE p.snapshot_id=s.snapshot_id)"
                ).fetchone()[0]
            )
            unique_chunks = int(
                connection.execute("SELECT count(*) FROM chunks WHERE stored=1").fetchone()[0]
            )
        physical = source_bytes = semantic_bytes = index_bytes = catalog_bytes = 0
        for directory, _, names in os.walk(self.root):
            relative = Path(directory).relative_to(self.root)
            for name in names:
                try:
                    size = (Path(directory) / name).stat().st_size
                except FileNotFoundError:
                    continue
                physical += size
                if relative.parts and relative.parts[0] == "sources.git":
                    source_bytes += size
                if relative.parts and relative.parts[0] == "semantic":
                    semantic_bytes += size
                    if "_indices" in relative.parts:
                        index_bytes += size
                if not relative.parts and name.startswith("catalog.sqlite"):
                    catalog_bytes += size
        return {
            "physical_bytes": physical,
            "source_physical_bytes": source_bytes,
            "semantic_physical_bytes": semantic_bytes,
            "index_physical_bytes": index_bytes,
            "catalog_physical_bytes": catalog_bytes,
            "source_unique_logical_bytes": unique_source,
            "source_all_versions_logical_bytes": logical_source,
            "unique_vectors": unique_chunks,
            "published_snapshots": len(self.snapshots()),
        }

    def gc(self, *, dry_run: bool = True) -> dict[str, object]:
        if not dry_run:
            raise IndexBuildError("Shared pool garbage collection currently supports dry-run only")
        with self._connect() as connection:
            source_orphans = int(
                connection.execute(
                    "SELECT count(*) FROM source_objects o WHERE NOT EXISTS("
                    "SELECT 1 FROM snapshot_sources s JOIN snapshots p USING(snapshot_id) "
                    "WHERE s.sha256=o.sha256)"
                ).fetchone()[0]
            )
            vector_orphans = int(
                connection.execute(
                    "SELECT count(*) FROM chunks c WHERE NOT EXISTS("
                    "SELECT 1 FROM occurrences o JOIN snapshots p USING(snapshot_id) "
                    "WHERE o.chunk_id=c.id)"
                ).fetchone()[0]
            )
        return {
            "dry_run": True,
            "deleted_objects": 0,
            "unreferenced_source_objects": source_orphans,
            "unreferenced_vector_objects": vector_orphans,
            "protected_snapshots": len({v["snapshot_id"] for v in self.snapshots()}),
            "protected_lance_tags": sorted({v["lance_tag"] for v in self.snapshots()}),
            "note": "Published releases and task checkpoints are retained; no files removed",
        }

    def snapshots(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT metadata FROM snapshots ORDER BY created_at,target_id",
                )
            ]

    def catalog_entries(self) -> list[dict[str, Any]]:
        return self.snapshots()

    def snapshot(self, target_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT metadata FROM snapshots WHERE target_id=?", (target_id,)
            ).fetchone()
            return json.loads(row[0]) if row is not None else None

    def published_corpora(self) -> dict[str, Corpus]:
        return {item["target_id"]: self._corpus(item) for item in self.snapshots()}

    def _corpus(self, item: dict[str, Any]) -> Corpus:
        profile = dict(item["profile"])
        profile["include_extensions"] = tuple(profile["include_extensions"])
        profile["exclude_globs"] = tuple(profile["exclude_globs"])
        for name in ("source_root", "index_root", "semantic_store"):
            profile[name] = Path(profile[name]) if profile[name] is not None else None
        return Corpus(**profile)

    def build_git_snapshot(
        self,
        repository: GitSource,
        commit: str,
        target_id: str,
        profile: Corpus,
        *,
        tag: str | None = None,
        engine_version: str | None = None,
        progress: Progress | None = None,
        cancelled: Callable[[], bool] | None = None,
        legacy_generation: Path | None = None,
    ) -> SharedBuildResult:
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", target_id):
            raise IndexBuildError("Invalid target id")

        def check() -> None:
            if cancelled is not None and cancelled():
                raise SharedBuildCancelled("Shared snapshot build cancelled")

        def emit(stage: str, completed: int, total: int | None, message: str = "") -> None:
            check()
            if progress is not None:
                progress(
                    {
                        "stage": stage,
                        "completed": completed,
                        "total": total,
                        "unit": "items",
                        "message": message,
                    }
                )

        with build_lock(self.root):
            if self.schema_version != _SCHEMA_VERSION:
                raise IndexBuildError(
                    "Shared pool requires an explicit schema upgrade; "
                    "run codalith management upgrade-storage"
                )
            return self._build(
                repository,
                commit,
                target_id,
                profile,
                tag,
                engine_version,
                legacy_generation,
                check,
                emit,
            )

    def _build(
        self,
        repository: GitSource,
        commit: str,
        target_id: str,
        profile: Corpus,
        tag: str | None,
        engine_version: str | None,
        legacy_generation: Path | None,
        check: Callable[[], None],
        emit: Callable[[str, int, int | None, str], None],
    ) -> SharedBuildResult:
        existing = self.snapshot(target_id)
        if existing is not None:
            if existing["commit"] != commit:
                raise IndexBuildError("Target already pins another source commit")
            previous = self._corpus(existing)
            if (
                source_selection_hash(profile, self.policy) != existing["source_selection_hash"]
                or profile.adapter != previous.adapter
                or profile.embedding_provider != previous.embedding_provider
                or (profile.embedding_model and profile.embedding_model != previous.embedding_model)
                or existing["adapter_version"] != create_adapter(profile.adapter).version
                or existing["engine_version_id"] != ENGINE_VERSION
            ):
                raise IndexBuildError("Target already pins another indexing contract")
            return self._result(existing)
        adapter = create_adapter(profile.adapter)
        files = [
            item
            for item in repository.files(commit)
            if (
                (
                    not profile.include_extensions
                    or any(item.path.lower().endswith(ext) for ext in profile.include_extensions)
                )
                and not profile.excludes(item.path)
                and not self.policy.is_denied(item.path)
                and item.size <= self.policy.max_file_bytes
                and adapter.supports(Path(item.path))
            )
        ]
        if not files:
            raise IndexBuildError("Selected revision contains no allowed source files")
        config = engine_config(profile, self.root / "semantic", self.policy)
        provider = get_provider(config)
        dimension = provider.dim
        namespace = _digest(
            [
                provider.name,
                provider.model_id,
                dimension,
                ENGINE_VERSION,
                config.max_chunk_lines,
                config.window_lines,
                config.window_overlap,
                config.openai_base_url,
            ]
        )[:32]
        snapshot_id = _digest(
            [
                commit,
                source_selection_hash(profile, self.policy),
                profile.adapter,
                adapter.version,
                namespace,
            ]
        )[:32]
        with self._connect() as aliases:
            known = aliases.execute(
                "SELECT metadata FROM snapshots WHERE snapshot_id=? LIMIT 1",
                (snapshot_id,),
            ).fetchone()
            if known is not None:
                metadata = json.loads(known[0])
                alias = replace(
                    self._corpus(metadata),
                    corpus_id=target_id,
                    display_name=profile.display_name,
                    description=profile.description,
                )
                metadata.update(
                    target_id=target_id,
                    tag=tag,
                    reused_vectors=metadata["chunks"],
                    new_vectors=0,
                    statistics={
                        "reused_source_files": metadata["counts"]["files"],
                        "new_source_objects": 0,
                        "reused_parses": metadata["counts"]["files"],
                        "new_parses": 0,
                    },
                    profile={
                        key: str(value) if isinstance(value, Path) else value
                        for key, value in asdict(alias).items()
                    },
                )
                check()
                aliases.execute(
                    "INSERT INTO snapshots VALUES(?,?,?,?)",
                    (snapshot_id, target_id, _json(metadata), metadata["created_at"]),
                )
                return self._result(metadata)
        if not self.source_git.exists():
            result = subprocess.run(
                [
                    "git",
                    "init",
                    "--bare",
                    "--template=",
                    "--object-format=sha1",
                    str(self.source_git),
                ],
                capture_output=True,
                check=False,
            )
            if result.returncode:
                raise IndexBuildError("Cannot initialize managed source object store")
        source_rows = self._import_sources(repository, files, snapshot_id, check, emit)
        statistics = {
            "reused_source_files": 0,
            "new_source_objects": 0,
            "reused_parses": 0,
            "new_parses": 0,
        }
        emit("structure", 0, len(files), "Extracting and reusing structural facts")
        with (
            self._connect() as connection,
            tempfile.TemporaryDirectory(
                prefix="codalith-shared-",
                dir=self.root,
            ) as temporary,
        ):
            seen_sources: set[str] = set()
            for row in source_rows:
                digest = row["sha256"]
                known_source = digest in seen_sources or connection.execute(
                    "SELECT 1 FROM source_objects WHERE sha256=?", (digest,)
                ).fetchone() is not None
                statistics["reused_source_files" if known_source else "new_source_objects"] += 1
                seen_sources.add(digest)
            for table in _TABLES:
                if connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name=?", (f"members_{table}",)
                ).fetchone():
                    connection.execute(
                        f"DELETE FROM members_{table} WHERE snapshot_id=?", (snapshot_id,)
                    )
            connection.execute("DELETE FROM snapshot_sources WHERE snapshot_id=?", (snapshot_id,))
            connection.execute("DELETE FROM occurrences WHERE snapshot_id=?", (snapshot_id,))
            staging = Path(temporary) / "structure.sqlite"
            processed = 0

            def extract(path: str, text: str) -> ExtractionResult:
                nonlocal processed
                key = _digest(
                    [
                        path,
                        hashlib.sha256(text.encode()).hexdigest(),
                        profile.adapter,
                        adapter.version,
                    ]
                )
                cached = connection.execute(
                    "SELECT payload FROM extraction_cache WHERE key=?", (key,)
                ).fetchone()
                if cached is None:
                    statistics["new_parses"] += 1
                    extraction = adapter.extract(path, text)
                    payload: dict[str, Any] = {
                        "language": extraction.language,
                        "warnings": extraction.warnings,
                    }
                    for name in ("symbols", "references", "module_dependencies"):
                        object_keys = []
                        object_rows = []
                        for observation in getattr(extraction, name):
                            body = _json(asdict(observation))
                            object_key = hashlib.sha256(body.encode()).hexdigest()
                            object_rows.append((object_key, body))
                            object_keys.append(object_key)
                        connection.executemany(
                            "INSERT OR IGNORE INTO extraction_objects VALUES(?,?)", object_rows
                        )
                        payload[name] = object_keys
                    connection.execute(
                        "INSERT INTO extraction_cache VALUES(?,?)", (key, _json(payload))
                    )
                else:
                    statistics["reused_parses"] += 1
                    value = json.loads(cached[0])
                    observations: dict[str, list[dict[str, Any]]] = {}
                    object_keys = list(dict.fromkeys(
                        object_key
                        for name in ("symbols", "references", "module_dependencies")
                        for object_key in value[name]
                        if isinstance(object_key, str)
                    ))
                    objects: dict[str, dict[str, Any]] = {}
                    for start in range(0, len(object_keys), _SQL_BATCH_ROWS):
                        batch = object_keys[start : start + _SQL_BATCH_ROWS]
                        placeholders = ",".join("?" for _ in batch)
                        objects.update(
                            (row[0], json.loads(row[1]))
                            for row in connection.execute(
                                "SELECT key,payload FROM extraction_objects "
                                f"WHERE key IN ({placeholders})", batch
                            )
                        )
                    for name in ("symbols", "references", "module_dependencies"):
                        if value[name] and isinstance(value[name][0], dict):
                            observations[name] = value[name]
                            continue
                        try:
                            observations[name] = [objects[key] for key in value[name]]
                        except KeyError as exc:
                            raise IndexInvalidError("Cached extraction object is missing") from exc
                    extraction = ExtractionResult(
                        language=value["language"],
                        symbols=tuple(SymbolObservation(**v) for v in observations["symbols"]),
                        references=tuple(
                            ReferenceObservation(**v) for v in observations["references"]
                        ),
                        module_dependencies=tuple(
                            ModuleDependencyObservation(**v)
                            for v in observations["module_dependencies"]
                        ),
                        warnings=tuple(value["warnings"]),
                    )
                processed += 1
                emit("structure", processed, len(files), path)
                return extraction

            stable_profile = replace(profile, corpus_id="shared")
            summary = write_structure(
                stable_profile,
                self.policy,
                staging,
                sources=self._source_inputs(source_rows, check),
                extract=extract,
                fact_staging=True,
            )
            connection.commit()
            counts = self._import_facts(connection, staging, snapshot_id)
            connection.executemany(
                "INSERT OR IGNORE INTO source_objects VALUES(?,?,?)",
                [(r["sha256"], r["git_oid"], r["size_bytes"]) for r in source_rows],
            )
            connection.executemany(
                "INSERT INTO snapshot_sources VALUES(?,?,?)",
                [(snapshot_id, r["path"], r["sha256"]) for r in source_rows],
            )
            missing_text = [
                row
                for row in source_rows
                if connection.execute(
                    "SELECT 1 FROM source_text_indexed WHERE sha256=?",
                    (row["sha256"],),
                ).fetchone()
                is None
            ]
            for row, (_, data) in zip(
                missing_text,
                self._source_inputs(missing_text, check),
                strict=True,
            ):
                indexed = connection.execute(
                    "SELECT 1 FROM source_text_indexed WHERE sha256=?",
                    (row["sha256"],),
                ).fetchone()
                if indexed is None:
                    object_id = connection.execute(
                        "SELECT rowid FROM source_objects WHERE sha256=?",
                        (row["sha256"],),
                    ).fetchone()[0]
                    connection.execute(
                        "INSERT INTO source_text_search(rowid,text) VALUES(?,?)",
                        (object_id, data.decode("utf-8", errors="replace")),
                    )
                    connection.execute(
                        "INSERT INTO source_text_indexed VALUES(?)", (row["sha256"],)
                    )
            pool = SharedVectors(self.root / "semantic" / namespace, dimension)
            members, reused, added = self._build_vectors(
                connection,
                pool,
                provider,
                config,
                namespace,
                snapshot_id,
                source_rows,
                legacy_generation,
                check,
                emit,
                statistics,
            )
            if not members:
                raise IndexBuildError("Selected revision contains no semantic code chunks")
            emit("index", 0, None, "Updating filtered search indexes")
            version, column, lance_tag = pool.publish(snapshot_id, members, check)
            emit("index", 1, 1, "Search indexes complete")
            corpus = replace(
                profile,
                corpus_id=target_id,
                revision=commit,
                source_root=self.source_git,
                index_root=self.root,
                semantic_store=pool.root,
                embedding_model=str(provider.model_id),
            )
            profile_json = {
                key: str(value) if isinstance(value, Path) else value
                for key, value in asdict(corpus).items()
            }
            if engine_version is None and profile.adapter == "cpp-ue" and tag:
                match = re.search(r"\d+\.\d+(?:\.\d+)?", tag)
                engine_version = match.group() if match else None
            metadata = {
                "snapshot_id": snapshot_id,
                "generation_id": snapshot_id,
                "target_id": target_id,
                "tag": tag,
                "commit": commit,
                "source_commit": commit,
                "engine_version": engine_version,
                "ready": True,
                "created_at": datetime.now(UTC).isoformat(),
                "counts": counts,
                "profile": profile_json,
                "semantic_namespace": namespace,
                "table_version": version,
                "membership_column": column,
                "lance_tag": lance_tag,
                "dimension": dimension,
                "source_fingerprint": summary.source_fingerprint,
                "source_selection_hash": source_selection_hash(corpus, self.policy),
                "adapter_version": adapter.version,
                "engine_version_id": ENGINE_VERSION,
                "reused_vectors": reused,
                "new_vectors": added,
                "statistics": statistics,
                "chunks": len(members),
            }
            emit("publish", 0, 1, "Publishing verified snapshot")
            check()
            connection.execute(
                "INSERT INTO snapshots VALUES(?,?,?,?)",
                (snapshot_id, target_id, _json(metadata), metadata["created_at"]),
            )
            self._analyze(connection)
            check()
            connection.commit()
            try:
                emit("publish", 1, 1, "Snapshot published")
            except SharedBuildCancelled:
                pass
            return self._result(metadata)

    def _result(self, item: dict[str, Any]) -> SharedBuildResult:
        return SharedBuildResult(
            item["snapshot_id"],
            item["target_id"],
            item["snapshot_id"],
            self._corpus(item),
            item["counts"],
            item["reused_vectors"],
            item["new_vectors"],
            item.get("statistics", {}),
        )

    def _import_sources(
        self,
        repository: GitSource,
        files: list[Any],
        snapshot_id: str,
        check: Callable[[], None],
        emit: Callable[[str, int, int | None, str], None],
    ) -> list[dict[str, Any]]:
        unique: dict[str, Any] = {}
        for item in files:
            if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", item.blob_oid):
                raise IndexBuildError("Expected a complete source Git object ID")
            if item.blob_oid in unique and unique[item.blob_oid].size != item.size:
                raise IndexBuildError("Conflicting sizes for one source Git object")
            unique[item.blob_oid] = item
        objects: dict[str, dict[str, Any]] = {}
        with self._connect() as connection:
            for object_format, oid_length in (("sha1", 40), ("sha256", 64)):
                oids = [oid for oid in unique if len(oid) == oid_length]
                for start in range(0, len(oids), _SQL_BATCH_ROWS):
                    batch = oids[start : start + _SQL_BATCH_ROWS]
                    placeholders = ",".join("?" for _ in batch)
                    for row in connection.execute(
                        "SELECT source_oid,sha256,git_oid,size_bytes FROM source_blob_map "
                        f"WHERE object_format=? AND source_oid IN ({placeholders})",
                        (object_format, *batch),
                    ):
                        objects[row["source_oid"]] = dict(row)
        missing = [oid for oid in unique if oid not in objects]
        new_mappings: list[tuple[str, str, str, str, int]] = []
        marks: dict[str, int] = {}
        with tempfile.TemporaryFile() as stream:
            blobs = (
                cast(Any, repository).read_blobs(missing)
                if hasattr(repository, "read_blobs")
                else ((oid, repository.read_blob(oid)) for oid in missing)
            )
            emit("source", 0, len(missing) + 1, "Importing missing source objects")
            for number, (source_oid, (returned_oid, data)) in enumerate(
                zip(missing, blobs, strict=True), 1
            ):
                check()
                item = unique[source_oid]
                path = self.policy.normalize_path(item.path)
                if returned_oid != source_oid or len(data) != item.size:
                    raise IndexBuildError(f"Source blob identity or size changed: {path}")
                if len(data) > self.policy.max_file_bytes:
                    raise IndexBuildError(f"Source blob exceeds byte limit: {path}")
                if data.startswith(b"version https://git-lfs.github.com/spec/v1"):
                    raise IndexBuildError(f"Unresolved Git LFS source: {path}")
                raw_object = b"blob " + str(len(data)).encode() + b"\0" + data
                managed_oid = hashlib.sha1(raw_object).hexdigest()
                object_format = "sha1" if len(source_oid) == 40 else "sha256"
                actual_oid = (
                    managed_oid if object_format == "sha1" else hashlib.sha256(raw_object).hexdigest()
                )
                if actual_oid != source_oid:
                    raise IndexBuildError(f"Source blob integrity mismatch: {path}")
                digest = hashlib.sha256(data).hexdigest()
                objects[source_oid] = {
                    "sha256": digest,
                    "git_oid": managed_oid,
                    "size_bytes": len(data),
                }
                new_mappings.append((object_format, source_oid, digest, managed_oid, len(data)))
                marks[source_oid] = number
                stream.write(f"blob\nmark :{number}\ndata {len(data)}\n".encode())
                stream.write(data)
                stream.write(b"\n")
                emit("source", number, len(missing) + 1, path)
            rows: list[dict[str, Any]] = []
            stream.write(f"commit refs/codalith/snapshots/{snapshot_id}\n".encode())
            stream.write(b"committer Codalith <codalith@localhost> 0 +0000\ndata 8\nsnapshot\n")
            stream.write(b"deleteall\n")
            for item in files:
                check()
                record = objects[item.blob_oid]
                if record["size_bytes"] != item.size:
                    raise IndexBuildError(f"Cached source blob size mismatch: {item.path}")
                path = self.policy.normalize_path(item.path)
                rows.append({"path": path, **record})
                object_ref = (
                    f":{marks[item.blob_oid]}"
                    if item.blob_oid in marks else record["git_oid"]
                )
                stream.write(
                    f"M 100644 {object_ref} ".encode() + _quoted_git_path(path) + b"\n"
                )
            stream.write(b"\ndone\n")
            stream.seek(0)
            _git(self.source_git, "fast-import", "--quiet", stdin=stream)
        with self._connect() as connection:
            connection.executemany(
                "INSERT INTO source_blob_map VALUES(?,?,?,?,?)", new_mappings
            )
        emit("source", len(missing) + 1, len(missing) + 1, "Managed source objects imported")
        return rows

    def _source_inputs(
        self,
        rows: list[dict[str, Any]],
        check: Callable[[], None],
    ) -> Generator[tuple[str, bytes], None, None]:
        from codalith.corpus.git_source import GitRepository

        repository = GitRepository(self.source_git)
        blobs = repository.read_blobs([row["git_oid"] for row in rows])
        for row, (_, data) in zip(rows, blobs, strict=True):
            check()
            if hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise IndexInvalidError("Managed source object hash mismatch")
            yield row["path"], data

    def _import_facts(
        self,
        connection: sqlite3.Connection,
        staging: Path,
        snapshot: str,
    ) -> dict[str, int]:
        source = sqlite3.connect(staging)
        source.row_factory = sqlite3.Row
        counts: dict[str, int] = {}
        try:
            for table in _TABLES:
                columns = [
                    (row[1], row[2] or "TEXT")
                    for row in source.execute(f'PRAGMA table_info("{table}")')
                ]
                definitions = ",".join(f'"{name}" {kind}' for name, kind in columns)
                names = ",".join(f'"{name}"' for name, _ in columns)
                connection.execute(
                    f"CREATE TABLE IF NOT EXISTS facts_{table} "
                    f"(fact_id TEXT PRIMARY KEY,{definitions})"
                )
                connection.execute(
                    f"CREATE TABLE IF NOT EXISTS members_{table} "
                    "(snapshot_id TEXT NOT NULL,fact_id TEXT NOT NULL,"
                    "PRIMARY KEY(snapshot_id,fact_id))"
                )
                indexed = {
                    "files": ("path",),
                    "symbols": ("symbol_id", "name", "qualified_name", "path", "comparison_key"),
                    "references": ("source_symbol_id", "target_symbol_id", "target_name"),
                    "module_dependencies": ("source_module", "target_module"),
                }[table]
                for name in indexed:
                    collation = (
                        " COLLATE NOCASE"
                        if name
                        in {
                            "name",
                            "qualified_name",
                            "path",
                            "target_name",
                            "source_module",
                            "target_module",
                        }
                        else ""
                    )
                    connection.execute(
                        f"CREATE INDEX IF NOT EXISTS facts_{table}_{name} "
                        f'ON facts_{table}("{name}"{collation})'
                    )
                if table in {"files", "symbols"}:
                    connection.execute(
                        f"CREATE INDEX IF NOT EXISTS facts_{table}_path_binary "
                        f"ON facts_{table}(path COLLATE BINARY)"
                    )
                if table == "symbols":
                    existed = (
                        connection.execute(
                            "SELECT 1 FROM sqlite_master WHERE name='facts_symbols_search'"
                        ).fetchone()
                        is not None
                    )
                    connection.execute(
                        "CREATE VIRTUAL TABLE IF NOT EXISTS facts_symbols_search USING fts5("
                        "name,qualified_name,content='facts_symbols',content_rowid='rowid',"
                        "tokenize='trigram',columnsize=0)"
                    )
                    if not existed:
                        connection.execute(
                            "INSERT INTO facts_symbols_search(facts_symbols_search) VALUES('rebuild')"
                        )
                placeholders = ",".join("?" for _ in columns)
                insert_sql = (
                    f"INSERT OR IGNORE INTO facts_{table}(fact_id,{names}) "
                    f"VALUES(?,{placeholders})"
                )
                members_sql = f"INSERT OR IGNORE INTO members_{table} VALUES(?,?)"
                cursor = source.execute(f'SELECT * FROM "{table}"')
                while batch := cursor.fetchmany(_SQL_BATCH_ROWS):
                    records: dict[str, tuple[Any, ...]] = {}
                    for row in batch:
                        values = list(row)
                        if table in {"references", "module_dependencies"}:
                            values[0] = 0
                        fact_id = _digest([v.hex() if isinstance(v, bytes) else v for v in values])
                        if table in {"references", "module_dependencies"}:
                            values[0] = int(fact_id[:15], 16)
                        records[fact_id] = (fact_id, *values)
                    new_symbols: set[str] = set()
                    if table == "symbols":
                        fact_ids = tuple(records)
                        known = {
                            row[0] for row in connection.execute(
                                "SELECT fact_id FROM facts_symbols WHERE fact_id IN ("
                                + ",".join("?" for _ in fact_ids) + ")", fact_ids
                            )
                        }
                        new_symbols = set(fact_ids) - known
                    connection.executemany(insert_sql, records.values())
                    if new_symbols:
                        connection.execute(
                            "INSERT INTO facts_symbols_search(rowid,name,qualified_name) "
                            "SELECT rowid,name,qualified_name FROM facts_symbols WHERE fact_id IN ("
                            + ",".join("?" for _ in new_symbols) + ")", tuple(new_symbols)
                        )
                    connection.executemany(
                        members_sql, ((snapshot, fact_id) for fact_id in records)
                    )
                counts[table] = int(
                    connection.execute(
                        f"SELECT count(*) FROM members_{table} WHERE snapshot_id=?",
                        (snapshot,),
                    ).fetchone()[0]
                )
        finally:
            source.close()
        return counts

    def _build_vectors(
        self,
        connection: sqlite3.Connection,
        pool: SharedVectors,
        provider: Any,
        config: Config,
        namespace: str,
        snapshot: str,
        sources: list[dict[str, Any]],
        legacy: Path | None,
        check: Callable[[], None],
        emit: Callable[[str, int, int | None, str], None],
        statistics: dict[str, int],
    ) -> tuple[set[int], int, int]:
        connection.execute("PRAGMA temp_store=FILE")
        connection.execute(
            "CREATE TEMP TABLE pending_vectors(id INTEGER PRIMARY KEY,text TEXT NOT NULL)"
        )
        pending: dict[str, int] = {}
        members: set[int] = set()
        reused = 0
        for number, (path, data) in enumerate(self._source_inputs(sources, check), 1):
            if not has_indexable_text(data):
                continue
            from codalith.languages.catalog import language_for_path

            language = language_for_path(path)
            key = _digest(
                [
                    hashlib.sha256(data).hexdigest(),
                    language,
                    ENGINE_VERSION,
                    config.max_chunk_lines,
                    config.window_lines,
                    config.window_overlap,
                ]
            )
            cached = connection.execute(
                "SELECT payload FROM chunk_cache WHERE key=?", (key,)
            ).fetchone()
            decoded = data.decode("utf-8", errors="replace")
            if cached is None:
                chunks = chunk_file(decoded, language, config)
                descriptors = []
                offsets = [0, *(match.end() for match in re.finditer("\n", decoded))]
                for chunk in chunks:
                    descriptor = asdict(chunk)
                    descriptor.pop("text")
                    offset = decoded.find(chunk.text, offsets[chunk.start_line - 1])
                    if offset < 0:
                        raise IndexBuildError("Chunk text is not an exact managed source slice")
                    descriptor.update(
                        offset=offset,
                        length=len(chunk.text),
                        input_hash=hashlib.sha256(chunk.text.encode()).hexdigest(),
                    )
                    descriptors.append(descriptor)
                connection.execute(
                    "INSERT INTO chunk_cache VALUES(?,?)",
                    (key, _json(descriptors)),
                )
            else:
                chunks = []
                for descriptor in json.loads(cached[0]):
                    if "text" in descriptor:
                        cached_chunk = Chunk(**descriptor)
                        if cached_chunk.text not in decoded:
                            raise IndexInvalidError("Cached chunk text differs from managed source")
                        chunks.append(cached_chunk)
                        continue
                    offset = descriptor.pop("offset")
                    length = descriptor.pop("length")
                    input_hash = descriptor.pop("input_hash")
                    text = decoded[offset : offset + length]
                    if hashlib.sha256(text.encode()).hexdigest() != input_hash:
                        raise IndexInvalidError("Cached chunk slice integrity check failed")
                    chunks.append(Chunk(text=text, **descriptor))
            for chunk in chunks:
                input_hash = hashlib.sha256(chunk.text.encode()).hexdigest()
                existing = connection.execute(
                    "SELECT id,stored FROM chunks WHERE namespace=? AND input_hash=?",
                    (namespace, input_hash),
                ).fetchone()
                if existing is None:
                    cursor = connection.execute(
                        "INSERT INTO chunks(namespace,input_hash) VALUES(?,?)",
                        (namespace, input_hash),
                    )
                    chunk_id = int(cast(int, cursor.lastrowid))
                    pending[input_hash] = chunk_id
                else:
                    chunk_id = int(existing[0])
                    if not existing[1]:
                        pending[input_hash] = chunk_id
                    elif input_hash not in pending:
                        reused += 1
                members.add(chunk_id)
                if input_hash in pending:
                    connection.execute(
                        "INSERT OR IGNORE INTO pending_vectors VALUES(?,?)", (chunk_id, chunk.text)
                    )
                connection.execute(
                    "INSERT OR IGNORE INTO occurrences VALUES(?,?,?,?,?,?,?,?)",
                    (
                        snapshot,
                        chunk_id,
                        path,
                        chunk.start_line,
                        chunk.end_line,
                        chunk.symbol,
                        chunk.kind,
                        chunk.language,
                    ),
                )
            emit("chunk", number, len(sources), path)
        imported: list[dict[str, Any]] = []
        for digest, text, vector in self._legacy_vectors(
            legacy,
            provider,
            sources,
            pending,
            connection,
            check,
            emit,
            statistics,
        ):
            check()
            chunk_id = pending[digest]
            if vector.shape != (provider.dim,) or not np.isfinite(vector).all():
                raise IndexBuildError("Invalid legacy embedding vector")
            imported.append(
                {"id": chunk_id, "input_hash": digest, "text": text, "vector": vector.tolist()}
            )
            pending.pop(digest)
            reused += 1
            statistics["legacy_imported_vectors"] += 1
            if len(imported) >= _VECTOR_FLUSH_ROWS:
                self._append_vectors(connection, pool, imported)
                imported.clear()
        if imported:
            self._append_vectors(connection, pool, imported)
        total = len(pending)
        added = 0
        items = list(pending.items())
        buffered: list[dict[str, Any]] = []
        emit("embedding", 0, total, "Reusing compatible vectors and embedding missing inputs")
        for start in range(0, total, config.embed_batch_size):
            check()
            batch = [
                (
                    digest,
                    (
                        chunk_id,
                        str(
                            connection.execute(
                                "SELECT text FROM pending_vectors WHERE id=?",
                                (chunk_id,),
                            ).fetchone()[0]
                        ),
                    ),
                )
                for digest, chunk_id in items[start : start + config.embed_batch_size]
            ]
            missing = [text for _, (_, text) in batch]
            vectors = iter(
                np.asarray(provider.embed_documents(missing), dtype="float32")
                if missing
                else np.empty((0, provider.dim), dtype="float32")
            )
            records = []
            for digest, (chunk_id, text) in batch:
                vector = next(vectors)
                added += 1
                if vector.shape != (provider.dim,) or not np.isfinite(vector).all():
                    raise IndexBuildError("Invalid embedding vector")
                records.append(
                    {"id": chunk_id, "input_hash": digest, "text": text, "vector": vector.tolist()}
                )
            buffered.extend(records)
            if len(buffered) >= _VECTOR_FLUSH_ROWS:
                self._append_vectors(connection, pool, buffered)
                buffered.clear()
            emit("embedding", min(start + len(batch), total), total, "Embedding inputs complete")
        if buffered:
            self._append_vectors(connection, pool, buffered)
        return members, reused, added

    def _append_vectors(
        self,
        connection: sqlite3.Connection,
        pool: SharedVectors,
        records: list[dict[str, Any]],
    ) -> None:
        connection.commit()
        pool.append(records)
        connection.executemany(
            "UPDATE chunks SET stored=1 WHERE id=?", [(record["id"],) for record in records]
        )
        connection.commit()

    def _legacy_vectors(
        self,
        generation_path: Path | None,
        provider: Any,
        sources: list[dict[str, Any]],
        expected: dict[str, int],
        connection: sqlite3.Connection,
        check: Callable[[], None],
        emit: Callable[[str, int, int | None, str], None],
        statistics: dict[str, int],
    ) -> Iterator[tuple[str, str, np.ndarray]]:
        statistics.update(
            legacy_imported_vectors=0,
            legacy_missing_inputs=0,
            legacy_incompatible_inputs=0,
        )
        if generation_path is None:
            return
        import lance

        from codalith.indexing.semantic import store_fingerprint
        from codalith.semantic.store import read_provenance

        path = generation_path.resolve()
        manifest = IndexManifest.load(path / "manifest.json")
        for artifact in manifest.artifacts:
            artifact_path = path / artifact.path
            if sha256_file(artifact_path) != artifact.sha256:
                raise IndexBuildError("Legacy generation artifact hash mismatch")
        if (
            manifest.embedding_provider != provider.name
            or manifest.embedding_model != provider.model_id
            or manifest.embedding_dimension != provider.dim
            or manifest.semantic_engine_version not in {"1.1.0", ENGINE_VERSION}
        ):
            statistics["legacy_incompatible_inputs"] = len(expected)
            return
        store = Path(manifest.semantic_store or "semantic")
        if not store.is_absolute():
            store = path / store
        from codalith.semantic.store import SemanticStoreError

        try:
            provenance = read_provenance(store)
        except SemanticStoreError:
            statistics["legacy_incompatible_inputs"] = len(expected)
            return
        if provenance.model != provider.model_id or provenance.dimension != provider.dim:
            statistics["legacy_incompatible_inputs"] = len(expected)
            return
        if store_fingerprint(store) != manifest.semantic_store_fingerprint:
            raise IndexBuildError("Legacy semantic store fingerprint mismatch")
        allowed = {row["path"]: row["sha256"] for row in sources}
        legacy_db = sqlite3.connect((path / "structure.sqlite").as_uri() + "?mode=ro", uri=True)
        try:
            hashes = dict(legacy_db.execute("SELECT path,sha256 FROM files"))
        finally:
            legacy_db.close()
        matching = {name for name, digest in allowed.items() if hashes.get(name) == digest}
        semantic_files = lance.dataset(str(store / "files.lance"))
        covered: set[str] = set()
        for batch in semantic_files.scanner(
            columns=["path", "content_hash"], batch_size=512
        ).to_batches():
            check()
            for row in batch.to_pylist():
                name = str(row["path"]).replace("\\", "/")
                if name in matching:
                    if row["content_hash"] != allowed[name]:
                        raise IndexBuildError("Legacy semantic source hash differs from snapshot")
                    covered.add(name)
        matching &= covered
        dataset = lance.dataset(str(store / "chunks.lance"))
        scanner = dataset.scanner(columns=["path", "text", "vector"], batch_size=512)
        processed = 0
        total = dataset.count_rows()
        for batch in scanner.to_batches():
            check()
            for row in batch.to_pylist():
                processed += 1
                if str(row["path"]).replace("\\", "/") in matching:
                    text = str(row["text"])
                    input_hash = hashlib.sha256(text.encode()).hexdigest()
                    if input_hash not in expected:
                        continue
                    wanted = connection.execute(
                        "SELECT text FROM pending_vectors WHERE id=?",
                        (expected[input_hash],),
                    ).fetchone()
                    if wanted is None or wanted[0] != text:
                        continue
                    yield (
                        input_hash,
                        text,
                        np.asarray(row["vector"], dtype="float32"),
                    )
            emit("legacy", processed, total, "Verified legacy vector inputs")
        statistics["legacy_missing_inputs"] = len(expected)

    def read_bytes(self, snapshot_id: str, path: str) -> bytes:
        canonical = self.policy.normalize_path(path)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT o.git_oid,o.sha256,o.size_bytes FROM snapshot_sources s "
                "JOIN source_objects o USING(sha256) WHERE s.snapshot_id=? AND s.path=?",
                (snapshot_id, canonical),
            ).fetchone()
        if row is None:
            raise SourceReadError("Path does not belong to the published snapshot")
        data = _git(self.source_git, "cat-file", "blob", row["git_oid"])
        if len(data) != row["size_bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise SourceReadError("Managed source object integrity check failed")
        return data

    def search_text(
        self,
        snapshot: str,
        query: str,
        *,
        limit: int,
        max_file_bytes: int,
    ) -> list[TextHit]:
        with self._connect() as connection:
            incomplete = connection.execute(
                "SELECT 1 FROM snapshot_sources s LEFT JOIN source_text_indexed i USING(sha256) "
                "WHERE s.snapshot_id=? AND i.sha256 IS NULL LIMIT 1",
                (snapshot,),
            ).fetchone()
            if len(query) >= 3 and query.isascii() and incomplete is None:
                phrase = '"' + query.replace('"', '""') + '"'
                selected = connection.execute(
                    "SELECT s.path,o.git_oid,o.sha256,o.size_bytes FROM source_text_search t "
                    "JOIN source_objects o ON o.rowid=t.rowid "
                    "JOIN snapshot_sources s ON s.sha256=o.sha256 "
                    "WHERE source_text_search MATCH ? AND s.snapshot_id=? AND o.size_bytes<=? "
                    "ORDER BY length(s.path),s.path",
                    (phrase, snapshot, max_file_bytes),
                ).fetchall()
            else:
                selected = connection.execute(
                    "SELECT s.path,o.git_oid,o.sha256,o.size_bytes FROM snapshot_sources s "
                    "JOIN source_objects o USING(sha256) WHERE s.snapshot_id=? AND o.size_bytes<=? "
                    "ORDER BY length(s.path),s.path",
                    (snapshot, max_file_bytes),
                ).fetchall()
        rows = [dict(row) for row in selected if not self.policy.is_denied(row["path"])]
        hits: list[TextHit] = []
        inputs = self._source_inputs(rows, lambda: None)
        try:
            for path, data in inputs:
                matched = 0
                for number, line in enumerate(
                    data.decode("utf-8", errors="replace").split("\n"), 1
                ):
                    if query.casefold() in line.casefold():
                        hits.append(TextHit(path, number, line.rstrip("\r")))
                        matched += 1
                        if len(hits) >= limit:
                            return hits
                        if matched == 3:
                            break
        finally:
            inputs.close()
        return hits


class SharedStructureIndex(StructureIndex):
    def __init__(self, pool: SharedPool, snapshot: dict[str, Any], corpus: Corpus) -> None:
        connection = sqlite3.connect(
            pool.catalog.as_uri() + "?mode=ro",
            uri=True,
            check_same_thread=False,
            isolation_level=None,
        )
        try:
            snapshot_id = snapshot["snapshot_id"]
            connection.execute("CREATE TEMP TABLE selected_snapshot(id TEXT PRIMARY KEY)")
            connection.execute("INSERT INTO selected_snapshot VALUES(?)", (snapshot_id,))
            for table in _TABLES:
                columns = [
                    row[1]
                    for row in connection.execute(f"PRAGMA table_info(facts_{table})")
                    if row[1] != "fact_id"
                ]
                selection = ",".join(f'f."{name}"' for name in columns)
                connection.execute(
                    f'CREATE TEMP VIEW "{table}" AS SELECT {selection} '
                    f"FROM facts_{table} f JOIN members_{table} m USING(fact_id) "
                    "WHERE m.snapshot_id=(SELECT id FROM selected_snapshot)"
                )
            super().__init__(
                pool.catalog, retrieval_profile(corpus.adapter), connection=connection
            )
        except BaseException:
            connection.close()
            raise

    def _search_ranked(self, query: str, needed: int, exclude: set[str]) -> list[str]:
        phrase = '"' + query.replace('"', '""') + '"'
        ranked: list[str] = []
        for expression in (f"name : ^{phrase}", phrase):
            rows = self._all(
                "SELECT symbol_id FROM (SELECT f.* FROM facts_symbols_search s "
                "JOIN facts_symbols f ON f.rowid=s.rowid "
                "JOIN members_symbols m ON m.fact_id=f.fact_id "
                "WHERE facts_symbols_search MATCH ? "
                "AND m.snapshot_id=(SELECT id FROM selected_snapshot)) "
                f"ORDER BY {self._symbol_order} LIMIT ?",
                (expression, needed + len(exclude)),
            )
            for row in rows:
                if row[0] not in exclude and row[0] not in ranked:
                    ranked.append(str(row[0]))
            if len(ranked) >= needed:
                break
        return ranked[:needed]

    def _scan_ranked(self, query: str, needed: int, exclude: set[str]) -> list[str]:
        pattern = _escape_like(query)
        rows = self._all(
            "SELECT symbol_id FROM symbols WHERE name LIKE ? ESCAPE '\\' "
            "OR qualified_name LIKE ? ESCAPE '\\' ORDER BY "
            "CASE WHEN name LIKE ? ESCAPE '\\' THEN 0 ELSE 1 END, "
            f"{self._symbol_order} LIMIT ?",
            (f"%{pattern}%", f"%{pattern}%", f"{pattern}%", needed + len(exclude)),
        )
        return [str(row[0]) for row in rows if row[0] not in exclude][:needed]


class _SharedGenerations(GenerationRepository):
    def __init__(self, pool: SharedPool, policy: SourcePolicy) -> None:
        super().__init__(policy)
        self.pool = pool

    def active(self, corpus: Corpus, *, verify_artifacts: bool = False) -> ActiveGeneration:
        item = self.pool.snapshot(corpus.corpus_id)
        if item is None:
            return super().active(corpus, verify_artifacts=verify_artifacts)
        if item["source_selection_hash"] != source_selection_hash(corpus, self.policy):
            raise IndexInvalidError("Shared snapshot source selection no longer matches policy")
        if item["adapter_version"] != ADAPTERS[corpus.adapter].version:
            raise IndexInvalidError("Shared snapshot adapter version is stale")
        if item["engine_version_id"] != ENGINE_VERSION:
            raise IndexInvalidError("Shared snapshot semantic version is stale")
        counts = item["counts"]
        manifest = IndexManifest(
            schema_version=MANIFEST_SCHEMA_VERSION,
            generation_id=item["snapshot_id"],
            corpus_id=corpus.corpus_id,
            source_revision=item["commit"],
            source_fingerprint=item["source_fingerprint"],
            created_at=item["created_at"],
            semantic_engine_version=ENGINE_VERSION,
            embedding_provider=corpus.embedding_provider,
            embedding_model=corpus.embedding_model,
            embedding_dimension=item["dimension"],
            semantic_store_fingerprint=_digest(
                [item["semantic_namespace"], item["table_version"], item["membership_column"]]
            ),
            semantic_store=str(corpus.semantic_store),
            semantic_covered_files=counts["files"],
            semantic_extra_files=0,
            source_selection_hash=item["source_selection_hash"],
            adapter=corpus.adapter,
            adapter_version=item["adapter_version"],
            structure_schema_version=STRUCTURE_SCHEMA_VERSION,
            files=counts["files"],
            symbols=counts["symbols"],
            references=counts["references"],
            module_dependencies=counts["module_dependencies"],
            semantic_available=True,
            artifacts=(Artifact("shared-snapshot", _digest(item), 0),),
        )
        return ActiveGeneration(
            self.pool.root,
            self.pool.catalog,
            self.pool.catalog,
            cast(Path, corpus.semantic_store),
            manifest,
        )


class _SharedSources(SourceReader):
    def __init__(self, pool: SharedPool, policy: SourcePolicy) -> None:
        super().__init__(policy)
        self.pool = pool

    def read(
        self,
        corpus: Corpus,
        catalog: FileCatalog,
        path: str,
        *,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> SourceSlice:
        if self.pool.snapshot(corpus.corpus_id) is None:
            return super().read(corpus, catalog, path, start_line=start_line, end_line=end_line)
        canonical = self.policy.normalize_path(path)
        start, requested_end = self.policy.validate_range(start_line, end_line)
        indexed = catalog.get_file(canonical)
        if indexed is None:
            raise SourceReadError("Path does not belong to this snapshot")
        data = self.pool.read_bytes(catalog.generation_id, canonical)
        if len(data) > self.policy.max_file_bytes:
            raise SourceReadError("Managed source exceeds current source policy limit")
        parts = data.split(b"\n")
        total_lines = len(parts)
        if start > total_lines:
            raise SourceReadError(f"start_line {start} exceeds {total_lines} lines in {canonical}")
        actual_end = min(requested_end, total_lines)
        real_parts = parts[:-1] if data.endswith(b"\n") else parts
        selected = [
            (part[:-1] if part.endswith(b"\r") and number < total_lines else part).decode(
                "utf-8",
                errors="replace",
            )
            for number, part in enumerate(real_parts, 1)
            if start <= number <= actual_end
        ]
        text = "\n".join(selected)
        digest = hashlib.sha256(data).hexdigest()
        return SourceSlice(
            corpus.corpus_id,
            corpus.revision,
            catalog.generation_id,
            source_uri(corpus.corpus_id, canonical, start_line=start, end_line=actual_end),
            canonical,
            start,
            actual_end,
            total_lines,
            text,
            digest,
            indexed.sha256,
            digest != indexed.sha256 or len(data) != indexed.size_bytes,
            end_line is None and requested_end < total_lines,
            text.count("\ufffd"),
        )


class _SharedSemanticResource:
    def __init__(
        self,
        config: Config,
        provider: EmbeddingProvider,
        snapshot: SharedVectorSnapshot,
    ) -> None:
        self.config = config
        self.provider = provider
        self.snapshot = snapshot

    def search(self, query: str, limit: int) -> list[tuple[int, float, float, str]]:
        vector = self.provider.embed_query(query)
        return self.snapshot.search(query, vector=vector, config=self.config, limit=limit)

    def close(self) -> None:
        try:
            self.snapshot.close()
        finally:
            self.provider.close()


class _SharedSemantic(SemanticBackend):
    def __init__(self, pool: SharedPool, policy: SourcePolicy) -> None:
        super().__init__(policy)
        self.pool = pool
        self._shared: GenerationLeases[_SharedSemanticResource] = GenerationLeases(
            _SharedSemanticResource.close
        )

    def close(self) -> None:
        try:
            self._shared.close()
        finally:
            super().close()

    def _open_shared(
        self, corpus: Corpus, generation: ActiveGeneration, item: dict[str, Any]
    ) -> _SharedSemanticResource:
        config = engine_config(
            corpus,
            generation.semantic_path,
            self.policy,
            provider=generation.manifest.embedding_provider,
            model=generation.manifest.embedding_model,
        )
        provider = get_provider(config)
        try:
            if str(provider.model_id) != generation.manifest.embedding_model:
                raise IndexInvalidError("Shared query embedding provenance mismatch")
            provider.prepare()
            if provider.dim != item["dimension"]:
                raise IndexInvalidError("Shared query embedding provenance mismatch")
            vectors = SharedVectors(config.store_dir, item["dimension"], writable=False)
            snapshot = vectors.open_snapshot(item["table_version"], item["membership_column"])
            return _SharedSemanticResource(config, provider, snapshot)
        except BaseException:
            provider.close()
            raise

    def search(
        self,
        corpus: Corpus,
        generation: ActiveGeneration,
        query: str,
        *,
        limit: int,
    ) -> list[SemanticHit]:
        item = self.pool.snapshot(corpus.corpus_id)
        if item is None:
            return super().search(corpus, generation, query, limit=limit)
        if item["snapshot_id"] != generation.manifest.generation_id:
            raise RetrievalError("Shared semantic snapshot does not match the request generation")
        try:
            with self._shared.lease(
                corpus.corpus_id,
                generation.manifest.generation_id,
                lambda: self._open_shared(corpus, generation, item),
            ) as resource:
                ranked = resource.search(query, limit)
        except Exception as exc:
            raise RetrievalError(
                f"Shared semantic search failed: {type(exc).__name__}: {exc}"
            ) from exc
        hits: list[SemanticHit] = []
        with self.pool._connect() as connection:
            for chunk_id, score, similarity, text in ranked:
                rows = connection.execute(
                    "SELECT * FROM occurrences WHERE snapshot_id=? AND chunk_id=? "
                    "ORDER BY path,start_line LIMIT ?",
                    (item["snapshot_id"], chunk_id, limit),
                )
                for row in rows:
                    hits.append(
                        SemanticHit(
                            row["path"],
                            row["symbol"],
                            row["kind"],
                            row["language"],
                            row["start_line"],
                            row["end_line"],
                            text,
                            score,
                            similarity,
                        )
                    )
        return hits[:limit]


class SharedQueryService(QueryService):
    def __init__(self, registry: CorpusRegistry, policy: SourcePolicy, pool: SharedPool) -> None:
        super().__init__(registry, policy)
        self.pool = pool
        self.generations = _SharedGenerations(pool, policy)
        self.sources = _SharedSources(pool, policy)
        self.semantic = _SharedSemantic(pool, policy)

    def open_structure(self, corpus: Corpus, generation: ActiveGeneration) -> StructureIndex:
        item = self.pool.snapshot(corpus.corpus_id)
        return (
            SharedStructureIndex(self.pool, item, corpus)
            if item
            else super().open_structure(
                corpus,
                generation,
            )
        )

    def verify_semantic(self, generation: ActiveGeneration) -> None:
        if self.pool.snapshot(generation.manifest.corpus_id) is None:
            super().verify_semantic(generation)
            return
        import lance

        item = self.pool.snapshot(generation.manifest.corpus_id)
        assert item is not None
        path = self.pool.root / "semantic" / item["semantic_namespace"] / "chunks.lance"
        try:
            dataset = lance.dataset(str(path), version=item["table_version"])
            if item["membership_column"] not in dataset.schema.names:
                raise IndexInvalidError("Shared snapshot membership is missing")
        except (OSError, ValueError) as exc:
            raise IndexInvalidError(f"Shared semantic release is unavailable: {exc}") from exc

    def search_text(
        self,
        corpus: Corpus,
        index: StructureIndex,
        query: str,
        *,
        limit: int,
        max_file_bytes: int,
    ) -> list[TextHit]:
        item = self.pool.snapshot(corpus.corpus_id)
        if item is None:
            return super().search_text(
                corpus, index, query, limit=limit, max_file_bytes=max_file_bytes
            )
        return self.pool.search_text(
            item["snapshot_id"], query, limit=limit, max_file_bytes=max_file_bytes
        )
