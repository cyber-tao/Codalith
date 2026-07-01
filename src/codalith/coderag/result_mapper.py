"""Helpers for mapping retrieval hits."""

from __future__ import annotations

from codalith.coderag.adapter import RetrievalHit


def hits_to_source_spans(hits: list[RetrievalHit]) -> list[dict[str, object]]:
    return [
        {
            "uri": hit.uri,
            "path": hit.path,
            "start_line": hit.start_line,
            "end_line": hit.end_line,
            "reason": hit.reason,
            "source": hit.source,
            "guard": None,
        }
        for hit in hits
    ]
