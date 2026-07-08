# Codalith

[中文文档](README.zh-CN.md)

Codalith is a Python MCP gateway for versioned source-code corpora. The core is domain-neutral: default code, configuration, tests, and Docker services use a small sample source corpus, and production corpora are declared through `configs/corpus_registry.json`.

UE 5.7 appears only in `eval/` because the current retrieval-quality baseline uses an existing CodeRAG embedding store built from UE 5.7 source. It is not part of the default MCP service path.

## Features

- MCP stdio and Streamable HTTP gateways for AI coding tools.
- Versioned corpus registry with configurable source, indexed, CodeRAG, card, source-prior, and seed-card paths.
- Unified source URI resolution for `codalith://<corpus_id>/...` resources.
- Source-root-first `SourceReader` with indexed-root fallback for evidence reads.
- Source-read policy, scopes, rate limits, and audit log support.
- Optional semantic graph store and knowledge-card verification.
- CodeRAG acceptance jobs and eval reports for default sample and explicit UE eval runs.

## Requirements

- Python 3.11 or newer.
- [uv](https://docs.astral.sh/uv/) for local Python workflows.
- Docker Compose for containerized validation.
- Git submodules for the pinned CodeRAG checkout when running native CodeRAG acceptance.

## Quick Start

```bash
cp .env.example .env
uv sync --extra dev
uv run pytest
uv run codalith-mcp
```

Run the HTTP MCP server locally:

```bash
uv run codalith-mcp-http --host 127.0.0.1 --port 8765 --endpoint /mcp
```

HTTP endpoint:

```text
http://127.0.0.1:8765/mcp
```

## Configuration

Default local development uses `fixtures/sample_corpus`:

- `configs/corpus_registry.json`
- `configs/source_policy.json`
- `configs/source_priors.json`
- `configs/seed_cards.json`

Host-specific values live in `.env`; do not edit `docker-compose.yml` per machine.

Common variables:

| Variable | Purpose |
| --- | --- |
| `CODALITH_SAMPLE_SOURCE_ROOT` | Source root for the default sample corpus. |
| `CODALITH_SAMPLE_INDEXED_ROOT` | Indexed root used for search/index operations. |
| `CODALITH_SAMPLE_CODERAG_STORE_DIR` | CodeRAG store path for the sample corpus. |
| `CODALITH_SAMPLE_SOURCE_PRIORS` | Optional source-prior config for deterministic source entry points. |
| `CODALITH_SAMPLE_SEED_CARDS` | Optional seed-card config. |
| `CODALITH_SCOPES` | Explicit scope override; empty grants base scopes plus registry access scopes. |
| `CODALITH_CODERAG_PROVIDER` | Default CodeRAG provider for local commands. |

## Docker Workflows

Run default checks:

```bash
docker compose run --rm test
```

Run the HTTP MCP service:

```bash
docker compose up -d mcp-http
```

Run default sample corpus acceptance:

```bash
docker compose --profile acceptance run --rm corpus-acceptance
```

Run native CodeRAG acceptance against the sample dataset:

```bash
docker compose --profile coderag run --rm coderag-acceptance
```

Run the explicit UE eval profile:

```bash
docker compose --profile eval-ue run --rm ue-eval
```

The UE profile uses `eval/configs/ue_5_7_4_registry.json`, `eval/configs/ue_source_priors.json`, `eval/configs/ue_seed_cards.json`, and `eval/datasets/ue_eval_suite.jsonl`.

## CLI

| Command | Purpose |
| --- | --- |
| `codalith-mcp` | stdio MCP server. |
| `codalith-mcp-http` | Streamable HTTP MCP server. |
| `codalith-index-corpus --corpus <id>` | Index any configured corpus. |
| `codalith-extract-semantic --corpus <id>` | Run the configured semantic profile; no profile is a successful no-op. |
| `codalith-generate-cards --corpus <id>` | Generate and verify configured seed cards. |
| `codalith-verify-cards --corpus <id>` | Verify configured seed cards. |
| `codalith-eval --dataset <path>` | Run in-process eval. |
| `codalith-mcp-eval --endpoint <url> --dataset <path>` | Run eval through an MCP HTTP endpoint. |

## Validation

```bash
uv run pytest
uv run ruff check src tests jobs
uv run mypy src
docker compose config --quiet
docker compose --env-file .env.example config --quiet
```

UE eval, when the UE source/checkpoint store is available:

```bash
uv run python -m codalith.eval.runner --registry eval/configs/ue_5_7_4_registry.json --dataset eval/datasets/ue_eval_suite.jsonl --output-dir reports/eval/ue_eval_suite --version 5.7.4 --max-source-spans 20
```
