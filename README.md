# Codalith

[中文文档](README.zh-CN.md)

Codalith is a Python MCP gateway for versioned source-code corpora. It gives AI coding tools such as Claude Code and Codex source-backed Context Packs, bounded source reads, symbol/graph lookup, examples, and corpus comparison.

The core is domain-neutral. The default service uses the small sample corpus under `fixtures/sample_corpus`; UE 5.7.4 is an opt-in product corpus under `configs/corpora/ue-5.7.4/` with an explicit native CodeRAG store and acceptance suite.

## Architecture

- Official Python MCP SDK v1 for stdio and Streamable HTTP.
- Validated `source` / `project` / `generated` corpus registry and revision provenance.
- Native CodeRAG backend with strict manifest validation plus a bounded deterministic local fallback.
- Canonical source slices, policy, per-identity rate limiting, and audit records.
- Filesystem-backed Knowledge Cards with evidence and semantic verification states.
- Optional versioned SQLite/PostgreSQL semantic graph store.
- Shared in-process and MCP eval metrics/gates.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Docker Compose for container workflows
- `external/CodeRAG` submodule only for native retrieval/acceptance

## Quick start

```bash
cp .env.example .env
uv sync --extra dev
uv run pytest
uv run codalith-mcp
```

HTTP:

```bash
uv run codalith-mcp-http --host 127.0.0.1 --port 8765 --endpoint /mcp
```

Endpoint: `http://127.0.0.1:8765/mcp`

Client installation helpers:

```bash
sh scripts/install-mcp-client.sh
# or
powershell -File scripts/install-mcp-client.ps1
```

## Configuration

Default sample assets:

- `configs/sample/registry.json`
- `configs/sample/source_priors.json`
- `configs/sample/seed_cards.json`
- `configs/source_policy.json`

UE product assets:

- `configs/corpora/ue-5.7.4/registry.json`
- `configs/corpora/ue-5.7.4/source_priors.json`
- `configs/corpora/ue-5.7.4/seed_cards.json`
- `configs/corpora/ue-5.7.4/store_manifest.json`
- `eval/datasets/ue_eval_suite.jsonl`

Copy `.env.example` for neutral development. Append values from `.env.ue.example` only when using the UE corpus, and replace its relative host paths/credentials locally. Never commit `.env`, stores, reports, or source mounts.

Product corpora must provide a non-empty `source_revision`. Native stores with a configured manifest are rejected when model, dimension, schema, corpus, or revision metadata does not match.

## CLI

| Command | Purpose |
| --- | --- |
| `codalith-mcp` | MCP stdio server |
| `codalith-mcp-http` | MCP Streamable HTTP server |
| `codalith-index-corpus --corpus <id>` | Index or smoke-check a corpus |
| `codalith-semantic-status --corpus <id>` | Record/report semantic store state |
| `codalith-generate-cards --corpus <id>` | Generate evidence-verified cards |
| `codalith-verify-cards --corpus <id>` | Verify configured cards |
| `codalith-coderag-acceptance --corpus <id>` | Native CodeRAG acceptance |
| `codalith-backup-coderag-store` | Backup a CodeRAG store |
| `codalith-eval --corpus <id>` | In-process eval |
| `codalith-mcp-eval --corpus <id>` | Eval through MCP HTTP |
| `codalith-ue-eval` | Cross-platform real UE MCP acceptance |

## Docker

```bash
docker compose run --rm test
docker compose up -d mcp-http
docker compose --profile acceptance run --rm corpus-acceptance
docker compose --profile coderag run --rm coderag-acceptance
```

After configuring UE host paths and the query embedding provider in `.env`:

```bash
docker compose --profile ue up -d mcp-http-ue
docker compose --profile eval-ue run --rm ue-eval
```

Both UE services run native retrieval in strict mode and mount the selected store directory read-only.

## Eval

Default pytest validates the 80-row UE dataset contract but does not fake UE retrieval. Real acceptance is explicit:

```bash
uv run codalith-ue-eval \
  --source-root /path/to/UnrealEngine_5.7 \
  --indexed-root /path/to/UnrealEngine_5.7 \
  --store-dir .local/coderag-openai-store/ue-5.7.4-openai-qwen3-embedding-8b-3072c-3584b-full
```

The gate requires 80 rows, all applicable retrieval/module/symbol metrics, zero citation/version errors, native backend, zero fallback, and a validated store manifest. Dataset expectations are normalized with:

```bash
uv run python scripts/normalize_eval_dataset.py --check
```

## Validation

```bash
uv run pytest
uv run ruff check src tests scripts/normalize_eval_dataset.py
uv run mypy src
docker compose config --quiet
docker compose --env-file .env.example config --quiet
```

Semantic stores are schema-versioned. This development build deliberately rejects unversioned legacy stores; recreate them instead of attempting an implicit migration.
