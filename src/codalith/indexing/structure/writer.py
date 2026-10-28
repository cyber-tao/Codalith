"""Write the structural SQLite snapshot of one corpus."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from codalith.corpus.generations import STRUCTURE_SCHEMA_VERSION, count_structure_rows
from codalith.corpus.registry import Corpus
from codalith.corpus.source_policy import SourcePolicy
from codalith.errors import IndexBuildError
from codalith.indexing.structure.schema import INDEX_SQL, SCHEMA_SQL, SYMBOL_SEARCH_SQL
from codalith.indexing.structure.store import symbol_rank_order
from codalith.languages import create_adapter
from codalith.languages.base import ExtractionResult, ReferenceObservation, SymbolObservation
from codalith.languages.catalog import (
    RetrievalProfile,
    language_for_path,
    retrieval_profile,
)

_MAX_REPORTED_WARNINGS = 200
_COMPARISON_DIGEST_BYTES = 16


@dataclass(frozen=True, slots=True)
class StructureSummary:
    counts: dict[str, int]
    source_fingerprint: str
    warnings: tuple[str, ...]


@dataclass(slots=True)
class _WarningCollector:
    items: list[str] = field(default_factory=list)
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


def write_structure(
    corpus: Corpus,
    policy: SourcePolicy,
    database: Path,
    *,
    progress: Callable[[str], None] | None = None,
    sources: Iterable[tuple[str, bytes]] | None = None,
    extract: Callable[[str, str], ExtractionResult] | None = None,
) -> StructureSummary:
    """Extract every selected source file of ``corpus`` into a new SQLite file."""

    adapter = create_adapter(corpus.adapter)
    warnings = _WarningCollector()
    fingerprint = hashlib.sha256()
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = OFF")
        connection.execute("PRAGMA synchronous = OFF")
        connection.execute("PRAGMA temp_store = FILE")
        connection.executescript(SCHEMA_SQL)
        _create_symbol_search(connection)
        _create_resolution_tables(connection)
        with connection:
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                ("schema_version", str(STRUCTURE_SCHEMA_VERSION)),
            )
            processed_files = 0
            inputs = (
                sources
                if sources is not None
                else (
                    (path.relative_to(corpus.source_root).as_posix(), path.read_bytes())
                    for path in _source_files(corpus, policy, adapter.supports)
                )
            )
            for relative, raw in inputs:
                if len(raw) > policy.max_file_bytes:
                    warnings.add(f"{relative}: skipped; file exceeds byte limit")
                    continue
                file_hash = hashlib.sha256(raw).hexdigest()
                fingerprint.update(relative.encode("utf-8"))
                fingerprint.update(b"\0")
                fingerprint.update(file_hash.encode("ascii"))
                text = raw.decode("utf-8", errors="replace")
                try:
                    extraction = (extract or adapter.extract)(relative, text)
                except Exception as exc:
                    if extract is not None:
                        raise
                    warnings.add(
                        f"{relative}: structural extraction failed with {type(exc).__name__}: {exc}"
                    )
                    extraction = ExtractionResult(language=language_for_path(relative))
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
                    metadata_json = json.dumps(
                        symbol.metadata,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    symbol_rows.append(
                        (
                            symbol_id,
                            f"{symbol.qualified_name}\0{symbol.kind}",
                            _comparison_digest(
                                symbol.signature,
                                relative,
                                symbol.module,
                                metadata_json,
                            ),
                            symbol.qualified_name,
                            symbol.name,
                            symbol.kind,
                            symbol.signature,
                            relative,
                            symbol.start_line,
                            symbol.end_line,
                            symbol.module,
                            metadata_json,
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
                    "symbol_id, comparison_key, comparison_digest, qualified_name, name, "
                    "kind, signature, path, start_line, end_line, module, "
                    "parent_symbol_id, metadata_json"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                    symbol_rows,
                )
                connection.executemany(
                    "INSERT INTO symbol_keys(key_kind, lookup_key, path, symbol_id) "
                    "VALUES (?, ?, ?, ?)",
                    symbol_key_rows,
                )
                connection.executemany(
                    "INSERT INTO pending_parents(symbol_id, parent_key, path) VALUES (?, ?, ?)",
                    parent_rows,
                )
                processed_files += 1
                if progress is not None and (processed_files == 1 or processed_files % 500 == 0):
                    progress(f"Indexed {processed_files} files; current path: {relative}")
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
            _populate_symbol_search(connection, retrieval_profile(corpus.adapter))
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise IndexBuildError(f"SQLite integrity check failed: {result}")
        counts = count_structure_rows(connection)
        connection.execute("VACUUM")
    finally:
        connection.close()
    return StructureSummary(counts, fingerprint.hexdigest(), warnings.result())


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
                normalized_filename.endswith(extension) for extension in corpus.include_extensions
            ):
                continue
            if supports(path):
                yield path


def _is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


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


def _comparison_digest(
    signature: str,
    path: str,
    module: str | None,
    metadata_json: str,
) -> bytes:
    """Digest exactly the fields codalith_compare compares, so equal groups need no records."""

    payload = json.dumps([signature, path, module, metadata_json], separators=(",", ":"))
    return hashlib.blake2b(payload.encode("ascii"), digest_size=_COMPARISON_DIGEST_BYTES).digest()


def _create_symbol_search(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript(SYMBOL_SEARCH_SQL)
    except sqlite3.OperationalError as exc:
        raise IndexBuildError(
            f"SQLite {sqlite3.sqlite_version} cannot create the symbol search index; "
            "it requires FTS5 with the trigram tokenizer (SQLite 3.34 or newer)"
        ) from exc


def _populate_symbol_search(connection: sqlite3.Connection, profile: RetrievalProfile) -> None:
    connection.execute(
        "INSERT INTO symbol_search(rowid, symbol_id, name, qualified_name) "
        f"SELECT row_number() OVER (ORDER BY {symbol_rank_order(profile)}), "
        "symbol_id, name, qualified_name FROM symbols"
    )
    connection.execute("INSERT INTO symbol_search(symbol_search) VALUES ('optimize')")


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
                WHEN named.candidate_count = 1 THEN 'name_match'
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
