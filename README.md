# UE Context Engine

[中文文档](README.zh-CN.md)

UE Context Engine is a Python MCP gateway for Unreal Engine source context. It wraps CodeRAG-style retrieval with UE-aware corpus resolution, source-read policy, audit logging, semantic extraction, knowledge-card verification, and evaluation tooling.

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
- Optional: a local or remote Unreal Engine source checkout.
- Optional: Claude Code CLI for MCP client setup.

## Quick Start

```bash
cp .env.example .env
docker compose run --rm test
```

Run the stdio MCP server locally:

```bash
uv sync
uv run ue-context-mcp
```

Run the HTTP MCP server locally:

```bash
uv sync
uv run ue-context-mcp-http --host 127.0.0.1 --port 8765 --endpoint /mcp
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
| `UE_CONTEXT_ENGINE_HOST_ROOT` | Host path to the UE checkout mounted as the engine source root. |
| `UE_CONTEXT_ENGINE_SOURCE_HOST_ROOT` | Host path to `Engine/Source` for CodeRAG indexed mounts. |
| `UE_CONTEXT_GAMEPLAY_ABILITIES_HOST_ROOT` | Host path to the GameplayAbilities plugin used by the acceptance profile. |
| `UE_CONTEXT_ENGINE_SOURCE_ROOT` | Container path for the UE source root. |
| `UE_CONTEXT_ENGINE_INDEXED_ROOT` | Container path for the indexed corpus root. |
| `UE_CONTEXT_CODERAG_STORE_DIR` | Container path for the default CodeRAG store. |
| `UE_CONTEXT_CODERAG_OLLAMA_STORE_DIR` | Container path for the Ollama/OpenAI-compatible CodeRAG store. |

Linux server example:

```dotenv
UE_CONTEXT_ENGINE_HOST_ROOT=/opt/unreal/UE_5.7
UE_CONTEXT_ENGINE_SOURCE_HOST_ROOT=/opt/unreal/UE_5.7/Engine/Source
UE_CONTEXT_GAMEPLAY_ABILITIES_HOST_ROOT=/opt/unreal/UE_5.7/Engine/Plugins/Runtime/GameplayAbilities
```

Windows workstation example:

```dotenv
UE_CONTEXT_ENGINE_HOST_ROOT=E:/UnrealEngine_5.7
UE_CONTEXT_ENGINE_SOURCE_HOST_ROOT=E:/UnrealEngine_5.7/Engine/Source
UE_CONTEXT_GAMEPLAY_ABILITIES_HOST_ROOT=E:/UnrealEngine_5.7/Engine/Plugins/Runtime/GameplayAbilities
```

`configs/corpus_registry.yaml` and `configs/mcp_server.yaml` also support `${VAR:-default}` placeholders, so the same repository can run on different machines without rewriting committed config files.

## Docker Workflows

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

Run the Ollama/OpenAI-compatible CodeRAG acceptance path:

```bash
docker compose --profile coderag run --rm coderag-ollama-acceptance
```

## Configure Claude Code MCP on Another Machine

Claude Code supports HTTP MCP servers with:

```bash
claude mcp add --scope user --transport http ue-context https://mcp.example.com/mcp
```

For a bearer token:

```bash
claude mcp add --scope user --transport http ue-context https://mcp.example.com/mcp \
  --header "Authorization: Bearer $UE_CONTEXT_MCP_TOKEN"
```

Check the connection:

```bash
claude mcp list
```

Claude Code scopes:

| Scope | Use when |
| --- | --- |
| `user` | You want the MCP server available in every project on this computer. |
| `local` | You want the server private to one local project checkout. |
| `project` | You want a `.mcp.json` entry that can be shared with the repository. |

Reference: [Claude Code MCP documentation](https://code.claude.com/docs/en/mcp).

## One-Line Client Install

Host the scripts in `scripts/` from your own domain or GitHub raw URL, then use one of these commands.

Linux/macOS:

```bash
curl -fsSL https://example.com/install-mcp-client.sh | bash -s -- https://mcp.example.com/mcp
```

Linux/macOS with token:

```bash
curl -fsSL https://example.com/install-mcp-client.sh | \
  UE_CONTEXT_MCP_TOKEN="$UE_CONTEXT_MCP_TOKEN" bash -s -- https://mcp.example.com/mcp
```

Windows PowerShell:

```powershell
$env:UE_CONTEXT_MCP_URL = "https://mcp.example.com/mcp"
irm https://example.com/install-mcp-client.ps1 | iex
```

Windows PowerShell with token:

```powershell
$env:UE_CONTEXT_MCP_URL = "https://mcp.example.com/mcp"
$env:UE_CONTEXT_MCP_TOKEN = "<token>"
irm https://example.com/install-mcp-client.ps1 | iex
```

Optional overrides:

```bash
UE_CONTEXT_MCP_NAME=ue-context
UE_CONTEXT_MCP_SCOPE=user
```

## Server Deployment Notes

The built-in HTTP server is intended to be placed behind your deployment boundary. For shared or remote use, terminate TLS and authentication at a reverse proxy, VPN, or trusted gateway, then expose only the MCP endpoint you intend clients to use.

Example server command:

```bash
UE_CONTEXT_HTTP_HOST=0.0.0.0 \
UE_CONTEXT_HTTP_PORT=8765 \
UE_CONTEXT_HTTP_ENDPOINT=/mcp \
uv run ue-context-mcp-http
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
