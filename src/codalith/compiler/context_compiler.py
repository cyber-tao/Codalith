"""Compile CodeRAG hits and UE metadata into Context Pack v0."""

from __future__ import annotations

import re
from typing import Any

from codalith.cards.hashing import source_sha256
from codalith.coderag.adapter import CodeRAGAdapter, RetrievalHit, language_for_path
from codalith.compiler.context_pack import ContextPack, ContextSummary
from codalith.compiler.entity_detector import detect_identifiers, detect_modules
from codalith.compiler.evidence_selector import select_source_spans
from codalith.compiler.intent_detector import detect_intent
from codalith.compiler.reranker import rerank
from codalith.compiler.retrieval_planner import plan_queries
from codalith.compiler.source_locator import locate_source_priors
from codalith.corpus.registry import CorpusRegistry
from codalith.semantic.graph import query_graph


class ContextCompiler:
    def __init__(
        self,
        registry: CorpusRegistry,
        adapter: CodeRAGAdapter,
        *,
        semantic_store: Any | None = None,
    ) -> None:
        self.registry = registry
        self.adapter = adapter
        self.semantic_store = semantic_store

    def compile(
        self,
        *,
        query: str,
        version: str = "5.7.4",
        project: str | None = None,
        mode: str | None = None,
        max_source_spans: int = 8,
        include_project_overlay: bool = True,
        include_generated_overlay: bool = False,
    ) -> ContextPack:
        resolution = self.registry.resolve(
            version,
            project,
            include_project_overlay,
            include_generated_overlay=include_generated_overlay,
        )
        intent = detect_intent(query, mode)
        identifiers = detect_identifiers(query)
        modules = detect_modules(query)
        search_top_k = max_source_spans
        raw_hits: list[RetrievalHit] = []
        for corpus in resolution.ordered:
            raw_hits.extend(
                locate_source_priors(
                    corpus,
                    query=query,
                    identifiers=identifiers,
                    max_hits=search_top_k,
                )
            )
            for planned_query in plan_queries(query, identifiers):
                raw_hits.extend(
                    self.adapter.search_code(
                        corpus.corpus_id,
                        planned_query,
                        top_k=search_top_k,
                    )
                )
        hits = rerank(
            _unique_hits(raw_hits),
            identifiers=identifiers,
            max_hits=max_source_spans,
            mode=intent,
        )
        inferred_modules = _module_entries(version, modules, hits)
        source_spans = self._enriched_source_spans(hits)
        source_spans.extend(self._card_evidence_spans(hits))
        # Card evidence must not let the pack exceed the caller's span budget.
        source_spans = source_spans[:max_source_spans]
        graph_edges = self._graph_edges(
            [corpus.corpus_id for corpus in resolution.ordered],
            [*identifiers, *(module["name"] for module in inferred_modules)],
        )
        cards = [
            {
                "uri": hit.uri,
                "title": hit.title,
                # Cards are only indexed after codalith-generate-cards verifies
                # them, so a UE_KNOWLEDGE hit implies a verified card.
                "verification_status": "verified",
            }
            for hit in hits
            if "UE_KNOWLEDGE" in hit.path
        ]
        return ContextPack(
            query=query,
            version=resolution.engine.ue_version or version,
            source_commit=resolution.engine.source_commit,
            project=project,
            intent=intent,
            confidence=_confidence(hits),
            summary=ContextSummary(
                text="Context pack compiled from CodeRAG retrieval, UE URI resolution, and v0 heuristics."
            ),
            modules=inferred_modules,
            symbols=self._symbol_entries([corpus.corpus_id for corpus in resolution.ordered], identifiers, version),
            cards=cards,
            source_spans=source_spans,
            graph_edges=graph_edges,
            caveats=[
                _graph_caveat(graph_edges, self.semantic_store is not None),
                "Exact UE behavior can depend on build target, platform guards, and project overrides.",
            ],
            recommended_next_calls=[
                {
                    "tool": "codalith_read_source",
                    "args": {"uri": span["uri"]},
                }
                for span in source_spans[:3]
            ],
        )

    def _graph_edges(self, corpus_ids: list[str], nodes: list[str], max_edges: int = 24) -> list[dict[str, object]]:
        if self.semantic_store is None:
            return []
        edges: dict[tuple[object, object, object], dict[str, object]] = {}
        for corpus_id in corpus_ids:
            for node in nodes:
                result = query_graph(
                    self.semantic_store,
                    corpus_id=corpus_id,
                    node=node,
                    depth=1,
                    max_nodes=24,
                )
                for edge in result["edges"]:
                    if not isinstance(edge, dict):
                        continue
                    key = (edge.get("from"), edge.get("edge_type"), edge.get("to"))
                    edge = {**edge, "corpus_id": corpus_id}
                    edges[key] = edge
                    if len(edges) >= max_edges:
                        return list(edges.values())
        return list(edges.values())

    def _enriched_source_spans(self, hits: list[RetrievalHit]) -> list[dict[str, object]]:
        spans = select_source_spans(hits)
        for span in spans:
            corpus_id = str(span.get("corpus_id", ""))
            path = str(span.get("path", ""))
            raw_start = span.get("start_line", 0)
            raw_end = span.get("end_line", raw_start)
            start = int(raw_start) if isinstance(raw_start, int | str) else 0
            end = int(raw_end) if isinstance(raw_end, int | str) else start
            hit = next(
                (
                    item
                    for item in hits
                    if item.corpus_id == corpus_id
                    and item.path == path
                    and item.start_line == start
                    and item.end_line == end
                ),
                None,
            )
            if hit is not None:
                span["source_hash"] = source_sha256(hit.snippet)
                span["language"] = hit.language
                span["kind"] = hit.kind
                span["extractor"] = hit.metadata.get("matched_by") or hit.source
                span["confidence"] = min(1.0, max(0.0, hit.score / (hit.score + 1.0)))
            if self.semantic_store is not None and corpus_id and path and start:
                guards = self.semantic_store.guards_for_span(corpus_id, path, start, end)
                if guards:
                    span["guard"] = [
                        {
                            "macro": guard["macro"],
                            "expression": guard["expression"],
                            "start_line": guard["start_line"],
                            "end_line": guard["end_line"],
                        }
                        for guard in guards
                    ]
        return spans

    def _card_evidence_spans(self, hits: list[RetrievalHit]) -> list[dict[str, object]]:
        spans: list[dict[str, object]] = []
        for hit in hits:
            if "UE_KNOWLEDGE" not in hit.path:
                continue
            for uri in _extract_evidence_uris(hit.snippet):
                parsed = _parse_source_uri(uri)
                if parsed is None:
                    continue
                version, path, start, end = parsed
                span: dict[str, object] = {
                    "uri": uri,
                    "path": path,
                    "start_line": start,
                    "end_line": end,
                    "reason": f"Evidence linked from verified card {hit.title}.",
                    "source": "card-evidence",
                    "language": language_for_path(path),
                    "guard": None,
                }
                span.update(self._card_evidence_provenance(version, path, start, end))
                spans.append(span)
        return spans

    def _card_evidence_provenance(
        self,
        version: str,
        path: str,
        start: int,
        end: int,
    ) -> dict[str, object]:
        try:
            corpus = self.registry.get_engine(version)
            snippet = self.adapter.get_file(corpus.corpus_id, path, start, end)
        except Exception:
            # Evidence pointing at an unavailable corpus stays cited but unhashed.
            return {"corpus_id": None, "source_hash": None}
        return {"corpus_id": corpus.corpus_id, "source_hash": source_sha256(snippet)}

    def _symbol_entries(
        self,
        corpus_ids: list[str],
        identifiers: list[str],
        version: str,
    ) -> list[dict[str, object]]:
        entries: list[dict[str, object]] = []
        seen: set[tuple[str, str, str | None]] = set()
        for identifier in identifiers:
            found = False
            if self.semantic_store is not None:
                for corpus_id in corpus_ids:
                    for row in self.semantic_store.find_symbols(corpus_id, identifier, limit=5):
                        key = (str(row["name"]), str(row["kind"]), row.get("declaration_uri"))
                        if key in seen:
                            continue
                        seen.add(key)
                        entries.append(
                            {
                                "name": row["name"],
                                "qualified_name": row.get("qualified_name"),
                                "kind": row["kind"],
                                "module": row.get("module_name"),
                                "uri": row.get("declaration_uri") or f"ue://{version}/symbol/{identifier}",
                                "reason": "Resolved from semantic symbol table.",
                            }
                        )
                        found = True
            if not found:
                entries.append(
                    {
                        "name": identifier,
                        "uri": f"ue://{version}/symbol/{identifier}",
                        "kind": "symbol",
                        "reason": "Identifier detected in the user query.",
                    }
                )
        return entries


def _unique_hits(hits: list[RetrievalHit]) -> list[RetrievalHit]:
    seen: set[tuple[str, str, int, int]] = set()
    out: list[RetrievalHit] = []
    for hit in hits:
        key = (hit.corpus_id, hit.path, hit.start_line, hit.end_line)
        if key not in seen:
            out.append(hit)
            seen.add(key)
    return out


def _module_entries(
    version: str,
    detected_modules: list[str],
    hits: list[RetrievalHit],
) -> list[dict[str, str]]:
    names = list(dict.fromkeys(detected_modules + [hit.module for hit in hits if hit.module]))
    return [
        {
            "name": name,
            "uri": f"ue://{version}/module/{name}",
            "reason": "Detected from query or source path.",
        }
        for name in names
    ]


def _confidence(hits: list[RetrievalHit]) -> str:
    if not hits:
        return "low"
    if any(hit.source == "ue-source-locator" for hit in hits):
        return "high"
    return "medium"


def _extract_evidence_uris(text: str) -> list[str]:
    return re.findall(r"ue://[^\s)]+", text)


def _parse_source_uri(uri: str) -> tuple[str, str, int, int] | None:
    match = re.match(
        r"ue://(?P<version>[^/]+)/source/(?P<path>[^#]+)#L(?P<start>\d+)-L(?P<end>\d+)",
        uri,
    )
    if not match:
        return None
    return (
        match.group("version"),
        match.group("path"),
        int(match.group("start")),
        int(match.group("end")),
    )


def _graph_caveat(graph_edges: list[dict[str, object]], has_store: bool) -> str:
    if graph_edges:
        return "Semantic graph edges are included from extractor output where available."
    if has_store:
        return "Semantic graph store is configured, but no matching graph edges were found for this query."
    return "Semantic graph store is not configured; graph expansion is unavailable for this context pack."
