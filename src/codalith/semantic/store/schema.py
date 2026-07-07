"""Schema DDL loading and lightweight migrations for the semantic store."""

from __future__ import annotations

from pathlib import Path

from codalith.semantic.store.connection import ConnectionBase


def initialize_schema(store: ConnectionBase) -> None:
    schema_name = "schema_postgres.sql" if store.dialect == "postgresql" else "schema.sql"
    schema = (Path(__file__).parent / schema_name).read_text(encoding="utf-8")
    _drop_legacy_reflection_table(store)
    if store.dialect == "postgresql":
        with store.connection.cursor() as cursor:
            for statement in _split_sql_statements(schema):
                cursor.execute(statement)
    else:
        store.connection.executescript(schema)
        _migrate_legacy_sqlite_graph_edges(store)
    store.connection.commit()


def _drop_legacy_reflection_table(store: ConnectionBase) -> None:
    # v0 stored owner names in an owner_symbol_id column. The store holds
    # derived data, so rebuild the table and re-run extraction instead of
    # migrating rows in place.
    if store.dialect == "postgresql":
        legacy = store.connection.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'ue_reflection_entities' AND column_name = 'owner_symbol_id'
            """
        ).fetchone()
    else:
        columns = store.connection.execute(
            "PRAGMA table_info(ue_reflection_entities)"
        ).fetchall()
        legacy = next((column for column in columns if column["name"] == "owner_symbol_id"), None)
    if legacy is not None:
        store.connection.execute("DROP TABLE ue_reflection_entities")
        store.connection.commit()


def _migrate_legacy_sqlite_graph_edges(store: ConnectionBase) -> None:
    legacy = store.connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'ue_graph_edges'"
    ).fetchone()
    if legacy is None:
        return
    store.connection.execute(
        """
        INSERT OR IGNORE INTO codalith_graph_edges
          (corpus_id, edge_id, from_node, to_node, edge_type, evidence_uri,
           extractor, confidence, metadata)
        SELECT corpus_id, edge_id, from_node, to_node, edge_type, evidence_uri,
               extractor, confidence, metadata
        FROM ue_graph_edges
        """
    )


def _split_sql_statements(schema: str) -> list[str]:
    return [statement.strip() for statement in schema.split(";") if statement.strip()]
