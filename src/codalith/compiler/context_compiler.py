"""Compile CodeRAG hits and semantic metadata into a Context Pack."""

from __future__ import annotations

import logging
import re

from codalith.cards import is_card_path
from codalith.cards.hashing import source_sha256
from codalith.coderag import CodeRAGAdapter, RetrievalHit, language_for_path
from codalith.compiler.context_pack import (
    CardEntry,
    ContextPack,
    ContextSummary,
    ModuleEntry,
    RecommendedCall,
    SourceSpanEntry,
    SymbolEntry,
)
from codalith.compiler.entity_detector import detect_identifiers, detect_modules
from codalith.compiler.intent_detector import detect_intent
from codalith.compiler.reranker import rerank
from codalith.compiler.source_locator import load_source_domain_config, locate_source_priors
from codalith.corpus.registry import CorpusRegistry
from codalith.corpus.source_reader import SourceReader
from codalith.corpus.uris import SCHEME, module_uri, parse_source_uri, source_uri, symbol_uri
from codalith.errors import CorpusNotFoundError, SourceReadError
from codalith.semantic.graph import aggregate_graph_neighborhood
from codalith.semantic.store import SemanticStore

_FRONT_MATTER_STATUS_RE = re.compile(r"^verification_status:\s*(?P<status>\S+)\s*$", re.MULTILINE)
_LOG = logging.getLogger(__name__)


class ContextCompiler:
    def __init__(
        self,
        registry: CorpusRegistry,
        adapter: CodeRAGAdapter,
        *,
        semantic_store: SemanticStore | None = None,
        source_reader: SourceReader | None = None,
    ) -> None:
        self.registry = registry
        self.adapter = adapter
        self.semantic_store = semantic_store
        self.source_reader = source_reader or SourceReader(registry)

    def compile(
        self,
        *,
        query: str,
        version: str | None = None,
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
        resolved_version = resolution.base.version_label
        intent = detect_intent(query, mode)
        domain_configs = {
            corpus.corpus_id: load_source_domain_config(corpus.source_priors_path)
            for corpus in resolution.ordered
        }
        stopwords = frozenset(
            item
            for config in domain_configs.values()
            for item in config.identifier_stopwords
        )
        module_hint_values = frozenset(
            item for config in domain_configs.values() for item in config.module_hints
        )
        identifiers = detect_identifiers(query, stopwords=stopwords)
        modules = detect_modules(query, module_hints=module_hint_values)
        search_top_k = max_source_spans
        raw_hits: list[RetrievalHit] = []
        for corpus in resolution.ordered:
            raw_hits.extend(
                locate_source_priors(
                    corpus,
                    query=query,
                    identifiers=identifiers,
                    max_hits=search_top_k,
                    priors=domain_configs[corpus.corpus_id].priors,
                    source_reader=self.source_reader,
                )
            )
            for planned_query in _build_queries(query, identifiers):
                raw_hits.extend(
                    self.adapter.search_code(
                        corpus.corpus_id,
                        planned_query,
                        top_k=search_top_k,
                    )
                )
        # Rerank a wider window than the span budget so card hits do not evict
        # source hits: cards land in the cards section, not in source_spans.
        ranked = rerank(
            _unique_hits(raw_hits),
            identifiers=identifiers,
            max_hits=max_source_spans * 2,
            mode=intent,
        )
        card_hits = [hit for hit in ranked if is_card_path(hit.path)]
        hits = [hit for hit in ranked if not is_card_path(hit.path)][:max_source_spans]
        base_corpus_id = resolution.base.corpus_id
        corpus_kinds = {corpus.corpus_id: corpus.kind for corpus in resolution.ordered}
        inferred_modules = _module_entries(base_corpus_id, modules, hits)
        source_spans = self._enriched_source_spans(hits, corpus_kinds)
        source_spans.extend(self._card_evidence_spans(card_hits))
        # Card evidence must not let the pack exceed the caller's span budget.
        source_spans = source_spans[:max_source_spans]
        graph_edges = self._graph_edges(
            [corpus.corpus_id for corpus in resolution.ordered],
            [*identifiers, *(module["name"] for module in inferred_modules)],
        )
        cards: list[CardEntry] = [
            {
                "uri": hit.uri,
                "title": hit.title,
                "verification_status": self._card_verification_status(hit),
            }
            for hit in card_hits
        ]
        return ContextPack(
            query=query,
            version=resolved_version,
            corpus_id=base_corpus_id,
            source_revision=resolution.base.source_revision or resolution.base.version_label,
            project=project,
            intent=intent,
            confidence=_confidence(hits),
            summary=ContextSummary(
                text="Context pack compiled from CodeRAG retrieval, corpus URI resolution, and v0 heuristics."
            ),
            modules=inferred_modules,
            symbols=self._symbol_entries(
                [corpus.corpus_id for corpus in resolution.ordered], identifiers, base_corpus_id
            ),
            cards=cards,
            source_spans=source_spans,
            graph_edges=graph_edges,
            caveats=[
                _graph_caveat(graph_edges, self.semantic_store is not None),
                "Exact behavior can depend on compile guards, platform conditionals, and project overlays.",
            ],
            recommended_next_calls=[
                RecommendedCall(tool="codalith_read_source", args={"uri": span["uri"]})
                for span in source_spans[:3]
            ],
        )

    def _graph_edges(
        self, corpus_ids: list[str], nodes: list[str], max_edges: int = 24
    ) -> list[dict[str, object]]:
        if self.semantic_store is None:
            return []
        result = aggregate_graph_neighborhood(
            self.semantic_store,
            corpus_ids=corpus_ids,
            seed_nodes=nodes,
            depth=1,
            max_nodes=24,
            max_edges=max_edges,
            include_corpus_id=True,
        )
        return list(result["edges"])

    def _enriched_source_spans(
        self,
        hits: list[RetrievalHit],
        corpus_kinds: dict[str, str],
    ) -> list[SourceSpanEntry]:
        spans: list[SourceSpanEntry] = []
        for hit in hits:
            try:
                source_slice = self.source_reader.read_slice(
                    hit.corpus_id,
                    hit.path,
                    start_line=hit.start_line,
                    end_line=hit.end_line,
                )
            except SourceReadError:
                _LOG.warning(
                    "Dropping retrieval hit whose canonical source is unavailable: %s:%s#L%s-L%s",
                    hit.corpus_id,
                    hit.path,
                    hit.start_line,
                    hit.end_line,
                )
                continue
            canonical_hash = source_sha256(source_slice.content)
            span: SourceSpanEntry = {
                "uri": source_uri(
                    hit.corpus_id,
                    hit.path,
                    source_slice.start_line,
                    source_slice.end_line,
                ),
                "corpus_id": hit.corpus_id,
                "corpus_kind": corpus_kinds.get(hit.corpus_id),
                "path": hit.path,
                "start_line": source_slice.start_line,
                "end_line": source_slice.end_line,
                "reason": hit.reason,
                "source": hit.source,
                "module": hit.module,
                "score": hit.score,
                "guard": None,
                "source_hash": canonical_hash,
                "index_stale": source_sha256(hit.snippet) != canonical_hash,
                "language": hit.language,
                "kind": hit.kind,
                "extractor": hit.metadata.get("matched_by") or hit.source,
                "confidence": min(1.0, max(0.0, hit.score / (hit.score + 1.0))),
            }
            if self.semantic_store is not None:
                guards = self.semantic_store.guards_for_span(
                    hit.corpus_id,
                    hit.path,
                    source_slice.start_line,
                    source_slice.end_line,
                )
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
            spans.append(span)
        return spans

    def _card_verification_status(self, hit: RetrievalHit) -> str:
        """Read the actual verification status from the card's front matter.

        The retrieval snippet may start mid-file, so the card file head is read
        through the corpus source reader instead of trusting the hit snippet.
        """
        try:
            head = self.source_reader.read_source(hit.corpus_id, hit.path, 1, 10)
        except (CorpusNotFoundError, SourceReadError):
            return "unknown"
        except Exception:
            _LOG.warning(
                "Unexpected failure reading card head for %s:%s",
                hit.corpus_id,
                hit.path,
                exc_info=True,
            )
            return "unknown"
        match = _FRONT_MATTER_STATUS_RE.search(head)
        return match.group("status") if match else "unknown"

    def _card_evidence_spans(self, hits: list[RetrievalHit]) -> list[SourceSpanEntry]:
        spans: list[SourceSpanEntry] = []
        for hit in hits:
            if not is_card_path(hit.path):
                continue
            for uri in _extract_evidence_uris(hit.snippet):
                parsed = parse_source_uri(uri)
                if parsed is None:
                    continue
                corpus_id, path, start, end = parsed
                span: SourceSpanEntry = {
                    "uri": uri,
                    "path": path,
                    "start_line": start,
                    "end_line": end,
                    "reason": f"Evidence linked from verified card {hit.title}.",
                    "source": "card-evidence",
                    "language": language_for_path(path),
                    "guard": None,
                }
                span.update(self._card_evidence_provenance(corpus_id, path, start, end))
                spans.append(span)
        return spans

    def _card_evidence_provenance(
        self,
        corpus_id: str,
        path: str,
        start: int,
        end: int,
    ) -> SourceSpanEntry:
        try:
            corpus = self.registry.get_corpus(corpus_id)
            source_slice = self.source_reader.read_slice(
                corpus.corpus_id,
                path,
                start_line=start,
                end_line=end,
            )
        except (CorpusNotFoundError, SourceReadError):
            # Evidence pointing at an unavailable corpus stays cited but unhashed.
            return {"corpus_id": None, "corpus_kind": None, "source_hash": None}
        except Exception:
            _LOG.warning(
                "Unexpected failure reading card evidence %s:%s#%s-%s",
                corpus_id,
                path,
                start,
                end,
                exc_info=True,
            )
            return {"corpus_id": None, "corpus_kind": None, "source_hash": None}
        return {
            "corpus_id": corpus.corpus_id,
            "corpus_kind": corpus.kind,
            "source_hash": source_sha256(source_slice.content),
        }

    def _symbol_entries(
        self,
        corpus_ids: list[str],
        identifiers: list[str],
        base_corpus_id: str,
    ) -> list[SymbolEntry]:
        entries: list[SymbolEntry] = []
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
                                "name": str(row["name"]),
                                "qualified_name": row.get("qualified_name"),
                                "kind": str(row["kind"]),
                                "module": row.get("module_name"),
                                "uri": row.get("declaration_uri")
                                or symbol_uri(base_corpus_id, identifier),
                                "reason": "Resolved from semantic symbol table.",
                            }
                        )
                        found = True
            if not found:
                entries.append(
                    {
                        "name": identifier,
                        "uri": symbol_uri(base_corpus_id, identifier),
                        "kind": "symbol",
                        "reason": "Identifier detected in the user query.",
                    }
                )
        return entries


def _build_queries(query: str, identifiers: list[str] | None = None) -> list[str]:
    queries = [query]
    if identifiers:
        queries.append(" ".join(identifiers[:8]))
    return list(dict.fromkeys(item for item in queries if item.strip()))


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
    base_corpus_id: str,
    detected_modules: list[str],
    hits: list[RetrievalHit],
) -> list[ModuleEntry]:
    names = list(dict.fromkeys(detected_modules + [hit.module for hit in hits if hit.module]))
    return [
        {
            "name": name,
            "uri": module_uri(base_corpus_id, name),
            "reason": "Detected from query or source path.",
        }
        for name in names
    ]


def _confidence(hits: list[RetrievalHit]) -> str:
    if not hits:
        return "low"
    if any(hit.source == "source-locator" for hit in hits):
        return "high"
    return "medium"


def _extract_evidence_uris(text: str) -> list[str]:
    return re.findall(rf"{SCHEME}://[^\s)]+", text)


def _graph_caveat(graph_edges: list[dict[str, object]], has_store: bool) -> str:
    if graph_edges:
        return "Semantic graph edges are included from extractor output where available."
    if has_store:
        return "Semantic graph store is configured, but no matching graph edges were found for this query."
    return "Semantic graph store is not configured; graph expansion is unavailable for this context pack."
