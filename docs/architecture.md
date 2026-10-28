# Architecture and invariants

Codalith has one transport-independent query core and two retrieval planes:

1. The structural plane is a logical snapshot of language-adapter facts, served from a directory generation's SQLite database or a version-scoped view of shared facts.
2. The semantic plane is a hybrid retrieval store bound to that snapshot. The engine lives in `codalith.semantic`. Directory builds/adoptions retain their generation-bound stores; managed Git snapshots share unique chunks and vectors while pinning the permitted members and a Lance table version.

The query service fuses both planes and is the only API used by MCP transports. The HTTP application additionally hosts the operations dashboard and separately authenticated management routes. MCP tools remain read-only; indexing, task control and Git access are not MCP tools.

## Corpus and workspace model

A corpus identifies one source root, revision, index root, adapter, embedding configuration, extension allow-list, and excluded paths. A workspace is an ordered list of corpus IDs. IDs are lowercase URI-safe values and are globally unique across corpora and workspaces.

Version comparison is expressed as two corpora. There is no implicit overlay or mutable project corpus.

Static TOML declares directory corpora, workspaces, source policy and optional management roots/profiles. The management SQLite catalog owns dynamic repository/database/plan/job records. Published managed targets come only from committed shared-pool snapshot records; creating a database or queuing a task does not make an unbuilt version queryable. Static and managed target IDs cannot collide.

<a id="generation-layout"></a>

## Directory generation layout

```text
<index_root>/
  current.json
  .build.lock                 # held while a build runs
  generations/
    <timestamp>-<digest>/
      manifest.json
      structure.sqlite
      semantic-meta.json      # when semantic retrieval is available
      semantic/               # semantic build only; adopted stores stay external
```

A build holds an OS-level lock on the index root, writes a hidden staging directory, validates SQLite integrity, prepares the semantic plane, re-hashes every indexed source file, fsyncs the generation, renames staging to its final generation ID, and atomically replaces `current.json` through an fsynced temporary file. A failed build leaves the active generation and its store untouched. Old generations are never mutated or silently deleted.

The manifest (schema 4) binds source revision and fingerprint, adapter ID and version, a source-selection hash covering the corpus extensions and excludes plus the source policy's deny globs and byte limit, structure schema, semantic engine version, embedding provider, model and dimension, the semantic store location, fingerprint and coverage, counts, and local artifact hashes. Configuration, policy, adapter, or semantic engine version drift invalidates the active generation, which must then be rebuilt.

## Shared Git snapshot layout

```text
<management.storage_root>/
  management.sqlite           # durable control records and job events
  worker/                     # single worker lock
  shared/
    catalog.sqlite            # shared facts, members, occurrences and published snapshots
    sources.git/              # application-owned bare Git source objects
    semantic/<namespace>/
      chunks.lance/           # unique text/vector rows and retained releases
```

The source repository is read-only input. A saved build plan pins tag object IDs and resolved commits; reads use local tree/blob objects without checkout, network fetch or working-tree filters. Source policy and profile selection operate on virtual repository-relative paths. Selected objects are copied into the application-owned source pool, so later source repository changes or removal do not alter published source reads or literal text search.

Identical file bytes share source objects; Git packing also compresses similar objects. Adapter extraction and chunking results have content/configuration cache keys. Extraction keys include path context because moving a UE file can change its module. Structural facts are shared separately from snapshot membership and bindings; cross-file reference resolution remains snapshot-specific. Unchanged bytes do not imply unchanged resolved graph edges.

Semantic namespaces include provider/model/dimension and input configuration. Exact embedding inputs share one vector regardless of version, path or line position; occurrences restore the requested snapshot's locations. A published semantic snapshot has an immutable membership boolean column with a BITMAP index. Dense and FTS searches prefilter that column before top-K selection, then hydrate occurrences belonging to the same snapshot.

Each tag publishes separately after objects, facts, members and required indexes are durable. The snapshot record fixes its content/configuration fingerprints, semantic namespace, membership column, Lance table version and internal retention tag. Pool appends do not invalidate prior snapshot fingerprints, and queries open the pinned version rather than the latest pool state. This also keeps an older snapshot's FTS statistics stable while new versions are imported.

One writer holds the shared-pool build lock; readers continue to query published snapshots. Search index maintenance uses incremental delta segments when supported. An explicit unsupported-operation error may rebuild that index only; resource, permission, cancellation or corruption failures do not trigger a heavier rebuild. No automatic whole-table compact, Lance cleanup or deletion of retention tags runs. Historical search indexes and membership metadata consume additional space even when text/vector fragments are shared; storage accounting reports that cost separately.

The control catalog persists task attempts, per-tag states and ordered events. The worker reconciles unfinished tasks with committed snapshot records after restart, then leaves incomplete work for manual continuation. A crash after publication cannot cause a completed version to be treated as missing. Repeated requests use a saved plan and idempotency key; retries reuse committed shared objects. GC currently provides a read-only candidate report and deletes nothing.

## Semantic plane

The following build/adopt modes describe the directory-generation path. Managed Git builds use the shared snapshot model above; the explicit legacy importer verifies a directory generation and copies compatible vectors into a new shared pool without changing the old artifacts or pointers.

- **Build** materializes the snapshot's files into a temporary view and indexes all of them as text into a store that must not exist yet; every snapshot file must be stored with the same SHA-256 unless it has no indexable text (empty or binary).
- **Adopt** never writes the external store. The engine opens existing stores read-only and refuses one whose recorded embedding model or dimension differs from the configured provider; adopt also rejects any shared file whose content hash differs from the snapshot, records how many snapshot files the store covers and how many extra files it holds, and pins the store's absolute path in the manifest.
- **Queries** open a store only after checking its recorded provenance against the manifest, configure the embedding provider from the manifest rather than live configuration, discard hits outside the snapshot, and over-fetch in proportion to an adopted store's extra files.

`codalith.semantic.ENGINE_VERSION` is recorded in every semantic generation; raising it, which chunking, embedding-input, or store-layout changes require, invalidates them.

## Structural schema

- `files`: path, language, SHA-256, byte size, line count, module.
- `symbols`: ID derived from path, qualified name, kind, signature, and start line, so it is unique within a generation but changes when a symbol moves; qualified/name/kind/signature, range, parent, metadata, and a digest of the fields `codalith_compare` compares. The digest is indexed with the comparison key, so comparisons stream that index and load full records only for groups whose digests differ.
- `symbol_search`: an FTS5 trigram index over symbol names and qualified names. Its rowids are each symbol's rank (definitions, then the profile's declaration paths, then shorter qualified names), so fuzzy lookups read exact-name, prefix, and substring matches in rank order and stop at the limit instead of sorting every match. Queries shorter than three characters scan it in the same order.
- `references`: source/target IDs when resolvable, raw target, resolution state, evidence line.
- `module_dependencies`: adapter-derived module edges: Python imports, C# `using` namespaces, and typed `.Build.cs` dependencies. C++ `#include` directives are not module edges because their first path segment names a header directory, not a module.

Resolution is deliberately conservative. A reference is `resolved` only when its qualified target matches exactly one symbol; a target whose last name segment matches exactly one symbol anywhere in the corpus is `name_match`, because receiver types and imports are not analyzed. Multiple candidates remain `ambiguous`; missing candidates remain `unresolved`. Graph responses carry that state unchanged.

## Language adapters

Adapters implement `supports(path)` and `extract(path, text)` without filesystem or database access. `codalith.languages.catalog` is the dependency-free source of adapter IDs, versions, retrieval profiles, and the extension-to-language map; adapters load their parsers only when created.

- Python uses the standard-library AST.
- C# uses public tree-sitter APIs for types, callables, members, calls, namespaces, and using edges.
- C++/UE uses public tree-sitter APIs, byte-preserving masking for reflection/export macros, UE metadata extraction, and typed `.Build.cs` dependencies; embedded C# files use the C# adapter.
- Generic indexes file provenance only.

Changing extraction semantics or a retrieval profile requires a version increment in the catalog; profiles also fix the symbol ranking stored in `symbol_search`.

## Retrieval and context

Each request resolves every target corpus once and keeps that generation's resources leased until it finishes, so results never mix generations and a concurrent publish never closes resources in use; superseded resources close when their last request ends.

`auto` search combines exact symbol lookup for identifier candidates, file lookup by name and path terms, and semantic hits from the generation's store, ranked with weighted reciprocal-rank fusion. Engine-specific naming and layout conventions (for example Unreal type prefixes, `*.Build.cs` rules files, and header or `Public/` precedence) reach the query layer only through the adapter's `RetrievalProfile`; retrieval code never encodes evaluation questions or answers. `text` search matches the whole query as a case-insensitive literal across the generation's indexed files and does not need the semantic plane.

Every output passes through the source policy: denied paths are withheld from search, context, symbol, and graph results and reported in warnings.

`codalith_context` removes overlapping ranges, enforces span and character budgets, reads source through the policy layer, marks spans shortened by the line limit or the budget as `truncated`, and reports stale hashes or decoding replacements. Directory generations compare the working source against indexed hashes; managed snapshots read the retained Git source objects. Confidence is high only for corroborated structure plus semantic evidence; degraded or stale results are low confidence.

`codalith_catalog` lists registered versions without loading an embedding model. The project detector runs locally, matches `.uproject`/engine metadata against this directory, and the explicit binder saves an exact target/revision and installs a target-scoped HTTP endpoint. A bound endpoint supplies a missing target and rejects other target/URI scopes; deliberate cross-version comparison uses ordinary `/mcp`. Project paths and administrator credentials are not sent to the version catalog.

## Local security boundary

Codalith assumes a trusted local operator, not hostile tenants. Security therefore focuses on source confinement and browser/network exposure:

- canonical relative source paths and generation membership;
- deny globs and hard byte/line limits on reads and on every query output;
- loopback defaults, and DNS-rebinding Host plus Origin allow-lists applied to every HTTP route, including the dashboard and health endpoints;
- complete streamed-request buffering up to a fixed maximum;
- no request/response body logging; dashboard telemetry keeps bounded, process-local query previews only;
- administrator bearer authentication on management routes, with credentials supplied only through the configured process environment and request headers; no administrator token in query tools, project bindings or telemetry;
- allowed server source roots and fixed Git commands for management operations; no remote Git transport or arbitrary shell execution;
- read-only MCP/catalog/monitoring routes remain distinct from administrator access and still require an appropriate deployment boundary.
