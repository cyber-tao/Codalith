"""Connection management and SQL dialect helpers for the semantic store."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any


class ConnectionBase:
    """Owns thread-local DB connections and dialect-aware statement execution."""

    def __init__(self, path: str | Path | None = None) -> None:
        configured = str(
            path
            or os.getenv("CODALITH_SEMANTIC_DSN")
            or os.getenv("CODALITH_SEMANTIC_DB")
            or ":memory:"
        )
        self.dialect = "postgresql" if _is_postgres_dsn(configured) else "sqlite"
        self.configured_target = configured
        self._sqlite_uri = False
        if configured == ":memory:":
            self.configured_target = (
                f"file:codalith-{uuid.uuid4().hex}?mode=memory&cache=shared"
            )
            self._sqlite_uri = True
        self._local = threading.local()
        self._connections: list[Any] = []
        self._connections_lock = threading.RLock()
        self._closed = False
        _ = self.connection

    @property
    def connection(self) -> Any:
        if self._closed:
            raise RuntimeError("Semantic store is closed")
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = self._open_connection()
            self._local.connection = connection
            with self._connections_lock:
                self._connections.append(connection)
        return connection

    def _open_connection(self) -> Any:
        if self.dialect == "postgresql":
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:  # pragma: no cover - exercised in PostgreSQL envs.
                raise RuntimeError(
                    "PostgreSQL semantic store requires the psycopg package"
                ) from exc
            return psycopg.connect(self.configured_target, row_factory=dict_row)
        if not self._sqlite_uri:
            Path(self.configured_target).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.configured_target,
            check_same_thread=False,
            uri=self._sqlite_uri,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

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
        with self._connections_lock:
            connections = list(self._connections)
            self._connections.clear()
            self._closed = True
        for connection in connections:
            connection.close()


def _is_postgres_dsn(value: str) -> bool:
    return value.startswith(("postgresql://", "postgres://"))
