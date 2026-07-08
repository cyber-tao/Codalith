"""Schema DDL loading and lightweight migrations for the semantic store."""

from __future__ import annotations

from pathlib import Path

from codalith.semantic.store.connection import ConnectionBase

# Domain-neutral renames introduced when the store dropped its UE-specific
# naming. Applied before the DDL runs so existing data carries over in place.
_LEGACY_TABLE_RENAMES = {
    "ue_graph_edges": "codalith_graph_edges",
    "ue_modules": "codalith_modules",
    "ue_module_deps": "codalith_module_deps",
    "ue_symbols": "codalith_symbols",
    "ue_reflection_entities": "codalith_reflection_entities",
    "ue_compile_guards": "codalith_compile_guards",
    "ue_targets": "codalith_targets",
    "ue_plugins": "codalith_plugins",
    "ue_projects": "codalith_projects",
}

_LEGACY_INDEX_RENAMES = {
    "idx_ue_graph_from": "idx_codalith_graph_from",
    "idx_ue_graph_to": "idx_codalith_graph_to",
    "idx_ue_symbols_name": "idx_codalith_symbols_name",
    "idx_ue_symbols_qualified_name": "idx_codalith_symbols_qualified_name",
    "idx_ue_reflection_entities_name": "idx_codalith_reflection_entities_name",
    "idx_ue_compile_guards_path": "idx_codalith_compile_guards_path",
}


def initialize_schema(store: ConnectionBase) -> None:
    schema_name = "schema_postgres.sql" if store.dialect == "postgresql" else "schema.sql"
    schema = (Path(__file__).parent / schema_name).read_text(encoding="utf-8")
    _migrate_legacy_names(store)
    if store.dialect == "postgresql":
        with store.connection.cursor() as cursor:
            for statement in _split_sql_statements(schema):
                cursor.execute(statement)
    else:
        store.connection.executescript(schema)
    store.connection.commit()


def _migrate_legacy_names(store: ConnectionBase) -> None:
    for old_table, new_table in _LEGACY_TABLE_RENAMES.items():
        if _table_exists(store, old_table) and not _table_exists(store, new_table):
            store.connection.execute(f"ALTER TABLE {old_table} RENAME TO {new_table}")
    if _column_exists(store, "codalith_corpora", "ue_version"):
        store.connection.execute("ALTER TABLE codalith_corpora RENAME COLUMN ue_version TO version")
    for old_index, new_index in _LEGACY_INDEX_RENAMES.items():
        if store.dialect == "postgresql":
            store.connection.execute(f"ALTER INDEX IF EXISTS {old_index} RENAME TO {new_index}")
        else:
            # SQLite cannot rename indexes; drop so the DDL recreates them.
            store.connection.execute(f"DROP INDEX IF EXISTS {old_index}")
    store.connection.commit()


def _table_exists(store: ConnectionBase, table: str) -> bool:
    if store.dialect == "postgresql":
        row = store.connection.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = %s",
            (table,),
        ).fetchone()
    else:
        row = store.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
    return row is not None


def _column_exists(store: ConnectionBase, table: str, column: str) -> bool:
    if not _table_exists(store, table):
        return False
    if store.dialect == "postgresql":
        row = store.connection.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = %s AND column_name = %s",
            (table, column),
        ).fetchone()
        return row is not None
    columns = store.connection.execute(f"PRAGMA table_info({table})").fetchall()
    return any(item["name"] == column for item in columns)


def _split_sql_statements(schema: str) -> list[str]:
    return [statement.strip() for statement in schema.split(";") if statement.strip()]
