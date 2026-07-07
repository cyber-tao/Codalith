"""Connection management and SQL dialect helpers for the semantic store."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any


class ConnectionBase:
    """Owns the DB connection and dialect-aware statement execution."""

    def __init__(self, path: str | Path | None = ":memory:") -> None:
        configured = str(path or os.getenv("CODALITH_SEMANTIC_DSN") or ":memory:")
        self.dialect = "postgresql" if _is_postgres_dsn(configured) else "sqlite"
        self.connection: Any
        if self.dialect == "postgresql":
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:  # pragma: no cover - exercised in PostgreSQL envs.
                raise RuntimeError(
                    "PostgreSQL semantic store requires the psycopg package"
                ) from exc
            self.connection = psycopg.connect(configured, row_factory=dict_row)
        else:
            if configured != ":memory:":
                Path(configured).parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(configured, check_same_thread=False)
            self.connection.row_factory = sqlite3.Row

    def _execute(
        self,
        sql: str,
        params: Iterable[Any] = (),
        *,
        commit: bool = False,
    ) -> Any:
        statement = self._sql(sql)
        cursor = self.connection.execute(statement, tuple(params))
        if commit:
            self.connection.commit()
        return cursor

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.dialect == "postgresql" else sql

    def _json(self, value: Any) -> Any:
        if self.dialect == "postgresql":
            try:
                from psycopg.types.json import Jsonb
            except ImportError as exc:  # pragma: no cover - exercised in PostgreSQL envs.
                raise RuntimeError("PostgreSQL semantic store requires psycopg Jsonb") from exc
            return Jsonb(value)
        return json.dumps(value, sort_keys=True)

    def commit(self) -> None:
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()


def _is_postgres_dsn(value: str) -> bool:
    return value.startswith(("postgresql://", "postgres://"))
