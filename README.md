# Codalith

[中文说明](README.zh-CN.md)

Codalith is a local-first MCP source-intelligence service for AI coding agents. It combines an immutable SQLite structural index with CodeRAG retrieval, then returns bounded, hash-verified source evidence through the official MCP protocol.

The core is language-neutral. Python, C#, and C++/Unreal Engine have structural adapters today; other text corpora can use the generic adapter without pretending to provide symbols.

## What it guarantees

- Every source result identifies a corpus revision and immutable index generation.
- `codalith://` citations can only read files present in that generation.
- Source reads are bounded by a shared deny policy and report post-index changes.
- A generation is published with one atomic pointer update only after its artifacts validate.
- stdio and Streamable HTTP expose the same seven strict, read-only MCP tools.
- The HTTP service is loopback-first, checks Host and Origin, and bounds streamed bodies.

Codalith is not a hosted multi-tenant gateway. Authentication, RBAC, audit databases, rate-limit shells, Knowledge Cards, source priors, and the old PostgreSQL semantic store are intentionally absent.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Git submodules
- Docker Compose (optional)

## Quick start

```bash
git submodule update --init --recursive
uv sync --frozen --extra dev
uv run codalith index build --corpus sample --semantic build
uv run codalith doctor --target sample --deep
uv run codalith serve --transport http
```

The MCP endpoint is `http://127.0.0.1:8765/mcp`. Print a client configuration without modifying user files:

```bash
uv run codalith client-config --client codex --transport http
uv run codalith client-config --client claude --transport stdio
```

Docker performs the sample index step before starting the server:

```bash
docker compose up --build mcp-http
```

## MCP tools

| Tool | Purpose |
| --- | --- |
| `codalith_search` | Structural, semantic, or exact-text discovery |
| `codalith_context` | Bounded context pack with verified source text |
| `codalith_read` | Exact read of a canonical source URI |
| `codalith_symbol` | Exact or fuzzy symbol resolution |
| `codalith_graph` | Bounded incoming/outgoing reference traversal |
| `codalith_compare` | Structural comparison of two corpora |
| `codalith_status` | Side-effect-free readiness and provenance |

See [MCP API](docs/mcp-api.md) for schemas and error behavior.

## Repository layout

```text
configs/                 TOML corpus registry and source policy
benchmarks/datasets/     independent sample, UE regression, and holdout sets
external/CodeRAG/        pinned retrieval-engine submodule
fixtures/sample_corpus/  deterministic local smoke corpus
src/codalith/corpus/     registry, policy, URIs, generations, source reads
src/codalith/languages/  structural adapter contracts and implementations
src/codalith/indexing/   SQLite builder and CodeRAG integration
src/codalith/query/      transport-independent query service
src/codalith/mcp/        official SDK bindings and transports
src/codalith/benchmarks/ real MCP benchmark runner
src/codalith/cli/        single `codalith` command tree
tests/                   unit, integration, security, and protocol tests
```

## Development validation

```bash
uv run --frozen ruff check src tests
uv run --frozen mypy src
uv run --frozen pytest -q
docker compose config --quiet
docker compose --profile ue config --quiet
docker compose --profile test config --quiet
```

Run an independent dataset against a live endpoint:

```bash
uv run codalith benchmark \
  --dataset benchmarks/datasets/sample-smoke.jsonl \
  --endpoint-url http://127.0.0.1:8765/mcp \
  --label local-sample
```

## Documentation

- [Architecture and invariants](docs/architecture.md)
- [Configuration](docs/configuration.md)
- [MCP API](docs/mcp-api.md)
- [Operations and UE setup](docs/operations.md)
- [Evaluation](docs/evaluation.md)

This repository is in development. Index and configuration schemas may change without compatibility shims; rebuild generations after such changes.
