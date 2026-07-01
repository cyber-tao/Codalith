"""Evidence projection helpers."""

from __future__ import annotations

from codalith.coderag.adapter import RetrievalHit
from codalith.coderag.result_mapper import hits_to_source_spans


def select_source_spans(hits: list[RetrievalHit]) -> list[dict[str, object]]:
    return hits_to_source_spans(hits)
