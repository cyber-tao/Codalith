---
title: 在 Agent 中取得源码证据
description: 构建示例语料、启动本地 MCP 服务，并通过官方 SDK 验证源码引用。
---

本教程从源码检出开始，带你构建索引并取得一个经过验证的 `codalith://` 源码引用。仓库内置的示例包含少量 Python 缓存和事件代码，无需模型密钥，也无需额外源码语料。

示例使用确定性的 `fake` embedding provider，用来验证 generation 发布和检索协议，**不能用于证明真实模型的语义检索质量**。实际项目应在[配置](../guides/configuration/)中选择适合的 embedding provider 和模型。

## 1. 准备源码检出

安装 Git、Python 3.11 或更高版本，以及 [uv](https://docs.astral.sh/uv/getting-started/installation/)。下列步骤使用当前源码检出中的命令和配置，不以独立发布的软件包为前提。

```bash
git clone https://github.com/cyber-tao/Codalith.git
cd Codalith
uv sync --frozen --extra dev
```

后续命令均在该检出目录运行。第一次同步依赖可能需要下载 Python 包。`configs/registry.toml` 已包含默认 sample 配置，本教程无需 `.env` 文件或 UE 源码。

## 2. 构建并检查示例

```bash
uv run codalith index build --corpus sample --semantic build
uv run codalith doctor --target sample --deep
```

构建在 `data/indexes/sample/` 下发布 generation，通过验证后更新 `current.json`。Doctor 检查该 generation 及其产物哈希。成功的 sample 应处于 `ready` 状态，具有 generation ID，并且结构平面和语义平面均可用。每次主动重建都会发布新 generation，ID 并非固定的示例值。

## 3. 启动 HTTP 服务

```bash
uv run codalith serve --transport http
```

保持该终端运行。MCP 地址为 `http://127.0.0.1:8765/mcp`；默认 sample 目标就绪时，`http://127.0.0.1:8765/readyz` 返回 HTTP 200。服务默认绑定回环地址；修改网络边界前请阅读 [HTTP 暴露](../guides/operations/#http-exposure)。

运维面板是可选的。源码检出需要单独构建前端静态资源，参见[面板配置](../guides/operations/#operations-dashboard)。仅启动 MCP 不会构建这些资源。

## 4. 检索并验证证据

在另一个终端中，将下面的程序保存为检出目录下的 `quickstart.py`。程序使用 `uv sync` 已安装的官方 MCP SDK，建立真实 Streamable HTTP 会话，列出工具、检查 sample 状态、取得上下文，再重新读取其中一条源码引用。

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

输出应列出七个工具，并包含示例缓存模块的真实源码、`codalith://sample/source/...#Lx-Ly` URI、`sample-v1` revision、当前 generation ID，以及一致的 `sha256` 和 `indexed_sha256`。先检查 `truncated`，再判断返回范围是否覆盖完整实现。引用时原样保留 `codalith_read` 返回的 URI，它标识实际读到的范围。

## 5. 连接编码 Agent

保持服务运行，先预览安装计划：

```powershell
.\install.ps1 --agent codex --agent claude --dry-run
```

Windows 安装命令：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 --agent codex --agent claude
```

macOS 或 Linux：

```bash
sh ./install.sh --agent codex --agent claude --dry-run
sh ./install.sh --agent codex --agent claude
```

安装器安装 `codalith-source` 技能，并为所选客户端合并原生 HTTP MCP 配置。它会写入对应客户端文件，并备份发生更改的文件。完成后重启 Agent 或重新加载 MCP 配置，再让 Agent 在 `sample` 目标下解释 `CachedValue` 并引用源码。

[安装指南](../guides/installation/)介绍其他支持的客户端、项目级安装、离线模式、验证和恢复。运行于 WSL、SSH 或容器内的 Agent，需要使用从其所在环境可达的服务地址。

## 首次运行排障

| 现象 | 下一步 |
| --- | --- |
| `index_missing` 或未就绪 | 构建 sample generation，再运行 doctor。 |
| `index_invalid` | 查看 doctor 报告中的配置、策略、源码或产物指纹差异，重建目标语料。 |
| 连接被拒绝 | 保持 HTTP 终端运行，检查地址与端口。 |
| 8765 端口已占用 | 复用预期的现有服务，或明确停止发生冲突的服务。 |
| 客户端安装存在配置冲突 | 检查 `--dry-run`，仅在确实要替换 Codalith 配置时使用 `--replace`。 |
| 哈希不同或 `stale` 为 true | 阅读警告；对预期的源码修改重新建库后，再声明快照验证通过。 |

## 停止与后续操作

在服务终端按 **Ctrl+C** 停止本地服务。示例索引保留在磁盘上供下次使用，停止服务不会删除 generation。如果这是一次性检出，确认服务已停止后，只清理你有意创建的文件和 sample 数据。客户端集成单独保留；需要撤销时遵循[安装恢复说明](../guides/installation/#preview-validate-and-recover)。

接下来可以[配置自己的语料](../guides/configuration/)、查看[七个 MCP 工具](../guides/mcp-api/)，或按[运维指南](../guides/operations/)使用 Compose 和现有 UE 索引。
