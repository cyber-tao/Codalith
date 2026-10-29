# Operations

## Local sample

```bash
uv sync --frozen --extra dev
uv run codalith index build --corpus sample --semantic build
uv run codalith doctor --target sample --deep
uv run codalith serve --transport http
```

`/healthz` reports process health. `/readyz` returns 200 only when the default target has a semantic-ready generation. Both routes enforce the same Host/Origin allow-lists as the MCP endpoint.

For stdio, use `codalith serve --transport stdio`. `codalith client-config` prints Codex or Claude configuration and includes an explicit uv project directory so clients can start it from any working directory.

## Compose sample

```bash
cp .env.example .env
docker compose up --build mcp-http
docker compose ps
```

`index-sample` completes before `mcp-http` starts. The index persists in `codalith-data`; each intentional rebuild publishes a new generation. The image includes the built operations dashboard.

<a id="ue-574"></a>

## Directory-based UE 5.7.4

Set these local `.env` values:

- `CODALITH_UE_SOURCE_HOST_DIR`: UE checkout root containing `Engine/`.
- `CODALITH_UE_INDEX_HOST_DIR`: writable structural generation directory.
- `CODALITH_UE_SEMANTIC_HOST_DIR`: existing semantic store built over the same UE source.
- embedding provider/model/base URL/key matching that store.

Then adopt the existing semantic store and build the structural generation:

```bash
docker compose --profile ue run --rm index-ue
docker compose --profile ue up -d mcp-http-ue
uv run codalith client-config --client codex --transport http
```

`adopt` never re-embeds and never writes the external store. It refuses a store whose recorded embedding model or dimension differs from the configured provider, rejects files whose content hash differs from the structural snapshot, records coverage, and pins the store's absolute path in the generation manifest. A generation adopted inside a container therefore points at the container path; re-adopt to serve the same store from the host. `--semantic build` always embeds into the new generation's own `semantic/` directory; it is expensive and may call a paid embedding endpoint.

Only one build per index root runs at a time; a second build fails immediately while `.build.lock` is held.

For a fresh local Ollama build, use an installed `qwen3-embedding` model through its OpenAI-compatible `/v1` endpoint. The 4B model produces 2560-dimensional vectors. Create a local model alias with `num_ctx=32768` before using that endpoint, because its OpenAI interface cannot set the context size per request. Set the Compose base URL to `http://host.docker.internal:11434/v1`; host-side builds use `http://127.0.0.1:11434/v1`. Leave the API key empty for the local service. Pin the UE Git tag's resolved commit in `CODALITH_UE_SOURCE_REVISION` and export its selected source to a separate local directory instead of changing an unrelated checkout.

Override the indexing service's adoption command when rebuilding vectors:

```bash
docker compose --profile ue run --rm index-ue index build --corpus ue-5.7.4 --semantic build
docker compose --profile ue up -d mcp-http-ue
```

The indexer streams files into bounded batches shared across files. Semantic chunks also have an 8000-byte UTF-8 input limit, preserving source line references while splitting long lines when necessary. Qwen3 queries receive a general retrieval instruction; documents remain unprefixed. Semantic engine version 1.1.0 invalidates earlier generations for these input changes. Verify unusually dense numeric chunks with Ollama's native `truncate=false` preflight before a large build, then confirm the OpenAI endpoint reports the same token count.

## Diagnostics

```bash
uv run codalith index status --target sample
uv run codalith doctor --target sample --deep
```

Deep doctor hashes local artifacts and every semantic store byte, so it can be slow for a large UE store. A fingerprint mismatch, store provenance drift, revision/config/source-policy drift, adapter or semantic engine version change, or missing artifact makes the generation invalid instead of silently falling back. Status reports `missing` when no generation was published and `invalid` when the active one must be rebuilt.

Old generation directories are retained for diagnosis and rollback-by-pointer repair. Codalith intentionally has no automatic retention job during development; remove old generations only while the service is stopped and after preserving the active generation.

## Operations dashboard

The container image ships the built dashboard and sets `CODALITH_DASHBOARD_DIR`. For a local checkout, build it before starting the HTTP transport:

```bash
bun install --cwd dashboard --frozen-lockfile
bun run --cwd dashboard build
uv run codalith serve --transport http
```

Open `http://127.0.0.1:8765/dashboard/`. The dashboard visualizes MCP query volume and latency, tool usage, process CPU and peak memory, corpus generation size and readiness, recent queries, structured logs, and active incidents. Its corpus, time-range, chart, log-level, tool, search, pause, refresh, and export controls are all local to the browser.

The backing endpoint is `GET /api/dashboard/snapshot`. Telemetry is process-local and bounded (10,000 calls and 2,000 log entries): it keeps each call's tool, target, the first 180 characters of its query or URI, timings, result counts, and error codes and messages, and resets when the HTTP process restarts. It is served only through the Host/Origin-checked HTTP application. `CODALITH_DASHBOARD_DIR` selects the asset directory explicitly; without it the server looks for `dashboard/dist` in the checkout, and `/dashboard` returns a build instruction when no assets exist.

## HTTP exposure

Published Compose ports default to `127.0.0.1`. Add exact Host/Origin values only when a trusted local proxy requires them. Administrator bearer authentication protects management endpoints; it is not an authentication layer for the read-only MCP, catalog or monitoring endpoints. Keep those behind an appropriate trusted boundary rather than exposing the complete service directly to an untrusted network.

## Shared database workflow

The current source checkout supports managed Git versions alongside the existing directory-based index workflow. Configure `[management]`, source roots, profiles and the administrator token as described in [Configuration](configuration.md#shared-git-databases-and-management), build the dashboard, then start the HTTP service. A management-enabled empty registry can serve the administration pages before any target is ready; `/readyz` may return 503 until a version is published.

1. Open `/dashboard/#databases` and connect with the administrator token. It remains in page memory, not browser storage.
2. Browse an allowed **server** source root and register an existing Git directory. Normal and bare repositories are supported. Existing checkout changes are not imported.
3. Create a named database and select its configured build profile. Select its repository, search the tag list, and select tags across pages.
4. Optionally select a legacy generation to reuse verified compatible vectors. Run **Preflight selected tags** and inspect the fixed commits and query targets.
5. Start the build. **Build jobs** shows the durable batch and per-tag state, real phase counts, published-version count, errors and build events. Successful tags become queryable independently; a later tag failure does not remove them.

Tags are resolved to commits in the saved plan. Moving a source tag afterward does not retarget an already submitted build or overwrite a published snapshot. Multiple tags with the same effective snapshot reuse stored content. Source object reads do not checkout branches, run Git filters or fetch missing objects. Selected files must exist locally; LFS pointers that would be indexed as source fail that version. Submodules and symlinks are not traversed. Source-policy exclusions and limits still apply.

The worker runs one build at a time and processes selected tags serially. Closing the page does not cancel a build. Use **Cancel remaining work** to request cancellation at a safe boundary; an in-flight embedding operation may finish first. A short publish commit is allowed to finish. Retrying or continuing processes unsuccessful items while retaining successful versions and reusing committed objects/vectors.

Task states include `queued`, `running`, `succeeded`, `partial`, `failed`, `cancel_requested`, `cancelled` and `interrupted`; the phase distinguishes file import, structure, embedding, index maintenance and publication. An interrupted worker is reconciled against published snapshots, including a publish that completed just before a crash. Unfinished work requires manual continuation after restart. Dashboard history polls every five seconds, active details/events every two seconds, without overlapping requests. Stage percentages use measured units; an index operation with no reliable total displays its stage instead of a fabricated percentage.

The storage panel reports shared physical usage separately from logical source size across versions. Source, catalog/structure, semantic data and historical index storage have different overheads; values describing the common pool must not be summed as independent per-database sizes. Measurements include their timestamp and are cached until a version is published or **Refresh storage measurement** is requested. Ordinary polling does not rescan the pool. Event details initially show the latest 500 events, then follow new events by sequence. No fixed UE 5.0–5.8 space-saving ratio or full-corpus throughput is claimed.

### Local management commands

The CLI uses the same catalog, saved plans and worker locks. All commands below use the configured registry and source policy; global `--registry` and `--policy` options precede `management` when selecting different files.

```bash
uv run codalith management config
uv run codalith management repositories --root-id 0 --path UnrealEngine
uv run codalith management tags REPO_ID --query 5.7 --limit 25
uv run codalith management databases --name "Unreal Engine" --repo-id REPO_ID --profile-id ue-shared
uv run codalith management plan --database-id DATABASE_ID --repo-id REPO_ID --tag 5.7.4-release --tag 5.8.0-release
uv run codalith management build --plan-id PLAN_ID --idempotency-key MY_UNIQUE_REQUEST
uv run codalith management jobs --job-id JOB_ID
uv run codalith management cancel JOB_ID
uv run codalith management resume JOB_ID
uv run codalith management retry JOB_ID
uv run codalith management space --database-id DATABASE_ID
uv run codalith management space --database-id DATABASE_ID --refresh
uv run codalith management gc --dry-run
```

Replace uppercase IDs with values returned by the preceding command and choose tags actually present in the repository. Reuse an idempotency key only when repeating the same plan submission. `build --submit-only` queues work for the HTTP service's worker instead of attempting execution in the CLI. `resume` and `retry` requeue unsuccessful work; the running worker picks it up. Without an HTTP worker, run `management build` again with the same saved plan and idempotency key to execute the requeued job locally.

### Upgrade an existing shared catalog

New pools use catalog schema 2. Schema 1 pools remain queryable; starting a service does not migrate them. Before appending new versions to an existing pool, explicitly run:

```bash
uv run codalith management upgrade-storage
```

The command takes the shared build lock, saves a SQLite backup under `shared/backups/`, and adds the source-object mapping and exact-path indexes in one transaction. It reports `previous_version`, `schema_version` and `backup_path`; repeating it on schema 2 makes no changes. Failed upgrades roll back and retain the backup. Published snapshot records, source objects and retained Lance versions stay intact. New imports reuse verified source OIDs without reading their payload again; ordinary tag builds do not repack the whole source pool. Keep the reported backup until validation is complete. Restore it only with the service and worker stopped, and before any later successful imports, to avoid discarding their catalog records.

Administrator clients can request `GET /api/admin/jobs/JOB_ID/events?tail=500` for the initial event window and then use `after_seq`; the parameters are mutually exclusive. Storage responses include `measured_at`; `GET /api/admin/databases/DATABASE_ID/space?refresh=true` explicitly refreshes the cached measurement.

### Import a legacy generation

Keep the old directories and service intact while creating a new shared database in a separate management storage root. The dashboard offers current generations from configured directory corpora; the CLI accepts one of those generation IDs:

```bash
uv run codalith management plan --database-id DATABASE_ID --repo-id REPO_ID --tag 5.7.4-release --legacy-generation GENERATION_ID
uv run codalith management build --plan-id PLAN_ID --idempotency-key LEGACY_IMPORT_REQUEST
```

The importer verifies the old manifest/artifacts, file hashes, recorded provider/model/dimension and embedding input format. It copies only vectors whose source and complete input agree with the selected Git snapshot; missing or incompatible inputs require new embedding calls. Per-version statistics distinguish imported vectors, missing inputs and incompatible inputs, alongside source and parse reuse. Artifact corruption fails the build. Missing provenance cannot prove identical model weights. Old symbol IDs are rebound for the new snapshot, and the old generation, `current.json` and adopted external store remain unchanged. Compare MCP results and source citations before switching clients to the new target. No legacy data is deleted automatically.

`management gc --dry-run` reports unreachable object candidates and retained releases without deleting files. Destructive shared-pool GC is not enabled. Do not run Lance cleanup or remove internal retention tags manually; published snapshots depend on their retained table versions.

## Project version binding

Run detection on the client machine that contains the UE project. The server cannot read the client's `.uproject` or engine installation:

```bash
uv run codalith project detect --project "/work/MyGame" --server "http://127.0.0.1:8765/mcp"
uv run codalith project bind --project "/work/MyGame" --server "http://127.0.0.1:8765/mcp" --agent codex --dry-run
uv run codalith project bind --project "/work/MyGame" --server "http://127.0.0.1:8765/mcp" --agent codex
```

Use the existing binding when present; otherwise detection reads the selected `.uproject`'s `EngineAssociation`, locates its registered or explicitly supplied engine, and reads `Engine/Build/Build.version`. Supply `--project-file Game.uproject` when a directory contains multiple projects, `--engine-root /path/to/UnrealEngine` when automatic engine discovery is insufficient, or `--target TARGET_ID` to select an exact matching catalog entry. An unavailable, ambiguous or conflicting match returns a nonzero result instead of choosing the latest version. When local engine Git provenance is available, its commit must also match.

`bind` writes `.codalith/project.json` and updates only selected project-scoped agent configurations through the installer, with exact-byte backups and rollback on failure. It stores the server, selected target, source revision and local metadata references, never the administrator token. Repeat `--agent` to select multiple clients. `--dry-run` previews the changes without writing files. An engine upgrade requires detection and explicit rebinding; third-party HTTP clients do not automatically inspect local project changes.

The generated endpoint is `/mcp/targets/TARGET_ID`. It supplies the bound target when a tool omits it and rejects a conflicting target or another corpus's source/symbol URI. Cross-version `codalith_compare` is rejected there; use ordinary `/mcp` with explicit `from_corpus` and `to_corpus` for comparisons. `GET /api/catalog` and `codalith_catalog` expose the currently registered version directory.

## Migration notes

These changes altered persisted formats and public contracts. Rebuild or re-adopt every generation after upgrading; old generations report `invalid` until then. `adopt` never re-embeds.

- Semantic engine 2.0.0 introduces shared input-addressed vectors and version membership. The explicit legacy importer accepts verified 1.1.0 and 2.0.0 inputs. Keep the previous service binary serving its old storage during a side-by-side migration: upgrading that binary in place invalidates its 1.1.0 semantic generations until they are rebuilt or re-adopted. Import into a separate management root and switch clients only after comparison; neither the old storage nor service is switched automatically.
- Manifest schema 4 finishes absorbing CodeRAG into `codalith.semantic`: the provenance fields are now `semantic_engine_version`, `semantic_store`, `semantic_store_fingerprint`, `semantic_covered_files`, and `semantic_extra_files`, the artifact is `semantic-meta.json`, and a built store lives in the generation's `semantic/` directory. The registry key `coderag_store` is now `semantic_store`, and the Compose variables are `CODALITH_UE_SEMANTIC_STORE_DIR` and `CODALITH_UE_SEMANTIC_HOST_DIR`; rename `CODALITH_UE_CODERAG_HOST_DIR` in `.env`, or move the store, since the default host path changed from `.local/coderag/` to `.local/semantic/`. `codalith_search` reports the `semantic` backend instead of `coderag`, and fastembed models are cached under `~/.cache/codalith/fastembed`.
- Manifest schema 3: `source_selection_hash` (now covering the source policy's deny globs and byte limit) replaces `chunk_policy_hash`, and semantic generations record `coderag_store`, `coderag_covered_files`, and `coderag_extra_files`.
- Structure schema 4 adds the `name_match` reference resolution, the `symbol_search` trigram index, and per-symbol comparison digests; the `cpp-ue` adapter version 6 no longer stores `#include` directives as module dependencies. Building requires SQLite with FTS5 and the trigram tokenizer (3.34 or newer), which the container image and standard Python builds provide.
- `--allow-external-rebuild` is removed. `--semantic build` always builds a generation-local store; `--semantic adopt` is the only way to use an external store.
- MCP error codes are a fixed contract (see [MCP API](mcp-api.md)), `codalith_read` responses and graph nodes carry `generation_id`, graph edges may report `name_match`, `codalith_compare` responses carry `warnings` and stop at a 30-second budget, and source URIs require `#Lx-Ly` ranges.
- `text` search now matches the whole query literally and no longer requires a semantic store.
