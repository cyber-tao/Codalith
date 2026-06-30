CREATE TABLE IF NOT EXISTS ue_module_deps (
  corpus_id TEXT NOT NULL,
  from_module TEXT NOT NULL,
  to_module TEXT NOT NULL,
  dep_kind TEXT NOT NULL,
  evidence_uri TEXT NOT NULL,
  metadata TEXT DEFAULT '{}',
  PRIMARY KEY(corpus_id, from_module, to_module, dep_kind)
);

CREATE TABLE IF NOT EXISTS ue_reflection_entities (
  corpus_id TEXT NOT NULL,
  reflection_id TEXT PRIMARY KEY,
  cpp_symbol_id TEXT,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  owner_symbol_id TEXT,
  module_name TEXT,
  declaration_uri TEXT,
  generated_uri TEXT,
  specifiers TEXT DEFAULT '{}',
  metadata TEXT DEFAULT '{}',
  confidence REAL DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS ue_graph_edges (
  corpus_id TEXT NOT NULL,
  edge_id TEXT PRIMARY KEY,
  from_node TEXT NOT NULL,
  to_node TEXT NOT NULL,
  edge_type TEXT NOT NULL,
  evidence_uri TEXT,
  extractor TEXT NOT NULL,
  confidence REAL DEFAULT 1.0,
  metadata TEXT DEFAULT '{}'
);
