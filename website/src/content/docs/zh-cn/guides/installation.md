---
title: 安装技能与 MCP
description: 将编码 Agent 连接到现有服务，配置项目级集成，并预览、验证或恢复安装。
---

Codalith 集成包含两部分：`codalith-source` 技能教 Agent 如何查找和验证源码证据，MCP 服务从配置好的不可变索引返回证据。应将两者一起安装。

源码检出包含 Windows 的 `install.ps1` 和 macOS/Linux 的 `install.sh`，它们调用相同的无第三方依赖 Python 安装器。可以从其他工作目录运行，也支持含空格或 Unicode 的路径。有 `uv` 时优先使用，否则需要 Python 3.11+。客户端安装不执行 `uv sync`、不安装 embedding 依赖，也不下载 Agent 应用。

## 连接现有服务

在源码检出目录运行：

```powershell
# Windows PowerShell or PowerShell 7
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

```bash
# macOS / Linux
sh ./install.sh
```

Windows 命令的 execution policy 仅作用于该 PowerShell 进程，不修改系统或用户的策略。

默认地址为 `http://127.0.0.1:8765/mcp`。安装器发现支持的 Agent，通过 MCP `initialize`、`tools/list` 和 `codalith_status` 验证服务，安装技能，并向每个所选客户端配置添加名为 `codalith` 的 server。默认目标必须同时具备就绪的结构和语义 generation；检查会报告语料 revision 与 generation ID。检查仅读取服务元数据，不对查询进行 embedding，也不重建索引。完成后重启 Agent 或重新加载 MCP 配置，并按客户端要求接受项目信任或 MCP 授权提示。

显式选择客户端或服务：

```powershell
.\install.ps1 --agent codex --agent claude --endpoint http://127.0.0.1:8765/mcp
```

```bash
sh ./install.sh --agent codex --agent claude --endpoint http://127.0.0.1:8765/mcp
```

由 Agent 进程访问 endpoint。处于 WSL、SSH、开发容器或远程工作空间中的 Agent，可能需要其他可达地址，因为 `127.0.0.1` 指向该环境自身。按[HTTP 暴露](../operations/#http-exposure)让服务 Host/Origin 限制与地址保持一致。

## 支持的 Agent

下表中 `~` 指所选用户主目录，项目路径相对于 `--project`。这些适配器配置原生 HTTP MCP 连接，不额外安装 MCP proxy。

| `--agent` | 默认用户 MCP 配置 | 项目 MCP 配置 | HTTP 配置入口 |
| --- | --- | --- | --- |
| `codex` | `~/.codex/config.toml` | `.codex/config.toml` | `[mcp_servers.codalith] url` |
| `claude-code`（别名 `claude`） | `~/.claude.json` | `.mcp.json` | `mcpServers.codalith: {type: "http", url}` |
| `cursor` | `~/.cursor/mcp.json` | `.cursor/mcp.json` | `mcpServers.codalith.url` |
| `windsurf` | `~/.codeium/windsurf/mcp_config.json` | 不支持 | `mcpServers.codalith.serverUrl` |
| `vscode-copilot`（别名 `vscode`、`copilot`） | `~/.copilot/mcp-config.json` | `.mcp.json` | `mcpServers.codalith: {type: "http", url}` |
| `gemini-cli`（别名 `gemini`） | `~/.gemini/settings.json` | `.gemini/settings.json` | `mcpServers.codalith.httpUrl` |
| `opencode` | `~/.config/opencode/opencode.json` | `opencode.json` | `mcp.codalith: {type: "remote", url, enabled: true}` |

Claude Code 技能安装在 `~/.claude/skills/codalith-source/SKILL.md`，项目级为 `.claude/skills/codalith-source/SKILL.md`。其他客户端共享 `~/.agents/skills/codalith-source/SKILL.md`，项目级为 `.agents/skills/codalith-source/SKILL.md`，避免在使用相同原生发现目录的客户端之间重复复制。

安装器在适用时遵循 `CODEX_HOME`、`CLAUDE_CONFIG_DIR`、`COPILOT_HOME`、`GEMINI_CLI_HOME`、`XDG_CONFIG_HOME` 和 `OPENCODE_CONFIG`。Gemini 的自定义主目录也决定其用户 `.agents/skills` 根目录。现有受支持的 VS Code profile `mcp.json` 和项目 `.vscode/mcp.json` 保留 `servers` 格式，不会静默转为较新的格式。

OpenCode adapter 针对稳定 v1 `mcp` schema，也接受现有 `opencode.jsonc`；若 JSON/JSONC 同时存在，会因优先级不明确而拒绝操作。OpenCode 2 的不同 MCP schema 不受支持。Windsurf adapter 针对上表中的 Windsurf/Cascade 配置，不配置独立的 Devin Desktop 安装。

原生 MCP 支持不保证每个已安装 Agent 版本都从相同目录发现技能。如果看不到 `codalith-source`，请更新较旧客户端并重新加载 skills/MCP 视图。Codex 项目配置可能要求项目已受信任，其他客户端可能需要 MCP 授权。安装器保留这些原生控制。

适配器遵循客户端文档中的配置契约：[Codex](https://developers.openai.com/codex/mcp/)、[Claude Code](https://code.claude.com/docs/en/mcp)、[Cursor](https://cursor.com/docs/mcp)、[Windsurf](https://github.com/github/github-mcp-server/blob/main/docs/installation-guides/install-windsurf.md)、[VS Code](https://code.visualstudio.com/docs/agents/reference/mcp-configuration)、[Gemini CLI](https://geminicli.com/docs/tools/mcp-server/) 和 [OpenCode](https://opencode.ai/docs/mcp-servers/)。

## 一条命令启动服务并安装

先安装 Docker 和 Compose。安装器使用检出目录中现有的 Compose 文件和配置，不安装 Docker，也不修改系统服务。

启动新的示例服务：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 --start-service sample
```

```bash
sh ./install.sh --start-service sample
```

这会构建并启动 `mcp-http` 及其 `index-sample` 依赖，后者在 Compose 数据卷中创建 sample 演示 generation。构建镜像会安装服务端依赖并构建面板，首次运行可能需要网络。MCP 地址仍为 `http://127.0.0.1:8765/mcp`，面板地址为 `http://127.0.0.1:8765/dashboard/`。

使用现有 UE 索引时，先按 [UE 运维](../operations/#ue-574)准备 `.env`：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 --start-service ue
```

```bash
sh ./install.sh --start-service ue
```

UE 选项以 `--no-deps` 仅构建/启动 `mcp-http-ue`，复用配置好的源码和已发布索引。它不运行 `index-ue`、不重新 embedding、不迁移数据库，也不移除 generation。缺失或无效的索引必须先按索引流程准备好，安装检查才能通过。复用已运行的 UE 服务时，省略 `--start-service`。

同一宿主机端口只能被一个 HTTP 服务占用。若 8765 已被使用，连接现有服务，或在明确了解影响后停止冲突服务。自定义 Compose 端口和 endpoint 时，用 `--endpoint` 指定匹配的 URL。

启动 Compose 前，安装器检查所选客户端配置能否合并。只有 MCP 探测通过后才写入客户端文件。镜像构建或启动失败时，客户端文件保持原样；已完成的 Compose 操作保留，可用 `docker compose ps` 和 `docker compose logs` 诊断。

## 安装范围与重复运行

默认使用用户范围。项目级安装可让该仓库中的 Agent 使用同一集成：

```powershell
.\install.ps1 --agent codex --agent claude --scope project --project "D:\Projects\My Project"
```

```bash
sh ./install.sh --agent codex --agent claude --scope project --project "/path/to/My Project"
```

`--scope project` 省略 `--project` 时，以当前工作目录作为目标项目。安装器所在检出无需是目标项目。`--home PATH` 选择隔离的用户目录，并忽略宿主机路径覆盖与基于可执行文件的自动发现，不改变 shell 环境变量。填充全新的隔离目录时应显式指定 `--agent`。

`--agent all` 表示主动选择所有受支持集成；项目范围选择支持项目 MCP 的六种客户端，不包含仅支持用户范围的 Windsurf。没有该选项时，自动发现避免为未检测到的 Agent 创建配置目录。显式选定了不支持的范围或没有检测到客户端都会报错；应显式指定一个受支持的 `--agent`，不能把空安装当成成功。

重复安装是幂等的。内容相同的技能和 MCP 条目不会重写。已有 `codalith` 条目冲突或技能文件被修改时会停止；先检查 dry run，只有确实要替换这些 Codalith 自有内容时才使用 `--replace`。其他 server 条目和无关设置被保留。

<a id="preview-validate-and-recover"></a>

## 预览、验证与恢复

仅预览，不写文件、不探测 endpoint、不启动 Docker：

```powershell
.\install.ps1 --agent codex --agent claude --dry-run
```

```bash
sh ./install.sh --agent codex --agent claude --dry-run
```

只验证现有安装，不进行修复：

```powershell
.\install.ps1 --agent codex --agent claude --check
```

```bash
sh ./install.sh --agent codex --agent claude --check
```

默认探测将服务缺失或不可达视为失败。`--no-probe` 是显式离线配置模式，它跳过服务验证，不能证明 Agent 可以连接。不能将其与 `--start-service` 组合，`--check` 也不能启动服务。

替换已有且变化的文件前，安装器在原文件旁按 `<filename>.codalith-backup-<UTC timestamp>-<8 hex characters>` 保存原始字节并报告路径。保留现有权限；在支持的平台上，新配置文件采用私有权限。写入使用每个文件的原子替换。

写入失败或取消时触发回滚；若发现并发修改或恢复错误，会报告问题，不覆盖这些变化，原始备份仍保留。请在 Agent 中确认集成可用后再清理备份。恢复时先停止相关 Agent，从报告的备份恢复对应原始文件；对于新建文件，仅移除 Codalith 条目及安装的 `codalith-source` 技能。若共享客户端配置之后发生过其他修改，不要直接用旧备份覆盖它。

格式错误、有歧义、符号链接或不受支持的配置会被拒绝，而不是覆盖。错误返回非零退出码；先检查报告中的文件再重试。安装器不打印模型 provider 凭证。

Shell 执行受限时可使用等价的 Python 入口：

```bash
python -X utf8 scripts/install_agents.py --agent codex --agent claude
```

运行 `python scripts/install_agents.py --help` 查看完整选项。该安装器随源码检出提供，不依赖远程 `curl | sh` 引导或独立发布的安装器包。
