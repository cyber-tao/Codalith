"""SQLite/PostgreSQL semantic store split into connection/schema/writers/queries."""

from __future__ import annotations

from pathlib import Path

from codalith.semantic.store.schema import initialize_schema
from codalith.semantic.store.writers import SemanticWriters


class SemanticStore(SemanticWriters):
    """Facade combining connection handling, writers, and queries."""

    def __init__(self, path: str | Path | None = None) -> None:
        super().__init__(path)
        self.initialize()

    def initialize(self) -> None:
        initialize_schema(self)


__all__ = ["SemanticStore"]
