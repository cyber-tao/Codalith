"""Optional HTTP model reranker for source retrieval hits."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import replace
from typing import Protocol

from codalith.coderag.adapter import RetrievalHit
from codalith.errors import CodalithError


class RerankerError(CodalithError):
    """Raised when a configured model reranker cannot score candidates."""


class ModelReranker(Protocol):
    max_candidates: int

    def rerank(self, query: str, hits: list[RetrievalHit]) -> list[RetrievalHit]:
        """Return hits ordered by model relevance."""


class HTTPModelReranker:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout_seconds: float = 30.0,
        max_candidates: int = 40,
        strict: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_candidates = max_candidates
        self.strict = strict

    def rerank(self, query: str, hits: list[RetrievalHit]) -> list[RetrievalHit]:
        candidates = hits[: self.max_candidates]
        if not candidates:
            return hits
        try:
            scores = self._score(query, candidates)
        except Exception as exc:
            if self.strict:
                raise RerankerError(str(exc)) from exc
            return hits
        rescored: list[RetrievalHit] = []
        for index, hit in enumerate(candidates):
            score = scores.get(index)
            if score is None:
                rescored.append(hit)
                continue
            metadata = {
                **hit.metadata,
                "reranker_model": self.model,
                "reranker_score": score,
                "pre_rerank_score": hit.score,
            }
            rescored.append(
                replace(
                    hit,
                    score=score,
                    reason=f"{hit.reason} Reordered by model reranker.",
                    metadata=metadata,
                )
            )
        return sorted(rescored, key=lambda hit: hit.score, reverse=True) + hits[self.max_candidates :]

    def _score(self, query: str, hits: list[RetrievalHit]) -> dict[int, float]:
        payload = {
            "model": self.model,
            "query": query,
            "documents": [_document(hit) for hit in hits],
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            f"{self.base_url}/rerank",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise RerankerError(f"reranker HTTP {exc.code}: {detail}") from exc
        results = body.get("results")
        if not isinstance(results, list):
            raise RerankerError("reranker response missing results")
        scores: dict[int, float] = {}
        for result in results:
            if not isinstance(result, dict):
                continue
            index = result.get("index")
            score = result.get("relevance_score", result.get("score"))
            if isinstance(index, int) and isinstance(score, (int, float)):
                scores[index] = float(score)
        return scores


def reranker_from_env() -> ModelReranker | None:
    raw_enabled = os.getenv("CODALITH_RERANKER_ENABLED", "").lower()
    if raw_enabled in {"0", "false", "no"}:
        return None
    enabled = raw_enabled in {"1", "true", "yes"}
    base_url = os.getenv("CODALITH_RERANKER_BASE_URL", "").strip()
    model = os.getenv("CODALITH_RERANKER_MODEL", "").strip()
    if not enabled and not base_url:
        return None
    if not base_url or not model:
        raise RerankerError("CODALITH_RERANKER_BASE_URL and CODALITH_RERANKER_MODEL are required")
    timeout = _env_float("CODALITH_RERANKER_TIMEOUT_SECONDS", 30.0)
    max_candidates = _env_int("CODALITH_RERANKER_MAX_CANDIDATES", 40)
    strict = os.getenv("CODALITH_RERANKER_STRICT", "").lower() in {"1", "true", "yes"}
    api_key = os.getenv("CODALITH_RERANKER_API_KEY") or os.getenv("API_KEY")
    return HTTPModelReranker(
        base_url=base_url,
        model=model,
        api_key=api_key,
        timeout_seconds=timeout,
        max_candidates=max_candidates,
        strict=strict,
    )


def retrieval_top_k_from_env(*, reranker_enabled: bool) -> int | None:
    raw = os.getenv("CODALITH_RETRIEVAL_TOP_K")
    if raw:
        return _env_int("CODALITH_RETRIEVAL_TOP_K", 0)
    if reranker_enabled:
        return _env_int("CODALITH_RERANKER_MAX_CANDIDATES", 40)
    return None


def _document(hit: RetrievalHit) -> str:
    return "\n".join(
        [
            f"path: {hit.path}",
            f"module: {hit.module or ''}",
            f"symbol: {hit.symbol or ''}",
            hit.snippet,
        ]
    )


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RerankerError(f"{name} must be an integer") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RerankerError(f"{name} must be a number") from exc
