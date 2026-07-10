"""Read-only queries over the semantic store."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from codalith.semantic.graph import GraphEdge, edge_from_row, node_candidates
from codalith.semantic.store.connection import ConnectionBase


class SemanticQueries(ConnectionBase):
    def list_modules(self, corpus_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
        rows = self._execute(
            """
            SELECT * FROM codalith_modules
            WHERE corpus_id = ?
            ORDER BY module_name
            LIMIT ?
            """,
            (corpus_id, limit),
        ).fetchall()
        return [_row(row) for row in rows]

    def list_module_deps(self, corpus_id: str, module: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM codalith_module_deps WHERE corpus_id = ?"
        params: list[Any] = [corpus_id]
        if module:
            sql += " AND from_module = ?"
            params.append(module)
        rows = self._execute(sql, params).fetchall()
        return [_row(row) for row in rows]

    def list_graph_edges(
        self,
        corpus_id: str,
        *,
        node: str | None = None,
        edge_types: Iterable[str] | None = None,
        limit: int = 200,
    ) -> list[GraphEdge]:
        sql = "SELECT * FROM codalith_graph_edges WHERE corpus_id = ?"
        params: list[Any] = [corpus_id]
        candidates = node_candidates(node) if node else []
        if candidates:
            placeholders = ",".join("?" for _ in candidates)
            sql += f" AND (from_node IN ({placeholders}) OR to_node IN ({placeholders}))"
            params.extend(candidates)
            params.extend(candidates)
        edge_type_values = list(edge_types or [])
        if edge_type_values:
            placeholders = ",".join("?" for _ in edge_type_values)
            sql += f" AND edge_type IN ({placeholders})"
            params.extend(edge_type_values)
        sql += " ORDER BY confidence DESC, edge_type, from_node, to_node LIMIT ?"
        params.append(limit)
        rows = self._execute(sql, params).fetchall()
        return [edge_from_row(_row(row)) for row in rows]

    def get_module(self, corpus_id: str, module_name: str) -> dict[str, Any] | None:
        row = self._execute(
            "SELECT * FROM codalith_modules WHERE corpus_id = ? AND module_name = ?",
            (corpus_id, module_name),
        ).fetchone()
        return _row(row) if row is not None else None

    def module_exists(self, corpus_id: str, module_name: str) -> bool:
        row = self._execute(
            "SELECT 1 AS ok FROM codalith_modules WHERE corpus_id = ? AND module_name = ?",
            (corpus_id, module_name),
        ).fetchone()
        return row is not None

    def find_symbols(
        self,
        corpus_id: str,
        name: str,
        *,
        kind: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        normalized = name.split("::")[-1]
        escaped = (
            normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        sql = """
            SELECT * FROM codalith_symbols
            WHERE corpus_id = ?
              AND (
                LOWER(name) = LOWER(?)
                OR LOWER(qualified_name) = LOWER(?)
                OR LOWER(qualified_name) LIKE LOWER(?) ESCAPE '\\'
              )
            """
        params: list[Any] = [corpus_id, normalized, name, f"%::{escaped}"]
        if kind and kind != "any":
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY confidence DESC, module_name, name LIMIT ?"
        params.append(limit)
        return [_row(row) for row in self._execute(sql, params).fetchall()]

    def symbol_exists(self, corpus_id: str, node: str) -> bool:
        name = node.split(":", maxsplit=1)[-1]
        return bool(self.find_symbols(corpus_id, name, limit=1))

    def guards_for_span(
        self,
        corpus_id: str,
        path: str,
        start_line: int,
        end_line: int,
    ) -> list[dict[str, Any]]:
        rows = self._execute(
            """
            SELECT * FROM codalith_compile_guards
            WHERE corpus_id = ?
              AND path = ?
              AND start_line <= ?
              AND COALESCE(end_line, start_line) >= ?
            ORDER BY start_line, macro
            """,
            (corpus_id, path, end_line, start_line),
        ).fetchall()
        return [_row(row) for row in rows]

    def source_file_exists(self, corpus_id: str, path: str) -> bool:
        row = self._execute(
            """
            SELECT 1 AS ok FROM codalith_source_files
            WHERE corpus_id = ? AND path = ?
            LIMIT 1
            """,
            (corpus_id, path),
        ).fetchone()
        return row is not None

    def semantic_status(self, corpus_id: str) -> dict[str, Any]:
        graph = self._execute(
            "SELECT COUNT(*) AS edge_count FROM codalith_graph_edges WHERE corpus_id = ?",
            (corpus_id,),
        ).fetchone()
        graph_nodes = self._execute(
            """
            SELECT COUNT(*) AS node_count FROM (
                SELECT from_node AS node FROM codalith_graph_edges WHERE corpus_id = ?
                UNION
                SELECT to_node AS node FROM codalith_graph_edges WHERE corpus_id = ?
            ) AS nodes
            """,
            (corpus_id, corpus_id),
        ).fetchone()
        counts = {
            name: self._count(table, corpus_id)
            for name, table in (
                ("source_files", "codalith_source_files"),
                ("modules", "codalith_modules"),
                ("module_dependencies", "codalith_module_deps"),
                ("symbols", "codalith_symbols"),
                ("compile_guards", "codalith_compile_guards"),
            )
        }
        return {
            "corpus_id": corpus_id,
            "dialect": self.dialect,
            **counts,
            "graph_edges": int(graph["edge_count"]),
            "graph_nodes": int(graph_nodes["node_count"] or 0),
        }

    def _count(self, table: str, corpus_id: str) -> int:
        row = self._execute(
            f"SELECT COUNT(*) AS count FROM {table} WHERE corpus_id = ?",  # noqa: S608 - fixed table names.
            (corpus_id,),
        ).fetchone()
        return int(row["count"])


def _row(row: Any) -> dict[str, Any]:
    data = dict(row)
    for key in ("metadata", "evidence_uris"):
        if key in data and isinstance(data[key], str):
            data[key] = json.loads(data[key])
    return data
