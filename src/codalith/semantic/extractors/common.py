"""Shared text helpers for source extractors."""

from __future__ import annotations

import re

_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"//.*?$", re.MULTILINE)


def strip_comments(text: str) -> str:
    """Remove C-style block and line comments before regex scanning."""
    return _LINE_COMMENT_RE.sub("", _BLOCK_COMMENT_RE.sub("", text))
