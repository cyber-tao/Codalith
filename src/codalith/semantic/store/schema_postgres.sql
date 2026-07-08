CREATE TABLE IF NOT EXISTS codalith_corpora (
  corpus_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  version TEXT,
  source_commit TEXT,
  source_root TEXT,
  indexed_root TEXT,
  semantic_schema TEXT,
  metadata JSONB DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS codalith_source_files (
  corpus_id TEXT NOT NULL,
  path TEXT NOT NULL,
  language TEXT NOT NULL DEFAULT 'text',
  module_name TEXT,
  source_hash TEXT,
  line_count INTEGER DEFAULT 0,
  metadata JSONB DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY(corpus_id, path)
);

CREATE TABLE IF NOT EXISTS codalith_modules (
  corpus_id TEXT NOT NULL,
  module_name TEXT NOT NULL,
  module_type TEXT,
  source_uri TEXT,
  metadata JSONB DEFAULT '{}'::jsonb,
  PRIMARY KEY(corpus_id, module_name)
);

CREATE TABLE IF NOT EXISTS codalith_module_deps (
  corpus_id TEXT NOT NULL,
  from_module TEXT NOT NULL,
  to_module TEXT NOT NULL,
  dep_kind TEXT NOT NULL,
  evidence_uri TEXT NOT NULL,
  metadata JSONB DEFAULT '{}'::jsonb,
  PRIMARY KEY(corpus_id, from_module, to_module, dep_kind)
);

CREATE TABLE IF NOT EXISTS codalith_symbols (
  corpus_id TEXT NOT NULL,
  symbol_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  qualified_name TEXT,
  kind TEXT NOT NULL,
  module_name TEXT,
  declaration_uri TEXT,
  definition_uri TEXT,
  signature TEXT,
  build_guard TEXT,
  metadata JSONB DEFAULT '{}'::jsonb,
  confidence DOUBLE PRECISION DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS codalith_compile_guards (
  corpus_id TEXT NOT NULL,
  guard_id TEXT PRIMARY KEY,
  path TEXT NOT NULL,
  macro TEXT NOT NULL,
  expression TEXT NOT NULL,
  start_line INTEGER NOT NULL,
  end_line INTEGER,
  evidence_uri TEXT,
  metadata JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS knowledge_cards (
  corpus_id TEXT NOT NULL,
  card_id TEXT NOT NULL,
  card_type TEXT NOT NULL,
  title TEXT NOT NULL,
  version TEXT,
  verification_status TEXT NOT NULL,
  related_nodes JSONB DEFAULT '[]'::jsonb,
  source_hashes JSONB DEFAULT '{}'::jsonb,
  metadata JSONB DEFAULT '{}'::jsonb,
  PRIMARY KEY(corpus_id, card_id)
);

CREATE TABLE IF NOT EXISTS codalith_graph_edges (
  corpus_id TEXT NOT NULL,
  edge_id TEXT PRIMARY KEY,
  from_node TEXT NOT NULL,
  to_node TEXT NOT NULL,
  edge_type TEXT NOT NULL,
  evidence_uri TEXT,
  extractor TEXT NOT NULL,
  confidence DOUBLE PRECISION DEFAULT 1.0,
  metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_codalith_graph_from
ON codalith_graph_edges(corpus_id, from_node, edge_type);

CREATE INDEX IF NOT EXISTS idx_codalith_graph_to
ON codalith_graph_edges(corpus_id, to_node, edge_type);

CREATE INDEX IF NOT EXISTS idx_codalith_symbols_name
ON codalith_symbols(corpus_id, name, kind);

CREATE INDEX IF NOT EXISTS idx_codalith_symbols_qualified_name
ON codalith_symbols(corpus_id, qualified_name);

CREATE INDEX IF NOT EXISTS idx_codalith_compile_guards_path
ON codalith_compile_guards(corpus_id, path, start_line, end_line);
