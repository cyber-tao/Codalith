"""SQLite schema for immutable structural snapshots."""

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE files (
    path TEXT PRIMARY KEY,
    language TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    line_count INTEGER NOT NULL CHECK(line_count >= 1),
    module TEXT
) WITHOUT ROWID;

CREATE TABLE symbols (
    symbol_id TEXT PRIMARY KEY,
    comparison_key TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    signature TEXT NOT NULL,
    path TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
    start_line INTEGER NOT NULL CHECK(start_line >= 1),
    end_line INTEGER NOT NULL CHECK(end_line >= start_line),
    module TEXT,
    parent_symbol_id TEXT REFERENCES symbols(symbol_id),
    metadata_json TEXT NOT NULL DEFAULT '{}'
) WITHOUT ROWID;

CREATE TABLE "references" (
    reference_id INTEGER PRIMARY KEY,
    source_symbol_id TEXT REFERENCES symbols(symbol_id) ON DELETE CASCADE,
    target_name TEXT NOT NULL,
    target_symbol_id TEXT REFERENCES symbols(symbol_id),
    resolution TEXT NOT NULL CHECK(resolution IN ('resolved', 'ambiguous', 'unresolved')),
    kind TEXT NOT NULL,
    path TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
    line INTEGER NOT NULL CHECK(line >= 1)
);

CREATE TABLE module_dependencies (
    dependency_id INTEGER PRIMARY KEY,
    source_module TEXT NOT NULL,
    target_module TEXT NOT NULL,
    kind TEXT NOT NULL,
    path TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
    line INTEGER NOT NULL CHECK(line >= 1),
    UNIQUE(source_module, target_module, kind, path, line)
);

"""

INDEX_SQL = """
CREATE INDEX symbols_name_idx ON symbols(name COLLATE NOCASE);
CREATE INDEX symbols_qualified_idx ON symbols(qualified_name COLLATE NOCASE);
CREATE INDEX symbols_comparison_idx ON symbols(comparison_key);
CREATE INDEX symbols_path_line_idx ON symbols(path, start_line, end_line);

CREATE INDEX references_source_idx ON "references"(source_symbol_id);
CREATE INDEX references_target_idx ON "references"(target_symbol_id);
CREATE INDEX references_name_idx ON "references"(target_name COLLATE NOCASE);

CREATE INDEX module_dependencies_source_idx ON module_dependencies(source_module);
CREATE INDEX module_dependencies_target_idx ON module_dependencies(target_module);
"""
