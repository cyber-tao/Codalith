# Operations

## Local sample

```bash
git submodule update --init --recursive
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

## UE 5.7.4

Set these local `.env` values:

- `CODALITH_UE_SOURCE_HOST_DIR`: UE checkout root containing `Engine/`.
- `CODALITH_UE_INDEX_HOST_DIR`: writable structural generation directory.
- `CODALITH_UE_CODERAG_HOST_DIR`: existing CodeRAG store built over the same UE source.
- embedding provider/model/base URL/key matching that store.

Then adopt the existing semantic store and build the structural generation:

```bash
docker compose --profile ue run --rm index-ue
docker compose --profile ue up -d mcp-http-ue
uv run codalith client-config --client codex --transport http
```

`adopt` never re-embeds and never writes the external store. It refuses a store whose recorded embedding model or dimension differs from the configured provider, rejects files whose content hash differs from the structural snapshot, records coverage, and pins the store's absolute path in the generation manifest. A generation adopted inside a container therefore points at the container path; re-adopt to serve the same store from the host. `--semantic build` always embeds into the new generation's own `coderag/` directory; it is expensive and may call a paid embedding endpoint.

Only one build per index root runs at a time; a second build fails immediately while `.build.lock` is held.

## Diagnostics

```bash
uv run codalith index status --target sample
uv run codalith doctor --target sample --deep
```

Deep doctor hashes local artifacts and every CodeRAG store byte, so it can be slow for a large UE store. A fingerprint mismatch, store provenance drift, revision/config/source-policy drift, adapter or CodeRAG version change, or missing artifact makes the generation invalid instead of silently falling back. Status reports `missing` when no generation was published and `invalid` when the active one must be rebuilt.

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

Published Compose ports default to `127.0.0.1`. Add exact Host/Origin values only when a trusted local proxy requires them. Codalith has no network authentication layer; do not expose it directly to an untrusted network.

## Migration notes

The audit remediation changed persisted formats and public contracts. Rebuild or re-adopt every generation after upgrading; old generations report `invalid` until then.

- Manifest schema 3: `source_selection_hash` (now covering the source policy's deny globs and byte limit) replaces `chunk_policy_hash`, and semantic generations record `coderag_store`, `coderag_covered_files`, and `coderag_extra_files`.
- Structure schema 3 adds the `name_match` reference resolution and the `symbol_search` trigram index; the `cpp-ue` adapter version 6 no longer stores `#include` directives as module dependencies. Building requires SQLite with FTS5 and the trigram tokenizer (3.34 or newer), which the container image and standard Python builds provide.
- `--allow-external-rebuild` is removed. `--semantic build` always builds a generation-local store; `--semantic adopt` is the only way to use an external store.
- MCP error codes are a fixed contract (see [MCP API](mcp-api.md)), `codalith_read` responses and graph nodes carry `generation_id`, graph edges may report `name_match`, and source URIs require `#Lx-Ly` ranges.
- `text` search now matches the whole query literally and no longer requires a semantic store.
