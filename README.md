# Codalith

[中文说明](README.zh-CN.md)

Codalith is a local-first MCP source-intelligence service for AI coding agents. It combines an immutable SQLite structural index with hybrid semantic retrieval, then returns bounded, hash-verified source evidence through the official MCP protocol.

Use the bundled `codalith-source` skill together with MCP: the skill guides tool selection and evidence checking, while MCP supplies versioned source text.

The core is language-neutral. Python, C#, and C++/Unreal Engine have structural adapters today; other text corpora can use the generic adapter without pretending to provide symbols.

## What it guarantees

- Every source result, read, and graph node identifies a corpus revision and immutable index generation, and one call never mixes generations.
- `codalith://` citations can only read files present in that generation.
- The shared deny policy bounds reads and filters every tool output; reads report post-index changes.
- A generation is published with one atomic pointer update only after its artifacts validate; a failed build changes nothing.
- A semantic store is either built inside its generation from exactly the snapshot's files or adopted after content verification, and is never rewritten by Codalith.
- stdio and Streamable HTTP expose the same seven strict, read-only MCP tools with stable error codes.
- The HTTP service is loopback-first, checks Host and Origin on every route, and bounds streamed bodies.

Codalith is not a hosted multi-tenant gateway. Authentication, RBAC, audit databases, rate-limit shells, Knowledge Cards, source priors, and the old PostgreSQL semantic store are intentionally absent.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- [Bun](https://bun.sh/) (optional, to build the operations dashboard locally)
- Docker Compose (optional)

## Install the skill and connect your agents

From a source checkout, connect supported agents to an existing service with one command:

```powershell
# Windows
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

```bash
# macOS / Linux
sh ./install.sh
```

The installer needs only `uv` or Python 3.11+, without an embedding environment or `uv sync`. It detects supported agents, installs the skill, merges the `codalith` MCP configuration, and checks the service at `http://127.0.0.1:8765/mcp`. Existing configuration is preserved and changed files are backed up. Use `--agent codex --agent claude` to select clients explicitly, or `--dry-run` to preview offline.

For a new sample service, add `--start-service sample`; this starts Docker Compose, builds the demo index, and then installs the client integration:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 --start-service sample
```

```bash
sh ./install.sh --start-service sample
```

See [cross-platform installation](docs/installation.md) for project scope, supported agents, existing UE indexes, backups, and validation.

## Start a service manually

```bash
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

Docker performs the sample index step before starting the server, and its image includes the operations dashboard at `http://127.0.0.1:8765/dashboard/`:

```bash
docker compose up --build mcp-http
```

## MCP tools

| Tool | Purpose |
| --- | --- |
| `codalith_search` | Structural, semantic, or literal-text discovery |
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
dashboard/               React operations dashboard (built with Bun)
fixtures/sample_corpus/  deterministic local smoke corpus
src/codalith/corpus/     registry, policy, URIs, generations, source reads
src/codalith/languages/  adapter catalog, contracts, and implementations
src/codalith/semantic/   hybrid retrieval engine (chunking, embeddings, LanceDB)
src/codalith/indexing/   generation builder, SQLite structure writer/reader, semantic store binding, literal text search
src/codalith/query/      transport-independent query service
src/codalith/mcp/        official SDK bindings and transports
src/codalith/dashboard/  process-local telemetry and dashboard HTTP routes
src/codalith/benchmarks/ real MCP benchmark runner
src/codalith/cli/        single `codalith` command tree
tests/                   unit, integration, security, and protocol tests
```

## Development validation

```bash
uv run --frozen ruff check src tests
uv run --frozen mypy src
uv run --frozen pytest -q
bun install --cwd dashboard --frozen-lockfile
bun run --cwd dashboard typecheck
bun run --cwd dashboard test
bun run --cwd dashboard build
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

- [Cross-platform skill + MCP installation](docs/installation.md)
- [Architecture and invariants](docs/architecture.md)
- [Configuration](docs/configuration.md)
- [MCP API](docs/mcp-api.md)
- [Operations, UE setup, and migration notes](docs/operations.md)
- [Evaluation](docs/evaluation.md)

This repository is in development. Index and configuration schemas may change without compatibility shims; rebuild generations after such changes (see the migration notes in [Operations](docs/operations.md)).
