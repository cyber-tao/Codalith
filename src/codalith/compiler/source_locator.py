"""Deterministic source entry-point locator."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from codalith.coderag import RetrievalHit, language_for_path
from codalith.config import load_config
from codalith.corpus.registry import Corpus
from codalith.corpus.source_reader import SourceReader
from codalith.corpus.uris import source_uri
from codalith.errors import ConfigurationError, SourceReadError
from codalith.text import normalize, tokenize


@dataclass(frozen=True, slots=True)
class SourcePrior:
    path: str
    title: str
    module: str | None
    triggers: tuple[str, ...]
    line_terms: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceDomainConfig:
    priors: tuple[SourcePrior, ...] = ()
    module_hints: frozenset[str] = frozenset()
    identifier_stopwords: frozenset[str] = frozenset()


@lru_cache(maxsize=64)
def load_source_domain_config(path: str | Path | None) -> SourceDomainConfig:
    """Load optional corpus-local source priors and detector vocabulary."""
    if path is None:
        return SourceDomainConfig()
    resolved = Path(path)
    raw = load_config(resolved)
    return SourceDomainConfig(
        priors=_parse_priors(resolved, raw),
        module_hints=frozenset(str(item) for item in raw.get("module_hints", [])),
        identifier_stopwords=frozenset(
            str(item) for item in raw.get("identifier_stopwords", [])
        ),
    )


def reset_domain_config_cache() -> None:
    """Clear every cached view of the domain config (tests, config reloads)."""
    load_source_domain_config.cache_clear()


def _parse_priors(path: Path, raw: dict[str, Any]) -> tuple[SourcePrior, ...]:
    priors = raw.get("priors", [])
    if not isinstance(priors, list):
        raise ConfigurationError(f"{path} must define a 'priors' list")
    loaded: list[SourcePrior] = []
    for index, item in enumerate(priors):
        if not isinstance(item, dict):
            raise ConfigurationError(f"{path} priors[{index}] must be an object")
        try:
            loaded.append(
                SourcePrior(
                    path=str(item["path"]),
                    title=str(item["title"]),
                    module=str(item["module"]) if item.get("module") is not None else None,
                    triggers=tuple(str(trigger) for trigger in item["triggers"]),
                    line_terms=tuple(str(term) for term in item.get("line_terms", [])),
                )
            )
        except KeyError as exc:
            raise ConfigurationError(f"{path} priors[{index}] is missing key {exc}") from exc
    return tuple(loaded)


def locate_source_priors(
    corpus: Corpus,
    *,
    query: str,
    identifiers: list[str],
    max_hits: int,
    source_reader: SourceReader,
    priors: tuple[SourcePrior, ...] = (),
) -> list[RetrievalHit]:
    scored: list[tuple[float, SourcePrior]] = []
    normalized_query = normalize(query)
    identifier_terms = {normalize(identifier) for identifier in identifiers}
    query_tokens = set(tokenize(normalized_query))
    for prior in priors:
        score = _score_prior(prior, normalized_query, identifier_terms, query_tokens)
        if score > 0:
            scored.append((score, prior))

    hits: list[RetrievalHit] = []
    for score, prior in sorted(scored, key=lambda item: item[0], reverse=True):
        hit = _hit_for_prior(corpus, prior, query=query, score=score, source_reader=source_reader)
        if hit is not None:
            hits.append(hit)
        if len(hits) >= max_hits:
            break
    return hits


def _score_prior(
    prior: SourcePrior,
    normalized_query: str,
    identifier_terms: set[str],
    query_tokens: set[str],
) -> float:
    score = 0.0
    for trigger in prior.triggers:
        normalized_trigger = normalize(trigger).strip()
        if not normalized_trigger:
            continue
        if " " in normalized_trigger:
            if normalized_trigger in normalized_query:
                score += 8.0
            continue
        # Single-word triggers require exact token matches; substring matching
        # would let "actor" fire on "factor" or "refactor".
        if normalized_trigger in identifier_terms:
            score += 10.0
        elif normalized_trigger in query_tokens:
            score += 6.0
    basename = normalize(Path(prior.path).name)
    if basename and basename in normalized_query:
        score += 10.0
    if prior.module and normalize(prior.module) in normalized_query:
        score += 4.0
    return score


def _hit_for_prior(
    corpus: Corpus,
    prior: SourcePrior,
    *,
    query: str,
    score: float,
    source_reader: SourceReader,
) -> RetrievalHit | None:
    try:
        lines = source_reader.read_lines(corpus.corpus_id, prior.path)
    except (OSError, SourceReadError):
        return None
    if not lines:
        return None
    start, end = _window(lines, query=query, line_terms=prior.line_terms)
    snippet = "\n".join(lines[start - 1 : end])
    return RetrievalHit(
        source="source-locator",
        corpus_id=corpus.corpus_id,
        uri=source_uri(corpus.corpus_id, prior.path, start, end),
        path=prior.path,
        start_line=start,
        end_line=end,
        title=f"{prior.title}: {Path(prior.path).name}",
        snippet=snippet,
        score=score,
        kind="source-prior",
        language=language_for_path(prior.path),
        module=prior.module,
        reason="High-confidence source entry point matched from query terms.",
        metadata={"matched_by": "source-locator"},
    )


def _window(lines: list[str], *, query: str, line_terms: tuple[str, ...]) -> tuple[int, int]:
    # Curated line_terms are trusted at any length (e.g. "GC", "RPC"); free-form
    # query tokens below three characters are too noisy to anchor a window.
    query_terms = tokenize(query, min_length=3)
    lowered_terms = {normalize(term) for term in [*query_terms, *line_terms] if term}
    best_line = 1
    best_matches = 0
    for index, line in enumerate(lines, start=1):
        normalized_line = normalize(line)
        matches = sum(1 for term in lowered_terms if term in normalized_line)
        if matches > best_matches:
            best_line = index
            best_matches = matches
    start = max(1, best_line - 4)
    end = min(len(lines), best_line + 15)
    return start, end
