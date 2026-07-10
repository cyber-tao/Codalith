"""MCP tool implementations over the configured source corpora."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

from codalith.cards.hashing import source_sha256
from codalith.coderag import CodeRAGAdapter, RetrievalHit
from codalith.compiler.context_compiler import ContextCompiler
from codalith.corpus.registry import Corpus, CorpusRegistry
from codalith.corpus.source_policy import SourcePolicy, SourceReadRateLimiter
from codalith.corpus.source_reader import SourceReader
from codalith.corpus.uri_resolver import ResolvedURI, URIResolver
from codalith.errors import CorpusNotFoundError, SourcePolicyError
from codalith.gateway.audit import AuditLogger, AuditRecord
from codalith.gateway.auth import AuthContext, AuthError, current_auth_context, default_scopes
from codalith.semantic.graph import aggregate_graph_neighborhood
from codalith.semantic.store import SemanticStore


@dataclass(slots=True)
class ToolRuntime:
    registry: CorpusRegistry
    resolver: URIResolver
    policy: SourcePolicy
    source_reader: SourceReader
    adapter: CodeRAGAdapter
    compiler: ContextCompiler
    audit: AuditLogger
    identity: AuthContext
    semantic_store: SemanticStore | None = None
    rate_limiter: SourceReadRateLimiter | None = None


def create_runtime(
    *,
    registry_path: str | None = None,
    source_policy_path: str | None = None,
    audit_log: str | None = None,
) -> ToolRuntime:
    resolved_registry_path = registry_path
    if resolved_registry_path is None:
        resolved_registry_path = (
            os.getenv("CODALITH_CORPUS_REGISTRY")
            or "configs/sample/registry.json"
        )
    resolved_source_policy_path = source_policy_path
    if resolved_source_policy_path is None:
        resolved_source_policy_path = os.getenv("CODALITH_SOURCE_POLICY") or "configs/source_policy.json"
    registry = CorpusRegistry.from_file(resolved_registry_path)
    resolver = URIResolver(registry)
    policy = SourcePolicy.from_file(resolved_source_policy_path)
    source_reader = SourceReader(registry)
    adapter = CodeRAGAdapter(registry)
    semantic_target = os.getenv("CODALITH_SEMANTIC_DSN") or os.getenv("CODALITH_SEMANTIC_DB")
    semantic_store = SemanticStore(semantic_target) if semantic_target else None
    compiler = ContextCompiler(
        registry,
        adapter,
        semantic_store=semantic_store,
        source_reader=source_reader,
    )
    audit = AuditLogger(
        audit_log
        or os.getenv("CODALITH_AUDIT_LOG")
        or str(Path("data") / "audit" / "source_reads.jsonl")
    )
    return ToolRuntime(
        registry=registry,
        resolver=resolver,
        policy=policy,
        source_reader=source_reader,
        adapter=adapter,
        compiler=compiler,
        audit=audit,
        identity=AuthContext.local(default_scopes(registry)),
        semantic_store=semantic_store,
        rate_limiter=SourceReadRateLimiter(policy),
    )


class CodalithTools:
    def __init__(self, runtime: ToolRuntime) -> None:
        self.runtime = runtime

    def codalith_context(
        self,
        *,
        query: str,
        corpus: str | None = None,
        project: str | None = None,
        mode: str = "auto",
        max_source_spans: int = 8,
        include_project_overlay: bool = True,
        include_generated_overlay: bool = False,
    ) -> dict[str, Any]:
        resolution = self.runtime.registry.resolve(
            corpus,
            project,
            include_project_overlay,
            include_generated_overlay=include_generated_overlay,
        )
        self._require_resolution_access(resolution)
        pack = self.runtime.compiler.compile(
            query=query,
            corpus=corpus,
            project=project,
            mode=None if mode == "auto" else mode,
            max_source_spans=max_source_spans,
            include_project_overlay=include_project_overlay,
            include_generated_overlay=include_generated_overlay,
        )
        return pack.as_dict()

    def codalith_read_source(
        self,
        *,
        uri: str,
        start_line: int | None = None,
        end_line: int | None = None,
        with_line_numbers: bool = True,
    ) -> dict[str, Any]:
        resolved = self.runtime.resolver.resolve_source(uri)
        if start_line is not None or end_line is not None:
            resolved = replace(
                resolved,
                start_line=start_line if start_line is not None else resolved.start_line,
                end_line=end_line if end_line is not None else resolved.end_line,
            )
        try:
            resolved = self._bounded_read_range(resolved)
            self.require_corpus_access(resolved.corpus_id)
            self.runtime.policy.check(resolved, self.scopes())
            auth = self._auth()
            assert resolved.start_line is not None
            assert resolved.end_line is not None
            requested_line_count = resolved.line_count or 0
            if self.runtime.rate_limiter is not None:
                self.runtime.rate_limiter.record_read(
                    line_count=requested_line_count,
                    path=resolved.relative_path,
                    start_line=resolved.start_line,
                    end_line=resolved.end_line,
                    key=f"{auth.user_id}\x1f{auth.session_id}",
                )
            source_slice = self.runtime.source_reader.read_slice(
                resolved.corpus_id,
                resolved.relative_path,
                start_line=resolved.start_line,
                end_line=resolved.end_line,
            )
            line_count = source_slice.line_count
            content = source_slice.content
            source_hash = source_sha256(content)
            if with_line_numbers:
                content = "\n".join(
                    f"{source_slice.start_line + index}|{line}"
                    for index, line in enumerate(content.splitlines())
                )
            self.runtime.audit.write(
                AuditRecord.create(
                    tool="codalith_read_source",
                    uri=uri,
                    corpus_id=resolved.corpus_id,
                    path=resolved.relative_path,
                    start_line=source_slice.start_line,
                    end_line=source_slice.end_line,
                    line_count=line_count,
                    decision="allowed",
                    source_hash=source_hash,
                    user_id=auth.user_id,
                    session_id=auth.session_id,
                    client=auth.client,
                )
            )
            return {
                "uri": uri,
                "corpus_id": resolved.corpus_id,
                "path": resolved.relative_path,
                "start_line": source_slice.start_line,
                "end_line": source_slice.end_line,
                "source_hash": source_hash,
                "content": content,
            }
        except Exception as exc:
            start = resolved.start_line or 0
            end = resolved.end_line or start
            auth = self._auth()
            self.runtime.audit.write(
                AuditRecord.create(
                    tool="codalith_read_source",
                    uri=uri,
                    corpus_id=resolved.corpus_id,
                    path=resolved.relative_path,
                    start_line=start,
                    end_line=end,
                    line_count=max(0, end - start + 1),
                    decision="denied",
                    reason=str(exc),
                    user_id=auth.user_id,
                    session_id=auth.session_id,
                    client=auth.client,
                )
            )
            raise

    def codalith_index_status(
        self,
        *,
        corpus: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        self._require_scope("index:status")
        resolution = self.runtime.registry.resolve(corpus, project, include_project_overlay=bool(project))
        self._require_resolution_access(resolution)
        semantic = {
            "base": self.runtime.semantic_store.semantic_status(resolution.base.corpus_id)
            if self.runtime.semantic_store
            else None,
            "project": self.runtime.semantic_store.semantic_status(resolution.project.corpus_id)
            if self.runtime.semantic_store and resolution.project
            else None,
        }
        return {
            "base": self.runtime.adapter.status(resolution.base.corpus_id),
            "project": self.runtime.adapter.status(resolution.project.corpus_id)
            if resolution.project
            else None,
            "semantic": semantic,
        }

    def codalith_lookup_symbol(
        self,
        *,
        symbol: str,
        corpus: str | None = None,
        project: str | None = None,
        kind: str = "any",
        include_examples: bool = True,
    ) -> dict[str, Any]:
        resolution = self.runtime.registry.resolve(corpus, project, include_project_overlay=bool(project))
        self._require_resolution_access(resolution)
        semantic_matches: list[dict[str, Any]] = []
        if self.runtime.semantic_store is not None:
            for resolved_corpus in resolution.ordered:
                semantic_matches.extend(
                    {
                        **row,
                        "corpus_id": resolved_corpus.corpus_id,
                    }
                    for row in self.runtime.semantic_store.find_symbols(
                        resolved_corpus.corpus_id,
                        symbol,
                        kind=kind,
                        limit=20,
                    )
                )
        pack = self.codalith_context(
            query=symbol,
            corpus=corpus,
            project=project,
            mode="api_usage" if include_examples else "explain",
            max_source_spans=8,
        )
        warnings: list[str] = []
        result: dict[str, Any] = {
            "symbol": symbol,
            "kind": kind,
            "semantic_matches": semantic_matches,
            "definitions": [
                match
                for match in semantic_matches
                if match.get("definition_uri")
            ],
            "declarations": [
                match for match in semantic_matches if match.get("declaration_uri")
            ],
            "modules": sorted(
                {str(match["module_name"]) for match in semantic_matches if match.get("module_name")}
            ),
            "context": pack,
        }
        if "graph:read" in self.scopes():
            result["graph"] = self.codalith_graph(
                node=symbol,
                corpus=corpus,
                project=project,
                max_nodes=24,
            )
        else:
            warnings.append("Graph neighborhood omitted: missing scope graph:read.")
        result["examples"] = (
            self.codalith_examples(
                symbol_or_api=symbol,
                corpus=corpus,
                project=project,
                max_examples=5,
            )["examples"]
            if include_examples
            else []
        )
        if warnings:
            result["warnings"] = warnings
        return result

    def codalith_graph(
        self,
        *,
        node: str,
        corpus: str | None = None,
        project: str | None = None,
        edge_types: list[str] | None = None,
        depth: int = 1,
        max_nodes: int = 80,
    ) -> dict[str, Any]:
        self._require_scope("graph:read")
        resolution = self.runtime.registry.resolve(corpus, project, include_project_overlay=bool(project))
        self._require_resolution_access(resolution)
        resolved_version = resolution.base.version_label
        if self.runtime.semantic_store is None:
            return {
                "node": node,
                "version": resolved_version,
                "project": project,
                "edge_types": edge_types or [],
                "depth": depth,
                "max_nodes": max_nodes,
                "nodes": [],
                "edges": [],
                "caveat": "Semantic graph store is not configured.",
            }
        result = aggregate_graph_neighborhood(
            self.runtime.semantic_store,
            corpus_ids=[corpus.corpus_id for corpus in resolution.ordered],
            seed_nodes=[node],
            edge_types=edge_types,
            depth=depth,
            max_nodes=max_nodes,
        )
        edges = result["edges"]
        return {
            "node": node,
            "version": resolved_version,
            "project": project,
            "edge_types": edge_types or [],
            "depth": depth,
            "max_nodes": max_nodes,
            "nodes": result["nodes"],
            "edges": edges,
            "caveat": None
            if edges
            else "No semantic graph edges matched this node. Run codalith-semantic-status with --semantic-db for this corpus.",
        }

    def codalith_examples(
        self,
        *,
        symbol_or_api: str,
        corpus: str | None = None,
        project: str | None = None,
        scope: str = "all",
        max_examples: int = 8,
    ) -> dict[str, Any]:
        allowed_scopes = set(example_scopes(self.runtime.registry))
        if scope not in allowed_scopes:
            raise ValueError(
                f"Unknown examples scope: {scope!r}; expected one of {sorted(allowed_scopes)}"
            )
        resolution = self.runtime.registry.resolve(
            corpus,
            project,
            include_project_overlay=scope in {"project", "all"},
            include_generated_overlay=scope == "generated"
            or (scope == "all" and "generated:read" in self.scopes()),
        )
        self._require_resolution_access(resolution)
        hits: list[RetrievalHit] = []
        for resolved_corpus in resolution.ordered:
            prefixes = _search_path_prefixes(scope, resolved_corpus)
            if prefixes is None:
                continue
            if not prefixes:
                corpus_hits = self.runtime.adapter.search_code(
                    resolved_corpus.corpus_id,
                    symbol_or_api,
                    top_k=max_examples * 2,
                )
                hits.extend(
                    hit
                    for hit in corpus_hits
                    if _scope_matches(
                        scope,
                        hit.path,
                        resolved_corpus,
                        allowed_scopes=allowed_scopes,
                    )
                )
                continue
            for prefix in prefixes:
                corpus_hits = self.runtime.adapter.search_code(
                    resolved_corpus.corpus_id,
                    symbol_or_api,
                    top_k=max_examples * 2,
                    filters={"path_prefix": prefix},
                )
                hits.extend(
                    hit
                    for hit in corpus_hits
                    if _scope_matches(
                        scope,
                        hit.path,
                        resolved_corpus,
                        allowed_scopes=allowed_scopes,
                    )
                )
        return {
            "symbol_or_api": symbol_or_api,
            "examples": [_example_entry(hit) for hit in hits[:max_examples]],
        }

    def codalith_compare_versions(
        self,
        *,
        target: str,
        from_corpus: str,
        to_corpus: str,
        diff_type: str = "symbols",
    ) -> dict[str, Any]:
        if diff_type not in ("module_deps", "symbols"):
            raise ValueError(f"Unsupported diff_type: {diff_type}")
        from_resolution = self.runtime.registry.resolve(from_corpus)
        to_resolution = self.runtime.registry.resolve(to_corpus)
        self._require_resolution_access(from_resolution)
        self._require_resolution_access(to_resolution)
        from_pack = self.codalith_context(
            query=target,
            corpus=from_corpus,
            mode="compare",
            max_source_spans=5,
        )
        to_pack = self.codalith_context(
            query=target,
            corpus=to_corpus,
            mode="compare",
            max_source_spans=5,
        )
        diff = self._semantic_diff(
            target=target,
            diff_type=diff_type,
            from_corpus=from_resolution.base.corpus_id,
            to_corpus=to_resolution.base.corpus_id,
        )
        return {
            "target": target,
            "from_corpus": from_corpus,
            "to_corpus": to_corpus,
            "diff_type": diff_type,
            "diff": diff,
            "from": from_pack,
            "to": to_pack,
        }

    def _auth(self) -> AuthContext:
        return current_auth_context(self.runtime.identity)

    def scopes(self) -> set[str]:
        return set(self._auth().scopes)

    def _require_scope(self, scope: str) -> None:
        if scope not in self.scopes():
            raise AuthError(f"Missing required scope: {scope}")

    def _bounded_read_range(self, resolved: ResolvedURI) -> ResolvedURI:
        # When a caller omits an explicit range, serve a default bounded window.
        start = resolved.start_line if resolved.start_line is not None else 1
        end = (
            resolved.end_line
            if resolved.end_line is not None
            else start + self.runtime.policy.default_max_lines - 1
        )
        if start < 1:
            raise SourcePolicyError(f"start_line must be >= 1: {start}")
        if end < start:
            raise SourcePolicyError(f"Descending line range: {start}-{end}")
        return replace(resolved, start_line=start, end_line=end)

    def _require_resolution_access(self, resolution: Any) -> None:
        for corpus in resolution.ordered:
            self.require_corpus_access(corpus.corpus_id)

    def require_corpus_access(self, corpus_id: str) -> None:
        corpus = self.runtime.registry.get_corpus(corpus_id)
        missing = sorted(scope for scope in corpus.access_scopes if scope not in self.scopes())
        if missing:
            raise AuthError(f"Missing required corpus scope(s) for {corpus_id}: {', '.join(missing)}")

    def _semantic_diff(
        self,
        *,
        target: str,
        diff_type: str,
        from_corpus: str,
        to_corpus: str,
    ) -> dict[str, Any]:
        if self.runtime.semantic_store is None:
            return {"status": "unavailable", "reason": "Semantic store is not configured."}
        if diff_type == "module_deps":
            from_rows = self.runtime.semantic_store.list_module_deps(from_corpus, target)
            to_rows = self.runtime.semantic_store.list_module_deps(to_corpus, target)
            from_map = {_module_dep_key(row): row for row in from_rows}
            to_map = {_module_dep_key(row): row for row in to_rows}
        else:
            from_rows = self.runtime.semantic_store.find_symbols(from_corpus, target, limit=100)
            to_rows = self.runtime.semantic_store.find_symbols(to_corpus, target, limit=100)
            from_map = {_symbol_key(row): row for row in from_rows}
            to_map = {_symbol_key(row): row for row in to_rows}
        added = [to_map[item] for item in sorted(to_map.keys() - from_map.keys(), key=str)]
        removed = [from_map[item] for item in sorted(from_map.keys() - to_map.keys(), key=str)]
        common = sorted(from_map.keys() & to_map.keys(), key=str)
        changed = [
            {"from": from_map[item], "to": to_map[item]}
            for item in common
            if from_map[item] != to_map[item]
        ]
        return {
            "status": "ok",
            "target": target,
            "diff_type": diff_type,
            "added": added,
            "removed": removed,
            "changed": changed,
            "unchanged_count": len(common) - len(changed),
        }


_FIXED_EXAMPLE_SCOPES = ("tests", "project", "generated", "all")


def example_scopes(registry: CorpusRegistry) -> list[str]:
    """Union of configured path-prefix scopes plus fixed overlay/all scopes."""
    configured: set[str] = set()
    for corpus in registry.corpora.values():
        configured.update(corpus.scope_prefixes)
    for corpus in registry.projects.values():
        configured.update(corpus.scope_prefixes)
    for corpus in registry.generated.values():
        configured.update(corpus.scope_prefixes)
    ordered = sorted(configured)
    for fixed in _FIXED_EXAMPLE_SCOPES:
        if fixed not in configured:
            ordered.append(fixed)
    return ordered


def _search_path_prefixes(scope: str, corpus: Corpus) -> list[str] | None:
    """Return path prefixes to push into retrieval, or None to skip the corpus.

    An empty list means search the whole corpus (still post-filtered by
    ``_scope_matches``). Overlay scopes only search matching corpus kinds.
    """
    if scope == "all":
        return []
    if scope == "project":
        return [] if corpus.kind == "project" else None
    if scope == "generated":
        return [] if corpus.kind == "generated" else None
    if corpus.kind in {"project", "generated"}:
        return None
    if scope == "tests":
        return []
    prefixes = corpus.scope_prefixes.get(scope)
    if not prefixes:
        raise ValueError(f"Unknown examples scope: {scope!r}")
    return list(prefixes)


def _scope_matches(
    scope: str,
    path: str,
    corpus: Corpus,
    *,
    allowed_scopes: set[str] | None = None,
) -> bool:
    if allowed_scopes is not None and scope not in allowed_scopes:
        raise ValueError(f"Unknown examples scope: {scope!r}")
    if scope == "all":
        return True
    if scope == "project":
        return corpus.kind == "project"
    if scope == "generated":
        return corpus.kind == "generated"
    if corpus.kind in {"project", "generated"}:
        return False
    if scope == "tests":
        lowered = path.lower()
        return "/test" in lowered or "/tests/" in lowered or "automation" in lowered
    prefixes = corpus.scope_prefixes.get(scope)
    if not prefixes:
        raise ValueError(f"Unknown examples scope: {scope!r}")
    return any(path.startswith(prefix) for prefix in prefixes)


def _module_dep_key(row: dict[str, Any]) -> tuple[object, ...]:
    return (row["from_module"], row["to_module"], row["dep_kind"])


def _symbol_key(row: dict[str, Any]) -> tuple[object, ...]:
    return (row["name"], row["kind"], row.get("qualified_name"), row.get("signature"))


def _example_entry(hit: RetrievalHit) -> dict[str, Any]:
    return {
        "source": hit.source,
        "corpus_id": hit.corpus_id,
        "uri": hit.uri,
        "path": hit.path,
        "start_line": hit.start_line,
        "end_line": hit.end_line,
        "title": hit.title,
        "score": hit.score,
        "kind": hit.kind,
        "language": hit.language,
        "symbol": hit.symbol,
        "module": hit.module,
        "reason": hit.reason,
        "metadata": hit.metadata,
    }


def _corpus_property(default_corpus: str | None) -> dict[str, Any]:
    prop: dict[str, Any] = {"type": "string"}
    if default_corpus:
        prop["default"] = default_corpus
    return prop


def _object_output_schema(*required: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {key: {} for key in required},
        "required": list(required),
        "additionalProperties": True,
    }


def _tool_schema_data(
    default_corpus: str | None,
    scopes: tuple[str, ...],
) -> list[dict[str, Any]]:
    scope_enum = list(scopes) if scopes else list(_FIXED_EXAMPLE_SCOPES)
    return [
        {
            "name": "codalith_context",
            "description": (
                "Use first for any source-level question about the corpora this server indexes "
                "(see server instructions). Returns a version-pinned, source-backed Context Pack "
                "using CodeRAG retrieval plus the semantic graph."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "corpus": _corpus_property(default_corpus),
                    "project": {"type": "string"},
                    "mode": {
                        "type": "string",
                        "enum": [
                            "auto",
                            "explain",
                            "trace",
                            "implement",
                            "debug",
                            "api_usage",
                            "compare",
                        ],
                        "default": "auto",
                    },
                    "max_source_spans": {"type": "integer", "default": 8, "minimum": 1, "maximum": 20},
                    "include_project_overlay": {"type": "boolean", "default": True},
                    "include_generated_overlay": {"type": "boolean", "default": False},
                },
                "required": ["query"],
            },
            "outputSchema": _object_output_schema(
                "schema_version",
                "query",
                "version",
                "corpus_id",
                "source_revision",
                "source_spans",
            ),
        },
        {
            "name": "codalith_read_source",
            "description": "Read a bounded line range from a versioned source URI with policy and audit.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "uri": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                    "with_line_numbers": {"type": "boolean", "default": True},
                },
                "required": ["uri"],
            },
            "outputSchema": _object_output_schema(
                "uri",
                "corpus_id",
                "path",
                "start_line",
                "end_line",
                "source_hash",
                "content",
            ),
        },
        {
            "name": "codalith_index_status",
            "description": "Report CodeRAG index status plus semantic graph status per corpus.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "corpus": _corpus_property(default_corpus),
                    "project": {"type": "string"},
                },
            },
            "outputSchema": _object_output_schema("base", "project", "semantic"),
        },
        {
            "name": "codalith_lookup_symbol",
            "description": (
                "Resolve a source symbol to definitions, declarations, modules, "
                "examples, graph context, and source URIs."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "corpus": _corpus_property(default_corpus),
                    "project": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "default": "any",
                        "description": (
                            "Symbol kind filter: 'any' or a kind emitted by the corpus "
                            "extractors (e.g. class, struct, function, method, macro, module)."
                        ),
                    },
                    "include_examples": {"type": "boolean", "default": True},
                },
                "required": ["symbol"],
            },
            "outputSchema": _object_output_schema(
                "symbol",
                "kind",
                "semantic_matches",
                "definitions",
                "declarations",
                "modules",
                "context",
                "examples",
            ),
        },
        {
            "name": "codalith_graph",
            "description": "Return semantic graph neighbors for modules, symbols, build dependencies, include edges, and usage examples.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "node": {"type": "string"},
                    "corpus": _corpus_property(default_corpus),
                    "project": {"type": "string"},
                    "edge_types": {"type": "array", "items": {"type": "string"}},
                    "depth": {"type": "integer", "default": 1, "minimum": 1, "maximum": 4},
                    "max_nodes": {"type": "integer", "default": 80, "minimum": 1, "maximum": 200},
                },
                "required": ["node"],
            },
            "outputSchema": _object_output_schema(
                "node",
                "version",
                "nodes",
                "edges",
                "caveat",
            ),
        },
        {
            "name": "codalith_examples",
            "description": (
                "Find real usages of an API or symbol across configured corpus scopes "
                "(path-prefix scopes from the registry, plus tests, project, generated, and all)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "symbol_or_api": {"type": "string"},
                    "corpus": _corpus_property(default_corpus),
                    "project": {"type": "string"},
                    "scope": {
                        "type": "string",
                        "enum": scope_enum,
                        "default": "all",
                    },
                    "max_examples": {"type": "integer", "default": 8, "minimum": 1, "maximum": 50},
                },
                "required": ["symbol_or_api"],
            },
            "outputSchema": _object_output_schema("symbol_or_api", "examples"),
        },
        {
            "name": "codalith_compare_versions",
            "description": "Compare a symbol or module across two base corpora.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "from_corpus": {"type": "string"},
                    "to_corpus": {"type": "string"},
                    "diff_type": {
                        "type": "string",
                        "enum": ["module_deps", "symbols"],
                        "default": "symbols",
                    },
                },
                "required": ["target", "from_corpus", "to_corpus"],
            },
            "outputSchema": _object_output_schema(
                "target",
                "from_corpus",
                "to_corpus",
                "diff_type",
                "diff",
                "from",
                "to",
            ),
        },
    ]


# Single source of truth: tools/list schemas and call_tool dispatch both derive
# from this registry, so a tool cannot be listed without being callable. The
# default corpus and example scopes are injected from the corpus registry.
@lru_cache(maxsize=8)
def _tool_registry(
    default_corpus: str | None,
    scopes: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    return {schema["name"]: schema for schema in _tool_schema_data(default_corpus, scopes)}


def _default_corpus(registry: CorpusRegistry) -> str | None:
    try:
        return registry.get_base(None).corpus_id
    except CorpusNotFoundError:
        return None


def _registry_cache_key(registry: CorpusRegistry) -> tuple[str | None, tuple[str, ...]]:
    return _default_corpus(registry), tuple(example_scopes(registry))


def tool_schemas(registry: CorpusRegistry) -> list[dict[str, Any]]:
    default_corpus, scopes = _registry_cache_key(registry)
    return list(_tool_registry(default_corpus, scopes).values())


def call_tool(tools: CodalithTools, name: str, arguments: dict[str, Any]) -> Any:
    default_corpus, scopes = _registry_cache_key(tools.runtime.registry)
    schema = _tool_registry(default_corpus, scopes).get(name)
    if schema is None:
        raise ValueError(f"Unknown tool: {name}")
    _validate_arguments(name, schema["inputSchema"], arguments)
    method = getattr(tools, name)
    return method(**arguments)


def _validate_arguments(name: str, input_schema: dict[str, Any], arguments: dict[str, Any]) -> None:
    properties = input_schema.get("properties", {})
    missing = [key for key in input_schema.get("required", []) if key not in arguments]
    if missing:
        raise ValueError(f"{name} is missing required argument(s): {', '.join(sorted(missing))}")
    unknown = [key for key in arguments if key not in properties]
    if unknown:
        raise ValueError(f"{name} got unexpected argument(s): {', '.join(sorted(unknown))}")
    for key, value in arguments.items():
        if value is None:
            # No tool argument is nullable; null would silently override the
            # handler default, so callers must omit the key instead.
            raise ValueError(f"{name} argument {key!r} must not be null; omit it instead")
        prop = properties[key]
        _validate_type(name, key, prop, value)
        _validate_range(name, key, prop, value)
        allowed = prop.get("enum")
        if allowed is not None and value not in allowed:
            raise ValueError(f"{name} argument {key!r} must be one of {allowed}, got {value!r}")


def _validate_type(name: str, key: str, prop: dict[str, Any], value: Any) -> None:
    expected = prop.get("type")
    if expected is None:
        return
    if expected == "string" and not isinstance(value, str):
        raise ValueError(f"{name} argument {key!r} must be a string, got {type(value).__name__}")
    if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        raise ValueError(f"{name} argument {key!r} must be an integer, got {type(value).__name__}")
    if expected == "boolean" and not isinstance(value, bool):
        raise ValueError(f"{name} argument {key!r} must be a boolean, got {type(value).__name__}")
    if expected == "array":
        if not isinstance(value, list):
            raise ValueError(f"{name} argument {key!r} must be an array, got {type(value).__name__}")
        item_type = prop.get("items", {}).get("type")
        if item_type == "string" and any(not isinstance(item, str) for item in value):
            raise ValueError(f"{name} argument {key!r} items must be strings")


def _validate_range(name: str, key: str, prop: dict[str, Any], value: Any) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        return
    minimum = prop.get("minimum")
    maximum = prop.get("maximum")
    if isinstance(minimum, int) and value < minimum:
        raise ValueError(f"{name} argument {key!r} must be >= {minimum}, got {value}")
    if isinstance(maximum, int) and value > maximum:
        raise ValueError(f"{name} argument {key!r} must be <= {maximum}, got {value}")
