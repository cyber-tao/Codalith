"""Lightweight semantic store used by extractors and tests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ue_context.semantic.extractors.build_cs import ModuleDependency
from ue_context.semantic.extractors.compile_guards import CompileGuard
from ue_context.semantic.extractors.cpp_symbols import CppSymbol
from ue_context.semantic.extractors.uht_reflection import ReflectionEntity
from ue_context.semantic.graph import GraphEdge, edge_from_row, node_candidates


class SemanticStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
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
        self.upsert_graph_edge(
            corpus_id=corpus_id,
            from_node=f"module:{dependency.from_module}",
            edge_type=f"module_{dependency.dep_kind}_dependency",
            to_node=f"module:{dependency.to_module}",
            evidence_uri=evidence_uri,
            extractor="build_cs",
            metadata=dependency.metadata,
            commit=False,
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
        node = f"reflection:{entity.kind}:{entity.name}"
        if entity.module_name:
            self.upsert_graph_edge(
                corpus_id=corpus_id,
                from_node=f"module:{entity.module_name}",
                edge_type="declares_reflection",
                to_node=node,
                evidence_uri=entity.declaration_uri,
                extractor="uht_reflection",
                confidence=entity.confidence,
                metadata={"kind": entity.kind},
                commit=False,
            )
        self.upsert_graph_edge(
            corpus_id=corpus_id,
            from_node=f"symbol:{entity.name}",
            edge_type="has_reflection",
            to_node=node,
            evidence_uri=entity.declaration_uri,
            extractor="uht_reflection",
            confidence=entity.confidence,
            metadata={"kind": entity.kind, "specifiers": entity.specifiers},
            commit=False,
        )
        if entity.owner:
            self.upsert_graph_edge(
                corpus_id=corpus_id,
                from_node=f"symbol:{entity.owner}",
                edge_type="owns_reflection",
                to_node=node,
                evidence_uri=entity.declaration_uri,
                extractor="uht_reflection",
                confidence=entity.confidence,
                metadata={"kind": entity.kind},
                commit=False,
            )
        rep_notify = entity.metadata.get("rep_notify")
        if isinstance(rep_notify, str) and rep_notify:
            self.upsert_graph_edge(
                corpus_id=corpus_id,
                from_node=node,
                edge_type="replicated_using",
                to_node=f"symbol:{rep_notify}",
                evidence_uri=entity.declaration_uri,
                extractor="uht_reflection",
                confidence=entity.confidence,
                metadata={"property": entity.name},
                commit=False,
            )
        if entity.generated_header:
            self.upsert_graph_edge(
                corpus_id=corpus_id,
                from_node=node,
                edge_type="generated_header",
                to_node=f"file:{entity.generated_header}",
                evidence_uri=entity.declaration_uri,
                extractor="uht_reflection",
                confidence=entity.confidence,
                commit=False,
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

    def upsert_compile_guard(
        self,
        *,
        corpus_id: str,
        path: str,
        guard: CompileGuard,
        evidence_uri: str,
    ) -> None:
        self.upsert_graph_edge(
            corpus_id=corpus_id,
            from_node=f"source:{path}",
            edge_type="compile_guard",
            to_node=f"macro:{guard.macro}",
            evidence_uri=evidence_uri,
            extractor="compile_guards",
            metadata={"line": guard.line, "expression": guard.expression},
        )

    def upsert_cpp_symbol(
        self,
        *,
        corpus_id: str,
        path: str,
        symbol: CppSymbol,
        evidence_uri: str,
        module_name: str | None = None,
    ) -> None:
        symbol_node = f"symbol:{symbol.name}"
        self.upsert_graph_edge(
            corpus_id=corpus_id,
            from_node=f"source:{path}",
            edge_type="declares_symbol",
            to_node=symbol_node,
            evidence_uri=evidence_uri,
            extractor="cpp_symbols",
            metadata={"kind": symbol.kind, "line": symbol.line},
            commit=False,
        )
        if module_name:
            self.upsert_graph_edge(
                corpus_id=corpus_id,
                from_node=f"module:{module_name}",
                edge_type="declares_symbol",
                to_node=symbol_node,
                evidence_uri=evidence_uri,
                extractor="cpp_symbols",
                metadata={"kind": symbol.kind, "path": path},
                commit=False,
            )
        self.connection.commit()

    def upsert_graph_edge(
        self,
        *,
        corpus_id: str,
        from_node: str,
        edge_type: str,
        to_node: str,
        evidence_uri: str | None = None,
        extractor: str,
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
        commit: bool = True,
    ) -> None:
        edge_id = _edge_id(corpus_id, from_node, edge_type, to_node, evidence_uri)
        self.connection.execute(
            """
            INSERT OR REPLACE INTO ue_graph_edges
              (corpus_id, edge_id, from_node, to_node, edge_type, evidence_uri,
               extractor, confidence, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                corpus_id,
                edge_id,
                from_node,
                to_node,
                edge_type,
                evidence_uri,
                extractor,
                confidence,
                json.dumps(metadata or {}, sort_keys=True),
            ),
        )
        if commit:
            self.connection.commit()

    def list_graph_edges(
        self,
        corpus_id: str,
        *,
        node: str | None = None,
        edge_types: Iterable[str] | None = None,
        limit: int = 200,
    ) -> list[GraphEdge]:
        sql = "SELECT * FROM ue_graph_edges WHERE corpus_id = ?"
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
        rows = self.connection.execute(sql, params).fetchall()
        return [edge_from_row(_row(row)) for row in rows]

    def semantic_status(self, corpus_id: str) -> dict[str, Any]:
        graph = self.connection.execute(
            """
            SELECT COUNT(*) AS edge_count,
                   COUNT(DISTINCT from_node) + COUNT(DISTINCT to_node) AS node_observations
            FROM ue_graph_edges
            WHERE corpus_id = ?
            """,
            (corpus_id,),
        ).fetchone()
        module_deps = self.connection.execute(
            "SELECT COUNT(*) AS count FROM ue_module_deps WHERE corpus_id = ?",
            (corpus_id,),
        ).fetchone()
        reflection = self.connection.execute(
            "SELECT COUNT(*) AS count FROM ue_reflection_entities WHERE corpus_id = ?",
            (corpus_id,),
        ).fetchone()
        return {
            "corpus_id": corpus_id,
            "module_dependencies": int(module_deps["count"]),
            "reflection_entities": int(reflection["count"]),
            "graph_edges": int(graph["edge_count"]),
            "graph_node_observations": int(graph["node_observations"] or 0),
        }

    def close(self) -> None:
        self.connection.close()


def _row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for key in ("metadata", "specifiers"):
        if key in data and isinstance(data[key], str):
            data[key] = json.loads(data[key])
    return data


def _edge_id(
    corpus_id: str,
    from_node: str,
    edge_type: str,
    to_node: str,
    evidence_uri: str | None,
) -> str:
    raw = "\x1f".join([corpus_id, from_node, edge_type, to_node, evidence_uri or ""])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()
