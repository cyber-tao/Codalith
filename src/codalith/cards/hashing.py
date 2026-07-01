"""Source evidence hashing shared by cards and source reads."""

from __future__ import annotations

import hashlib


def source_sha256(content: str) -> str:
    canonical = content.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
