"""Upsert operations for the semantic store."""

from __future__ import annotations

import hashlib
from typing import Any

from codalith.cards.schema import KnowledgeCard
from codalith.corpus.registry import Corpus
from codalith.semantic.extractors.build_cs import ModuleDependency
from codalith.semantic.extractors.compile_guards import CompileGuard
from codalith.semantic.extractors.cpp_symbols import CppSymbol
from codalith.semantic.extractors.target_cs import TargetDefinition
from codalith.semantic.extractors.uht_reflection import ReflectionEntity
from codalith.semantic.extractors.uplugin import PluginDescriptor
from codalith.semantic.extractors.uproject import ProjectDescriptor
from codalith.semantic.store.queries import SemanticQueries


class SemanticWriters(SemanticQueries):
    def upsert_module_dep(
        self,
        *,
        corpus_id: str,
        dependency: ModuleDependency,
        evidence_uri: str,
        commit: bool = True,
    ) -> None:
        self.upsert_module(
            corpus_id=corpus_id,
            module_name=dependency.from_module,
            metadata={"observed_from": "Build.cs"},
            commit=False,
        )
        self.upsert_module(
            corpus_id=corpus_id,
            module_name=dependency.to_module,
            metadata={"observed_from": "Build.cs dependency"},
            commit=False,
        )
        if self.dialect == "postgresql":
            sql = """
                INSERT INTO ue_module_deps
                  (corpus_id, from_module, to_module, dep_kind, evidence_uri, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (corpus_id, from_module, to_module, dep_kind)
                DO UPDATE SET evidence_uri = EXCLUDED.evidence_uri,
                              metadata = EXCLUDED.metadata
                """
        else:
            sql = """
                INSERT OR REPLACE INTO ue_module_deps
                  (corpus_id, from_module, to_module, dep_kind, evidence_uri, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
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
            extractor="build_cs",
            metadata=dependency.metadata,
            commit=False,
        )
        if commit:
            self.connection.commit()

    def upsert_corpus(self, corpus: Corpus) -> None:
        if self.dialect == "postgresql":
            sql = """
                INSERT INTO codalith_corpora
                  (corpus_id, kind, ue_version, source_commit, source_root,
                   indexed_root, semantic_schema, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (corpus_id)
                DO UPDATE SET kind = EXCLUDED.kind,
                              ue_version = EXCLUDED.ue_version,
                              source_commit = EXCLUDED.source_commit,
                              source_root = EXCLUDED.source_root,
                              indexed_root = EXCLUDED.indexed_root,
                              semantic_schema = EXCLUDED.semantic_schema,
                              metadata = EXCLUDED.metadata,
                              updated_at = now()
                """
        else:
            sql = """
                INSERT OR REPLACE INTO codalith_corpora
                  (corpus_id, kind, ue_version, source_commit, source_root,
                   indexed_root, semantic_schema, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """
        self._execute(
            sql,
            (
                corpus.corpus_id,
                corpus.kind,
                corpus.ue_version,
                corpus.source_commit,
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
                INSERT OR REPLACE INTO codalith_source_files
                  (corpus_id, path, language, module_name, source_hash, line_count, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
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
        loading_phase: str | None = None,
        supported_platforms: list[str] | None = None,
        public_include_paths: list[str] | None = None,
        private_include_paths: list[str] | None = None,
        source_uri: str | None = None,
        metadata: dict[str, Any] | None = None,
        commit: bool = True,
    ) -> None:
        existing = self.get_module(corpus_id, module_name)
        module_type = module_type or (str(existing["module_type"]) if existing and existing.get("module_type") else None)
        loading_phase = loading_phase or (
            str(existing["loading_phase"]) if existing and existing.get("loading_phase") else None
        )
        supported_platforms = supported_platforms or _json_list(existing, "supported_platforms")
        public_include_paths = public_include_paths or _json_list(existing, "public_include_paths")
        private_include_paths = private_include_paths or _json_list(existing, "private_include_paths")
        if source_uri is None and existing and existing.get("source_uri"):
            source_uri = str(existing["source_uri"])
        merged_metadata = _merge_dict(existing.get("metadata") if existing else None, metadata or {})
        if self.dialect == "postgresql":
            sql = """
                INSERT INTO ue_modules
                  (corpus_id, module_name, module_type, loading_phase,
                   supported_platforms, public_include_paths, private_include_paths,
                   source_uri, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (corpus_id, module_name)
                DO UPDATE SET module_type = EXCLUDED.module_type,
                              loading_phase = EXCLUDED.loading_phase,
                              supported_platforms = EXCLUDED.supported_platforms,
                              public_include_paths = EXCLUDED.public_include_paths,
                              private_include_paths = EXCLUDED.private_include_paths,
                              source_uri = EXCLUDED.source_uri,
                              metadata = EXCLUDED.metadata
                """
        else:
            sql = """
                INSERT OR REPLACE INTO ue_modules
                  (corpus_id, module_name, module_type, loading_phase,
                   supported_platforms, public_include_paths, private_include_paths,
                   source_uri, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
        self._execute(
            sql,
            (
                corpus_id,
                module_name,
                module_type,
                loading_phase,
                self._json(supported_platforms or []),
                self._json(public_include_paths or []),
                self._json(private_include_paths or []),
                source_uri,
                self._json(merged_metadata),
            ),
            commit=commit,
        )

    def upsert_reflection_entity(
        self,
        *,
        corpus_id: str,
        entity: ReflectionEntity,
        commit: bool = True,
    ) -> None:
        if self.dialect == "postgresql":
            sql = """
                INSERT INTO ue_reflection_entities
                  (corpus_id, reflection_id, kind, name, owner_name,
                   module_name, declaration_uri, generated_uri, specifiers, metadata, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (reflection_id)
                DO UPDATE SET kind = EXCLUDED.kind,
                              name = EXCLUDED.name,
                              owner_name = EXCLUDED.owner_name,
                              module_name = EXCLUDED.module_name,
                              declaration_uri = EXCLUDED.declaration_uri,
                              generated_uri = EXCLUDED.generated_uri,
                              specifiers = EXCLUDED.specifiers,
                              metadata = EXCLUDED.metadata,
                              confidence = EXCLUDED.confidence
                """
        else:
            sql = """
                INSERT OR REPLACE INTO ue_reflection_entities
                  (corpus_id, reflection_id, kind, name, owner_name,
                   module_name, declaration_uri, generated_uri, specifiers, metadata, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
        self._execute(
            sql,
            (
                corpus_id,
                f"{corpus_id}:{entity.kind}:{entity.owner or ''}:{entity.name}",
                entity.kind,
                entity.name,
                entity.owner,
                entity.module_name,
                entity.declaration_uri,
                entity.generated_header,
                self._json(entity.specifiers),
                self._json(entity.metadata),
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
        if commit:
            self.connection.commit()

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
        guard_id = _edge_id(corpus_id, f"source:{path}:{guard.line}", guard.macro, guard.expression, evidence_uri)
        if self.dialect == "postgresql":
            sql = """
                INSERT INTO ue_compile_guards
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
                INSERT OR REPLACE INTO ue_compile_guards
                  (corpus_id, guard_id, path, macro, expression, start_line,
                   end_line, evidence_uri, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    def upsert_cpp_symbol(
        self,
        *,
        corpus_id: str,
        path: str,
        symbol: CppSymbol,
        evidence_uri: str,
        module_name: str | None = None,
        commit: bool = True,
    ) -> None:
        symbol_node = f"symbol:{symbol.name}"
        symbol_id = f"{corpus_id}:{symbol.kind}:{symbol.qualified_name or symbol.name}:{path}:{symbol.line}"
        if self.dialect == "postgresql":
            sql = """
                INSERT INTO ue_symbols
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
                INSERT OR REPLACE INTO ue_symbols
                  (corpus_id, symbol_id, name, qualified_name, kind, module_name,
                   declaration_uri, definition_uri, signature, build_guard, metadata, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            extractor="cpp_symbols",
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
                extractor="cpp_symbols",
                metadata={"kind": symbol.kind, "path": path},
                commit=False,
            )
        if commit:
            self.connection.commit()

    def upsert_target(
        self,
        *,
        corpus_id: str,
        target: TargetDefinition,
        evidence_uri: str,
        commit: bool = True,
    ) -> None:
        if self.dialect == "postgresql":
            sql = """
                INSERT INTO ue_targets
                  (corpus_id, target_name, target_type, extra_modules,
                   build_settings, declaration_uri, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (corpus_id, target_name)
                DO UPDATE SET target_type = EXCLUDED.target_type,
                              extra_modules = EXCLUDED.extra_modules,
                              build_settings = EXCLUDED.build_settings,
                              declaration_uri = EXCLUDED.declaration_uri,
                              metadata = EXCLUDED.metadata
                """
        else:
            sql = """
                INSERT OR REPLACE INTO ue_targets
                  (corpus_id, target_name, target_type, extra_modules,
                   build_settings, declaration_uri, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """
        self._execute(
            sql,
            (
                corpus_id,
                target.name,
                target.target_type,
                self._json(target.extra_modules),
                target.build_settings,
                evidence_uri,
                self._json(target.metadata),
            ),
            commit=False,
        )
        target_node = f"target:{target.name}"
        for module in target.extra_modules:
            self.upsert_module(corpus_id=corpus_id, module_name=module, commit=False)
            self.upsert_graph_edge(
                corpus_id=corpus_id,
                from_node=target_node,
                edge_type="target_uses_module",
                to_node=f"module:{module}",
                evidence_uri=evidence_uri,
                extractor="target_cs",
                metadata={"target_type": target.target_type, "build_settings": target.build_settings},
                commit=False,
            )
        if commit:
            self.connection.commit()

    def upsert_plugin(
        self,
        *,
        corpus_id: str,
        plugin: PluginDescriptor,
        evidence_uri: str,
        commit: bool = True,
    ) -> None:
        if self.dialect == "postgresql":
            sql = """
                INSERT INTO ue_plugins
                  (corpus_id, plugin_name, path, modules, supported_platforms, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (corpus_id, plugin_name)
                DO UPDATE SET path = EXCLUDED.path,
                              modules = EXCLUDED.modules,
                              supported_platforms = EXCLUDED.supported_platforms,
                              metadata = EXCLUDED.metadata
                """
        else:
            sql = """
                INSERT OR REPLACE INTO ue_plugins
                  (corpus_id, plugin_name, path, modules, supported_platforms, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                """
        self._execute(
            sql,
            (
                corpus_id,
                plugin.name,
                plugin.path,
                self._json([module.as_dict() for module in plugin.modules]),
                self._json(plugin.supported_platforms),
                self._json(plugin.metadata),
            ),
            commit=False,
        )
        plugin_node = f"plugin:{plugin.name}"
        for module in plugin.modules:
            self.upsert_module(
                corpus_id=corpus_id,
                module_name=module.name,
                module_type=module.module_type,
                loading_phase=module.loading_phase,
                supported_platforms=module.supported_platforms,
                source_uri=evidence_uri,
                metadata={"plugin": plugin.name},
                commit=False,
            )
            self.upsert_graph_edge(
                corpus_id=corpus_id,
                from_node=plugin_node,
                edge_type="plugin_contains_module",
                to_node=f"module:{module.name}",
                evidence_uri=evidence_uri,
                extractor="uplugin",
                metadata=module.as_dict(),
                commit=False,
            )
        if commit:
            self.connection.commit()

    def upsert_project(
        self,
        *,
        corpus_id: str,
        project: ProjectDescriptor,
        evidence_uri: str,
        commit: bool = True,
    ) -> None:
        if self.dialect == "postgresql":
            sql = """
                INSERT INTO ue_projects
                  (corpus_id, project_name, path, modules, plugins, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (corpus_id, project_name)
                DO UPDATE SET path = EXCLUDED.path,
                              modules = EXCLUDED.modules,
                              plugins = EXCLUDED.plugins,
                              metadata = EXCLUDED.metadata
                """
        else:
            sql = """
                INSERT OR REPLACE INTO ue_projects
                  (corpus_id, project_name, path, modules, plugins, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                """
        self._execute(
            sql,
            (
                corpus_id,
                project.name,
                project.path,
                self._json([module.as_dict() for module in project.modules]),
                self._json(project.plugins),
                self._json(project.metadata),
            ),
            commit=False,
        )
        project_node = f"project:{project.name}"
        for module in project.modules:
            self.upsert_module(
                corpus_id=corpus_id,
                module_name=module.name,
                module_type=module.module_type,
                loading_phase=module.loading_phase,
                supported_platforms=module.supported_platforms,
                source_uri=evidence_uri,
                metadata={"project": project.name},
                commit=False,
            )
            self.upsert_graph_edge(
                corpus_id=corpus_id,
                from_node=project_node,
                edge_type="project_contains_module",
                to_node=f"module:{module.name}",
                evidence_uri=evidence_uri,
                extractor="uproject",
                metadata=module.as_dict(),
                commit=False,
            )
        for plugin_name, enabled in project.plugins.items():
            edge_type = "project_enables_plugin" if enabled else "project_disables_plugin"
            self.upsert_graph_edge(
                corpus_id=corpus_id,
                from_node=project_node,
                edge_type=edge_type,
                to_node=f"plugin:{plugin_name}",
                evidence_uri=evidence_uri,
                extractor="uproject",
                metadata={"enabled": enabled},
                commit=False,
            )
        if commit:
            self.connection.commit()

    def upsert_knowledge_card(self, card: KnowledgeCard) -> None:
        if self.dialect == "postgresql":
            sql = """
                INSERT INTO knowledge_cards
                  (corpus_id, card_id, card_type, title, version,
                   verification_status, related_nodes, source_hashes, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (corpus_id, card_id)
                DO UPDATE SET card_type = EXCLUDED.card_type,
                              title = EXCLUDED.title,
                              version = EXCLUDED.version,
                              verification_status = EXCLUDED.verification_status,
                              related_nodes = EXCLUDED.related_nodes,
                              source_hashes = EXCLUDED.source_hashes,
                              metadata = EXCLUDED.metadata
                """
        else:
            sql = """
                INSERT OR REPLACE INTO knowledge_cards
                  (corpus_id, card_id, card_type, title, version,
                   verification_status, related_nodes, source_hashes, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
        self._execute(
            sql,
            (
                card.corpus_id,
                card.card_id,
                card.card_type,
                card.title,
                card.version,
                card.verification_status,
                self._json(card.related_nodes),
                self._json(card.source_hashes),
                self._json({"generated_by": card.generated_by}),
            ),
            commit=True,
        )

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
        if self.dialect == "postgresql":
            sql = """
                INSERT INTO codalith_graph_edges
                  (corpus_id, edge_id, from_node, to_node, edge_type, evidence_uri,
                   extractor, confidence, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (edge_id)
                DO UPDATE SET from_node = EXCLUDED.from_node,
                              to_node = EXCLUDED.to_node,
                              edge_type = EXCLUDED.edge_type,
                              evidence_uri = EXCLUDED.evidence_uri,
                              extractor = EXCLUDED.extractor,
                              confidence = EXCLUDED.confidence,
                              metadata = EXCLUDED.metadata
                """
        else:
            sql = """
                INSERT OR REPLACE INTO codalith_graph_edges
                  (corpus_id, edge_id, from_node, to_node, edge_type, evidence_uri,
                   extractor, confidence, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
        self._execute(
            sql,
            (
                corpus_id,
                edge_id,
                from_node,
                to_node,
                edge_type,
                evidence_uri,
                extractor,
                confidence,
                self._json(metadata or {}),
            ),
        )
        if commit:
            self.connection.commit()


def _json_list(row: dict[str, Any] | None, key: str) -> list[str]:
    if not row:
        return []
    value = row.get(key)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _merge_dict(left: object, right: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(left) if isinstance(left, dict) else {}
    merged.update(right)
    return merged


def _edge_id(
    corpus_id: str,
    from_node: str,
    edge_type: str,
    to_node: str,
    evidence_uri: str | None,
) -> str:
    raw = "\x1f".join([corpus_id, from_node, edge_type, to_node, evidence_uri or ""])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()
