# Architecture and invariants

Codalith has one transport-independent query core and two retrieval planes:

1. The structural plane is an immutable SQLite snapshot built by a language adapter.
2. The retrieval plane is a CodeRAG store built from that snapshot's exact file list, or an
   explicitly adopted external store whose results are constrained to snapshot membership.

The query service fuses both planes and is the only API used by MCP transports.

## Corpus and workspace model

A corpus identifies one source root, revision, index root, adapter, embedding configuration, extension allow-list, and ignored directories. A workspace is an ordered list of corpus IDs. IDs are lowercase URI-safe values and are globally unique across corpora and workspaces.

Version comparison is expressed as two corpora. There is no implicit overlay or mutable project corpus.

## Generation layout

```text
<index_root>/
  current.json
  generations/
    <timestamp>-<digest>/
      manifest.json
      structure.sqlite
      coderag-meta.json       # when semantic retrieval is available
      coderag/                # omitted when an external store is adopted
```

The builder writes a hidden staging directory, validates SQLite integrity, records artifact hashes, verifies source hashes after semantic indexing, renames staging to its final generation ID, then atomically replaces `current.json`. Old generations are never mutated or silently deleted.

The manifest binds source revision/fingerprint, adapter ID/version, source-selection policy, structure schema, CodeRAG version, embedding provider/model/dimension, store fingerprint, counts, and local artifact hashes. Configuration or adapter drift invalidates the active generation.

## Structural schema

- `files`: path, language, SHA-256, byte size, line count, module.
- `symbols`: stable content-derived ID, qualified/name/kind/signature, range, parent, metadata.
- `references`: source/target IDs when uniquely resolvable, raw target, resolution state, evidence line.
- `module_dependencies`: adapter-derived import/include/build edges.

Resolution is deliberately conservative. Multiple candidates remain `ambiguous`; missing candidates remain `unresolved`. Graph responses retain that state instead of inventing an edge.

## Language adapters

Adapters implement `supports(path)` and `extract(path, text)` without filesystem or database access.

- Python uses the standard-library AST.
- C# uses public tree-sitter APIs for types, callables, members, calls, namespaces, and using edges.
- C++/UE uses public tree-sitter APIs, byte-preserving masking for reflection/export macros, UE metadata extraction, include edges, and typed `.Build.cs` dependencies; embedded C# files use the C# adapter.
- Generic indexes file provenance only.

Changing extraction semantics requires an adapter version increment.

## Retrieval and context

`auto` search extracts identifier candidates for exact structural lookup, obtains over-fetched CodeRAG candidates, rejects any semantic path absent from the active structural snapshot, and combines ranks with weighted reciprocal-rank fusion. Exact text search is also filtered through structural membership.

`codalith_context` removes overlapping ranges, enforces span/character budgets, reads current source through the policy layer, and reports stale hashes or decoding replacements. Confidence is high only for corroborated structure plus CodeRAG evidence; degraded or stale results are low confidence.

## Local security boundary

Codalith assumes a trusted local operator, not hostile tenants. Security therefore focuses on source confinement and browser/network exposure:

- canonical relative source paths and generation membership;
- deny globs and hard byte/line limits;
- loopback defaults, DNS-rebinding Host checks, exact Origin allow-list;
- complete streamed-request buffering up to a fixed maximum;
- no request/response body logging by default;
- no secret-bearing auth/database surface.
