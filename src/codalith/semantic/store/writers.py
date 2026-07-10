"""Upsert operations for the semantic store."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from codalith.corpus.registry import Corpus
from codalith.semantic.store.queries import SemanticQueries
from codalith.semantic.types import CompileGuard, ModuleDependency, SourceSymbol


class SemanticWriters(SemanticQueries):
    def upsert_module_dep(
        self,
        *,
        corpus_id: str,
        dependency: ModuleDependency,
        evidence_uri: str,
        extractor: str = "module_deps",
        observed_from: str | None = None,
        commit: bool = True,
    ) -> None:
        self.upsert_module(
            corpus_id=corpus_id,
            module_name=dependency.from_module,
            metadata={"observed_from": observed_from} if observed_from else {},
            commit=False,
        )
        self.upsert_module(
            corpus_id=corpus_id,
            module_name=dependency.to_module,
            metadata={"observed_from": f"{observed_from} dependency"} if observed_from else {},
            commit=False,
        )
        if self.dialect == "postgresql":
            sql = """
                INSERT INTO codalith_module_deps
                  (corpus_id, from_module, to_module, dep_kind, evidence_uri, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (corpus_id, from_module, to_module, dep_kind)
                DO UPDATE SET evidence_uri = EXCLUDED.evidence_uri,
                              metadata = EXCLUDED.metadata
                """
        else:
            sql = """
                INSERT INTO codalith_module_deps
                  (corpus_id, from_module, to_module, dep_kind, evidence_uri, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (corpus_id, from_module, to_module, dep_kind)
                DO UPDATE SET evidence_uri = excluded.evidence_uri,
                              metadata = excluded.metadata
                """
        self._execute(
            sql,
            (
                corpus_id,
                dependency.from_module,
                dependency.to_module,
                dependency.dep_kind,
                evidence_uri,
                self._json(dependency.metadata),
            ),
        )
        self.upsert_graph_edge(
            corpus_id=corpus_id,
            from_node=f"module:{dependency.from_module}",
            edge_type=f"module_{dependency.dep_kind}_dependency",
            to_node=f"module:{dependency.to_module}",
            evidence_uri=evidence_uri,
            extractor=extractor,
            metadata=dependency.metadata,
            commit=False,
        )
        if commit:
            self.connection.commit()

    def upsert_corpus(self, corpus: Corpus) -> None:
        if self.dialect == "postgresql":
            sql = """
                INSERT INTO codalith_corpora
                  (corpus_id, kind, version, source_revision, source_root,
                   indexed_root, semantic_schema, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (corpus_id)
                DO UPDATE SET kind = EXCLUDED.kind,
                              version = EXCLUDED.version,
                              source_revision = EXCLUDED.source_revision,
                              source_root = EXCLUDED.source_root,
                              indexed_root = EXCLUDED.indexed_root,
                              semantic_schema = EXCLUDED.semantic_schema,
                              metadata = EXCLUDED.metadata,
                              updated_at = now()
                """
        else:
            sql = """
                INSERT INTO codalith_corpora
                  (corpus_id, kind, version, source_revision, source_root,
                   indexed_root, semantic_schema, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (corpus_id)
                DO UPDATE SET kind = excluded.kind,
                              version = excluded.version,
                              source_revision = excluded.source_revision,
                              source_root = excluded.source_root,
                              indexed_root = excluded.indexed_root,
                              semantic_schema = excluded.semantic_schema,
                              metadata = excluded.metadata,
                              updated_at = CURRENT_TIMESTAMP
                """
        self._execute(
            sql,
            (
                corpus.corpus_id,
                corpus.kind,
                corpus.version,
                corpus.source_revision,
                str(corpus.source_root),
                str(corpus.indexed_root),
                corpus.semantic_schema,
                self._json({"access_scopes": sorted(corpus.access_scopes)}),
            ),
            commit=True,
        )

    def upsert_source_file(
        self,
        *,
        corpus_id: str,
        path: str,
        language: str,
        line_count: int,
        module_name: str | None = None,
        source_hash: str | None = None,
        metadata: dict[str, Any] | None = None,
        commit: bool = True,
    ) -> None:
        if self.dialect == "postgresql":
            sql = """
                INSERT INTO codalith_source_files
                  (corpus_id, path, language, module_name, source_hash, line_count, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (corpus_id, path)
                DO UPDATE SET language = EXCLUDED.language,
                              module_name = EXCLUDED.module_name,
                              source_hash = EXCLUDED.source_hash,
                              line_count = EXCLUDED.line_count,
                              metadata = EXCLUDED.metadata,
                              updated_at = now()
                """
        else:
            sql = """
                INSERT INTO codalith_source_files
                  (corpus_id, path, language, module_name, source_hash, line_count, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (corpus_id, path)
                DO UPDATE SET language = excluded.language,
                              module_name = excluded.module_name,
                              source_hash = excluded.source_hash,
                              line_count = excluded.line_count,
                              metadata = excluded.metadata,
                              updated_at = CURRENT_TIMESTAMP
                """
        self._execute(
            sql,
            (
                corpus_id,
                path,
                language,
                module_name,
                source_hash,
                line_count,
                self._json(metadata or {}),
            ),
            commit=commit,
        )

    def upsert_module(
        self,
        *,
        corpus_id: str,
        module_name: str,
        module_type: str | None = None,
        source_uri: str | None = None,
        metadata: dict[str, Any] | None = None,
        commit: bool = True,
    ) -> None:
        existing = self.get_module(corpus_id, module_name)
        module_type = module_type or (
            str(existing["module_type"]) if existing and existing.get("module_type") else None
        )
        if source_uri is None and existing and existing.get("source_uri"):
            source_uri = str(existing["source_uri"])
        merged_metadata = _merge_dict(existing.get("metadata") if existing else None, metadata or {})
        if self.dialect == "postgresql":
            sql = """
                INSERT INTO codalith_modules
                  (corpus_id, module_name, module_type, source_uri, metadata)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (corpus_id, module_name)
                DO UPDATE SET module_type = EXCLUDED.module_type,
                              source_uri = EXCLUDED.source_uri,
                              metadata = EXCLUDED.metadata
                """
        else:
            sql = """
                INSERT INTO codalith_modules
                  (corpus_id, module_name, module_type, source_uri, metadata)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (corpus_id, module_name)
                DO UPDATE SET module_type = excluded.module_type,
                              source_uri = excluded.source_uri,
                              metadata = excluded.metadata
                """
        self._execute(
            sql,
            (
                corpus_id,
                module_name,
                module_type,
                source_uri,
                self._json(merged_metadata),
            ),
            commit=commit,
        )

    def upsert_compile_guard(
        self,
        *,
        corpus_id: str,
        path: str,
        guard: CompileGuard,
        evidence_uri: str,
        commit: bool = True,
    ) -> None:
        guard_end = guard.end_line or guard.line
        guard_id = _stable_id(
            corpus_id,
            f"source:{path}:{guard.line}",
            guard.macro,
            guard.expression,
            evidence_uri,
        )
        if self.dialect == "postgresql":
            sql = """
                INSERT INTO codalith_compile_guards
                  (corpus_id, guard_id, path, macro, expression, start_line,
                   end_line, evidence_uri, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (guard_id)
                DO UPDATE SET path = EXCLUDED.path,
                              macro = EXCLUDED.macro,
                              expression = EXCLUDED.expression,
                              start_line = EXCLUDED.start_line,
                              end_line = EXCLUDED.end_line,
                              evidence_uri = EXCLUDED.evidence_uri,
                              metadata = EXCLUDED.metadata
                """
        else:
            sql = """
                INSERT INTO codalith_compile_guards
                  (corpus_id, guard_id, path, macro, expression, start_line,
                   end_line, evidence_uri, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (guard_id)
                DO UPDATE SET path = excluded.path,
                              macro = excluded.macro,
                              expression = excluded.expression,
                              start_line = excluded.start_line,
                              end_line = excluded.end_line,
                              evidence_uri = excluded.evidence_uri,
                              metadata = excluded.metadata
                """
        self._execute(
            sql,
            (
                corpus_id,
                guard_id,
                path,
                guard.macro,
                guard.expression,
                guard.line,
                guard_end,
                evidence_uri,
                self._json({}),
            ),
            commit=False,
        )
        self.upsert_graph_edge(
            corpus_id=corpus_id,
            from_node=f"source:{path}",
            edge_type="compile_guard",
            to_node=f"macro:{guard.macro}",
            evidence_uri=evidence_uri,
            extractor="compile_guards",
            metadata={"line": guard.line, "end_line": guard_end, "expression": guard.expression},
            commit=False,
        )
        if commit:
            self.connection.commit()

    def upsert_symbol(
        self,
        *,
        corpus_id: str,
        path: str,
        symbol: SourceSymbol,
        evidence_uri: str,
        module_name: str | None = None,
        commit: bool = True,
    ) -> None:
        symbol_node = f"symbol:{symbol.name}"
        symbol_id = f"{corpus_id}:{symbol.kind}:{symbol.qualified_name or symbol.name}:{path}:{symbol.line}"
        if self.dialect == "postgresql":
            sql = """
                INSERT INTO codalith_symbols
                  (corpus_id, symbol_id, name, qualified_name, kind, module_name,
                   declaration_uri, definition_uri, signature, build_guard, metadata, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (symbol_id)
                DO UPDATE SET name = EXCLUDED.name,
                              qualified_name = EXCLUDED.qualified_name,
                              kind = EXCLUDED.kind,
                              module_name = EXCLUDED.module_name,
                              declaration_uri = EXCLUDED.declaration_uri,
                              definition_uri = EXCLUDED.definition_uri,
                              signature = EXCLUDED.signature,
                              build_guard = EXCLUDED.build_guard,
                              metadata = EXCLUDED.metadata,
                              confidence = EXCLUDED.confidence
                """
        else:
            sql = """
                INSERT INTO codalith_symbols
                  (corpus_id, symbol_id, name, qualified_name, kind, module_name,
                   declaration_uri, definition_uri, signature, build_guard, metadata, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (symbol_id)
                DO UPDATE SET name = excluded.name,
                              qualified_name = excluded.qualified_name,
                              kind = excluded.kind,
                              module_name = excluded.module_name,
                              declaration_uri = excluded.declaration_uri,
                              definition_uri = excluded.definition_uri,
                              signature = excluded.signature,
                              build_guard = excluded.build_guard,
                              metadata = excluded.metadata,
                              confidence = excluded.confidence
                """
        self._execute(
            sql,
            (
                corpus_id,
                symbol_id,
                symbol.name,
                symbol.qualified_name,
                symbol.kind,
                module_name,
                evidence_uri,
                evidence_uri if symbol.is_definition else None,
                symbol.signature,
                symbol.build_guard,
                self._json({"path": path, "line": symbol.line}),
                symbol.confidence,
            ),
            commit=False,
        )
        self.upsert_graph_edge(
            corpus_id=corpus_id,
            from_node=f"source:{path}",
            edge_type="declares_symbol",
            to_node=symbol_node,
            evidence_uri=evidence_uri,
            extractor="symbols",
            metadata={"kind": symbol.kind, "line": symbol.line, "qualified_name": symbol.qualified_name},
            commit=False,
        )
        if module_name:
            self.upsert_graph_edge(
                corpus_id=corpus_id,
                from_node=f"module:{module_name}",
                edge_type="declares_symbol",
                to_node=symbol_node,
                evidence_uri=evidence_uri,
                extractor="symbols",
                metadata={"kind": symbol.kind, "path": path},
                commit=False,
            )
        if commit:
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
        edge_id = _edge_id(corpus_id, from_node, edge_type, to_node)
        existing = self._execute(
            "SELECT evidence_uris, extractor, confidence, metadata "
            "FROM codalith_graph_edges WHERE edge_id = ?",
            (edge_id,),
        ).fetchone()
        evidence_uris = set(_json_list(existing["evidence_uris"]) if existing else [])
        if evidence_uri:
            evidence_uris.add(evidence_uri)
        merged_metadata = _merge_dict(
            _json_mapping(existing["metadata"]) if existing else None,
            metadata or {},
        )
        raw_extractors = merged_metadata.get("extractors")
        extractors = (
            {str(item) for item in raw_extractors if isinstance(item, str)}
            if isinstance(raw_extractors, list)
            else set()
        )
        if existing and existing["extractor"]:
            extractors.add(str(existing["extractor"]))
        extractors.add(extractor)
        merged_metadata["extractors"] = sorted(extractors)
        existing_confidence = float(existing["confidence"]) if existing else 0.0
        merged_confidence = max(existing_confidence, confidence)
        if self.dialect == "postgresql":
            sql = """
                INSERT INTO codalith_graph_edges
                  (corpus_id, edge_id, from_node, to_node, edge_type, evidence_uris,
                   extractor, confidence, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (edge_id)
                DO UPDATE SET from_node = EXCLUDED.from_node,
                              to_node = EXCLUDED.to_node,
                              edge_type = EXCLUDED.edge_type,
                              evidence_uris = EXCLUDED.evidence_uris,
                              extractor = EXCLUDED.extractor,
                              confidence = EXCLUDED.confidence,
                              metadata = EXCLUDED.metadata
                """
        else:
            sql = """
                INSERT INTO codalith_graph_edges
                  (corpus_id, edge_id, from_node, to_node, edge_type, evidence_uris,
                   extractor, confidence, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (edge_id)
                DO UPDATE SET from_node = excluded.from_node,
                              to_node = excluded.to_node,
                              edge_type = excluded.edge_type,
                              evidence_uris = excluded.evidence_uris,
                              extractor = excluded.extractor,
                              confidence = excluded.confidence,
                              metadata = excluded.metadata
                """
        self._execute(
            sql,
            (
                corpus_id,
                edge_id,
                from_node,
                to_node,
                edge_type,
                self._json(sorted(evidence_uris)),
                extractor,
                merged_confidence,
                self._json(merged_metadata),
            ),
        )
        if commit:
            self.connection.commit()


def _merge_dict(left: object, right: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(left) if isinstance(left, dict) else {}
    merged.update(right)
    return merged


def _edge_id(
    corpus_id: str,
    from_node: str,
    edge_type: str,
    to_node: str,
) -> str:
    return _stable_id(corpus_id, from_node, edge_type, to_node)


def _stable_id(*parts: str) -> str:
    raw = "\x1f".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _json_list(value: object) -> list[str]:
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _json_mapping(value: object) -> dict[str, Any]:
    parsed = json.loads(value) if isinstance(value, str) else value
    return dict(parsed) if isinstance(parsed, dict) else {}
