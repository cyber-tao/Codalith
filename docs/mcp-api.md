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

Tool failures return an MCP error result whose structured content is:

```json
{
  "error": {
    "code": "index_missing",
    "message": "human-readable detail",
    "retryable": false,
    "details": {}
  }
}
```

Codes are a stable contract declared in `codalith.errors`, not derived from exception class names. Resource reads return the same payload as their JSON content.

| Code | Meaning | Retryable |
| --- | --- | --- |
| `invalid_arguments` | Input failed schema or range validation | no |
| `invalid_uri` | Malformed or non-canonical `codalith://` URI | no |
| `corpus_not_found` | Unknown corpus or workspace | no |
| `symbol_not_found` | Symbol id is not part of the active generation | no |
| `source_policy` | Path or line range violates the source policy | no |
| `source_unreadable` | Indexed file is missing or unreadable now | no |
| `index_missing` | The corpus has no published generation | no |
| `index_invalid` | The active generation no longer matches its artifacts or configuration | no |
| `index_unavailable` | No corpus in the target can serve the request | no |
| `retrieval_failed` | Semantic or text retrieval failed, for example an embedding endpoint error | yes |
| `configuration_invalid` | Registry or source policy is invalid | no |
| `index_build_failed` | Building or publishing a generation failed (CLI) | no |
| `internal` | Unexpected failure; logged without request bodies, message withheld | yes |
