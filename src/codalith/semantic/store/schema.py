"""Schema DDL loading and lightweight migrations for the semantic store."""

from __future__ import annotations

from pathlib import Path

from codalith.errors import ConfigurationError
from codalith.semantic.store.connection import ConnectionBase

SCHEMA_VERSION = 1


def initialize_schema(store: ConnectionBase) -> None:
    has_metadata = _table_exists(store, "codalith_schema_meta")
    if _table_exists(store, "codalith_corpora") and not has_metadata:
        raise ConfigurationError(
            "Semantic store uses an unversioned legacy schema; recreate it for this development build"
        )
    schema_name = "schema_postgres.sql" if store.dialect == "postgresql" else "schema.sql"
    schema = (Path(__file__).parent / schema_name).read_text(encoding="utf-8")
    if store.dialect == "postgresql":
        with store.connection.cursor() as cursor:
            for statement in _split_sql_statements(schema):
                cursor.execute(statement)
    else:
        store.connection.executescript(schema)
    row = store._execute(  # noqa: SLF001 - schema initialization owns this contract.
        "SELECT schema_version FROM codalith_schema_meta WHERE singleton = 1"
    ).fetchone()
    if row is None:
        store._execute(  # noqa: SLF001
            "INSERT INTO codalith_schema_meta (singleton, schema_version) VALUES (1, ?)",
            (SCHEMA_VERSION,),
        )
    elif int(row["schema_version"]) != SCHEMA_VERSION:
        raise ConfigurationError(
            f"Semantic schema version {row['schema_version']} is incompatible with "
            f"required version {SCHEMA_VERSION}"
        )
    store.connection.commit()


def _split_sql_statements(schema: str) -> list[str]:
    return [statement.strip() for statement in schema.split(";") if statement.strip()]


def _table_exists(store: ConnectionBase, table: str) -> bool:
    if store.dialect == "postgresql":
        row = store._execute(  # noqa: SLF001
            "SELECT to_regclass(?) AS table_name",
            (table,),
        ).fetchone()
        return row is not None and row["table_name"] is not None
    row = store._execute(  # noqa: SLF001
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None
