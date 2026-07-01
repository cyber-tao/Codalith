# Codalith

[English README](README.md)

Codalith 是一个面向 Unreal Engine 源码上下文的 Python MCP 网关。它在 CodeRAG 风格检索之上增加 UE 语义的语料解析、源码读取策略、审计日志、语义抽取、Knowledge Card 校验和评测工具。

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
- 使用 Git submodule 固定 CodeRAG checkout。
- 可选：本地或远程 Unreal Engine 源码 checkout。
- 可选：用于 MCP 客户端配置的 Claude Code CLI、Codex、VS Code 或 Cursor。

## 带 Submodule 克隆

CodeRAG 作为 Git submodule 固定在 `external/CodeRAG`。

全新 clone：

```bash
git clone --recurse-submodules <repo-url>
```

已有 checkout：

```bash
git submodule update --init --recursive external/CodeRAG
```

## 快速开始

```bash
cp .env.example .env
docker compose run --rm test
```

本地启动 stdio MCP server：

```bash
uv sync
uv run codalith-mcp
```

本地启动 HTTP MCP server：

```bash
uv sync
uv run codalith-mcp-http --host 127.0.0.1 --port 8765 --endpoint /mcp
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
| `CODALITH_ENGINE_HOST_ROOT` | 宿主机上的 UE checkout 路径，会挂载为 engine source root。 |
| `CODALITH_ENGINE_SOURCE_HOST_ROOT` | 宿主机上的 `Engine/Source` 路径，用于 CodeRAG 索引挂载。 |
| `CODALITH_GAMEPLAY_ABILITIES_HOST_ROOT` | acceptance profile 需要的 GameplayAbilities 插件路径。 |
| `CODALITH_ENGINE_SOURCE_ROOT` | 容器内 UE source root。 |
| `CODALITH_ENGINE_INDEXED_ROOT` | 容器内 indexed corpus root。 |
| `CODALITH_CODERAG_STORE_DIR` | 默认 CodeRAG store 的容器内路径。 |
| `CODALITH_CODERAG_OLLAMA_STORE_DIR` | Ollama/OpenAI-compatible CodeRAG store 的容器内路径。 |

Linux 服务器示例：

```dotenv
CODALITH_ENGINE_HOST_ROOT=/opt/unreal/UE_5.7
CODALITH_ENGINE_SOURCE_HOST_ROOT=/opt/unreal/UE_5.7/Engine/Source
CODALITH_GAMEPLAY_ABILITIES_HOST_ROOT=/opt/unreal/UE_5.7/Engine/Plugins/Runtime/GameplayAbilities
```

Windows 工作站示例：

```dotenv
CODALITH_ENGINE_HOST_ROOT=E:/UnrealEngine_5.7
CODALITH_ENGINE_SOURCE_HOST_ROOT=E:/UnrealEngine_5.7/Engine/Source
CODALITH_GAMEPLAY_ABILITIES_HOST_ROOT=E:/UnrealEngine_5.7/Engine/Plugins/Runtime/GameplayAbilities
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

这个 profile 需要先初始化 `external/CodeRAG`。如果只是本地临时实验且没有 submodule，可以设置 `CODALITH_CODERAG_ALLOW_AUTO_CLONE=1`，允许浅克隆到 `/tmp/CodeRAG`。

运行 Ollama/OpenAI-compatible 的 CodeRAG acceptance：

```bash
docker compose --profile coderag run --rm coderag-ollama-acceptance
```

## MCP 客户端配置

Codalith 暴露 Streamable HTTP MCP endpoint。服务名使用 `codalith`，核心工具名是 `codalith_context`。

支持的客户端目标：

| 客户端 | 脚本参数 | 配置目标 |
| --- | --- | --- |
| Claude Code | `claude` | `claude mcp add --transport http ...` |
| Codex | `codex` | `~/.codex/config.toml` 或 `.codex/config.toml` |
| VS Code / GitHub Copilot | `vscode` 或 `copilot` | `code --add-mcp` 或 `.vscode/mcp.json` |
| Cursor | `cursor` | `~/.cursor/mcp.json` 或 `.cursor/mcp.json` |
| 所有支持的客户端 | `all` | 尽力配置上面所有客户端 |

手动配置示例：

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

参考：[Claude Code MCP](https://code.claude.com/docs/en/mcp)、[Codex MCP](https://developers.openai.com/codex/mcp)、[VS Code MCP servers](https://code.visualstudio.com/docs/copilot/customization/mcp-servers)、[Cursor MCP](https://cursor.com/docs/mcp)。

## 一键客户端安装

把 `scripts/` 下的脚本放到你自己的域名或 GitHub raw URL，然后使用下面命令。

配置当前机器上所有可用客户端：

```bash
curl -fsSL https://example.com/install-mcp-client.sh | bash -s -- --url https://mcp.example.com/mcp
```

只配置指定客户端：

```bash
curl -fsSL https://example.com/install-mcp-client.sh | \
  bash -s -- --client codex --scope user --url https://mcp.example.com/mcp
```

Linux/macOS 带 token：

```bash
curl -fsSL https://example.com/install-mcp-client.sh | \
  CODALITH_MCP_TOKEN="$CODALITH_MCP_TOKEN" bash -s -- --client all --url https://mcp.example.com/mcp
```

Windows PowerShell 配置所有客户端：

```powershell
$env:CODALITH_MCP_CLIENT = "all"
$env:CODALITH_MCP_URL = "https://mcp.example.com/mcp"
irm https://example.com/install-mcp-client.ps1 | iex
```

Windows PowerShell 配置指定客户端并带 token：

```powershell
$env:CODALITH_MCP_CLIENT = "cursor"
$env:CODALITH_MCP_URL = "https://mcp.example.com/mcp"
$env:CODALITH_MCP_TOKEN = "<token>"
irm https://example.com/install-mcp-client.ps1 | iex
```

可选覆盖：

```bash
CODALITH_MCP_NAME=codalith
CODALITH_MCP_CLIENT=all
CODALITH_MCP_SCOPE=user
CODALITH_MCP_CONFIG_PATH=/custom/path/to/config
```

Scope 会尽量映射到每个客户端自己的配置模型。`user` 表示机器级配置；`project`、`workspace` 和 `local` 会给 Codex、VS Code/Copilot、Cursor 写入项目本地配置；Claude Code 会把 `workspace` 映射到它的 `local` scope。

## 服务端部署说明

内置 HTTP server 适合放在你的部署边界后面使用。共享或远程使用时，建议在反向代理、VPN 或可信网关处处理 TLS 和认证，只暴露你希望客户端访问的 MCP endpoint。

服务端启动示例：

```bash
CODALITH_HTTP_HOST=0.0.0.0 \
CODALITH_HTTP_PORT=8765 \
CODALITH_HTTP_ENDPOINT=/mcp \
uv run codalith-mcp-http
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
