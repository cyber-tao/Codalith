# MCP API

Both transports use the official Python MCP SDK and the same strict Pydantic schemas. Unknown input fields are errors. Tool annotations declare read-only, non-destructive, idempotent, closed-world behavior.

Every tool resolves each target corpus to one generation for the whole call. Paths denied by the source policy are withheld from every output and counted in `warnings`.

## Agent workflow and citations

Install the `codalith-source` skill for retrieval choices and evidence boundaries; the MCP tool schemas remain the executable contract. Server initialization lists registered corpus revisions, workspaces, and the default target. Registration does not mean an index is ready. Check `codalith_status` when target/version or readiness is uncertain, rather than before every request, and select the requested target explicitly.

Use context text directly when it is sufficient, current, and hash-matched. Use search/symbol results for discovery, then read only the missing implementation. Stop when the question is answered or needs runtime evidence. Static call/reference evidence cannot enumerate a running process's callback registrations, registration history, or execution order.

Source citations use the exact returned `codalith://<corpus>/source/<path>#Lx-Ly` URI. A symbol's `uri` identifies the symbol; its `source_uri` locates source. Symbol/status/bare corpus URIs are not source citations. To cite a different range, request it through `codalith_read`, then cite the response's actual URI rather than the requested URI: ranges can be shortened at the end of a file. Keep the accompanying revision/generation provenance, check warnings/stale/truncation, and do not combine different generations into one verified snapshot.

## Tools

### `codalith_search`

Input: `query`, optional `target`, `strategy`, and `limit` (1–50).

- `auto`: exact symbol lookup for identifier candidates, file lookup by name and path terms, and semantic hits, fused by rank.
- `semantic`: semantic hits only; fails with `retrieval_failed` when the generation has no semantic plane.
- `text`: case-insensitive literal match of the whole query in the generation's indexed files (up to three lines per file, files with an exact-case match first), plus exact symbol evidence for identifiers in the query. It does not need the semantic plane.
- `symbol`: exact symbol lookup, falling back to substring matches.

Returns ranked hits with corpus/revision/generation, canonical source URI, path/range, symbol metadata, normalized score, contributing backends (`structure`, `semantic`, `text`), snippet, degradation flag, and warnings. A corpus that cannot serve part of the request makes the response `degraded` instead of failing it.

### `codalith_context`

Input: `query`, optional `target`, `max_spans` (1–20), and `max_chars` (1,000–100,000).

Returns non-overlapping source spans with current/indexed hashes, stale/decoding state, confidence, and warnings. `truncated` is true when a span ends before its symbol, chunk, or file does, because of the hard line limit or the character budget.

### `codalith_read`

Input: one canonical `codalith://<corpus>/source/<path>` URI, optionally with a `#Lx-Ly` range. Ranges always use both bounds; a single line is `#L5-L5`. Without a range, the source policy's default line limit applies. Symbol URIs are invalid for this tool.

The path must exist in the active structural generation. The response includes the `generation_id` it was verified against, the actual canonical range, total lines, text, both hashes, and stale state. The returned URI always identifies the actual range; use it unchanged as the citation.

### `codalith_symbol`

Input: symbol `query`, optional `target`, `exact`, and `limit` (1–100). Returns definitions and canonical symbol/source URIs.

`exact` matches the name or qualified name case-insensitively. With `exact=false`, results list exact names first, then names starting with the query, then names or qualified names containing it; within each group, definitions, then declarations, then shorter qualified names come first. The query is literal: `%`, `_`, and quotes have no special meaning.

### `codalith_graph`

Input: canonical symbol `root_uri`, `direction`, `depth` (1–3), and edge `limit` (1–1,000). Returns nodes with their `generation_id`, evidence-backed edges, and truncation. Edge `resolution` is `resolved` (exact qualified match), `name_match` (the last name segment matches exactly one symbol in the corpus), `ambiguous`, or `unresolved`.

### `codalith_compare`

Input: `from_corpus`, `to_corpus`, `include_unchanged`, and `limit` (1–1,000). Returns added/removed/changed/unchanged/ambiguous symbol groups and changed fields. Each group returns at most 20 definitions per side and marks group-level truncation explicitly.

Groups are compared in comparison-key order, by signature, path, module, and metadata. A comparison stops after 30 seconds: `truncated` is then true and `warnings` says how many groups were compared. `truncated` is also true when more than `limit` groups differ.

### `codalith_catalog`

Lists version targets, engine versions, exact source revisions, snapshot identities, readiness and retrieval capabilities. This read-only operation does not load embedding models or scan source trees. The same catalog is available at `GET /api/catalog`.

Version-bound clients use `/mcp/targets/<target_id>` (or the configured MCP endpoint followed by `/targets/<target_id>`). Omitted targets are bound automatically; different targets, cross-version source/symbol URIs and comparisons are rejected. Use the ordinary MCP endpoint for explicit cross-version comparisons.

### `codalith_status`

Input: optional `target`. Returns per-corpus state without loading embedding models or scanning source: `ready` (structural and semantic planes valid), `degraded` (no semantic plane), `missing` (no published generation), or `invalid` (the active generation no longer matches its artifacts, store provenance, or configuration).

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
