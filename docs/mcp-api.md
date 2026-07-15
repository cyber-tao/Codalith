# MCP API

Both transports use the official Python MCP SDK and the same strict Pydantic schemas. Unknown input fields are errors. Tool annotations declare read-only, non-destructive, idempotent, closed-world behavior.

## Tools

### `codalith_search`

Input: `query`, optional `target`, `strategy` (`auto`, `semantic`, `text`, `symbol`), and `limit` (1–50).

Returns ranked hits with corpus/revision/generation, canonical source URI, path/range, symbol metadata, normalized score, contributing backends, snippet, degradation flag, and warnings.

### `codalith_context`

Input: `query`, optional `target`, `max_spans` (1–20), and `max_chars` (1,000–100,000).

Returns non-overlapping source spans with current/indexed hashes, stale/truncation/decoding state, confidence, and warnings.

### `codalith_read`

Input: one canonical `codalith://<corpus>/source/<path>#Lx-Ly` URI.

The path must exist in the active structural generation. The response includes actual canonical range, total lines, text, both hashes, and stale state.

### `codalith_symbol`

Input: symbol `query`, optional `target`, `exact`, and `limit` (1–100). Returns definitions and canonical symbol/source URIs.

### `codalith_graph`

Input: canonical symbol `root_uri`, `direction`, `depth` (1–3), and edge `limit` (1–1,000). Returns nodes, evidence-backed edges, resolution state, and truncation.

### `codalith_compare`

Input: `from_corpus`, `to_corpus`, `include_unchanged`, and `limit` (1–1,000). Returns added/removed/changed/unchanged/ambiguous symbol groups and changed fields. Each group returns at most 20 definitions per side and marks group-level truncation explicitly.

### `codalith_status`

Input: optional `target`. Returns per-corpus ready/degraded/missing/invalid state and manifest counts without loading embedding models or scanning source.

## Resources

Listed resources expose corpus status. Templates expose indexed source and symbol URIs. Resource reads return JSON and use the same service methods as tools.

## Errors

Expected configuration, validation, index, policy, URI, source, and retrieval failures return MCP tool errors with:

```json
{
  "error": {
    "code": "snake_case_type",
    "message": "human-readable detail",
    "retryable": false,
    "details": {}
  }
}
```

Unexpected failures are logged without request bodies and return `internal` with `retryable: true`.
