"""Schema DDL loading and lightweight migrations for the semantic store."""

from __future__ import annotations

from pathlib import Path

from codalith.semantic.store.connection import ConnectionBase


def initialize_schema(store: ConnectionBase) -> None:
    schema_name = "schema_postgres.sql" if store.dialect == "postgresql" else "schema.sql"
    schema = (Path(__file__).parent / schema_name).read_text(encoding="utf-8")
    if store.dialect == "postgresql":
        with store.connection.cursor() as cursor:
            for statement in _split_sql_statements(schema):
                cursor.execute(statement)
    else:
        store.connection.executescript(schema)
    store.connection.commit()


def _split_sql_statements(schema: str) -> list[str]:
    return [statement.strip() for statement in schema.split(";") if statement.strip()]
