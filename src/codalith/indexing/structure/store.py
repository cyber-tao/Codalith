"""Read-only access to an immutable structural snapshot."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import cast

from codalith.errors import IndexUnavailableError
from codalith.indexing.structure.models import (
    FileRecord,
    ModuleDependencyRecord,
    ReferenceRecord,
    SymbolRecord,
)


class StructureIndex:
    def __init__(self, path: Path) -> None:
        if not path.is_file():
            raise IndexUnavailableError(f"Structure index does not exist: {path}")
        uri = path.resolve().as_uri() + "?mode=ro"
        self._connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()

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
            f"{_declaration_path_order()}, length(path), path LIMIT ?",
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
            f"{_declaration_path_order()}, length(path), path LIMIT ?",
            (*patterns, *patterns, limit),
        )
        return [FileRecord(**{key: row[key] for key in FileRecord.__annotations__}) for row in rows]

    def get_symbol(self, symbol_id: str) -> SymbolRecord | None:
        row = self._one("SELECT * FROM symbols WHERE symbol_id = ?", (symbol_id,))
        return _symbol(row) if row is not None else None

    def lookup_symbols(
        self,
        query: str,
        *,
        exact: bool = True,
        limit: int = 20,
    ) -> list[SymbolRecord]:
        if exact:
            rows = self._all(
                "SELECT * FROM symbols "
                "WHERE name = ? COLLATE NOCASE OR qualified_name = ? COLLATE NOCASE "
                "ORDER BY CASE WHEN qualified_name = ? THEN 0 WHEN name = ? THEN 1 "
                "WHEN qualified_name = ? COLLATE NOCASE THEN 2 ELSE 3 END, "
                f"{_definition_kind_order()}, "
                f"{_declaration_path_order()}, "
                "length(qualified_name), path, start_line LIMIT ?",
                (query, query, query, query, query, limit),
            )
        else:
            pattern = f"%{_escape_like(query)}%"
            rows = self._all(
                "SELECT * FROM symbols "
                "WHERE name LIKE ? ESCAPE '\\' COLLATE NOCASE "
                "OR qualified_name LIKE ? ESCAPE '\\' COLLATE NOCASE "
                "ORDER BY CASE WHEN name = ? COLLATE NOCASE THEN 0 "
                "WHEN name LIKE ? ESCAPE '\\' COLLATE NOCASE THEN 1 ELSE 2 END, "
                f"{_definition_kind_order()}, "
                f"{_declaration_path_order()}, "
                "length(qualified_name), path, start_line LIMIT ?",
                (pattern, pattern, query, f"{_escape_like(query)}%", limit),
            )
        return [_symbol(row) for row in rows]

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

    def iter_symbol_groups(self) -> Iterator[tuple[str, list[SymbolRecord]]]:
        """Stream comparison-key groups without materializing the symbol table."""

        with self._lock:
            cursor = self._connection.execute(
                "SELECT * FROM symbols ORDER BY comparison_key, path, start_line"
            )
        current_key: str | None = None
        group: list[SymbolRecord] = []
        try:
            while True:
                with self._lock:
                    rows = cursor.fetchmany(1_000)
                if not rows:
                    break
                for row in rows:
                    key = str(row["comparison_key"])
                    if current_key is not None and key != current_key:
                        yield current_key, group
                        group = []
                    current_key = key
                    group.append(_symbol(row))
            if current_key is not None:
                yield current_key, group
        finally:
            with self._lock:
                cursor.close()

    def integrity_check(self) -> str:
        row = self._one("PRAGMA integrity_check", ())
        return str(row[0]) if row is not None else "missing"

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for table in ("files", "symbols", "references", "module_dependencies"):
            quoted = '"references"' if table == "references" else table
            row = self._one(f"SELECT count(*) FROM {quoted}", ())
            result[table] = int(row[0]) if row is not None else 0
        return result

    def _one(self, sql: str, parameters: tuple[object, ...]) -> sqlite3.Row | None:
        with self._lock:
            return cast(sqlite3.Row | None, self._connection.execute(sql, parameters).fetchone())

    def _all(self, sql: str, parameters: tuple[object, ...]) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._connection.execute(sql, parameters).fetchall())


def _symbol(row: sqlite3.Row) -> SymbolRecord:
    raw = dict(row)
    metadata_raw = raw.pop("metadata_json")
    try:
        metadata = json.loads(metadata_raw)
    except (TypeError, json.JSONDecodeError):
        metadata = {"invalid_metadata": True}
    raw["metadata"] = metadata if isinstance(metadata, dict) else {}
    return SymbolRecord(**raw)


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


def _declaration_path_order() -> str:
    return (
        "CASE WHEN lower(path) LIKE '%.h' OR lower(path) LIKE '%.hh' "
        "OR lower(path) LIKE '%.hpp' OR lower(path) LIKE '%.hxx' "
        "OR lower(path) LIKE '%.inl' THEN 0 ELSE 1 END, "
        "CASE WHEN path LIKE 'Public/%' OR path LIKE 'Classes/%' "
        "OR path LIKE '%/Public/%' OR path LIKE '%/Classes/%' THEN 0 "
        "WHEN path LIKE 'Private/%' OR path LIKE '%/Private/%' THEN 2 ELSE 1 END"
    )
