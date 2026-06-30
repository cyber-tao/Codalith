"""UE-aware MCP tool implementations."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ue_context.coderag.adapter import CodeRAGAdapter
from ue_context.compiler.context_compiler import ContextCompiler
from ue_context.corpus.registry import CorpusRegistry
from ue_context.corpus.source_policy import SourcePolicy
from ue_context.corpus.uri_resolver import URIResolver
from ue_context.gateway.audit import AuditLogger, AuditRecord
from ue_context.gateway.auth import scopes_from_env


@dataclass(slots=True)
class ToolRuntime:
    registry: CorpusRegistry
    resolver: URIResolver
    policy: SourcePolicy
    adapter: CodeRAGAdapter
    compiler: ContextCompiler
    audit: AuditLogger
    scopes: set[str]


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
    compiler = ContextCompiler(registry, adapter)
    audit = AuditLogger(
        audit_log
        or os.getenv("UE_CONTEXT_AUDIT_LOG")
        or str(Path("data") / "audit" / "source_reads.jsonl")
    )
    return ToolRuntime(
        registry=registry,
        resolver=resolver,
        policy=policy,
        adapter=adapter,
        compiler=compiler,
        audit=audit,
        scopes=scopes_from_env(),
    )


class UETools:
    def __init__(self, runtime: ToolRuntime) -> None:
        self.runtime = runtime

    def ue_context(
        self,
        *,
        query: str,
        version: str = "5.7.4",
        project: str | None = None,
        mode: str = "explain",
        max_source_spans: int = 8,
        include_project_overlay: bool = True,
    ) -> dict[str, Any]:
        pack = self.runtime.compiler.compile(
            query=query,
            version=version,
            project=project,
            mode=mode,
            max_source_spans=max_source_spans,
            include_project_overlay=include_project_overlay,
        )
        return pack.as_dict()

    def ue_read_source(
        self,
        *,
        uri: str,
        start_line: int | None = None,
        end_line: int | None = None,
        with_line_numbers: bool = True,
    ) -> dict[str, Any]:
        resolved = self.runtime.resolver.resolve_source(uri)
        if start_line is not None or end_line is not None:
            resolved = type(resolved)(
                uri=resolved.uri,
                scheme=resolved.scheme,
                corpus_id=resolved.corpus_id,
                relative_path=resolved.relative_path,
                source_kind=resolved.source_kind,
                start_line=start_line or resolved.start_line,
                end_line=end_line or resolved.end_line,
            )
        try:
            self.runtime.policy.check(resolved, self.runtime.scopes)
            assert resolved.start_line is not None
            assert resolved.end_line is not None
            content = self.runtime.adapter.get_file(
                resolved.corpus_id,
                resolved.relative_path,
                resolved.start_line,
                resolved.end_line,
            )
            if with_line_numbers:
                content = "\n".join(
                    f"{resolved.start_line + index}|{line}"
                    for index, line in enumerate(content.splitlines())
                )
            line_count = resolved.line_count or 0
            self.runtime.audit.write(
                AuditRecord.create(
                    tool="ue_read_source",
                    uri=uri,
                    corpus_id=resolved.corpus_id,
                    path=resolved.relative_path,
                    start_line=resolved.start_line,
                    end_line=resolved.end_line,
                    line_count=line_count,
                    decision="allowed",
                )
            )
            return {
                "uri": uri,
                "corpus_id": resolved.corpus_id,
                "path": resolved.relative_path,
                "start_line": resolved.start_line,
                "end_line": resolved.end_line,
                "content": content,
            }
        except Exception as exc:
            start = resolved.start_line or 0
            end = resolved.end_line or start
            self.runtime.audit.write(
                AuditRecord.create(
                    tool="ue_read_source",
                    uri=uri,
                    corpus_id=resolved.corpus_id,
                    path=resolved.relative_path,
                    start_line=start,
                    end_line=end,
                    line_count=max(0, end - start + 1),
                    decision="denied",
                    reason=str(exc),
                )
            )
            raise

    def ue_index_status(
        self,
        *,
        version: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        if "index:status" not in self.runtime.scopes:
            return {"error": "Missing required scope: index:status"}
        resolution = self.runtime.registry.resolve(version, project, include_project_overlay=bool(project))
        return {
            "engine": self.runtime.adapter.status(resolution.engine.corpus_id),
            "project": self.runtime.adapter.status(resolution.project.corpus_id)
            if resolution.project
            else None,
        }

    def ue_lookup_symbol(
        self,
        *,
        symbol: str,
        version: str = "5.7.4",
        project: str | None = None,
        kind: str = "any",
        include_examples: bool = True,
    ) -> dict[str, Any]:
        pack = self.ue_context(
            query=symbol,
            version=version,
            project=project,
            mode="api_usage" if include_examples else "explain",
            max_source_spans=8,
        )
        return {"symbol": symbol, "kind": kind, "context": pack}

    def ue_graph(
        self,
        *,
        node: str,
        version: str = "5.7.4",
        project: str | None = None,
        edge_types: list[str] | None = None,
        depth: int = 1,
        max_nodes: int = 80,
    ) -> dict[str, Any]:
        return {
            "node": node,
            "version": version,
            "project": project,
            "edge_types": edge_types or [],
            "depth": depth,
            "max_nodes": max_nodes,
            "nodes": [],
            "edges": [],
            "caveat": "v0 graph endpoint is registered; semantic edge expansion is populated by extractor jobs.",
        }

    def ue_examples(
        self,
        *,
        symbol_or_api: str,
        version: str = "5.7.4",
        project: str | None = None,
        scope: str = "all",
        max_examples: int = 8,
    ) -> dict[str, Any]:
        resolution = self.runtime.registry.resolve(version, project, include_project_overlay=scope in {"project", "all"})
        hits = []
        for corpus in resolution.ordered:
            hits.extend(self.runtime.adapter.search_code(corpus.corpus_id, symbol_or_api, top_k=max_examples))
        return {"symbol_or_api": symbol_or_api, "examples": [hit.as_dict() for hit in hits[:max_examples]]}

    def ue_compare_versions(
        self,
        *,
        target: str,
        from_version: str,
        to_version: str,
        diff_type: str = "summary",
    ) -> dict[str, Any]:
        from_pack = self.ue_context(query=target, version=from_version, mode="compare", max_source_spans=5)
        to_pack = self.ue_context(query=target, version=to_version, mode="compare", max_source_spans=5)
        return {
            "target": target,
            "from_version": from_version,
            "to_version": to_version,
            "diff_type": diff_type,
            "from": from_pack,
            "to": to_pack,
        }


def tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "name": "ue_context",
            "description": "Use first for any Unreal Engine / UE5 source-level question. Returns a version-pinned, source-backed Context Pack.",
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
                },
                "required": ["query"],
            },
        },
        {
            "name": "ue_read_source",
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
            "name": "ue_index_status",
            "description": "Report CodeRAG index status plus UE semantic extractor status.",
            "inputSchema": {"type": "object", "properties": {"version": {"type": "string"}, "project": {"type": "string"}}},
        },
        {
            "name": "ue_lookup_symbol",
            "description": "Resolve a UE C++ or reflection symbol to source-backed context.",
            "inputSchema": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]},
        },
        {
            "name": "ue_graph",
            "description": "Return UE graph neighbors for modules, plugins, symbols, and reflection entities.",
            "inputSchema": {"type": "object", "properties": {"node": {"type": "string"}}, "required": ["node"]},
        },
        {
            "name": "ue_examples",
            "description": "Find real usages of a UE API or symbol in engine and project source.",
            "inputSchema": {"type": "object", "properties": {"symbol_or_api": {"type": "string"}}, "required": ["symbol_or_api"]},
        },
        {
            "name": "ue_compare_versions",
            "description": "Compare a UE symbol, module, file, or mechanism across engine versions.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "from_version": {"type": "string"},
                    "to_version": {"type": "string"},
                },
                "required": ["target", "from_version", "to_version"],
            },
        },
    ]


def call_tool(tools: UETools, name: str, arguments: dict[str, Any]) -> Any:
    if not hasattr(tools, name):
        raise ValueError(f"Unknown tool: {name}")
    method = getattr(tools, name)
    return method(**arguments)
