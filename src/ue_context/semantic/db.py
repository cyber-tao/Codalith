"""Lightweight semantic store used by extractors and tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ue_context.semantic.extractors.build_cs import ModuleDependency
from ue_context.semantic.extractors.uht_reflection import ReflectionEntity


class SemanticStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.initialize()

    def initialize(self) -> None:
        schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
        self.connection.executescript(schema)
        self.connection.commit()

    def upsert_module_dep(
        self,
        *,
        corpus_id: str,
        dependency: ModuleDependency,
        evidence_uri: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO ue_module_deps
              (corpus_id, from_module, to_module, dep_kind, evidence_uri, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                corpus_id,
                dependency.from_module,
                dependency.to_module,
                dependency.dep_kind,
                evidence_uri,
                json.dumps(dependency.metadata, sort_keys=True),
            ),
        )
        self.connection.commit()

    def list_module_deps(self, corpus_id: str, module: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM ue_module_deps WHERE corpus_id = ?"
        params: list[Any] = [corpus_id]
        if module:
            sql += " AND from_module = ?"
            params.append(module)
        rows = self.connection.execute(sql, params).fetchall()
        return [_row(row) for row in rows]

    def upsert_reflection_entity(self, *, corpus_id: str, entity: ReflectionEntity) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO ue_reflection_entities
              (corpus_id, reflection_id, cpp_symbol_id, kind, name, owner_symbol_id,
               module_name, declaration_uri, generated_uri, specifiers, metadata, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                corpus_id,
                f"{corpus_id}:{entity.kind}:{entity.owner or ''}:{entity.name}",
                None,
                entity.kind,
                entity.name,
                entity.owner,
                entity.module_name,
                entity.declaration_uri,
                entity.generated_header,
                json.dumps(entity.specifiers, sort_keys=True),
                json.dumps(entity.metadata, sort_keys=True),
                entity.confidence,
            ),
        )
        self.connection.commit()

    def list_reflection_entities(self, corpus_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM ue_reflection_entities WHERE corpus_id = ?"
        params: list[Any] = [corpus_id]
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        rows = self.connection.execute(sql, params).fetchall()
        return [_row(row) for row in rows]

    def close(self) -> None:
        self.connection.close()


def _row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for key in ("metadata", "specifiers"):
        if key in data and isinstance(data[key], str):
            data[key] = json.loads(data[key])
    return data
