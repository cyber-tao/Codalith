"""Hybrid searcher: dense + lexical retrieval fused with RRF, hydrated from the store."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from codalith.semantic.config import Config
from codalith.semantic.embeddings import EmbeddingProvider
from codalith.semantic.retrieval.fusion import reciprocal_rank_fusion
from codalith.semantic.retrieval.query_type import fusion_weights
from codalith.semantic.types import SearchHit

if TYPE_CHECKING:
    from codalith.semantic.store.lance_store import LanceStore

logger = logging.getLogger(__name__)


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

    def search(
        self,
        query: str,
        top_k: int,
        *,
        timings: dict[str, float] | None = None,
    ) -> list[SearchHit]:
        """Hybrid search. Pass a ``timings`` dict to receive per-phase latency in milliseconds."""
        if not query or not query.strip():
            return []

        fetch_k = max(self.config.fetch_k, top_k)

        # Dense retrieval (vector ANN over the store). The query embedding is the model
        # inference; on a busy/throttled host it can dwarf the store ops, so time it apart.
        t0 = time.perf_counter()
        qvec = self.provider.embed_query(query)
        if timings is not None:
            timings["embed_ms"] = (time.perf_counter() - t0) * 1000.0
        t0 = time.perf_counter()
        dense = self.store.vector_search(qvec, fetch_k)
        if timings is not None:
            timings["dense_ms"] = (time.perf_counter() - t0) * 1000.0
        similarity: dict[int, float] = {
            cid: float(max(0.0, min(1.0, s))) for cid, s in dense
        }
        dense_ranked = [cid for cid, _ in dense]

        # Lexical retrieval (BM25 over the store).
        t0 = time.perf_counter()
        lexical_ranked = [cid for cid, _ in self.store.lexical_search(query, fetch_k)]
        if timings is not None:
            timings["lexical_ms"] = (time.perf_counter() - t0) * 1000.0

        # Weights may adapt to the query type (dense-up for NL, balanced for identifiers).
        dense_w, lexical_w = fusion_weights(query, self.config)
        fused = reciprocal_rank_fusion(
            [dense_ranked, lexical_ranked],
            k=self.config.rrf_k,
            weights=[dense_w, lexical_w],
        )[:top_k]
        if not fused:
            return []

        ids = [cid for cid, _ in fused]
        t0 = time.perf_counter()
        rows = self.store.hydrate(ids)
        if timings is not None:
            timings["hydrate_ms"] = (time.perf_counter() - t0) * 1000.0

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
