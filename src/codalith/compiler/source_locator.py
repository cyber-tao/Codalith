"""Deterministic UE source entry-point locator.

CodeRAG provides broad semantic retrieval. This module adds high-confidence UE
source priors for canonical engine concepts so Context Packs still cite stable
source evidence when an embedding provider is intentionally low fidelity.

The prior data lives in configs/source_priors.json and is loaded once per
process; set CODALITH_SOURCE_PRIORS to point at an alternative file.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from codalith.coderag.adapter import RetrievalHit, language_for_path
from codalith.config import load_config
from codalith.corpus.registry import Corpus
from codalith.errors import ConfigurationError

_DEFAULT_PRIORS_PATH = "configs/source_priors.json"


@dataclass(frozen=True, slots=True)
class SourcePrior:
    path: str
    title: str
    module: str | None
    triggers: tuple[str, ...]
    line_terms: tuple[str, ...] = ()


@lru_cache(maxsize=1)
def source_priors() -> tuple[SourcePrior, ...]:
    """Load and cache the curated UE source priors."""
    return _load_priors(_priors_path())


def _priors_path() -> Path:
    override = os.getenv("CODALITH_SOURCE_PRIORS")
    if override:
        return Path(override)
    cwd_path = Path(_DEFAULT_PRIORS_PATH)
    if cwd_path.exists():
        return cwd_path
    return Path(__file__).resolve().parents[3] / _DEFAULT_PRIORS_PATH


def _load_priors(path: Path) -> tuple[SourcePrior, ...]:
    raw = load_config(path)
    priors = raw.get("priors")
    if not isinstance(priors, list) or not priors:
        raise ConfigurationError(f"{path} must define a non-empty 'priors' list")
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
) -> list[RetrievalHit]:
    scored: list[tuple[float, SourcePrior]] = []
    normalized_query = _normalize(query)
    identifier_terms = {_normalize(identifier) for identifier in identifiers}
    query_tokens = set(_query_tokens(normalized_query))
    for prior in source_priors():
        score = _score_prior(prior, normalized_query, identifier_terms, query_tokens)
        if score > 0:
            scored.append((score, prior))

    hits: list[RetrievalHit] = []
    for score, prior in sorted(scored, key=lambda item: item[0], reverse=True):
        hit = _hit_for_prior(corpus, prior, query=query, score=score)
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
        normalized_trigger = _normalize(trigger).strip()
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
    basename = _normalize(Path(prior.path).name)
    if basename and basename in normalized_query:
        score += 10.0
    if prior.module and _normalize(prior.module) in normalized_query:
        score += 4.0
    return score


def _hit_for_prior(corpus: Corpus, prior: SourcePrior, *, query: str, score: float) -> RetrievalHit | None:
    full_path = _root(corpus) / prior.path
    if not full_path.is_file():
        return None
    try:
        lines = full_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    if not lines:
        return None
    start, end = _window(lines, query=query, line_terms=prior.line_terms)
    snippet = "\n".join(lines[start - 1 : end])
    return RetrievalHit(
        source="ue-source-locator",
        corpus_id=corpus.corpus_id,
        uri=_uri_for(corpus, prior.path, start, end),
        path=prior.path,
        start_line=start,
        end_line=end,
        title=f"{prior.title}: {Path(prior.path).name}",
        snippet=snippet,
        score=score + 1000.0,
        kind="source-prior",
        language=language_for_path(prior.path),
        module=prior.module,
        reason="High-confidence UE source entry point matched from query terms.",
        metadata={"matched_by": "ue-source-locator"},
    )


def _window(lines: list[str], *, query: str, line_terms: tuple[str, ...]) -> tuple[int, int]:
    # Curated line_terms are trusted at any length (e.g. "GC", "RPC"); free-form
    # query tokens below three characters are too noisy to anchor a window.
    query_terms = [term for term in _query_tokens(query) if len(term) >= 3]
    lowered_terms = {_normalize(term) for term in [*query_terms, *line_terms] if term}
    best_line = 1
    best_matches = 0
    for index, line in enumerate(lines, start=1):
        normalized_line = _normalize(line)
        matches = sum(1 for term in lowered_terms if term in normalized_line)
        if matches > best_matches:
            best_line = index
            best_matches = matches
    start = max(1, best_line - 4)
    end = min(len(lines), best_line + 15)
    return start, end


def _root(corpus: Corpus) -> Path:
    return corpus.indexed_root if corpus.indexed_root.exists() else corpus.source_root


def _uri_for(corpus: Corpus, path: str, start: int, end: int) -> str:
    if corpus.kind == "project":
        return f"ue-project://{corpus.corpus_id}/source/{path}#L{start}-L{end}"
    if corpus.kind == "generated":
        return f"ue-generated://{corpus.corpus_id}/source/{path}#L{start}-L{end}"
    version = corpus.ue_version or corpus.corpus_id.removeprefix("ue-")
    return f"ue://{version}/source/{path}#L{start}-L{end}"


def _query_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", _normalize(text))


def _normalize(text: str) -> str:
    return text.lower().replace("-", " ")
