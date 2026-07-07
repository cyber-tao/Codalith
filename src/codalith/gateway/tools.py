"""UE-aware MCP tool implementations."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from codalith.cards.hashing import source_sha256
from codalith.coderag.adapter import CodeRAGAdapter, RetrievalHit
from codalith.compiler.context_compiler import ContextCompiler
from codalith.corpus.registry import CorpusRegistry
from codalith.corpus.source_policy import SourcePolicy, SourceReadRateLimiter
from codalith.corpus.uri_resolver import ResolvedURI, URIResolver
from codalith.errors import SourcePolicyError
from codalith.gateway.audit import AuditLogger, AuditRecord
from codalith.gateway.auth import AuthContext, current_auth_context
from codalith.semantic.db import SemanticStore
from codalith.semantic.graph import query_graph


@dataclass(slots=True)
class ToolRuntime:
    registry: CorpusRegistry
    resolver: URIResolver
    policy: SourcePolicy
    adapter: CodeRAGAdapter
    compiler: ContextCompiler
    audit: AuditLogger
    identity: AuthContext
    semantic_store: SemanticStore | None = None
    rate_limiter: SourceReadRateLimiter | None = None


def create_runtime(
    *,
    registry_path: str = "configs/corpus_registry.yaml",
    source_policy_path: str = "configs/source_policy.yaml",
    audit_log: str | None = None,
) -> ToolRuntime:
    registry = CorpusRegistry.from_file(registry_path)
    resolver = URIResolver(registry)
    policy = SourcePolicy.from_file(source_policy_path)
    adapter = CodeRAGAdapter(registry)
    semantic_target = os.getenv("CODALITH_SEMANTIC_DSN") or os.getenv("CODALITH_SEMANTIC_DB") or str(
        Path("data") / "semantic" / "codalith.sqlite"
    )
    semantic_store = SemanticStore(semantic_target)
    compiler = ContextCompiler(
        registry,
        adapter,
        semantic_store=semantic_store,
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
        adapter=adapter,
        compiler=compiler,
        audit=audit,
        identity=AuthContext.local(),
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
        version: str = "5.7.4",
        project: str | None = None,
        mode: str = "explain",
        max_source_spans: int = 8,
        include_project_overlay: bool = True,
        include_generated_overlay: bool = False,
    ) -> dict[str, Any]:
        resolution = self.runtime.registry.resolve(
            version,
            project,
            include_project_overlay,
            include_generated_overlay=include_generated_overlay,
        )
        self._require_resolution_access(resolution)
        pack = self.runtime.compiler.compile(
            query=query,
            version=version,
            project=project,
            mode=mode,
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
            self._require_corpus_access(resolved.corpus_id)
            self.runtime.policy.check(resolved, self._scopes())
            assert resolved.start_line is not None
            assert resolved.end_line is not None
            line_count = resolved.line_count or 0
            if self.runtime.rate_limiter is not None:
                self.runtime.rate_limiter.record_read(
                    line_count=line_count,
                    path=resolved.relative_path,
                    start_line=resolved.start_line,
                    end_line=resolved.end_line,
                )
            content = self.runtime.adapter.get_file(
                resolved.corpus_id,
                resolved.relative_path,
                resolved.start_line,
                resolved.end_line,
            )
            source_hash = source_sha256(content)
            auth = self._auth()
            if with_line_numbers:
                content = "\n".join(
                    f"{resolved.start_line + index}|{line}"
                    for index, line in enumerate(content.splitlines())
                )
            self.runtime.audit.write(
                AuditRecord.create(
                    tool="codalith_read_source",
                    uri=uri,
                    corpus_id=resolved.corpus_id,
                    path=resolved.relative_path,
                    start_line=resolved.start_line,
                    end_line=resolved.end_line,
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
                "start_line": resolved.start_line,
                "end_line": resolved.end_line,
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
        version: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        if "index:status" not in self._scopes():
            return {"error": "Missing required scope: index:status"}
        resolution = self.runtime.registry.resolve(version, project, include_project_overlay=bool(project))
        self._require_resolution_access(resolution)
        semantic = {
            "engine": self.runtime.semantic_store.semantic_status(resolution.engine.corpus_id)
            if self.runtime.semantic_store
            else None,
            "project": self.runtime.semantic_store.semantic_status(resolution.project.corpus_id)
            if self.runtime.semantic_store and resolution.project
            else None,
        }
        return {
            "engine": self.runtime.adapter.status(resolution.engine.corpus_id),
            "project": self.runtime.adapter.status(resolution.project.corpus_id)
            if resolution.project
            else None,
            "semantic": semantic,
        }

    def codalith_lookup_symbol(
        self,
        *,
        symbol: str,
        version: str = "5.7.4",
        project: str | None = None,
        kind: str = "any",
        include_examples: bool = True,
    ) -> dict[str, Any]:
        resolution = self.runtime.registry.resolve(version, project, include_project_overlay=bool(project))
        self._require_resolution_access(resolution)
        semantic_matches: list[dict[str, Any]] = []
        if self.runtime.semantic_store is not None:
            for corpus in resolution.ordered:
                semantic_matches.extend(
                    {
                        **row,
                        "corpus_id": corpus.corpus_id,
                    }
                    for row in self.runtime.semantic_store.find_symbols(
                        corpus.corpus_id,
                        symbol,
                        kind=kind,
                        limit=20,
                    )
                )
        pack = self.codalith_context(
            query=symbol,
            version=version,
            project=project,
            mode="api_usage" if include_examples else "explain",
            max_source_spans=8,
        )
        graph = self.codalith_graph(node=symbol, version=version, project=project, max_nodes=24)
        examples = (
            self.codalith_examples(
                symbol_or_api=symbol,
                version=version,
                project=project,
                max_examples=5,
            )["examples"]
            if include_examples
            else []
        )
        return {
            "symbol": symbol,
            "kind": kind,
            "semantic_matches": semantic_matches,
            "definitions": [
                match
                for match in semantic_matches
                if match.get("definition_uri") or match.get("declaration_uri")
            ],
            "modules": sorted(
                {str(match["module_name"]) for match in semantic_matches if match.get("module_name")}
            ),
            "context": pack,
            "graph": graph,
            "examples": examples,
        }

    def codalith_graph(
        self,
        *,
        node: str,
        version: str = "5.7.4",
        project: str | None = None,
        edge_types: list[str] | None = None,
        depth: int = 1,
        max_nodes: int = 80,
    ) -> dict[str, Any]:
        if "graph:read" not in self._scopes():
            return {"error": "Missing required scope: graph:read"}
        resolution = self.runtime.registry.resolve(version, project, include_project_overlay=bool(project))
        self._require_resolution_access(resolution)
        if self.runtime.semantic_store is None:
            return {
                "node": node,
                "version": version,
                "project": project,
                "edge_types": edge_types or [],
                "depth": depth,
                "max_nodes": max_nodes,
                "nodes": [],
                "edges": [],
                "caveat": "Semantic graph store is not configured.",
            }
        nodes: dict[str, dict[str, Any]] = {}
        edges: dict[tuple[object, object, object], dict[str, Any]] = {}
        for corpus in resolution.ordered:
            result = query_graph(
                self.runtime.semantic_store,
                corpus_id=corpus.corpus_id,
                node=node,
                edge_types=edge_types,
                depth=depth,
                max_nodes=max_nodes,
            )
            for result_node in result["nodes"]:
                if isinstance(result_node, dict):
                    nodes[str(result_node["id"])] = dict(result_node)
            for edge in result["edges"]:
                if isinstance(edge, dict):
                    key = (edge.get("from"), edge.get("edge_type"), edge.get("to"))
                    edges[key] = edge
        return {
            "node": node,
            "version": version,
            "project": project,
            "edge_types": edge_types or [],
            "depth": depth,
            "max_nodes": max_nodes,
            "nodes": list(nodes.values())[:max_nodes],
            "edges": list(edges.values()),
            "caveat": None
            if edges
            else "No semantic graph edges matched this node. Run codalith-extract-semantic with --semantic-db for this corpus.",
        }

    def codalith_examples(
        self,
        *,
        symbol_or_api: str,
        version: str = "5.7.4",
        project: str | None = None,
        scope: str = "all",
        max_examples: int = 8,
    ) -> dict[str, Any]:
        resolution = self.runtime.registry.resolve(
            version,
            project,
            include_project_overlay=scope in {"project", "all"},
            include_generated_overlay=scope == "generated"
            or (scope == "all" and "generated:read" in self._scopes()),
        )
        self._require_resolution_access(resolution)
        hits: list[RetrievalHit] = []
        for corpus in resolution.ordered:
            corpus_hits = self.runtime.adapter.search_code(corpus.corpus_id, symbol_or_api, top_k=max_examples * 2)
            hits.extend(hit for hit in corpus_hits if _scope_matches(scope, hit.path, corpus.kind))
        return {"symbol_or_api": symbol_or_api, "examples": [hit.as_dict() for hit in hits[:max_examples]]}

    def codalith_compare_versions(
        self,
        *,
        target: str,
        from_version: str,
        to_version: str,
        diff_type: str = "summary",
    ) -> dict[str, Any]:
        from_resolution = self.runtime.registry.resolve(from_version)
        to_resolution = self.runtime.registry.resolve(to_version)
        self._require_resolution_access(from_resolution)
        self._require_resolution_access(to_resolution)
        from_pack = self.codalith_context(
            query=target,
            version=from_version,
            mode="compare",
            max_source_spans=5,
        )
        to_pack = self.codalith_context(
            query=target,
            version=to_version,
            mode="compare",
            max_source_spans=5,
        )
        diff = self._semantic_diff(
            target=target,
            diff_type=diff_type,
            from_corpus=from_resolution.engine.corpus_id,
            to_corpus=to_resolution.engine.corpus_id,
        )
        return {
            "target": target,
            "from_version": from_version,
            "to_version": to_version,
            "diff_type": diff_type,
            "diff": diff,
            "from": from_pack,
            "to": to_pack,
        }

    def _auth(self) -> AuthContext:
        return current_auth_context(self.runtime.identity)

    def _scopes(self) -> set[str]:
        return set(self._auth().scopes)

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
            self._require_corpus_access(corpus.corpus_id)

    def _require_corpus_access(self, corpus_id: str) -> None:
        corpus = (
            self.runtime.registry.engines.get(corpus_id)
            or self.runtime.registry.projects.get(corpus_id)
            or self.runtime.registry.generated.get(corpus_id)
        )
        if corpus is None:
            raise ValueError(f"Unknown corpus: {corpus_id}")
        missing = sorted(scope for scope in corpus.access_scopes if scope not in self._scopes())
        if missing:
            raise PermissionError(f"Missing required corpus scope(s) for {corpus_id}: {', '.join(missing)}")

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


def _scope_matches(scope: str, path: str, corpus_kind: str) -> bool:
    if scope == "all":
        return True
    if scope == "project":
        return corpus_kind == "project"
    if scope == "generated":
        return corpus_kind == "generated"
    if corpus_kind == "project":
        return False
    if corpus_kind == "generated":
        return False
    if scope == "plugins":
        return path.startswith("Engine/Plugins/")
    if scope == "tests":
        lowered = path.lower()
        return "/test" in lowered or "/tests/" in lowered or "automation" in lowered
    if scope == "engine":
        return path.startswith("Engine/Source/")
    return True


def _module_dep_key(row: dict[str, Any]) -> tuple[object, ...]:
    return (row["from_module"], row["to_module"], row["dep_kind"])


def _symbol_key(row: dict[str, Any]) -> tuple[object, ...]:
    return (row["name"], row["kind"], row.get("qualified_name"), row.get("signature"))


def tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "name": "codalith_context",
            "description": "Use first for any Unreal Engine / UE5 source-level question. Returns a version-pinned, source-backed Context Pack using CodeRAG retrieval plus UE semantic graph.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "version": {"type": "string", "default": "5.7.4"},
                    "project": {"type": "string"},
                    "mode": {
                        "type": "string",
                        "enum": ["explain", "trace", "implement", "debug", "api_usage", "compare"],
                        "default": "explain",
                    },
                    "max_source_spans": {"type": "integer", "default": 8},
                    "include_project_overlay": {"type": "boolean", "default": True},
                    "include_generated_overlay": {"type": "boolean", "default": False},
                },
                "required": ["query"],
            },
        },
        {
            "name": "codalith_read_source",
            "description": "Read a bounded line range from a versioned UE source URI with policy and audit.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "uri": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                    "with_line_numbers": {"type": "boolean", "default": True},
                },
                "required": ["uri"],
            },
        },
        {
            "name": "codalith_index_status",
            "description": "Report CodeRAG index status plus UE semantic extractor status.",
            "inputSchema": {"type": "object", "properties": {"version": {"type": "string"}, "project": {"type": "string"}}},
        },
        {
            "name": "codalith_lookup_symbol",
            "description": "Resolve a UE C++ or reflection symbol to definitions, declarations, modules, UHT metadata, generated-code relation, references, examples, and source URIs.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "version": {"type": "string", "default": "5.7.4"},
                    "project": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": [
                            "any",
                            "class",
                            "struct",
                            "function",
                            "method",
                            "macro",
                            "module",
                            "uclass",
                            "ufunction",
                            "uproperty",
                        ],
                        "default": "any",
                    },
                    "include_examples": {"type": "boolean", "default": True},
                },
                "required": ["symbol"],
            },
        },
        {
            "name": "codalith_graph",
            "description": "Return UE graph neighbors for modules, plugins, C++ symbols, reflection entities, Build.cs dependencies, include edges, overrides, generated-code relations, and usage examples.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "node": {"type": "string"},
                    "version": {"type": "string", "default": "5.7.4"},
                    "project": {"type": "string"},
                    "edge_types": {"type": "array", "items": {"type": "string"}},
                    "depth": {"type": "integer", "default": 1},
                    "max_nodes": {"type": "integer", "default": 80},
                },
                "required": ["node"],
            },
        },
        {
            "name": "codalith_examples",
            "description": "Find real usages of a UE API or symbol in Engine source, plugins, tests, samples, and project overlay.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "symbol_or_api": {"type": "string"},
                    "version": {"type": "string", "default": "5.7.4"},
                    "project": {"type": "string"},
                    "scope": {
                        "type": "string",
                        "enum": ["engine", "plugins", "tests", "project", "generated", "all"],
                        "default": "all",
                    },
                    "max_examples": {"type": "integer", "default": 8},
                },
                "required": ["symbol_or_api"],
            },
        },
        {
            "name": "codalith_compare_versions",
            "description": "Compare a UE symbol, module, file, or mechanism across engine versions.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "from_version": {"type": "string"},
                    "to_version": {"type": "string"},
                    "diff_type": {
                        "type": "string",
                        "enum": ["summary", "api", "source", "module_deps", "reflection", "behavior"],
                        "default": "summary",
                    },
                },
                "required": ["target", "from_version", "to_version"],
            },
        },
    ]


TOOL_NAMES: frozenset[str] = frozenset(schema["name"] for schema in tool_schemas())


def call_tool(tools: CodalithTools, name: str, arguments: dict[str, Any]) -> Any:
    if name not in TOOL_NAMES:
        raise ValueError(f"Unknown tool: {name}")
    method = getattr(tools, name)
    return method(**arguments)
