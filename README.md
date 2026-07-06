# Codalith

[中文文档](README.zh-CN.md)

Codalith is a Python MCP gateway for Unreal Engine source context. It wraps CodeRAG-style retrieval with UE-aware corpus resolution, source-read policy, audit logging, semantic extraction, knowledge-card verification, and evaluation tooling.

## Features

- MCP stdio and Streamable HTTP gateways for AI coding tools.
- Versioned UE corpus registry with configurable source, indexed, CodeRAG, and card paths.
- Source URI resolution for `ue://...` and `ue-project://...` resources.
- Source-read policy, scopes, and audit log support.
- Semantic extractors for Build.cs dependencies, UHT reflection, C++ symbols, and compile guards.
- CodeRAG acceptance jobs and evaluation reports.

## Requirements

- Python 3.11 or newer.
- [uv](https://docs.astral.sh/uv/) for local Python workflows.
- Docker Compose for containerized validation.
- Git submodules for the pinned CodeRAG checkout.
- Optional: a local or remote Unreal Engine source checkout.
- Optional: Claude Code CLI, Codex, VS Code, or Cursor for MCP client setup.

## Clone With Submodules

CodeRAG is pinned as a Git submodule at `external/CodeRAG`.

Fresh clone:

```bash
git clone --recurse-submodules <repo-url>
```

Existing checkout:

```bash
git submodule update --init --recursive external/CodeRAG
```

## Quick Start

```bash
cp .env.example .env
docker compose run --rm test
```

Run the stdio MCP server locally:

```bash
uv sync
uv run codalith-mcp
```

Run the HTTP MCP server locally:

```bash
uv sync
uv run codalith-mcp-http --host 127.0.0.1 --port 8765 --endpoint /mcp
```

The HTTP endpoint is then available at:

```text
http://127.0.0.1:8765/mcp
```

## Configurable Docker Paths

All host-specific paths are configured through `.env`; do not edit `docker-compose.yml` for each server.

Start from:

```bash
cp .env.example .env
```

Set these variables for each machine:

| Variable | Purpose |
| --- | --- |
| `CODALITH_ENGINE_HOST_ROOT` | Host path to the UE checkout mounted as the engine source root. |
| `CODALITH_ENGINE_SOURCE_HOST_ROOT` | Host path to `Engine/Source` for CodeRAG indexed mounts. |
| `CODALITH_GAMEPLAY_ABILITIES_HOST_ROOT` | Host path to the GameplayAbilities plugin used by the acceptance profile. |
| `CODALITH_ENGINE_SOURCE_ROOT` | Container path for the UE source root. |
| `CODALITH_ENGINE_INDEXED_ROOT` | Container path for the indexed corpus root. |
| `CODALITH_CODERAG_STORE_DIR` | Container path for the default CodeRAG store. |
| `CODALITH_CODERAG_OPENAI_STORE_DIR` | Container path for the OpenAI-compatible CodeRAG store. |

Linux server example:

```dotenv
CODALITH_ENGINE_HOST_ROOT=/workdir/UnrealEngine_5.7
CODALITH_ENGINE_SOURCE_HOST_ROOT=/workdir/UnrealEngine_5.7/Engine/Source
CODALITH_GAMEPLAY_ABILITIES_HOST_ROOT=/workdir/UnrealEngine_5.7/Engine/Plugins/Runtime/GameplayAbilities
```

Windows workstation example:

```dotenv
CODALITH_ENGINE_HOST_ROOT=E:/UnrealEngine_5.7
CODALITH_ENGINE_SOURCE_HOST_ROOT=E:/UnrealEngine_5.7/Engine/Source
CODALITH_GAMEPLAY_ABILITIES_HOST_ROOT=E:/UnrealEngine_5.7/Engine/Plugins/Runtime/GameplayAbilities
```

`configs/corpus_registry.yaml` and `configs/mcp_server.yaml` also support `${VAR:-default}` placeholders, so the same repository can run on different machines without rewriting committed config files.

## Docker Workflows

Run the HTTP MCP server as a managed Compose service:

```bash
docker compose up -d mcp-http
```

The default endpoint is `http://127.0.0.1:8765/mcp` from the Docker host. `CODALITH_HTTP_HOST` controls the container listener, while `CODALITH_HTTP_BIND` controls the host port binding; keep `CODALITH_HTTP_BIND=127.0.0.1` unless the server should be reachable from other machines. For OpenAI-compatible CodeRAG acceptance, set `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `CODERAG_OPENAI_MODEL`, and `CODERAG_CHAT_MODEL` in `.env`; legacy aliases such as `BASE_URL` and `API_KEY` remain accepted as fallbacks.

Run default checks:

```bash
docker compose run --rm test
```

Run the optional UE source smoke path:

```bash
docker compose --profile ue run --rm ue-acceptance
```

Run the fake-provider CodeRAG acceptance path:

```bash
docker compose --profile coderag run --rm coderag-acceptance
```

This profile expects `external/CodeRAG` to be initialized. For temporary local experiments without the submodule, set `CODALITH_CODERAG_ALLOW_AUTO_CLONE=1` to allow a shallow clone into `/tmp/CodeRAG`.

Run the OpenAI-compatible CodeRAG acceptance path:

```bash
docker compose --profile coderag run --rm coderag-openai-acceptance
```

## MCP Client Configuration

Codalith exposes a Streamable HTTP MCP endpoint. Use the server name `codalith` and the primary tool `codalith_context`.

Supported client targets:

| Client | Script value | Configuration target |
| --- | --- | --- |
| Claude Code | `claude` | `claude mcp add --transport http ...` |
| Codex | `codex` | `~/.codex/config.toml` or `.codex/config.toml` |
| VS Code / GitHub Copilot | `vscode` or `copilot` | `code --add-mcp` or `.vscode/mcp.json` |
| Cursor | `cursor` | `~/.cursor/mcp.json` or `.cursor/mcp.json` |
| All supported clients | `all` | Best-effort setup for every client above |

Manual examples:

```bash
claude mcp add --scope user --transport http codalith https://mcp.example.com/mcp
```

```toml
# ~/.codex/config.toml
[mcp_servers.codalith]
url = "https://mcp.example.com/mcp"
```

```json
// .vscode/mcp.json
{
  "servers": {
    "codalith": {
      "type": "http",
      "url": "https://mcp.example.com/mcp"
    }
  }
}
```

```json
// ~/.cursor/mcp.json or .cursor/mcp.json
{
  "mcpServers": {
    "codalith": {
      "type": "http",
      "url": "https://mcp.example.com/mcp"
    }
  }
}
```

References: [Claude Code MCP](https://code.claude.com/docs/en/mcp), [Codex MCP](https://developers.openai.com/codex/mcp), [VS Code MCP servers](https://code.visualstudio.com/docs/copilot/customization/mcp-servers), and [Cursor MCP](https://cursor.com/docs/mcp).

## One-Line Client Install

Host the scripts in `scripts/` from your own domain or GitHub raw URL, then use one of these commands.

Install all supported clients that are available on the machine:

```bash
curl -fsSL https://example.com/install-mcp-client.sh | bash -s -- --url https://mcp.example.com/mcp
```

Install a specific client:

```bash
curl -fsSL https://example.com/install-mcp-client.sh | \
  bash -s -- --client codex --scope user --url https://mcp.example.com/mcp
```

Linux/macOS with token:

```bash
curl -fsSL https://example.com/install-mcp-client.sh | \
  CODALITH_MCP_TOKEN="$CODALITH_MCP_TOKEN" bash -s -- --client all --url https://mcp.example.com/mcp
```

Windows PowerShell all clients:

```powershell
$env:CODALITH_MCP_CLIENT = "all"
$env:CODALITH_MCP_URL = "https://mcp.example.com/mcp"
irm https://example.com/install-mcp-client.ps1 | iex
```

Windows PowerShell specific client with token:

```powershell
$env:CODALITH_MCP_CLIENT = "cursor"
$env:CODALITH_MCP_URL = "https://mcp.example.com/mcp"
$env:CODALITH_MCP_TOKEN = "<token>"
irm https://example.com/install-mcp-client.ps1 | iex
```

Optional overrides:

```bash
CODALITH_MCP_NAME=codalith
CODALITH_MCP_CLIENT=all
CODALITH_MCP_SCOPE=user
CODALITH_MCP_CONFIG_PATH=/custom/path/to/config
```

Scopes map to each client as closely as possible. `user` configures machine-wide settings. `project`, `workspace`, and `local` write project-local files for Codex, VS Code/Copilot, and Cursor; Claude Code maps `workspace` to its `local` scope.

## Server Deployment Notes

The built-in HTTP server is intended to be placed behind your deployment boundary. For shared or remote use, terminate TLS and authentication at a reverse proxy, VPN, or trusted gateway, then expose only the MCP endpoint you intend clients to use.

Example server command:

```bash
CODALITH_HTTP_HOST=0.0.0.0 \
CODALITH_HTTP_PORT=8765 \
CODALITH_HTTP_ENDPOINT=/mcp \
uv run codalith-mcp-http
```

Client URL:

```text
https://mcp.example.com/mcp
```

## Development

Run local tests:

```bash
uv sync --extra dev
uv run pytest
uv run ruff check src tests jobs
uv run mypy src
```

The repository never commits `.env`, generated data, reports, caches, or local virtual environments.
