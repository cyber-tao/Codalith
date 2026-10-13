"""Chunking: turn a source file into symbol-aware, non-overlapping chunks.

Dispatch order:
- Python -> stdlib ``ast`` symbol spans.
- JS/TS/TSX/Go/Rust/Java -> tree-sitter symbol spans.
- anything else, or any parse failure -> line-window fallback.

A parse error never breaks indexing; it degrades gracefully to windows.
"""

from __future__ import annotations

import logging

from codalith.semantic.chunking import base, languages
from codalith.semantic.config import Config
from codalith.semantic.types import Chunk

logger = logging.getLogger(__name__)


def chunk_file(text: str, language: str, config: Config) -> list[Chunk]:
    if not text.strip():
        return []

    spans = []
    try:
        if language == languages.PYTHON:
            from codalith.semantic.chunking import python_ast

            spans = python_ast.extract_spans(text)
        elif language in languages.TREE_SITTER_LANGUAGES:
            from codalith.semantic.chunking import treesitter

            spans = treesitter.extract_spans(text, language)
    except Exception as exc:  # SyntaxError, tree-sitter issues, etc.
        logger.debug(
            "Symbol extraction failed for %s (%s); using windows.", language, exc
        )
        spans = []

    return [
        bounded
        for chunk in base.build_chunks(text, language, spans, config)
        for bounded in base.limit_chunk_bytes(chunk)
    ]


__all__ = ["chunk_file", "languages"]
