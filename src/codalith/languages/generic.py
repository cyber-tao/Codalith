"""Fallback adapter for indexed text without structural semantics."""

from __future__ import annotations

from pathlib import Path

from codalith.languages.base import ExtractionResult
from codalith.languages.catalog import ADAPTERS


class GenericAdapter:
    adapter_id = "generic"
    version = ADAPTERS["generic"].version

    def supports(self, path: Path) -> bool:
        return True

    def extract(self, path: str, text: str) -> ExtractionResult:
        return ExtractionResult(language=path.rsplit(".", 1)[-1].lower() if "." in path else "text")
