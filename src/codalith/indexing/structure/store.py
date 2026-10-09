"""Read-only access to an immutable structural snapshot."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Generator, Sequence
from pathlib import Path
from typing import cast

from codalith.errors import IndexInvalidError
from codalith.indexing.structure.models import (
    ComparisonGroup,
    FileRecord,
    ModuleDependencyRecord,
    ReferenceRecord,
    SymbolRecord,
)
from codalith.languages.catalog import NEUTRAL_PROFILE, RetrievalProfile

_TRIGRAM_LENGTH = 3
_ID_BATCH = 500
_FETCH_BATCH = 1_000


class StructureIndex:
    def __init__(self, path: Path, profile: RetrievalProfile = NEUTRAL_PROFILE) -> None:
        if not path.is_file():
            raise IndexInvalidError(f"Structure index does not exist: {path}")
        uri = path.resolve().as_uri() + "?mode=ro"
        self._connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._declaration_order = _declaration_path_order(profile)
        self._symbol_order = symbol_rank_order(profile)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def get_file(self, path: str) -> FileRecord | None:
        row = self._one(
            "SELECT path, language, sha256, size_bytes, line_count, module "
            "FROM files WHERE path = ?",
            (path,),
        )
        return FileRecord(**dict(row)) if row is not None else None

    def lookup_files(self, query: str, *, limit: int = 20) -> list[FileRecord]:
        """Find an exact canonical path or basename without touching live source."""

        normalized = query.replace("\\", "/").strip("/")
        if not normalized:
            return []
        suffix = f"%/{_escape_like(normalized)}"
        stem_suffix = f"%/{_escape_like(normalized)}.%" if "." not in normalized else suffix
        rows = self._all(
            "SELECT path, language, sha256, size_bytes, line_count, module "
            "FROM files WHERE path = ? COLLATE NOCASE "
            "OR path LIKE ? ESCAPE '\\' COLLATE NOCASE "
            "OR path LIKE ? ESCAPE '\\' COLLATE NOCASE "
            "ORDER BY CASE WHEN path = ? COLLATE NOCASE THEN 0 ELSE 1 END, "
            f"{self._declaration_order}length(path), path LIMIT ?",
            (normalized, suffix, stem_suffix, normalized, limit),
        )
        return [FileRecord(**dict(row)) for row in rows]

    def list_files(self, *, limit: int | None = None) -> list[FileRecord]:
        sql = (
            "SELECT path, language, sha256, size_bytes, line_count, module FROM files ORDER BY path"
        )
        parameters: tuple[object, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            parameters = (limit,)
        return [FileRecord(**dict(row)) for row in self._all(sql, parameters)]

    def lookup_files_by_terms(
        self,
        terms: list[str],
        *,
        limit: int = 20,
    ) -> list[FileRecord]:
        normalized = [term.casefold().strip() for term in terms if term.strip()]
        if not normalized:
            return []
        patterns = [f"%{_escape_like(term)}%" for term in normalized]
        score = " + ".join(
            "CASE WHEN lower(path) LIKE ? ESCAPE '\\' THEN 1 ELSE 0 END" for _ in patterns
        )
        where = " OR ".join("lower(path) LIKE ? ESCAPE '\\'" for _ in patterns)
        rows = self._all(
            "SELECT path, language, sha256, size_bytes, line_count, module, "
            f"({score}) AS term_hits FROM files WHERE {where} "
            "ORDER BY term_hits DESC, "
            f"{self._declaration_order}length(path), path LIMIT ?",
            (*patterns, *patterns, limit),
        )
        return [FileRecord(**{key: row[key] for key in FileRecord.__annotations__}) for row in rows]

    def get_symbol(self, symbol_id: str) -> SymbolRecord | None:
        row = self._one("SELECT * FROM symbols WHERE symbol_id = ?", (symbol_id,))
        return _symbol(row) if row is not None else None

    def get_symbols(self, symbol_ids: Sequence[str]) -> list[SymbolRecord]:
        """Return symbols in the requested order; every ID must exist in this snapshot."""

        records: dict[str, SymbolRecord] = {}
        for start in range(0, len(symbol_ids), _ID_BATCH):
            batch = tuple(symbol_ids[start : start + _ID_BATCH])
            placeholders = ", ".join("?" for _ in batch)
            for row in self._all(
                f"SELECT * FROM symbols WHERE symbol_id IN ({placeholders})",
                batch,
            ):
                record = _symbol(row)
                records[record.symbol_id] = record
        missing = next((item for item in symbol_ids if item not in records), None)
        if missing is not None:
            raise IndexInvalidError(f"Structure index has no symbol {missing}")
        return [records[symbol_id] for symbol_id in symbol_ids]

    def lookup_symbols(
        self,
        query: str,
        *,
        exact: bool = True,
        limit: int = 20,
    ) -> list[SymbolRecord]:
        if not exact:
            return self._match_symbols(query, limit=limit)
        rows = self._all(
            "SELECT * FROM symbols "
            "WHERE name = ? COLLATE NOCASE OR qualified_name = ? COLLATE NOCASE "
            "ORDER BY CASE WHEN qualified_name = ? THEN 0 WHEN name = ? THEN 1 "
            "WHEN qualified_name = ? COLLATE NOCASE THEN 2 ELSE 3 END, "
            f"{self._symbol_order} LIMIT ?",
            (query, query, query, query, query, limit),
        )
        return [_symbol(row) for row in rows]

    def _match_symbols(self, query: str, *, limit: int) -> list[SymbolRecord]:
        """Rank exact names, then name prefixes, then substrings of either name.

        ``symbol_search`` rowids follow ``symbol_rank_order``, so matches are read in rank
        order and the lookup stops at ``limit`` instead of sorting every match.
        """

        rows = self._all(
            "SELECT * FROM symbols WHERE name = ? COLLATE NOCASE "
            f"ORDER BY {self._symbol_order} LIMIT ?",
            (query, limit),
        )
        found = [_symbol(row) for row in rows]
        needed = limit - len(found)
        if needed > 0:
            exact = {record.symbol_id for record in found}
            ranked = (
                self._search_ranked(query, needed, exact)
                if len(query) >= _TRIGRAM_LENGTH
                else self._scan_ranked(query, needed, exact)
            )
            found.extend(self.get_symbols(ranked))
        return found

    def _search_ranked(self, query: str, needed: int, exclude: set[str]) -> list[str]:
        phrase = '"' + query.replace('"', '""') + '"'
        ranked: list[str] = []
        for expression in (f"name : ^{phrase}", phrase):
            for row in self._all(
                "SELECT symbol_id FROM symbol_search WHERE symbol_search MATCH ? "
                "ORDER BY rowid LIMIT ?",
                (expression, needed + len(exclude)),
            ):
                symbol_id = str(row[0])
                if symbol_id not in exclude and symbol_id not in ranked:
                    ranked.append(symbol_id)
            if len(ranked) >= needed:
                break
        return ranked[:needed]

    def _scan_ranked(self, query: str, needed: int, exclude: set[str]) -> list[str]:
        # Trigrams never match fewer than three characters, so read the search table once in
        # rank order and keep name prefixes ahead of other substring matches.
        pattern = _escape_like(query)
        prefixed: list[str] = []
        contained: list[str] = []
        with self._lock:
            cursor = self._connection.execute(
                "SELECT symbol_id, name LIKE ? ESCAPE '\\' FROM symbol_search "
                "WHERE name LIKE ? ESCAPE '\\' OR qualified_name LIKE ? ESCAPE '\\' "
                "ORDER BY rowid",
                (f"{pattern}%", f"%{pattern}%", f"%{pattern}%"),
            )
        try:
            while len(prefixed) < needed:
                with self._lock:
                    rows = cursor.fetchmany(_FETCH_BATCH)
                if not rows:
                    break
                for row in rows:
                    symbol_id = str(row[0])
                    if symbol_id in exclude:
                        continue
                    if row[1]:
                        prefixed.append(symbol_id)
                    elif len(contained) < needed:
                        contained.append(symbol_id)
        finally:
            with self._lock:
                cursor.close()
        return (prefixed + contained)[:needed]

    def lookup_module(self, name: str) -> list[SymbolRecord]:
        rows = self._all(
            "SELECT * FROM symbols WHERE kind = 'module' "
            "AND (name = ? COLLATE NOCASE OR qualified_name = ? COLLATE NOCASE) "
            "ORDER BY path, start_line LIMIT 2",
            (name, name),
        )
        return [_symbol(row) for row in rows]

    def references(
        self,
        symbol_id: str,
        *,
        direction: str,
        limit: int = 200,
    ) -> list[ReferenceRecord]:
        if direction == "outgoing":
            where = "source_symbol_id = ?"
        elif direction == "incoming":
            where = "target_symbol_id = ?"
        else:
            raise ValueError("direction must be incoming or outgoing")
        rows = self._all(
            f'SELECT * FROM "references" WHERE {where} ORDER BY path, line LIMIT ?',
            (symbol_id, limit),
        )
        return [ReferenceRecord(**dict(row)) for row in rows]

    def module_dependencies(
        self,
        module: str,
        *,
        direction: str,
        limit: int = 200,
    ) -> list[ModuleDependencyRecord]:
        if direction == "outgoing":
            where = "source_module = ?"
        elif direction == "incoming":
            where = "target_module = ?"
        else:
            raise ValueError("direction must be incoming or outgoing")
        rows = self._all(
            f"SELECT * FROM module_dependencies WHERE {where} "
            "ORDER BY source_module, target_module, path, line LIMIT ?",
            (module, limit),
        )
        return [ModuleDependencyRecord(**dict(row)) for row in rows]

    def iter_comparison_groups(self) -> Generator[ComparisonGroup, None, None]:
        """Stream comparison-key groups from the covering comparison index."""

        with self._lock:
            cursor = self._connection.execute(
                "SELECT comparison_key, comparison_digest, symbol_id FROM symbols "
                "ORDER BY comparison_key, comparison_digest, symbol_id"
            )
            cursor.row_factory = None
        key: str | None = None
        digests: list[bytes] = []
        symbol_ids: list[str] = []
        try:
            while True:
                with self._lock:
                    rows = cursor.fetchmany(_FETCH_BATCH)
                if not rows:
                    break
                for row_key, digest, symbol_id in rows:
                    if row_key != key:
                        if key is not None:
                            yield ComparisonGroup(key, tuple(digests), tuple(symbol_ids))
                        key, digests, symbol_ids = row_key, [], []
                    digests.append(digest)
                    symbol_ids.append(symbol_id)
            if key is not None:
                yield ComparisonGroup(key, tuple(digests), tuple(symbol_ids))
        finally:
            with self._lock:
                cursor.close()

    def _one(self, sql: str, parameters: tuple[object, ...]) -> sqlite3.Row | None:
        with self._lock:
            return cast(sqlite3.Row | None, self._connection.execute(sql, parameters).fetchone())

    def _all(self, sql: str, parameters: tuple[object, ...]) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._connection.execute(sql, parameters).fetchall())


def _symbol(row: sqlite3.Row) -> SymbolRecord:
    raw = dict(row)
    raw.pop("comparison_digest")
    metadata_raw = raw.pop("metadata_json")
    try:
        metadata = json.loads(metadata_raw)
    except (TypeError, json.JSONDecodeError):
        metadata = {"invalid_metadata": True}
    raw["metadata"] = metadata if isinstance(metadata, dict) else {}
    return SymbolRecord(**raw)


def symbol_rank_order(profile: RetrievalProfile) -> str:
    """ORDER BY terms ranking definitions, then declarations, then shorter names.

    The builder stores this order as ``symbol_search`` rowids, so queries must use it unchanged.
    """

    return (
        f"{_definition_kind_order()}, {_declaration_path_order(profile)}"
        "length(qualified_name), path, start_line, symbol_id"
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _definition_kind_order() -> str:
    return (
        "CASE kind "
        "WHEN 'type_alias' THEN 0 "
        "WHEN 'class' THEN 1 "
        "WHEN 'struct' THEN 1 "
        "WHEN 'interface' THEN 1 "
        "WHEN 'enum' THEN 1 "
        "WHEN 'module' THEN 1 "
        "WHEN 'target' THEN 1 "
        "WHEN 'namespace' THEN 1 "
        "WHEN 'function' THEN 2 "
        "WHEN 'method' THEN 2 "
        "WHEN 'macro' THEN 2 "
        "ELSE 3 END"
    )


def _declaration_path_order(profile: RetrievalProfile) -> str:
    """Return ORDER BY terms, each followed by a separator, that rank declarations first."""

    terms: list[str] = []
    if profile.declaration_suffixes:
        suffixes = " OR ".join(
            f"lower(path) LIKE {_like_literal('%' + _escape_like(suffix.lower()))} ESCAPE '\\'"
            for suffix in profile.declaration_suffixes
        )
        terms.append(f"CASE WHEN {suffixes} THEN 0 ELSE 1 END")
    if profile.public_dirs or profile.private_dirs:
        terms.append(
            f"CASE WHEN {_directory_match(profile.public_dirs)} THEN 0 "
            f"WHEN {_directory_match(profile.private_dirs)} THEN 2 ELSE 1 END"
        )
    return "".join(f"{term}, " for term in terms)


def _directory_match(directories: tuple[str, ...]) -> str:
    if not directories:
        return "0"
    return " OR ".join(
        f"path LIKE {_like_literal(pattern)} ESCAPE '\\'"
        for directory in directories
        for pattern in (f"{_escape_like(directory)}/%", f"%/{_escape_like(directory)}/%")
    )


def _like_literal(pattern: str) -> str:
    return "'" + pattern.replace("'", "''") + "'"
