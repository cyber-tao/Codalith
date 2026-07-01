# UE Context Engine

[English README](README.md)

UE Context Engine 是一个面向 Unreal Engine 源码上下文的 Python MCP 网关。它在 CodeRAG 风格检索之上增加 UE 语义的语料解析、源码读取策略、审计日志、语义抽取、Knowledge Card 校验和评测工具。

## 功能

- 面向 AI 编程工具的 MCP stdio 和 Streamable HTTP 网关。
- 可配置源码路径、索引路径、CodeRAG 存储路径和卡片路径的 UE 版本化语料注册表。
- 支持 `ue://...` 和 `ue-project://...` 资源的源码 URI 解析。
- 源码读取策略、scope 和审计日志。
- Build.cs 依赖、UHT 反射、C++ 符号和编译宏条件抽取。
- CodeRAG acceptance job 和评测报告。

## 环境要求

- Python 3.11 或更新版本。
- 本地 Python 流程使用 [uv](https://docs.astral.sh/uv/)。
- 容器化验证使用 Docker Compose。
- 可选：本地或远程 Unreal Engine 源码 checkout。
- 可选：用于 MCP 客户端配置的 Claude Code CLI。

## 快速开始

```bash
cp .env.example .env
docker compose run --rm test
```

本地启动 stdio MCP server：

```bash
uv sync
uv run ue-context-mcp
```

本地启动 HTTP MCP server：

```bash
uv sync
uv run ue-context-mcp-http --host 127.0.0.1 --port 8765 --endpoint /mcp
```

HTTP endpoint：

```text
http://127.0.0.1:8765/mcp
```

## Docker 路径配置

所有和机器相关的路径都通过 `.env` 配置；不要为了换服务器去修改 `docker-compose.yml`。

先复制默认配置：

```bash
cp .env.example .env
```

每台机器重点改这些变量：

| 变量 | 作用 |
| --- | --- |
| `UE_CONTEXT_ENGINE_HOST_ROOT` | 宿主机上的 UE checkout 路径，会挂载为 engine source root。 |
| `UE_CONTEXT_ENGINE_SOURCE_HOST_ROOT` | 宿主机上的 `Engine/Source` 路径，用于 CodeRAG 索引挂载。 |
| `UE_CONTEXT_GAMEPLAY_ABILITIES_HOST_ROOT` | acceptance profile 需要的 GameplayAbilities 插件路径。 |
| `UE_CONTEXT_ENGINE_SOURCE_ROOT` | 容器内 UE source root。 |
| `UE_CONTEXT_ENGINE_INDEXED_ROOT` | 容器内 indexed corpus root。 |
| `UE_CONTEXT_CODERAG_STORE_DIR` | 默认 CodeRAG store 的容器内路径。 |
| `UE_CONTEXT_CODERAG_OLLAMA_STORE_DIR` | Ollama/OpenAI-compatible CodeRAG store 的容器内路径。 |

Linux 服务器示例：

```dotenv
UE_CONTEXT_ENGINE_HOST_ROOT=/opt/unreal/UE_5.7
UE_CONTEXT_ENGINE_SOURCE_HOST_ROOT=/opt/unreal/UE_5.7/Engine/Source
UE_CONTEXT_GAMEPLAY_ABILITIES_HOST_ROOT=/opt/unreal/UE_5.7/Engine/Plugins/Runtime/GameplayAbilities
```

Windows 工作站示例：

```dotenv
UE_CONTEXT_ENGINE_HOST_ROOT=E:/UnrealEngine_5.7
UE_CONTEXT_ENGINE_SOURCE_HOST_ROOT=E:/UnrealEngine_5.7/Engine/Source
UE_CONTEXT_GAMEPLAY_ABILITIES_HOST_ROOT=E:/UnrealEngine_5.7/Engine/Plugins/Runtime/GameplayAbilities
```

`configs/corpus_registry.yaml` 和 `configs/mcp_server.yaml` 也支持 `${VAR:-default}` 占位符，所以同一个仓库可以在不同机器上运行，不需要改已提交的配置文件。

## Docker 工作流

运行默认检查：

```bash
docker compose run --rm test
```

运行可选 UE source smoke path：

```bash
docker compose --profile ue run --rm ue-acceptance
```

运行 fake provider 的 CodeRAG acceptance：

```bash
docker compose --profile coderag run --rm coderag-acceptance
```

运行 Ollama/OpenAI-compatible 的 CodeRAG acceptance：

```bash
docker compose --profile coderag run --rm coderag-ollama-acceptance
```

## 在其他电脑配置 Claude Code MCP

Claude Code 可以通过 HTTP transport 添加 MCP server：

```bash
claude mcp add --scope user --transport http ue-context https://mcp.example.com/mcp
```

如果需要 bearer token：

```bash
claude mcp add --scope user --transport http ue-context https://mcp.example.com/mcp \
  --header "Authorization: Bearer $UE_CONTEXT_MCP_TOKEN"
```

检查连接：

```bash
claude mcp list
```

Claude Code scope：

| Scope | 适用场景 |
| --- | --- |
| `user` | 这台电脑上的所有项目都要使用该 MCP server。 |
| `local` | 只给当前本地项目 checkout 使用。 |
| `project` | 写入项目根目录 `.mcp.json`，用于团队共享。 |

参考：[Claude Code MCP documentation](https://code.claude.com/docs/en/mcp)。

## 一键客户端安装

把 `scripts/` 下的脚本放到你自己的域名或 GitHub raw URL，然后使用下面命令。

Linux/macOS：

```bash
curl -fsSL https://example.com/install-mcp-client.sh | bash -s -- https://mcp.example.com/mcp
```

Linux/macOS 带 token：

```bash
curl -fsSL https://example.com/install-mcp-client.sh | \
  UE_CONTEXT_MCP_TOKEN="$UE_CONTEXT_MCP_TOKEN" bash -s -- https://mcp.example.com/mcp
```

Windows PowerShell：

```powershell
$env:UE_CONTEXT_MCP_URL = "https://mcp.example.com/mcp"
irm https://example.com/install-mcp-client.ps1 | iex
```

Windows PowerShell 带 token：

```powershell
$env:UE_CONTEXT_MCP_URL = "https://mcp.example.com/mcp"
$env:UE_CONTEXT_MCP_TOKEN = "<token>"
irm https://example.com/install-mcp-client.ps1 | iex
```

可选覆盖：

```bash
UE_CONTEXT_MCP_NAME=ue-context
UE_CONTEXT_MCP_SCOPE=user
```

## 服务端部署说明

内置 HTTP server 适合放在你的部署边界后面使用。共享或远程使用时，建议在反向代理、VPN 或可信网关处处理 TLS 和认证，只暴露你希望客户端访问的 MCP endpoint。

服务端启动示例：

```bash
UE_CONTEXT_HTTP_HOST=0.0.0.0 \
UE_CONTEXT_HTTP_PORT=8765 \
UE_CONTEXT_HTTP_ENDPOINT=/mcp \
uv run ue-context-mcp-http
```

客户端 URL：

```text
https://mcp.example.com/mcp
```

## 开发

运行本地测试：

```bash
uv sync --extra dev
uv run pytest
uv run ruff check src tests jobs
uv run mypy src
```

仓库不会提交 `.env`、生成数据、报告、缓存或本地虚拟环境。
