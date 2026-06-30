"""Compile CodeRAG hits and UE metadata into Context Pack v0."""

from __future__ import annotations

from ue_context.coderag.adapter import CodeRAGAdapter, RetrievalHit
from ue_context.compiler.context_pack import ContextPack, ContextSummary
from ue_context.compiler.entity_detector import detect_identifiers, detect_modules
from ue_context.compiler.evidence_selector import select_source_spans
from ue_context.compiler.intent_detector import detect_intent
from ue_context.compiler.reranker import rerank
from ue_context.compiler.retrieval_planner import plan_queries
from ue_context.compiler.source_locator import locate_source_priors
from ue_context.corpus.registry import CorpusRegistry


class ContextCompiler:
    def __init__(self, registry: CorpusRegistry, adapter: CodeRAGAdapter) -> None:
        self.registry = registry
        self.adapter = adapter

    def compile(
        self,
        *,
        query: str,
        version: str = "5.7.4",
        project: str | None = None,
        mode: str | None = None,
        max_source_spans: int = 8,
        include_project_overlay: bool = True,
    ) -> ContextPack:
        resolution = self.registry.resolve(version, project, include_project_overlay)
        intent = detect_intent(query, mode)
        identifiers = detect_identifiers(query)
        modules = detect_modules(query)
        raw_hits: list[RetrievalHit] = []
        for corpus in resolution.ordered:
            raw_hits.extend(
                locate_source_priors(
                    corpus,
                    query=query,
                    identifiers=identifiers,
                    max_hits=max_source_spans,
                )
            )
            for planned_query in plan_queries(query, identifiers):
                raw_hits.extend(
                    self.adapter.search_code(
                        corpus.corpus_id,
                        planned_query,
                        top_k=max_source_spans,
                    )
                )
        hits = rerank(_unique_hits(raw_hits), identifiers=identifiers, max_hits=max_source_spans)
        inferred_modules = _module_entries(version, modules, hits)
        source_spans = select_source_spans(hits)
        source_spans.extend(_card_evidence_spans(hits))
        cards = [
            {
                "uri": hit.uri,
                "title": hit.title,
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
            confidence="medium" if hits else "low",
            summary=ContextSummary(
                text="Context pack compiled from CodeRAG retrieval, UE URI resolution, and v0 heuristics."
            ),
            modules=inferred_modules,
            symbols=[
                {
                    "name": identifier,
                    "uri": f"ue://{version}/symbol/{identifier}",
                    "kind": "symbol",
                    "reason": "Identifier detected in the user query.",
                }
                for identifier in identifiers
            ],
            cards=cards,
            source_spans=source_spans,
            graph_edges=[],
            caveats=[
                "v0 retrieval is source-backed but semantic graph expansion is intentionally conservative.",
                "Exact UE behavior can depend on build target, platform guards, and project overrides.",
            ],
            recommended_next_calls=[
                {
                    "tool": "ue_read_source",
                    "args": {"uri": span["uri"]},
                }
                for span in source_spans[:3]
            ],
        )


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


def _card_evidence_spans(hits: list[RetrievalHit]) -> list[dict[str, object]]:
    spans: list[dict[str, object]] = []
    for hit in hits:
        if "UE_KNOWLEDGE" not in hit.path:
            continue
        for uri in _extract_evidence_uris(hit.snippet):
            parsed = _parse_source_uri(uri)
            if parsed is not None:
                path, start, end = parsed
                spans.append(
                    {
                        "uri": uri,
                        "path": path,
                        "start_line": start,
                        "end_line": end,
                        "reason": f"Evidence linked from verified card {hit.title}.",
                        "source": "card-evidence",
                        "guard": None,
                    }
                )
    return spans


def _extract_evidence_uris(text: str) -> list[str]:
    import re

    return re.findall(r"ue://[^\s)]+", text)


def _parse_source_uri(uri: str) -> tuple[str, int, int] | None:
    import re

    match = re.match(r"ue://[^/]+/source/(?P<path>[^#]+)#L(?P<start>\d+)-L(?P<end>\d+)", uri)
    if not match:
        return None
    return match.group("path"), int(match.group("start")), int(match.group("end"))
