# Operations

## Local sample

```bash
git submodule update --init --recursive
uv sync --frozen --extra dev
uv run codalith index build --corpus sample --semantic build
uv run codalith doctor --target sample --deep
uv run codalith serve --transport http
```

`/healthz` reports process health. `/readyz` returns 200 only when the default target has a semantic-ready generation.

For stdio, use `codalith serve --transport stdio`. `codalith client-config` prints Codex or Claude configuration and includes an explicit uv project directory so clients can start it from any working directory.

## Compose sample

```bash
cp .env.example .env
docker compose up --build mcp-http
docker compose ps
```

`index-sample` completes before `mcp-http` starts. The index persists in `codalith-data`; each intentional rebuild publishes a new generation.

## UE 5.7.4

Set these local `.env` values:

- `CODALITH_UE_SOURCE_HOST_DIR`: UE checkout root containing `Engine/`.
- `CODALITH_UE_INDEX_HOST_DIR`: writable structural generation directory.
- `CODALITH_UE_CODERAG_HOST_DIR`: writable existing CodeRAG store.
- embedding provider/model/base URL/key matching that store.

Then adopt the existing semantic store and build the structural generation:

```bash
docker compose --profile ue run --rm index-ue
docker compose --profile ue up -d mcp-http-ue
uv run codalith client-config --client codex --transport http
```

`adopt` never re-embeds. To intentionally replace an external store, run the local/container index command with `--semantic build --allow-external-rebuild`; this is expensive and may call a paid embedding endpoint.

## Diagnostics

```bash
uv run codalith index status --target sample
uv run codalith doctor --target sample --deep
```

Deep doctor hashes local artifacts and every CodeRAG store byte, so it can be slow for a large UE store. A fingerprint mismatch, revision/config drift, adapter version change, or missing artifact makes the generation invalid instead of silently falling back.

Old generation directories are retained for diagnosis and rollback-by-pointer repair. Codalith intentionally has no automatic retention job during development; remove old generations only while the service is stopped and after preserving the active generation.

## HTTP exposure

Published Compose ports default to `127.0.0.1`. Add exact Host/Origin values only when a trusted local proxy requires them. Codalith has no network authentication layer; do not expose it directly to an untrusted network.
