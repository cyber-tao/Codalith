# Architecture and invariants

Codalith has one transport-independent query core and two retrieval planes:

1. The structural plane is an immutable SQLite snapshot built by a language adapter.
2. The semantic plane is a CodeRAG store bound to that snapshot. `--semantic build` embeds a
   materialized copy of exactly the snapshot's files into the generation's own store;
   `--semantic adopt` verifies an external store against the snapshot and pins it.

The query service fuses both planes and is the only API used by MCP transports. The HTTP application additionally hosts the operations dashboard; the MCP binding itself never imports dashboard code.

## Corpus and workspace model

A corpus identifies one source root, revision, index root, adapter, embedding configuration, extension allow-list, and excluded paths. A workspace is an ordered list of corpus IDs. IDs are lowercase URI-safe values and are globally unique across corpora and workspaces.

Version comparison is expressed as two corpora. There is no implicit overlay or mutable project corpus.

## Generation layout

```text
<index_root>/
  current.json
  .build.lock                 # held while a build runs
  generations/
    <timestamp>-<digest>/
      manifest.json
      structure.sqlite
      coderag-meta.json       # when semantic retrieval is available
      coderag/                # semantic build only; adopted stores stay external
```

A build holds an OS-level lock on the index root, writes a hidden staging directory, validates SQLite integrity, prepares the semantic plane, re-hashes every indexed source file, fsyncs the generation, renames staging to its final generation ID, and atomically replaces `current.json` through an fsynced temporary file. A failed build leaves the active generation and its store untouched. Old generations are never mutated or silently deleted.

The manifest (schema 3) binds source revision and fingerprint, adapter ID and version, a source-selection hash covering the corpus extensions and excludes plus the source policy's deny globs and byte limit, structure schema, CodeRAG version, embedding provider, model and dimension, the semantic store location, fingerprint and coverage, counts, and local artifact hashes. Configuration, policy, adapter, or CodeRAG version drift invalidates the active generation, which must then be rebuilt.

## Semantic plane

- **Build** materializes the snapshot's files into a temporary view and lets CodeRAG index all of them as text; every snapshot file must be stored with the same SHA-256 unless it has no indexable text (empty or binary).
- **Adopt** never writes the external store. It refuses a store whose recorded embedding model or dimension differs from the configured provider (CodeRAG would otherwise clear it), rejects any shared file whose content hash differs from the snapshot, records how many snapshot files the store covers and how many extra files it holds, and pins the store's absolute path in the manifest.
- **Queries** open a store only after checking its recorded provenance against the manifest, configure the embedding provider from the manifest rather than live configuration, discard hits outside the snapshot, and over-fetch in proportion to an adopted store's extra files.

## Structural schema

- `files`: path, language, SHA-256, byte size, line count, module.
- `symbols`: ID derived from path, qualified name, kind, signature, and start line, so it is unique within a generation but changes when a symbol moves; qualified/name/kind/signature, range, parent, metadata.
- `references`: source/target IDs when resolvable, raw target, resolution state, evidence line.
- `module_dependencies`: adapter-derived module edges: Python imports, C# `using` namespaces, and typed `.Build.cs` dependencies. C++ `#include` directives are not module edges because their first path segment names a header directory, not a module.

Resolution is deliberately conservative. A reference is `resolved` only when its qualified target matches exactly one symbol; a target whose last name segment matches exactly one symbol anywhere in the corpus is `name_match`, because receiver types and imports are not analyzed. Multiple candidates remain `ambiguous`; missing candidates remain `unresolved`. Graph responses carry that state unchanged.

## Language adapters

Adapters implement `supports(path)` and `extract(path, text)` without filesystem or database access. `codalith.languages.catalog` is the dependency-free source of adapter IDs, versions, retrieval profiles, and the extension-to-language map; adapters load their parsers only when created.

- Python uses the standard-library AST.
- C# uses public tree-sitter APIs for types, callables, members, calls, namespaces, and using edges.
- C++/UE uses public tree-sitter APIs, byte-preserving masking for reflection/export macros, UE metadata extraction, and typed `.Build.cs` dependencies; embedded C# files use the C# adapter.
- Generic indexes file provenance only.

Changing extraction semantics requires a version increment in the catalog.

## Retrieval and context

Each request resolves every target corpus once and keeps that generation's resources leased until it finishes, so results never mix generations and a concurrent publish never closes resources in use; superseded resources close when their last request ends.

`auto` search combines exact symbol lookup for identifier candidates, file lookup by name and path terms, and semantic hits from the generation's store, ranked with weighted reciprocal-rank fusion. Engine-specific naming and layout conventions (for example Unreal type prefixes, `*.Build.cs` rules files, and header or `Public/` precedence) reach the query layer only through the adapter's `RetrievalProfile`; retrieval code never encodes evaluation questions or answers. `text` search matches the whole query as a case-insensitive literal across the generation's indexed files and does not need the semantic plane.

Every output passes through the source policy: denied paths are withheld from search, context, symbol, and graph results and reported in warnings.

`codalith_context` removes overlapping ranges, enforces span and character budgets, reads current source through the policy layer, marks spans shortened by the line limit or the budget as `truncated`, and reports stale hashes or decoding replacements. Confidence is high only for corroborated structure plus CodeRAG evidence; degraded or stale results are low confidence.

## Local security boundary

Codalith assumes a trusted local operator, not hostile tenants. Security therefore focuses on source confinement and browser/network exposure:

- canonical relative source paths and generation membership;
- deny globs and hard byte/line limits on reads and on every query output;
- loopback defaults, and DNS-rebinding Host plus Origin allow-lists applied to every HTTP route, including the dashboard and health endpoints;
- complete streamed-request buffering up to a fixed maximum;
- no request/response body logging; dashboard telemetry keeps bounded, process-local query previews only;
- no secret-bearing auth/database surface.
