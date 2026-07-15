"""Transport-independent source query service."""

from __future__ import annotations

import json
import re
import threading
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Literal, cast

from codalith.corpus.registry import Corpus, CorpusRegistry
from codalith.corpus.source_policy import SourcePolicy
from codalith.corpus.source_reader import FileCatalog, SourceReader
from codalith.corpus.store_manifest import ActiveGeneration, GenerationRepository
from codalith.corpus.uris import parse_uri, source_uri, symbol_uri
from codalith.errors import IndexUnavailableError, RetrievalError, SourceReadError
from codalith.indexing.coderag import CodeRAGBackend
from codalith.indexing.structure.models import (
    FileRecord,
    ModuleDependencyRecord,
    ReferenceRecord,
    SymbolRecord,
)
from codalith.indexing.structure.store import StructureIndex
from codalith.query.models import (
    CompareChange,
    CompareResponse,
    ContextResponse,
    ContextSource,
    CorpusStatus,
    GraphEdge,
    GraphNode,
    GraphResponse,
    ReadResponse,
    SearchHit,
    SearchResponse,
    StatusResponse,
    SymbolDefinition,
    SymbolResponse,
)

SearchStrategy = Literal["auto", "semantic", "text", "symbol"]
GraphDirection = Literal["incoming", "outgoing", "both"]
_STRATEGIES = frozenset({"auto", "semantic", "text", "symbol"})
_IDENTIFIER = re.compile(r"(?u)[^\W\d]\w*(?:(?:::|\.)[^\W\d]\w*)*")
_QUOTED_IDENTIFIER = re.compile(r"`([^`]{1,200})`")
_COMMON_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "does",
        "for",
        "from",
        "how",
        "in",
        "is",
        "of",
        "or",
        "the",
        "to",
        "what",
        "where",
        "with",
    }
)
_EXTERNAL_SEMANTIC_POOL = 1_000
_GENERIC_FILE_TERMS = frozenset({"build.cs", "generated.h", "target.cs"})
_GENERIC_TITLE_WORDS = frozenset(
    {
        "code",
        "component",
        "engine",
        "file",
        "files",
        "find",
        "header",
        "how",
        "module",
        "modules",
        "runtime",
        "source",
        "system",
        "unreal",
        "what",
        "when",
        "where",
        "which",
        "why",
    }
)
_FILE_STEM_WORDS = frozenset({"actor", "array", "class", "world"})


@dataclass(slots=True)
class _Candidate:
    corpus: Corpus
    generation: ActiveGeneration
    path: str
    start_line: int
    end_line: int
    language: str
    symbol: str | None
    symbol_id: str | None
    kind: str
    snippet: str
    score: float = 0.0
    backends: list[str] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str, int, int, str | None]:
        return (
            self.corpus.corpus_id,
            self.path,
            self.start_line,
            self.end_line,
            self.symbol,
        )


class QueryService:
    def __init__(self, registry: CorpusRegistry, policy: SourcePolicy) -> None:
        self.registry = registry
        self.policy = policy
        self.generations = GenerationRepository()
        self.coderag = CodeRAGBackend(policy)
        self._indices: dict[tuple[str, str], StructureIndex] = {}
        self._lock = threading.RLock()
        self.sources = SourceReader(
            registry,
            policy,
            cast(Callable[[Corpus], FileCatalog], self._catalog_for),
        )

    def close(self) -> None:
        self.coderag.close()
        with self._lock:
            indices = list(self._indices.values())
            self._indices.clear()
        for index in indices:
            index.close()

    def search(
        self,
        query: str,
        *,
        target: str | None = None,
        strategy: SearchStrategy = "auto",
        limit: int = 10,
    ) -> SearchResponse:
        normalized_query = _validated_query(query)
        if strategy not in _STRATEGIES:
            raise ValueError(f"Unknown search strategy: {strategy}")
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        resolution = self.registry.resolve(target)
        warnings: list[str] = []
        degraded = False
        candidates: dict[tuple[str, str, int, int, str | None], _Candidate] = {}
        active_count = 0
        for corpus in resolution.corpora:
            try:
                generation = self.generations.active(corpus)
                index = self._index(corpus, generation)
                active_count += 1
            except IndexUnavailableError as exc:
                warnings.append(str(exc))
                degraded = True
                continue
            if strategy in {"auto", "symbol"}:
                terms = _identifier_candidates(normalized_query)
                if strategy == "symbol" and not terms:
                    terms = [normalized_query]
                for file_term, weight in _file_lookup_candidates(
                    normalized_query,
                    derive_type_stems=strategy == "auto",
                )[:12]:
                    for rank, file_record in enumerate(
                        index.lookup_files(file_term, limit=max(limit, 10)),
                        start=1,
                    ):
                        candidate = _from_file(
                            corpus,
                            generation,
                            file_record,
                            max_lines=self.policy.default_max_lines,
                        )
                        _merge(candidates, candidate, "structure", rank, weight)
                for term in terms[:12]:
                    records = index.lookup_symbols(
                        term,
                        exact=True,
                        limit=max(limit, 10),
                    )
                    if strategy == "symbol" and not records:
                        records = index.lookup_symbols(
                            term,
                            exact=False,
                            limit=max(limit, 10),
                        )
                    for rank, symbol_record in enumerate(records, start=1):
                        candidate = _from_symbol(
                            corpus,
                            generation,
                            symbol_record,
                            max_lines=self.policy.hard_max_lines,
                        )
                        _merge(candidates, candidate, "structure", rank, 1.0)
            if strategy in {"auto", "semantic"}:
                filtered_semantic_paths = 0
                try:
                    semantic_hits = self.coderag.search(
                        corpus,
                        generation,
                        normalized_query,
                        limit=(
                            _EXTERNAL_SEMANTIC_POOL
                            if corpus.coderag_store is not None
                            else max(limit * 4, 20)
                        ),
                    )
                except RetrievalError as exc:
                    if strategy == "semantic":
                        raise
                    warnings.append(str(exc))
                    degraded = True
                else:
                    eligible_semantic_hits = []
                    for hit in semantic_hits:
                        if index.get_file(hit.path) is None:
                            filtered_semantic_paths += 1
                            continue
                        eligible_semantic_hits.append(hit)
                    for rank, hit in enumerate(eligible_semantic_hits, start=1):
                        start_line, end_line = _bounded_range(
                            hit.start_line,
                            hit.end_line,
                            self.policy.hard_max_lines,
                        )
                        candidate = _Candidate(
                            corpus=corpus,
                            generation=generation,
                            path=hit.path,
                            start_line=start_line,
                            end_line=end_line,
                            language=hit.language,
                            symbol=hit.symbol,
                            symbol_id=None,
                            kind=hit.kind,
                            snippet=_bounded_snippet(hit.snippet),
                        )
                        _merge(candidates, candidate, "coderag", rank, 1.0)
                    if filtered_semantic_paths:
                        warnings.append(
                            f"Filtered {filtered_semantic_paths} CodeRAG hit(s) outside "
                            f"generation {generation.manifest.generation_id}"
                        )
            if strategy == "text":
                text_hits = self.coderag.text_search(
                    corpus,
                    generation,
                    normalized_query,
                    limit=max(limit * 4, 20),
                )
                for rank, text_hit in enumerate(text_hits, start=1):
                    indexed_file = index.get_file(text_hit.path)
                    if indexed_file is None:
                        continue
                    candidate = _Candidate(
                        corpus=corpus,
                        generation=generation,
                        path=text_hit.path,
                        start_line=text_hit.line,
                        end_line=text_hit.line,
                        language=indexed_file.language,
                        symbol=None,
                        symbol_id=None,
                        kind="text",
                        snippet=_bounded_snippet(text_hit.text),
                    )
                    _merge(candidates, candidate, "text", rank, 1.2)
        if active_count == 0:
            raise IndexUnavailableError("No corpus in the target has an active index generation")
        ranked = sorted(
            candidates.values(),
            key=lambda item: (-item.score, item.corpus.corpus_id, item.path, item.start_line),
        )
        selected = _diversified(ranked, limit=limit)
        maximum = selected[0].score if selected else 1.0
        hits = [_public_hit(item, item.score / maximum) for item in selected]
        return SearchResponse(
            query=normalized_query,
            target=resolution.target_id,
            strategy=strategy,
            degraded=degraded,
            warnings=_unique(warnings),
            hits=hits,
        )

    def context(
        self,
        query: str,
        *,
        target: str | None = None,
        max_spans: int = 8,
        max_chars: int = 24_000,
    ) -> ContextResponse:
        if not 1 <= max_spans <= 20:
            raise ValueError("max_spans must be between 1 and 20")
        if not 1_000 <= max_chars <= 100_000:
            raise ValueError("max_chars must be between 1000 and 100000")
        result = self.search(
            query,
            target=target,
            strategy="auto",
            limit=min(50, max_spans * 3),
        )
        warnings = list(result.warnings)
        sources: list[ContextSource] = []
        selected_ranges: list[tuple[str, str, int, int]] = []
        remaining = max_chars
        degraded = result.degraded
        for hit in result.hits:
            if len(sources) >= max_spans or remaining <= 0:
                break
            if any(
                corpus_id == hit.corpus_id
                and path == hit.path
                and max(start, hit.start_line) <= min(end, hit.end_line)
                for corpus_id, path, start, end in selected_ranges
            ):
                continue
            try:
                source_slice = self.sources.read_uri(hit.uri)
            except (SourceReadError, IndexUnavailableError) as exc:
                warnings.append(str(exc))
                degraded = True
                continue
            if len(source_slice.text) > remaining:
                fitted_end = _fit_end_line(
                    source_slice.text,
                    start_line=source_slice.start_line,
                    character_budget=remaining,
                )
                if fitted_end < source_slice.start_line:
                    break
                source_slice = self.sources.read(
                    source_slice.corpus_id,
                    source_slice.path,
                    start_line=source_slice.start_line,
                    end_line=fitted_end,
                )
            remaining -= len(source_slice.text)
            if source_slice.stale:
                degraded = True
                warnings.append(f"Source changed after indexing: {source_slice.path}")
            if source_slice.decode_replacements:
                warnings.append(
                    f"Source contained invalid UTF-8 bytes: {source_slice.path}"
                )
            sources.append(
                ContextSource(
                    **hit.model_dump(exclude={"uri", "end_line"}),
                    uri=source_slice.uri,
                    end_line=source_slice.end_line,
                    stale=source_slice.stale,
                    text=source_slice.text,
                    sha256=source_slice.sha256,
                    indexed_sha256=source_slice.indexed_sha256,
                    truncated=source_slice.truncated,
                    decode_replacements=source_slice.decode_replacements,
                )
            )
            selected_ranges.append(
                (
                    source_slice.corpus_id,
                    source_slice.path,
                    source_slice.start_line,
                    source_slice.end_line,
                )
            )
        confidence = _confidence(sources, degraded)
        return ContextResponse(
            query=result.query,
            target=result.target,
            confidence=confidence,
            degraded=degraded,
            warnings=_unique(warnings),
            sources=sources,
        )

    def read(self, uri: str) -> ReadResponse:
        return ReadResponse.model_validate(self.sources.read_uri(uri).to_dict())

    def symbol(
        self,
        query: str,
        *,
        target: str | None = None,
        exact: bool = True,
        limit: int = 20,
    ) -> SymbolResponse:
        normalized_query = _validated_query(query)
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        resolution = self.registry.resolve(target)
        definitions: list[SymbolDefinition] = []
        warnings: list[str] = []
        active_count = 0
        for corpus in resolution.corpora:
            try:
                generation = self.generations.active(corpus)
                index = self._index(corpus, generation)
                active_count += 1
            except IndexUnavailableError as exc:
                warnings.append(str(exc))
                continue
            definitions.extend(
                _definition(corpus, generation, record)
                for record in index.lookup_symbols(
                    normalized_query,
                    exact=exact,
                    limit=limit,
                )
            )
        if active_count == 0:
            raise IndexUnavailableError("No corpus in the target has an active index generation")
        return SymbolResponse(
            query=normalized_query,
            target=resolution.target_id,
            exact=exact,
            definitions=definitions[:limit],
            warnings=_unique(warnings),
        )

    def graph(
        self,
        root_uri: str,
        *,
        direction: GraphDirection = "both",
        depth: int = 1,
        limit: int = 200,
    ) -> GraphResponse:
        parsed = parse_uri(root_uri)
        if parsed.kind != "symbol":
            raise ValueError("root_uri must be a symbol URI")
        if direction not in {"incoming", "outgoing", "both"}:
            raise ValueError("direction must be incoming, outgoing, or both")
        if not 1 <= depth <= 3:
            raise ValueError("depth must be between 1 and 3")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        corpus = self.registry.get_corpus(parsed.corpus_id)
        generation = self.generations.active(corpus)
        index = self._index(corpus, generation)
        root = index.get_symbol(parsed.value)
        if root is None:
            raise IndexUnavailableError(f"Unknown symbol in active generation: {root_uri}")
        nodes: dict[str, GraphNode] = {
            root.symbol_id: _graph_node(corpus, root)
        }
        edges: list[GraphEdge] = []
        seen_edges: set[tuple[str, int]] = set()
        queue: deque[tuple[str, int]] = deque([(root.symbol_id, 0)])
        seen_symbols = {root.symbol_id}
        module_cache: dict[str, tuple[SymbolRecord | None, str]] = {}
        truncated = False
        while queue:
            symbol_id, level = queue.popleft()
            if level >= depth:
                continue
            current = index.get_symbol(symbol_id)
            if current is None:
                continue
            directions = (
                ("incoming", "outgoing") if direction == "both" else (direction,)
            )
            for edge_direction in directions:
                for reference in index.references(
                    symbol_id,
                    direction=edge_direction,
                    limit=limit + 1,
                ):
                    edge_key = ("reference", reference.reference_id)
                    if edge_key in seen_edges:
                        continue
                    if len(edges) >= limit:
                        truncated = True
                        break
                    seen_edges.add(edge_key)
                    source = (
                        index.get_symbol(reference.source_symbol_id)
                        if reference.source_symbol_id
                        else None
                    )
                    target_symbol = (
                        index.get_symbol(reference.target_symbol_id)
                        if reference.target_symbol_id
                        else None
                    )
                    edges.append(
                        _graph_edge(corpus, reference, source, target_symbol)
                    )
                    for adjacent in (source, target_symbol):
                        if adjacent is None:
                            continue
                        nodes.setdefault(adjacent.symbol_id, _graph_node(corpus, adjacent))
                        if adjacent.symbol_id not in seen_symbols:
                            seen_symbols.add(adjacent.symbol_id)
                            queue.append((adjacent.symbol_id, level + 1))
                if truncated:
                    break
                if current.kind != "module":
                    continue
                for dependency in index.module_dependencies(
                    current.name,
                    direction=edge_direction,
                    limit=limit + 1,
                ):
                    edge_key = ("module", dependency.dependency_id)
                    if edge_key in seen_edges:
                        continue
                    if len(edges) >= limit:
                        truncated = True
                        break
                    seen_edges.add(edge_key)
                    source, target_symbol, resolution = _module_endpoints(
                        index,
                        current,
                        dependency,
                        direction=edge_direction,
                        cache=module_cache,
                    )
                    edges.append(
                        _module_graph_edge(
                            corpus,
                            dependency,
                            source,
                            target_symbol,
                            resolution,
                        )
                    )
                    for adjacent in (source, target_symbol):
                        if adjacent is None:
                            continue
                        nodes.setdefault(adjacent.symbol_id, _graph_node(corpus, adjacent))
                        if adjacent.symbol_id not in seen_symbols:
                            seen_symbols.add(adjacent.symbol_id)
                            queue.append((adjacent.symbol_id, level + 1))
                if truncated:
                    break
            if truncated:
                break
        return GraphResponse(
            root_uri=symbol_uri(corpus.corpus_id, root.symbol_id),
            direction=direction,
            depth=depth,
            nodes=list(nodes.values()),
            edges=edges,
            truncated=truncated,
            warnings=[],
        )

    def resolve_symbol_uri(self, uri: str) -> SymbolDefinition:
        parsed = parse_uri(uri)
        if parsed.kind != "symbol":
            raise ValueError("URI must identify a symbol")
        corpus = self.registry.get_corpus(parsed.corpus_id)
        generation = self.generations.active(corpus)
        symbol = self._index(corpus, generation).get_symbol(parsed.value)
        if symbol is None:
            raise IndexUnavailableError(f"Unknown symbol in active generation: {uri}")
        return _definition(corpus, generation, symbol)

    def compare(
        self,
        from_corpus: str,
        to_corpus: str,
        *,
        include_unchanged: bool = False,
        limit: int = 500,
    ) -> CompareResponse:
        if from_corpus == to_corpus:
            raise ValueError("from_corpus and to_corpus must differ")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        old_corpus = self.registry.get_corpus(from_corpus)
        new_corpus = self.registry.get_corpus(to_corpus)
        old_generation = self.generations.active(old_corpus)
        new_generation = self.generations.active(new_corpus)
        old_index = self._index(old_corpus, old_generation)
        new_index = self._index(new_corpus, new_generation)
        changes: list[CompareChange] = []
        truncated = False
        for key, old, new in _merged_symbol_groups(old_index, new_index):
            change = _compare_group(
                key,
                old,
                new,
                old_corpus,
                old_generation,
                new_corpus,
                new_generation,
            )
            if change.status == "unchanged" and not include_unchanged:
                continue
            if len(changes) >= limit:
                truncated = True
                break
            changes.append(change)
        return CompareResponse(
            from_corpus=from_corpus,
            to_corpus=to_corpus,
            changes=changes,
            truncated=truncated,
        )

    def status(self, *, target: str | None = None) -> StatusResponse:
        resolution = self.registry.resolve(target)
        rows: list[CorpusStatus] = []
        for corpus in resolution.corpora:
            try:
                generation = self.generations.active(corpus)
            except IndexUnavailableError as exc:
                message = str(exc)
                state: Literal["missing", "invalid"] = (
                    "missing" if "No active index generation" in message else "invalid"
                )
                rows.append(
                    CorpusStatus(
                        corpus_id=corpus.corpus_id,
                        revision=corpus.revision,
                        state=state,
                        generation_id=None,
                        semantic_available=False,
                        files=0,
                        symbols=0,
                        references=0,
                        module_dependencies=0,
                        message=message,
                    )
                )
                continue
            manifest = generation.manifest
            rows.append(
                CorpusStatus(
                    corpus_id=corpus.corpus_id,
                    revision=corpus.revision,
                    state="ready" if manifest.semantic_available else "degraded",
                    generation_id=manifest.generation_id,
                    semantic_available=manifest.semantic_available,
                    files=manifest.files,
                    symbols=manifest.symbols,
                    references=manifest.references,
                    module_dependencies=manifest.module_dependencies,
                    message=None if manifest.semantic_available else "Semantic index unavailable",
                )
            )
        return StatusResponse(
            target=resolution.target_id,
            ready=all(row.state == "ready" for row in rows),
            corpora=rows,
        )

    def _catalog_for(self, corpus: Corpus) -> StructureIndex:
        generation = self.generations.active(corpus)
        return self._index(corpus, generation)

    def _index(self, corpus: Corpus, generation: ActiveGeneration) -> StructureIndex:
        key = (corpus.corpus_id, generation.manifest.generation_id)
        with self._lock:
            index = self._indices.get(key)
            if index is None:
                obsolete = [item for item in self._indices if item[0] == corpus.corpus_id]
                for old_key in obsolete:
                    self._indices.pop(old_key).close()
                index = StructureIndex(generation.structure_path)
                self._indices[key] = index
            return index


def _validated_query(query: str) -> str:
    normalized = query.strip()
    if not normalized:
        raise ValueError("query cannot be blank")
    if len(normalized) > 4096:
        raise ValueError("query cannot exceed 4096 characters")
    return normalized


def _identifier_candidates(query: str) -> list[str]:
    quoted = _QUOTED_IDENTIFIER.findall(query)
    values = [*quoted, *(_IDENTIFIER.findall(query))]
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        folded = normalized.casefold()
        if (
            folded in _COMMON_WORDS
            or folded in seen
            or len(normalized) < 2
            or (normalized not in quoted and not _looks_like_code_identifier(normalized))
        ):
            continue
        seen.add(folded)
        result.append(normalized)
        module_name = _module_name_from_rules_file(normalized)
        if module_name is not None and module_name.casefold() not in seen:
            seen.add(module_name.casefold())
            result.append(module_name)
    if not result and _IDENTIFIER.fullmatch(query.strip()):
        return [query.strip()]
    return result


def _looks_like_code_identifier(value: str) -> bool:
    if any(marker in value for marker in ("::", ".", "_")):
        return True
    if any(character.isdigit() for character in value):
        return True
    if value.isupper() and any(character.isalpha() for character in value):
        return True
    if len(value) >= 2 and value[0] in "AUFSTIE" and value[1].isupper():
        return True
    return any(
        left.islower() and right.isupper()
        for left, right in zip(value, value[1:], strict=False)
    )


def _module_name_from_rules_file(value: str) -> str | None:
    folded = value.casefold()
    for suffix in (".build.cs", ".target.cs"):
        if folded.endswith(suffix) and len(value) > len(suffix):
            return value[: -len(suffix)].rsplit("/", 1)[-1]
    return None


def _file_lookup_candidates(
    query: str,
    *,
    derive_type_stems: bool,
) -> list[tuple[str, float]]:
    result: list[tuple[str, float]] = []
    seen: set[str] = set()
    for value in _IDENTIFIER.findall(query):
        normalized = value.strip()
        folded = normalized.casefold()
        is_file_stem_word = folded in _FILE_STEM_WORDS
        is_short_acronym = normalized.isupper() and len(normalized) <= 3
        if (
            len(normalized) >= 2
            and folded not in _GENERIC_FILE_TERMS
            and folded not in _GENERIC_TITLE_WORDS
            and not is_short_acronym
            and (
                "." in normalized
                or _looks_like_code_identifier(normalized)
                or is_file_stem_word
            )
            and folded not in seen
        ):
            seen.add(folded)
            result.append((normalized, 2.0))
        if not derive_type_stems:
            continue
        stem = _ue_type_stem(normalized)
        if stem is not None and stem.casefold() not in seen:
            seen.add(stem.casefold())
            result.append((stem, 1.5))
    return result


def _ue_type_stem(value: str) -> str | None:
    if (
        len(value) >= 3
        and not value.isupper()
        and value[0] in "AUFSTIE"
        and value[1].isupper()
        and value.isidentifier()
    ):
        return value[1:]
    return None


def _diversified(candidates: list[_Candidate], *, limit: int) -> list[_Candidate]:
    selected: list[_Candidate] = []
    path_counts: dict[tuple[str, str], int] = {}
    for candidate in candidates:
        key = (candidate.corpus.corpus_id, candidate.path)
        if path_counts.get(key, 0) >= 3:
            continue
        selected.append(candidate)
        path_counts[key] = path_counts.get(key, 0) + 1
        if len(selected) == limit:
            break
    return selected


def _from_symbol(
    corpus: Corpus,
    generation: ActiveGeneration,
    symbol: SymbolRecord,
    *,
    max_lines: int,
) -> _Candidate:
    start_line, end_line = _bounded_range(
        symbol.start_line,
        symbol.end_line,
        max_lines,
    )
    return _Candidate(
        corpus=corpus,
        generation=generation,
        path=symbol.path,
        start_line=start_line,
        end_line=end_line,
        language=_language_from_path(symbol.path),
        symbol=symbol.qualified_name,
        symbol_id=symbol.symbol_id,
        kind=symbol.kind,
        snippet=symbol.signature,
    )


def _from_file(
    corpus: Corpus,
    generation: ActiveGeneration,
    record: FileRecord,
    *,
    max_lines: int,
) -> _Candidate:
    return _Candidate(
        corpus=corpus,
        generation=generation,
        path=record.path,
        start_line=1,
        end_line=min(record.line_count, max_lines),
        language=record.language,
        symbol=None,
        symbol_id=None,
        kind="file",
        snippet=record.path,
    )


def _bounded_range(start_line: int, end_line: int, max_lines: int) -> tuple[int, int]:
    start = max(1, start_line)
    end = max(start, end_line)
    return start, min(end, start + max_lines - 1)


def _merge(
    candidates: dict[tuple[str, str, int, int, str | None], _Candidate],
    incoming: _Candidate,
    backend: str,
    rank: int,
    weight: float,
) -> None:
    existing = candidates.get(incoming.key)
    if existing is None:
        existing = incoming
        candidates[incoming.key] = existing
    existing.score += weight / (60 + rank)
    if backend not in existing.backends:
        existing.backends.append(backend)
    if len(incoming.snippet) > len(existing.snippet):
        existing.snippet = incoming.snippet
    if existing.symbol_id is None and incoming.symbol_id is not None:
        existing.symbol_id = incoming.symbol_id


def _public_hit(candidate: _Candidate, normalized_score: float) -> SearchHit:
    return SearchHit(
        corpus_id=candidate.corpus.corpus_id,
        revision=candidate.corpus.revision,
        generation_id=candidate.generation.manifest.generation_id,
        uri=source_uri(
            candidate.corpus.corpus_id,
            candidate.path,
            start_line=candidate.start_line,
            end_line=candidate.end_line,
        ),
        path=candidate.path,
        start_line=candidate.start_line,
        end_line=candidate.end_line,
        language=candidate.language,
        symbol=candidate.symbol,
        symbol_id=candidate.symbol_id,
        kind=candidate.kind,
        score=round(normalized_score, 6),
        backends=candidate.backends,
        snippet=candidate.snippet,
    )


def _definition(
    corpus: Corpus,
    generation: ActiveGeneration,
    symbol: SymbolRecord,
) -> SymbolDefinition:
    return SymbolDefinition(
        corpus_id=corpus.corpus_id,
        revision=corpus.revision,
        generation_id=generation.manifest.generation_id,
        uri=symbol_uri(corpus.corpus_id, symbol.symbol_id),
        symbol_id=symbol.symbol_id,
        qualified_name=symbol.qualified_name,
        name=symbol.name,
        kind=symbol.kind,
        signature=symbol.signature,
        source_uri=source_uri(
            corpus.corpus_id,
            symbol.path,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
        ),
        path=symbol.path,
        start_line=symbol.start_line,
        end_line=symbol.end_line,
        module=symbol.module,
        metadata=symbol.metadata,
    )


def _graph_node(corpus: Corpus, symbol: SymbolRecord) -> GraphNode:
    return GraphNode(
        corpus_id=corpus.corpus_id,
        revision=corpus.revision,
        symbol_id=symbol.symbol_id,
        uri=symbol_uri(corpus.corpus_id, symbol.symbol_id),
        qualified_name=symbol.qualified_name,
        kind=symbol.kind,
        source_uri=source_uri(
            corpus.corpus_id,
            symbol.path,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
        ),
    )


def _graph_edge(
    corpus: Corpus,
    reference: ReferenceRecord,
    source: SymbolRecord | None,
    target: SymbolRecord | None,
) -> GraphEdge:
    return GraphEdge(
        source_uri=symbol_uri(corpus.corpus_id, source.symbol_id) if source else None,
        target_uri=symbol_uri(corpus.corpus_id, target.symbol_id) if target else None,
        target_name=reference.target_name,
        kind=reference.kind,
        resolution=reference.resolution,  # type: ignore[arg-type]
        evidence_uri=source_uri(
            corpus.corpus_id,
            reference.path,
            start_line=reference.line,
            end_line=reference.line,
        ),
    )


def _module_endpoints(
    index: StructureIndex,
    current: SymbolRecord,
    dependency: ModuleDependencyRecord,
    *,
    direction: str,
    cache: dict[str, tuple[SymbolRecord | None, str]],
) -> tuple[SymbolRecord | None, SymbolRecord | None, str]:
    adjacent_name = (
        dependency.target_module if direction == "outgoing" else dependency.source_module
    )
    adjacent, resolution = cache.get(adjacent_name, (None, ""))
    if not resolution:
        candidates = index.lookup_module(adjacent_name)
        adjacent = candidates[0] if len(candidates) == 1 else None
        resolution = "resolved" if adjacent else "ambiguous" if candidates else "unresolved"
        cache[adjacent_name] = adjacent, resolution
    if direction == "outgoing":
        return current, adjacent, resolution
    return adjacent, current, resolution


def _module_graph_edge(
    corpus: Corpus,
    dependency: ModuleDependencyRecord,
    source: SymbolRecord | None,
    target: SymbolRecord | None,
    resolution: str,
) -> GraphEdge:
    return GraphEdge(
        source_uri=symbol_uri(corpus.corpus_id, source.symbol_id) if source else None,
        target_uri=symbol_uri(corpus.corpus_id, target.symbol_id) if target else None,
        target_name=dependency.target_module,
        kind=dependency.kind,
        resolution=cast(Literal["resolved", "ambiguous", "unresolved"], resolution),
        evidence_uri=source_uri(
            corpus.corpus_id,
            dependency.path,
            start_line=dependency.line,
            end_line=dependency.line,
        ),
    )


def _merged_symbol_groups(
    old_index: StructureIndex,
    new_index: StructureIndex,
) -> Iterator[tuple[str, list[SymbolRecord], list[SymbolRecord]]]:
    old_groups = iter(old_index.iter_symbol_groups())
    new_groups = iter(new_index.iter_symbol_groups())
    old = next(old_groups, None)
    new = next(new_groups, None)
    while old is not None or new is not None:
        if old is not None and (new is None or old[0] < new[0]):
            yield old[0], old[1], []
            old = next(old_groups, None)
        elif new is not None and (old is None or new[0] < old[0]):
            yield new[0], [], new[1]
            new = next(new_groups, None)
        else:
            assert old is not None and new is not None
            yield old[0], old[1], new[1]
            old = next(old_groups, None)
            new = next(new_groups, None)


def _compare_group(
    key: str,
    old: list[SymbolRecord],
    new: list[SymbolRecord],
    old_corpus: Corpus,
    old_generation: ActiveGeneration,
    new_corpus: Corpus,
    new_generation: ActiveGeneration,
) -> CompareChange:
    max_definitions = 20
    if not old:
        status = "added"
        changed_fields: list[str] = []
    elif not new:
        status = "removed"
        changed_fields = []
    elif len(old) == len(new) == 1:
        changed_fields = [
            field
            for field in ("signature", "path", "module", "metadata")
            if getattr(old[0], field) != getattr(new[0], field)
        ]
        status = "changed" if changed_fields else "unchanged"
    else:
        if _comparison_snapshots(old) == _comparison_snapshots(new):
            status = "unchanged"
            changed_fields = []
        else:
            status = "ambiguous"
            changed_fields = []
            for field in ("signature", "path", "module", "metadata"):
                if _comparison_values(old, field) != _comparison_values(new, field):
                    changed_fields.append("overloads" if field == "signature" else field)
            if not changed_fields:
                changed_fields.append("mapping")
    return CompareChange(
        comparison_key=_public_comparison_key(key),
        status=status,  # type: ignore[arg-type]
        from_symbols=[
            _definition(old_corpus, old_generation, item)
            for item in old[:max_definitions]
        ],
        to_symbols=[
            _definition(new_corpus, new_generation, item)
            for item in new[:max_definitions]
        ],
        changed_fields=changed_fields,
        truncated=len(old) > max_definitions or len(new) > max_definitions,
    )


def _public_comparison_key(value: str) -> str:
    qualified_name, separator, kind = value.rpartition("\0")
    return f"{qualified_name} ({kind})" if separator else value


def _comparison_values(symbols: list[SymbolRecord], field: str) -> list[str]:
    values: list[str] = []
    for symbol in symbols:
        value = getattr(symbol, field)
        if isinstance(value, dict):
            values.append(json.dumps(value, sort_keys=True, separators=(",", ":")))
        else:
            values.append("" if value is None else str(value))
    return sorted(values)


def _comparison_snapshots(symbols: list[SymbolRecord]) -> list[tuple[str, str, str, str]]:
    return sorted(
        (
            symbol.signature,
            symbol.path,
            symbol.module or "",
            json.dumps(symbol.metadata, sort_keys=True, separators=(",", ":")),
        )
        for symbol in symbols
    )


def _confidence(
    sources: list[ContextSource],
    degraded: bool,
) -> Literal["high", "medium", "low", "none"]:
    if not sources:
        return "none"
    if degraded or any(source.stale for source in sources):
        return "low"
    if any({"structure", "coderag"}.issubset(source.backends) for source in sources):
        return "high"
    if len(sources) >= 2 or any("structure" in source.backends for source in sources):
        return "medium"
    return "low"


def _fit_end_line(text: str, *, start_line: int, character_budget: int) -> int:
    consumed = 0
    end = start_line - 1
    for offset, line in enumerate(text.split("\n")):
        cost = len(line) + (1 if offset else 0)
        if consumed + cost > character_budget:
            break
        consumed += cost
        end = start_line + offset
    return end


def _bounded_snippet(value: str) -> str:
    normalized = value.strip()
    return normalized if len(normalized) <= 2000 else normalized[:1999] + "…"


def _language_from_path(path: str) -> str:
    lower = path.lower()
    if lower.endswith((".h", ".hpp", ".inl", ".c", ".cc", ".cpp")):
        return "cpp"
    if lower.endswith((".py", ".pyi")):
        return "python"
    if lower.endswith(".cs"):
        return "csharp"
    if lower.endswith(".ispc"):
        return "ispc"
    if lower.endswith(".m"):
        return "objective-c"
    if lower.endswith(".mm"):
        return "objective-cpp"
    if lower.endswith((".usf", ".ush")):
        return "hlsl"
    return "text"


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
