<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/brand/codalith-logo-dark.png">
    <img src="docs/assets/brand/codalith-logo.png" alt="Codalith" width="420">
  </picture>
</p>

# Codalith

**English** · [简体中文](README.zh-CN.md)

**Versioned source evidence for AI coding agents.**

Codalith is a local-first MCP service that helps agents find implementations, resolve symbols, follow references, and compare source revisions. It combines an immutable SQLite structural index with hybrid semantic retrieval, then returns bounded source text with line citations, revision and generation metadata, and current/indexed SHA-256 hashes.

[Quick start](#-quick-start) · [MCP tools](#mcp-tools) · [Documentation](#documentation) · [Website source](website/README.md)

## ✨ What you can do

- **Find the implementation.** Combine symbol, file, semantic, and literal-text discovery; retrieve a bounded context pack for a question.
- **Check the evidence.** Read canonical `codalith://` source URIs and detect source changes since indexing through hash and stale-state reporting.
- **Explore relationships.** Resolve definitions, traverse bounded reference graphs, and compare symbol structure between registered corpora.
- **Pin the source version.** Each call uses one immutable generation per corpus. A new generation is published atomically after validation.
- **Use your existing agent.** The same eight read-only tools work over stdio and Streamable HTTP. The bundled `codalith-source` skill guides retrieval and citation checks.
- **Inspect service operation.** The optional HTTP dashboard shows readiness, generation sizes, query latency, tool usage, and process telemetry.

Structural adapters are available for **Python**, **C#**, and **C++ / Unreal Engine**. The **generic** adapter indexes other text without claiming structural symbol extraction.

## From a question to source evidence

For the bundled sample corpus, call `codalith_context` with:

```json
{
  "query": "How does EventBus dispatch an event?",
  "target": "sample",
  "max_spans": 2,
  "max_chars": 4000
}
```

The returned context includes `EventBus` at `codalith://sample/source/src/core/events.py#L15-L24`. Its dispatch implementation is:

```python
def dispatch(self, event: Event) -> None:
    for handler in self._handlers.get(event.name, []):
        handler(event)
```

The example is backed by the checked-in [sample source](fixtures/sample_corpus/src/core/events.py). A local sample run returned revision `sample-v1`, matching current/indexed hashes, `stale: false`, `truncated: false`, and `degraded: false`. Generation IDs change on each build. Inspect these fields and `warnings` before using the evidence; cite the exact returned source URI.

## 🚀 Quick start

### 1. Start the local sample service

Install **Python 3.11+**, [uv](https://docs.astral.sh/uv/), and Git. Run these commands from a terminal:

```bash
git clone https://github.com/cyber-tao/Codalith.git
cd Codalith
uv sync --frozen
uv run --frozen codalith index build --corpus sample --semantic build
uv run --frozen codalith doctor --target sample --deep
uv run --frozen codalith serve --transport http
```

The sample uses deterministic **fake embeddings**: no API key or embedding-model download is needed. It demonstrates the indexing and agent-integration flow; it is not a semantic-quality benchmark.

The build reports **2 files and 8 symbols**. Doctor should report `"ready": true` and `"failures": []`. Keep the server terminal open. The default MCP endpoint is `http://127.0.0.1:8765/mcp`; [readiness](http://127.0.0.1:8765/readyz) returns HTTP 200 when the default target is ready.

### 2. Connect your agent

Open another terminal in the checkout and run the installer for your platform:

```powershell
# Windows
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

```bash
# macOS / Linux
sh ./install.sh
```

The installer detects supported agents, verifies MCP connectivity and readiness, installs `codalith-source`, and merges the `codalith` MCP entry. Changed configuration files are backed up. Restart the selected agent or reload its MCP configuration, then ask:

> Use Codalith's sample corpus to explain how EventBus dispatches an event. Cite the source.

Supported integrations: **Codex, Claude Code, Cursor, Windsurf, VS Code Copilot, Gemini CLI, and OpenCode v1**. Use `--agent codex --agent claude` to select clients or `--dry-run` to preview changes. Native agent trust and MCP approval prompts still apply. See [installation](docs/installation.md) for project scope, remote environments, backups, and `--check`.

To print configuration without writing client files:

```bash
uv run --frozen codalith client-config --client codex --transport http
uv run --frozen codalith client-config --client claude --transport stdio
```

### Optional: use Docker and the dashboard

With Docker Compose installed, this command builds the image, indexes the sample, and starts the HTTP service:

```bash
docker compose up --build mcp-http
```

Open the [operations dashboard](http://127.0.0.1:8765/dashboard/). To start the sample service and install agent integration together, add `--start-service sample` to the platform installer instead. Use one service on port 8765 at a time.

For a source-run service, build the dashboard separately before starting the HTTP server:

```bash
bun install --cwd dashboard --frozen-lockfile
bun run --cwd dashboard build
```

See [operations](docs/operations.md) for health checks, dashboard behavior, and UE setup.

## MCP tools

| Tool | Use it to |
| --- | --- |
| `codalith_search` | Discover source with `auto`, `semantic`, `text`, or `symbol` retrieval |
| `codalith_context` | Get a bounded, non-overlapping source context pack |
| `codalith_read` | Read an indexed canonical source URI, with hash and stale-state reporting |
| `codalith_symbol` | Resolve exact or fuzzy symbol definitions |
| `codalith_graph` | Traverse bounded incoming or outgoing reference evidence |
| `codalith_compare` | Compare structural symbols between two corpora |
| `codalith_catalog` | Discover registered source versions, exact revisions, readiness, and retrieval capabilities |
| `codalith_status` | Check readiness and provenance without loading embedding models or scanning source |

Tool inputs reject unknown fields. Both transports use the official MCP Python SDK. Source-policy deny rules filter tool outputs; source reads use configured line and byte budgets. Full inputs, response fields, limits, and errors are in the [MCP API reference](docs/mcp-api.md).

## Bring your own source

Configure corpora in [configs/registry.toml](configs/registry.toml) and read budgets/deny patterns in [configs/source-policy.toml](configs/source-policy.toml). Paths resolve relative to the TOML file that declares them. Each corpus records a source root, revision, index root, adapter, file-selection rules, and embedding provider.

| Setting | Choices and behavior |
| --- | --- |
| Structural adapter | `python`, `csharp`, `cpp-ue`, `generic` |
| Embeddings | `fake` for deterministic tests, `fastembed` for local models, `openai` for an OpenAI-compatible endpoint |
| `--semantic build` | Builds a fresh generation-local semantic store from that snapshot's indexed files |
| `--semantic adopt` | Reuses an existing store after model/dimension and source-content verification; records coverage and pins the store path |
| `--semantic none` | Builds a structural generation; semantic retrieval and full readiness are unavailable |

Semantic queries also need their configured embedding provider at query time. Literal `text` and `symbol` retrieval do not need a semantic plane. Local-first deployment can use local embedding models; selecting an external endpoint sends embedding inputs to that provider.

The default sample index is under `data/indexes/sample`. Compose persists it in the `codalith-data` volume. Source selection, adapter versions, semantic-engine versions, and configuration are part of generation validity; relevant changes require rebuilding or re-adopting. See [configuration](docs/configuration.md), [architecture](docs/architecture.md), and [UE operations](docs/operations.md#ue-574).

## Status and boundaries

- **Development status:** persisted formats and configuration may change; follow the [migration notes](docs/operations.md#migration-notes) when upgrading.
- **Deployment:** HTTP publishes to loopback by default, checks Host/Origin, and limits request bodies. Administrator bearer authentication protects management routes; MCP, catalog, and monitoring routes have no built-in network authentication or multi-tenant access control. Keep remote access behind a trusted, authenticated boundary.
- **Evidence:** reference graphs describe static evidence. They do not establish a running process's callback registrations or execution order.
- **Operations:** dashboard telemetry is bounded, process-local, and reset on restart. It is not a durable audit log.

## Documentation

| Guide | Contents |
| --- | --- |
| [Installation](docs/installation.md) | Agent setup, supported clients, scope, verification, and recovery |
| [Configuration](docs/configuration.md) | Corpus registry, source policy, paths, and environment |
| [MCP API](docs/mcp-api.md) | Tools, source citations, schemas, and error contracts |
| [Architecture](docs/architecture.md) | Module boundaries, immutable generations, and invariants |
| [Operations](docs/operations.md) | Local/Compose services, dashboard, UE, and migrations |
| [Evaluation](docs/evaluation.md) | Real MCP benchmarks, independent datasets, and acceptance gates |
| [Website source](website/README.md) | Build and preview the bilingual project website |

## Development and feedback

Install development dependencies, then run the Python checks:

```bash
uv sync --frozen --extra dev
uv run --frozen pytest -q
uv run --frozen ruff check src tests
uv run --frozen mypy src
uv run --frozen python scripts/check_distribution.py
```

For dashboard changes:

```bash
bun install --cwd dashboard --frozen-lockfile
bun run --cwd dashboard typecheck
bun run --cwd dashboard test
bun run --cwd dashboard build
```

For an HTTP integration check against a running sample service:

```bash
uv run --frozen codalith benchmark --dataset benchmarks/datasets/sample-smoke.jsonl --endpoint-url http://127.0.0.1:8765/mcp --label local-sample
```

Report reproducible problems through [GitHub Issues](https://github.com/cyber-tao/Codalith/issues). Include the command, corpus revision, generation ID, relevant warnings, and a minimal reproduction; omit source you cannot share and all credentials.

## Licensing and attribution

The repository currently has no root-level license declaring terms for the whole project. The semantic engine includes its own [Apache-2.0 license](src/codalith/semantic/LICENSE) and [CodeRAG attribution](src/codalith/semantic/NOTICE). Those component notices do not establish a repository-wide license. Indexed source remains subject to its original license and access terms.
