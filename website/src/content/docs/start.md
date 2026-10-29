---
title: Get source evidence in your agent
description: Build the bundled sample corpus, start a local MCP service, and verify a source citation through the official SDK.
---

This source-checkout tutorial takes you from an empty index to a verified `codalith://` source citation. The bundled sample contains small Python cache and event primitives. It requires no model key or external source corpus.

The sample uses the deterministic `fake` embedding provider. It exercises generation publication and the retrieval protocol; it does **not** demonstrate learned semantic quality. For a real corpus, select a suitable embedding provider and model in the [configuration guide](../guides/configuration/).

## 1. Prepare a source checkout

Install Git, Python 3.11 or newer, and [uv](https://docs.astral.sh/uv/getting-started/installation/). These instructions use the commands and configuration in the current source checkout, rather than an independently published package.

```bash
git clone https://github.com/cyber-tao/Codalith.git
cd Codalith
uv sync --frozen --extra dev
```

Run all commands below from this checkout. The first dependency synchronization may download Python packages. The default sample configuration is already in `configs/registry.toml`; no `.env` file or UE source is needed for this tutorial.

## 2. Build and check the sample

```bash
uv run codalith index build --corpus sample --semantic build
uv run codalith doctor --target sample --deep
```

The build publishes a generation below `data/indexes/sample/` and updates its `current.json` pointer after validation. Doctor checks the generation and its artifact hashes. A successful sample has state `ready`, a generation ID, and both structural and semantic planes available. Each intentional rebuild publishes a new generation; IDs are not fixed example values.

## 3. Start the HTTP service

```bash
uv run codalith serve --transport http
```

Leave that terminal running. The MCP endpoint is `http://127.0.0.1:8765/mcp`; `http://127.0.0.1:8765/readyz` returns HTTP 200 when the default sample target is ready. The service binds to loopback by default. See [HTTP exposure](../guides/operations/#http-exposure) before changing its network boundary.

The operations dashboard is optional. A source checkout needs its frontend assets built separately; see [dashboard setup](../guides/operations/#operations-dashboard). Starting MCP alone does not build them.

## 4. Retrieve and verify evidence

In a second terminal, save the following as `quickstart.py` in the checkout. It uses the official MCP SDK already installed by `uv sync`. It initializes a real Streamable HTTP session, lists the tools, checks sample readiness, requests context, and reads one returned citation again.

```python
import json

import anyio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main():
    async with streamable_http_client(
        "http://127.0.0.1:8765/mcp"
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("Tools:", ", ".join(tool.name for tool in tools.tools))

            status = await session.call_tool("codalith_status", {"target": "sample"})
            assert not status.isError, status
            assert status.structuredContent["ready"], status.structuredContent

            context = await session.call_tool(
                "codalith_context",
                {"query": "CachedValue", "target": "sample", "max_spans": 3},
            )
            assert not context.isError, context
            pack = context.structuredContent
            assert not pack["degraded"], pack
            assert not pack["warnings"], pack
            assert pack["sources"], pack
            source = pack["sources"][0]

            result = await session.call_tool("codalith_read", {"uri": source["uri"]})
            assert not result.isError, result
            evidence = result.structuredContent
            assert evidence["generation_id"] == source["generation_id"]
            assert evidence["revision"] == source["revision"]
            assert evidence["sha256"] == evidence["indexed_sha256"]
            assert not evidence["stale"]
            assert evidence["decode_replacements"] == 0
            print(json.dumps(evidence, ensure_ascii=False, indent=2))


anyio.run(main)
```

```bash
uv run python quickstart.py
```

The output should list eight tools and include real source text from the sample cache module, a `codalith://sample/source/...#Lx-Ly` URI, revision `sample-v1`, the active generation ID, and matching `sha256` and `indexed_sha256`. Inspect `truncated` before assuming that a returned range is the whole implementation. Keep the exact URI returned by `codalith_read` as the citation: it identifies the range actually read.

## 5. Connect your coding agent

With the service running, preview the installer first:

```powershell
.\install.ps1 --agent codex --agent claude --dry-run
```

On Windows, install the selected integrations with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 --agent codex --agent claude
```

On macOS or Linux:

```bash
sh ./install.sh --agent codex --agent claude --dry-run
sh ./install.sh --agent codex --agent claude
```

The installer installs the `codalith-source` skill and merges a native HTTP MCP connection into the selected clients. Installation writes those client files and backs up changed files. Restart the agent or reload its MCP configuration afterward, then ask it to explain `CachedValue` using the `sample` target and cite its source.

See [installation](../guides/installation/) for other supported clients, project scope, offline mode, validation, and recovery. An agent running inside WSL, SSH, or a container needs an endpoint reachable from its own environment.

## First-run troubleshooting

| Symptom | Next step |
| --- | --- |
| `index_missing` or readiness is false | Build the sample generation, then rerun doctor. |
| `index_invalid` | Check doctor output for changed configuration, policy, source, or artifact fingerprints; rebuild the intended corpus. |
| Connection refused | Keep the HTTP terminal running and confirm the endpoint and port. |
| Port 8765 is already in use | Reuse the intended running service, or stop the conflicting service deliberately. |
| Client installation rejects a conflict | Inspect `--dry-run`; use `--replace` only for Codalith settings you intend to replace. |
| Source hashes differ or `stale` is true | Read the warning and rebuild after intended source changes before claiming snapshot verification. |

## Stop and continue

Press **Ctrl+C** in the server terminal to stop the local service. The sample index stays on disk for the next run; stopping does not delete generations. If this was a disposable checkout, remove only the files and sample data you intentionally created after confirming the service is stopped. Client integrations persist separately; follow the [installation recovery instructions](../guides/installation/#preview-validate-and-recover) if you want to undo them.

Next, [configure your own corpus](../guides/configuration/), review the [eight MCP tools](../guides/mcp-api/), or follow the [operations guide](../guides/operations/) for Compose and existing UE indexes.
