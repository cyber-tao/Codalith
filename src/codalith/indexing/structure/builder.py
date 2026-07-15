"""Build and atomically publish structural index generations."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import shutil
import sqlite3
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from codalith.corpus.registry import Corpus
from codalith.corpus.source_policy import SourcePolicy
from codalith.corpus.store_manifest import (
    MANIFEST_SCHEMA_VERSION,
    STRUCTURE_SCHEMA_VERSION,
    Artifact,
    GenerationRepository,
    IndexManifest,
    chunk_policy_hash,
    new_generation_id,
    sha256_file,
)
from codalith.errors import IndexBuildError
from codalith.indexing.coderag.backend import (
    preflight_semantic_index,
    prepare_semantic_index,
    write_semantic_metadata,
)
from codalith.indexing.structure.schema import INDEX_SQL, SCHEMA_SQL
from codalith.languages import create_adapter
from codalith.languages.base import ExtractionResult, ReferenceObservation, SymbolObservation

_MAX_REPORTED_WARNINGS = 200


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


class _Digest(Protocol):
    def update(self, data: bytes) -> None: ...

    def hexdigest(self) -> str: ...


@dataclass(slots=True)
class _WarningCollector:
    items: list[str]
    omitted: int = 0

    def add(self, warning: str) -> None:
        if len(self.items) < _MAX_REPORTED_WARNINGS:
            self.items.append(warning)
        else:
            self.omitted += 1

    def extend(self, warnings: tuple[str, ...]) -> None:
        for warning in warnings:
            self.add(warning)

    def result(self) -> tuple[str, ...]:
        if not self.omitted:
            return tuple(self.items)
        return (*self.items, f"{self.omitted} additional warning(s) omitted")


class StructureBuilder:
    def __init__(self, policy: SourcePolicy) -> None:
        self.policy = policy
        self.generations = GenerationRepository()

    def build(
        self,
        corpus: Corpus,
        *,
        semantic_mode: str = "none",
        allow_external_rebuild: bool = False,
        progress: Callable[[str], None] | None = None,
    ) -> BuildReport:
        if semantic_mode not in {"none", "build", "adopt"}:
            raise IndexBuildError("semantic_mode must be none, build, or adopt")
        if not corpus.source_root.is_dir():
            raise IndexBuildError(f"Corpus source root does not exist: {corpus.source_root}")
        if semantic_mode != "none":
            _report(progress, f"Preflighting semantic index in {semantic_mode} mode")
            preflight_semantic_index(
                corpus,
                self.policy,
                mode=semantic_mode,
                allow_external_rebuild=allow_external_rebuild,
            )
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
        warnings = _WarningCollector([])
        fingerprint = hashlib.sha256()
        try:
            _report(progress, f"Scanning structural source for {corpus.corpus_id}")
            counts = self._build_database(
                corpus,
                database,
                warnings,
                fingerprint,
                progress,
            )
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
            if semantic_mode != "none":
                _report(progress, f"Preparing semantic index in {semantic_mode} mode")
                semantic_store = corpus.coderag_store or (staging / "coderag")
                semantic_info = prepare_semantic_index(
                    corpus,
                    semantic_store,
                    self.policy,
                    mode=semantic_mode,
                    indexed_paths=_indexed_paths(database),
                    allow_external_rebuild=allow_external_rebuild,
                )
                _report(progress, "Semantic provenance verified")
                semantic_metadata = staging / "coderag-meta.json"
                write_semantic_metadata(semantic_metadata, semantic_info)
                artifacts.append(
                    Artifact(
                        path="coderag-meta.json",
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
                source_fingerprint=fingerprint.hexdigest(),
                created_at=datetime.now(UTC).isoformat(),
                coderag_version=_coderag_version(),
                embedding_provider=(
                    semantic_info.provider if semantic_info else corpus.embedding_provider
                ),
                embedding_model=(
                    semantic_info.model if semantic_info else corpus.embedding_model
                ),
                embedding_dimension=semantic_info.dimension if semantic_info else None,
                coderag_store_fingerprint=(
                    semantic_info.store_fingerprint if semantic_info else None
                ),
                chunk_policy_hash=chunk_policy_hash(corpus),
                adapter=corpus.adapter,
                adapter_version=create_adapter(corpus.adapter).version,
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
            os.replace(staging, final)
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
            warnings=warnings.result(),
            source_fingerprint=fingerprint.hexdigest(),
            semantic_available=semantic_info is not None,
        )

    def _build_database(
        self,
        corpus: Corpus,
        database: Path,
        warnings: _WarningCollector,
        fingerprint: _Digest,
        progress: Callable[[str], None] | None,
    ) -> dict[str, int]:
        adapter = create_adapter(corpus.adapter)
        connection = sqlite3.connect(database)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = OFF")
        connection.execute("PRAGMA synchronous = OFF")
        connection.execute("PRAGMA temp_store = FILE")
        connection.executescript(SCHEMA_SQL)
        _create_resolution_tables(connection)
        try:
            with connection:
                connection.execute(
                    "INSERT INTO metadata(key, value) VALUES (?, ?)",
                    ("schema_version", str(STRUCTURE_SCHEMA_VERSION)),
                )
                processed_files = 0
                for source_path in _source_files(corpus, self.policy, adapter.supports):
                    relative = source_path.relative_to(corpus.source_root).as_posix()
                    raw = source_path.read_bytes()
                    if len(raw) > self.policy.max_file_bytes:
                        warnings.add(f"{relative}: skipped; file exceeds byte limit")
                        continue
                    file_hash = hashlib.sha256(raw).hexdigest()
                    fingerprint.update(relative.encode("utf-8"))
                    fingerprint.update(b"\0")
                    fingerprint.update(file_hash.encode("ascii"))
                    text = raw.decode("utf-8", errors="replace")
                    try:
                        extraction = adapter.extract(relative, text)
                    except Exception as exc:
                        warnings.add(
                            f"{relative}: structural extraction failed with "
                            f"{type(exc).__name__}: {exc}"
                        )
                        extraction = ExtractionResult(language=_language_hint(source_path))
                    warnings.extend(extraction.warnings)
                    module = next(
                        (symbol.module for symbol in extraction.symbols if symbol.module),
                        None,
                    )
                    connection.execute(
                        "INSERT INTO files(path, language, sha256, size_bytes, line_count, module) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            relative,
                            extraction.language,
                            file_hash,
                            len(raw),
                            max(1, text.count("\n") + 1),
                            module,
                        ),
                    )
                    file_symbols: list[tuple[str, str, int, int]] = []
                    symbol_rows: list[tuple[object, ...]] = []
                    symbol_key_rows: list[tuple[str, str, str, str]] = []
                    parent_rows: list[tuple[str, str, str]] = []
                    for symbol in extraction.symbols:
                        symbol_id = _symbol_id(corpus.corpus_id, symbol)
                        file_symbols.append(
                            (symbol.qualified_name, symbol_id, symbol.start_line, symbol.end_line)
                        )
                        symbol_rows.append(
                            (
                                symbol_id,
                                f"{symbol.qualified_name}\0{symbol.kind}",
                                symbol.qualified_name,
                                symbol.name,
                                symbol.kind,
                                symbol.signature,
                                relative,
                                symbol.start_line,
                                symbol.end_line,
                                symbol.module,
                                json.dumps(
                                    symbol.metadata,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ),
                            ),
                        )
                        symbol_key_rows.extend(
                            (
                                (
                                    "qualified",
                                    symbol.qualified_name.casefold(),
                                    relative,
                                    symbol_id,
                                ),
                                ("name", symbol.name.casefold(), relative, symbol_id),
                            )
                        )
                        if symbol.parent_qualified_name:
                            parent_rows.append(
                                (
                                    symbol_id,
                                    symbol.parent_qualified_name.casefold(),
                                    relative,
                                ),
                            )
                    connection.executemany(
                        "INSERT INTO symbols("
                        "symbol_id, comparison_key, qualified_name, name, kind, signature, "
                        "path, start_line, end_line, module, parent_symbol_id, metadata_json"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                        symbol_rows,
                    )
                    connection.executemany(
                        "INSERT INTO symbol_keys(key_kind, lookup_key, path, symbol_id) "
                        "VALUES (?, ?, ?, ?)",
                        symbol_key_rows,
                    )
                    connection.executemany(
                        "INSERT INTO pending_parents("
                        "symbol_id, parent_key, path) VALUES (?, ?, ?)",
                        parent_rows,
                    )
                    processed_files += 1
                    if processed_files == 1 or processed_files % 500 == 0:
                        _report(
                            progress,
                            f"Indexed {processed_files} files; current path: {relative}",
                        )
                    connection.executemany(
                        "INSERT INTO pending_references("
                        "source_symbol_id, target_name, qualified_key, name_key, "
                        "kind, path, line"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            _reference_row(reference, file_symbols)
                            for reference in extraction.references
                        ),
                    )
                    connection.executemany(
                        "INSERT OR IGNORE INTO module_dependencies("
                        "source_module, target_module, kind, path, line"
                        ") VALUES (?, ?, ?, ?, ?)",
                        (
                            (
                                dependency.source_module,
                                dependency.target_module,
                                dependency.kind,
                                dependency.path,
                                dependency.line,
                            )
                            for dependency in extraction.module_dependencies
                        ),
                    )
                _resolve_pending(connection)
                connection.executescript(INDEX_SQL)
            result = connection.execute("PRAGMA integrity_check").fetchone()
            if result is None or result[0] != "ok":
                raise IndexBuildError(f"SQLite integrity check failed: {result}")
            counts = {
                table: _table_count(connection, table)
                for table in ("files", "symbols", "references", "module_dependencies")
            }
            connection.execute("VACUUM")
            return counts
        finally:
            connection.close()


def _source_files(
    corpus: Corpus,
    policy: SourcePolicy,
    supports: Callable[[Path], bool],
) -> Iterator[Path]:
    resolved_root = corpus.source_root.resolve()
    for dirpath, dirnames, filenames in os.walk(corpus.source_root):
        dirnames[:] = sorted(
            item
            for item in dirnames
            if not _is_link_like(Path(dirpath, item))
            and not corpus.excludes(
                Path(dirpath, item).relative_to(corpus.source_root).as_posix() + "/"
            )
            and not policy.is_denied(
                Path(dirpath, item).relative_to(corpus.source_root).as_posix() + "/"
            )
        )
        for filename in sorted(filenames):
            path = Path(dirpath, filename)
            if _is_link_like(path):
                continue
            try:
                resolved = path.resolve(strict=True)
            except OSError:
                continue
            if resolved_root not in resolved.parents:
                continue
            relative = path.relative_to(corpus.source_root).as_posix()
            if corpus.excludes(relative) or policy.is_denied(relative):
                continue
            normalized_filename = filename.casefold()
            if corpus.include_extensions and not any(
                normalized_filename.endswith(extension)
                for extension in corpus.include_extensions
            ):
                continue
            if supports(path):
                yield path


def _is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def _table_count(connection: sqlite3.Connection, table: str) -> int:
    quoted = '"references"' if table == "references" else table
    row = connection.execute(f"SELECT count(*) FROM {quoted}").fetchone()
    return int(row[0]) if row is not None else 0


def _indexed_paths(database: Path) -> tuple[str, ...]:
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        return tuple(
            str(row[0])
            for row in connection.execute("SELECT path FROM files ORDER BY path")
        )
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


def _symbol_id(corpus_id: str, symbol: SymbolObservation) -> str:
    value = "\0".join(
        (
            corpus_id,
            symbol.path,
            symbol.qualified_name,
            symbol.kind,
            symbol.signature,
            str(symbol.start_line),
        )
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _create_resolution_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TEMP TABLE symbol_keys (
            key_kind TEXT NOT NULL CHECK(key_kind IN ('qualified', 'name')),
            lookup_key TEXT NOT NULL,
            path TEXT NOT NULL,
            symbol_id TEXT NOT NULL
        );
        CREATE INDEX symbol_keys_lookup_idx
            ON symbol_keys(key_kind, lookup_key, path, symbol_id);

        CREATE TEMP TABLE pending_parents (
            symbol_id TEXT PRIMARY KEY,
            parent_key TEXT NOT NULL,
            path TEXT NOT NULL
        ) WITHOUT ROWID;

        CREATE TEMP TABLE pending_references (
            pending_id INTEGER PRIMARY KEY,
            source_symbol_id TEXT,
            target_name TEXT NOT NULL,
            qualified_key TEXT NOT NULL,
            name_key TEXT NOT NULL,
            kind TEXT NOT NULL,
            path TEXT NOT NULL,
            line INTEGER NOT NULL
        );
        """
    )


def _reference_row(
    observation: ReferenceObservation,
    file_symbols: list[tuple[str, str, int, int]],
) -> tuple[str | None, str, str, str, str, str, int]:
    source_candidates = {
        symbol_id
        for qualified, symbol_id, start, end in file_symbols
        if qualified == observation.source_qualified_name and start <= observation.line <= end
    }
    source_id = next(iter(source_candidates)) if len(source_candidates) == 1 else None
    normalized_target = _reference_name(observation.target_name)
    name_key = normalized_target.split("::")[-1].split(".")[-1].casefold()
    return (
        source_id,
        observation.target_name,
        normalized_target.casefold(),
        name_key,
        observation.kind,
        observation.path,
        observation.line,
    )


def _resolve_pending(connection: sqlite3.Connection) -> None:
    connection.execute(
        "UPDATE symbols SET parent_symbol_id = ("
        "SELECT MIN(keys.symbol_id) FROM pending_parents AS parents "
        "JOIN symbol_keys AS keys ON keys.key_kind = 'qualified' "
        "AND keys.lookup_key = parents.parent_key AND keys.path = parents.path "
        "WHERE parents.symbol_id = symbols.symbol_id "
        "GROUP BY parents.symbol_id HAVING COUNT(DISTINCT keys.symbol_id) = 1"
        ") WHERE symbol_id IN (SELECT symbol_id FROM pending_parents)"
    )
    connection.executescript(
        """
        CREATE TEMP TABLE key_stats AS
        SELECT
            key_kind,
            lookup_key,
            COUNT(DISTINCT symbol_id) AS candidate_count,
            MIN(symbol_id) AS symbol_id
        FROM symbol_keys
        GROUP BY key_kind, lookup_key;
        CREATE UNIQUE INDEX key_stats_lookup_idx ON key_stats(key_kind, lookup_key);

        INSERT INTO "references"(
            source_symbol_id,
            target_name,
            target_symbol_id,
            resolution,
            kind,
            path,
            line
        )
        SELECT
            pending.source_symbol_id,
            pending.target_name,
            CASE
                WHEN qualified.candidate_count = 1 THEN qualified.symbol_id
                WHEN qualified.candidate_count IS NULL AND named.candidate_count = 1
                    THEN named.symbol_id
                ELSE NULL
            END,
            CASE
                WHEN qualified.candidate_count = 1 THEN 'resolved'
                WHEN qualified.candidate_count > 1 THEN 'ambiguous'
                WHEN named.candidate_count = 1 THEN 'resolved'
                WHEN named.candidate_count > 1 THEN 'ambiguous'
                ELSE 'unresolved'
            END,
            pending.kind,
            pending.path,
            pending.line
        FROM pending_references AS pending
        LEFT JOIN key_stats AS qualified
            ON qualified.key_kind = 'qualified'
            AND qualified.lookup_key = pending.qualified_key
        LEFT JOIN key_stats AS named
            ON named.key_kind = 'name'
            AND named.lookup_key = pending.name_key
        ORDER BY pending.pending_id;
        """
    )


def _reference_name(value: str) -> str:
    result = value.strip()
    result = re_sub_templates(result)
    if "->" in result:
        result = result.rsplit("->", 1)[-1]
    return result


def re_sub_templates(value: str) -> str:
    depth = 0
    output: list[str] = []
    for character in value:
        if character == "<":
            depth += 1
        elif character == ">" and depth:
            depth -= 1
        elif depth == 0:
            output.append(character)
    return "".join(output)


def _coderag_version() -> str:
    try:
        return importlib.metadata.version("coderag")
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def _report(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _language_hint(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".c": "cpp",
        ".cc": "cpp",
        ".cpp": "cpp",
        ".cs": "csharp",
        ".h": "cpp",
        ".hpp": "cpp",
        ".inl": "cpp",
        ".ispc": "ispc",
        ".m": "objective-c",
        ".mm": "objective-cpp",
        ".usf": "hlsl",
        ".ush": "hlsl",
    }.get(suffix, "text")
