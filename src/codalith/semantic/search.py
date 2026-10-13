"""Hybrid search: dense vectors and BM25, fused with weighted Reciprocal Rank Fusion."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from typing import TYPE_CHECKING

from codalith.semantic.config import Config
from codalith.semantic.types import SearchHit

if TYPE_CHECKING:
    from codalith.semantic.embeddings import EmbeddingProvider
    from codalith.semantic.store import LanceStore


class HybridSearcher:
    def __init__(
        self,
        config: Config,
        provider: EmbeddingProvider,
        store: LanceStore,
    ) -> None:
        self.config = config
        self.provider = provider
        self.store = store

    def search(self, query: str, top_k: int) -> list[SearchHit]:
        if not query or not query.strip():
            return []

        fetch_k = max(self.config.fetch_k, top_k)
        dense = self.store.vector_search(self.provider.embed_query(query), fetch_k)
        similarity: dict[int, float] = {
            cid: float(max(0.0, min(1.0, s))) for cid, s in dense
        }
        dense_ranked = [cid for cid, _ in dense]
        lexical_ranked = [cid for cid, _ in self.store.lexical_search(query, fetch_k)]

        dense_w, lexical_w = fusion_weights(query, self.config)
        fused = reciprocal_rank_fusion(
            [dense_ranked, lexical_ranked],
            k=self.config.rrf_k,
            weights=[dense_w, lexical_w],
        )[:top_k]
        if not fused:
            return []

        rows = self.store.hydrate([cid for cid, _ in fused])
        hits: list[SearchHit] = []
        for cid, score in fused:
            row = rows.get(cid)
            if row is None:
                continue
            hits.append(
                SearchHit(
                    chunk_id=cid,
                    path=row["path"],
                    symbol=row["symbol"] or None,
                    kind=row["kind"],
                    language=row["language"],
                    start_line=int(row["start_line"]),
                    end_line=int(row["end_line"]),
                    text=row["text"],
                    score=float(score),
                    similarity=similarity.get(cid, 0.0),
                )
            )
        return hits


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[int]],
    k: int = 60,
    weights: Sequence[float] | None = None,
) -> list[tuple[int, float]]:
    """Fuse ranked id-lists by position only, so dense cosine and BM25 scales never mix.

    An id that appears in several lists has its contributions summed, which also deduplicates.
    Returns ``(id, score)`` sorted best-first.
    """
    if weights is None:
        weights = [1.0] * len(ranked_lists)
    scores: dict[int, float] = defaultdict(float)
    for ranked, weight in zip(ranked_lists, weights, strict=False):
        for rank, item_id in enumerate(ranked):
            scores[item_id] += weight * (1.0 / (k + rank + 1))
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


# Adaptive fusion weighting. A fixed 1:1 dense/BM25 fusion is a compromise: on pure
# natural-language "where is X handled" queries the dense retriever is much stronger and
# equal-weight RRF drags it down with weak BM25. Keying on query *shape* (short and
# code-looking) misclassifies prose that mentions a specific symbol, e.g. a commit message
# "Fix tuple order in `AliasGenerator.generate_aliases()`", where the exact identifier BM25
# matches is the discriminating signal. So the routing question is whether the query
# references a code identifier: if so, fusion stays neutral; pure prose leans dense.

# Query-wide signals, both linear (disjoint character classes, no catastrophic backtracking
# on the client-supplied query): a `backtick`-quoted span, or a call ``word(``.
_BACKTICK = re.compile(r"`[^`]+`")
_CALL = re.compile(r"\w\(")
# Punctuation stripped from a token before classifying it (query prose, not code).
_STRIP = "`'\".,:;!?()[]{}<>"


def _is_snake_case(token: str) -> bool:
    """An underscore flanked by alphanumerics, e.g. ``foo_bar`` (linear scan)."""
    return any(
        token[i] == "_" and token[i - 1].isalnum() and token[i + 1].isalnum()
        for i in range(1, len(token) - 1)
    )


def _is_dotted_path(token: str) -> bool:
    """Two adjacent identifier parts joined by a dot, e.g. ``Foo.bar``; excludes ``e.g``/``3.11``."""
    parts = token.split(".")
    return any(
        len(a) >= 2 and len(b) >= 2 and a.isidentifier() and b.isidentifier()
        for a, b in zip(parts, parts[1:], strict=False)
    )


def _is_camel_case(token: str) -> bool:
    """A lower->upper boundary, e.g. ``fooBar`` / ``AliasGenerator`` (linear scan)."""
    return any(
        a.islower() and b.isupper() for a, b in zip(token, token[1:], strict=False)
    )


def references_identifier(query: str) -> bool:
    """True if ``query`` names a specific code identifier, even inside a prose sentence.

    Linear token scanning rather than one backtracking regex, so a crafted query cannot
    become a ReDoS.
    """
    if not query:
        return False
    if _BACKTICK.search(query) or _CALL.search(query):
        return True
    for raw in query.split():
        token = raw.strip(_STRIP)
        if len(token) >= 2 and (
            _is_snake_case(token) or _is_dotted_path(token) or _is_camel_case(token)
        ):
            return True
    return False


def fusion_weights(query: str, config: Config) -> tuple[float, float]:
    """Return ``(dense_weight, lexical_weight)`` for ``query``.

    A query that references an identifier uses the (neutral by default) code weights, keeping
    BM25's exact-match signal; a pure natural-language query uses the dense-leaning weights.
    """
    if references_identifier(query):
        return config.code_dense_weight, config.code_lexical_weight
    return config.nl_dense_weight, config.nl_lexical_weight
