# Cross-platform skill + MCP installation

Codalith's integration has two parts: the `codalith-source` skill teaches an agent how to find and verify source evidence; the MCP server returns that evidence from the configured immutable index. Install both together.

The source checkout contains `install.ps1` for Windows and `install.sh` for macOS/Linux. Both invoke the same dependency-free Python installer. They work from other working directories and from paths containing spaces or Unicode. They use `uv` when available and otherwise require Python 3.11+. Client installation does not run `uv sync`, install embedding dependencies, or download an Agent application.

## Connect to an existing service

Run from the checkout:

```powershell
# Windows PowerShell or PowerShell 7
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

```bash
# macOS / Linux
sh ./install.sh
```

The Windows command applies its execution policy only to that PowerShell process. It does not change the machine's or user's policy.

The default endpoint is `http://127.0.0.1:8765/mcp`. The installer discovers supported Agent installations, verifies the server through MCP `initialize`, `tools/list`, and `codalith_status`, installs the skill, and adds a named `codalith` server to each selected client's configuration. It requires the default target to have a ready structural and semantic generation, and reports corpus revisions and generation IDs. These checks read server metadata without embedding a query or rebuilding an index. Restart the affected Agent or reload its MCP configuration afterward; accept any native project-trust or MCP approval prompt that the Agent requires.

Select clients or another service explicitly:

```powershell
.\install.ps1 --agent codex --agent claude --endpoint http://127.0.0.1:8765/mcp
```

```bash
sh ./install.sh --agent codex --agent claude --endpoint http://127.0.0.1:8765/mcp
```

The endpoint is reached by the Agent process. An Agent running inside WSL, SSH, a development container, or a remote workspace may require a different reachable URL; its `127.0.0.1` refers to that environment. Keep the service's Host/Origin restrictions aligned with the chosen endpoint, as described in [Operations](operations.md#http-exposure).

## Supported agents

`~` below means the selected user home. Project paths are relative to `--project`. These adapters configure native HTTP MCP connections; they do not install an additional MCP proxy.

| `--agent` | Default user MCP configuration | Project MCP configuration | HTTP entry |
| --- | --- | --- | --- |
| `codex` | `~/.codex/config.toml` | `.codex/config.toml` | `[mcp_servers.codalith] url` |
| `claude-code` (alias `claude`) | `~/.claude.json` | `.mcp.json` | `mcpServers.codalith: {type: "http", url}` |
| `cursor` | `~/.cursor/mcp.json` | `.cursor/mcp.json` | `mcpServers.codalith.url` |
| `windsurf` | `~/.codeium/windsurf/mcp_config.json` | Not supported | `mcpServers.codalith.serverUrl` |
| `vscode-copilot` (aliases `vscode`, `copilot`) | `~/.copilot/mcp-config.json` | `.mcp.json` | `mcpServers.codalith: {type: "http", url}` |
| `gemini-cli` (alias `gemini`) | `~/.gemini/settings.json` | `.gemini/settings.json` | `mcpServers.codalith.httpUrl` |
| `opencode` | `~/.config/opencode/opencode.json` | `opencode.json` | `mcp.codalith: {type: "remote", url, enabled: true}` |

Claude Code's skill is installed at `~/.claude/skills/codalith-source/SKILL.md` or `.claude/skills/codalith-source/SKILL.md` in project scope. The other clients share `~/.agents/skills/codalith-source/SKILL.md` or the project's `.agents/skills/codalith-source/SKILL.md`, avoiding duplicate skill copies between clients that use the same native discovery directory.

The installer respects `CODEX_HOME`, `CLAUDE_CONFIG_DIR`, `COPILOT_HOME`, `GEMINI_CLI_HOME`, `XDG_CONFIG_HOME`, and `OPENCODE_CONFIG` where applicable. Gemini's custom home also determines its user `.agents/skills` root. Existing supported VS Code profile `mcp.json` and project `.vscode/mcp.json` configurations retain their `servers` format instead of being silently converted to the newer format. OpenCode's adapter targets the stable v1 `mcp` schema and accepts an existing `opencode.jsonc`; it rejects simultaneous JSON/JSONC configurations because precedence would be ambiguous. OpenCode 2's different MCP schema is not supported by this adapter. The Windsurf adapter targets the Windsurf/Cascade configuration above; it does not configure a separate Devin Desktop installation.

Native MCP support does not guarantee that every installed Agent version discovers skills in the same directories. Update an older client if it does not show `codalith-source`, then reload its skills/MCP view. Codex project configuration may require a trusted project, and other clients may ask for MCP approval. The installer leaves those native controls in place.

The adapters follow the clients' documented configuration contracts: [Codex](https://developers.openai.com/codex/mcp/), [Claude Code](https://code.claude.com/docs/en/mcp), [Cursor](https://cursor.com/docs/mcp), [Windsurf](https://github.com/github/github-mcp-server/blob/main/docs/installation-guides/install-windsurf.md), [VS Code](https://code.visualstudio.com/docs/agents/reference/mcp-configuration), [Gemini CLI](https://geminicli.com/docs/tools/mcp-server/), and [OpenCode](https://opencode.ai/docs/mcp-servers/).

## Start a service and install in one command

Install Docker with Compose first. The installer uses the checkout's existing Compose file and configuration rather than installing Docker or changing system services.

For a new sample service:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 --start-service sample
```

```bash
sh ./install.sh --start-service sample
```

This builds and starts `mcp-http` and its `index-sample` dependency. The dependency creates a sample demonstration generation in the Compose data volume. Docker image building installs the server's dependencies and builds the dashboard; it can require network access on the first run. The endpoint remains `http://127.0.0.1:8765/mcp`, and the dashboard is at `http://127.0.0.1:8765/dashboard/`.

For an existing UE index, prepare `.env` according to [UE operations](operations.md#ue-574), then use:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 --start-service ue
```

```bash
sh ./install.sh --start-service ue
```

The UE option builds/starts only `mcp-http-ue`, with `--no-deps`; it reuses the configured source and published index. It does not run `index-ue`, re-embed the corpus, migrate a database, or remove generations. A missing or invalid index must be prepared using the indexing instructions before installation can pass its service check. To reuse an already running UE server, omit `--start-service`.

Only one HTTP service can occupy the same host port. If an existing service already uses port 8765, connect to it or stop the conflicting service deliberately before starting another. For a custom Compose port/endpoint, pass its matching URL with `--endpoint`.

Before starting Compose, the installer checks whether the selected client configuration can be merged. It writes client files only after the service passes its MCP probe. If image building or startup fails, client files stay untouched; any Compose work already completed remains available for diagnosis with `docker compose ps` and `docker compose logs`.

## Scope and repeatable installation

User scope is the default. Use project scope to share a repository's integration with the agents working in that repository:

```powershell
.\install.ps1 --agent codex --agent claude --scope project --project "D:\Projects\My Project"
```

```bash
sh ./install.sh --agent codex --agent claude --scope project --project "/path/to/My Project"
```

When `--scope project` omits `--project`, the current working directory is the target project. The checkout containing the installer does not need to be the target project. `--home PATH` selects an isolated user-home root and ignores host path overrides and executable-based automatic detection; it does not change the shell's environment variables. Use explicit `--agent` values to populate a new isolated home.

Use `--agent all` to select every supported integration deliberately. Without it, automatic discovery avoids creating configuration directories for Agent applications that are not detected. An unsupported scope or no detected client is an actionable error; use an explicit supported `--agent` rather than treating an empty installation as success.

Repeated installation is idempotent. Matching skill content and MCP entries are left unchanged. Conflicting existing `codalith` entries or modified skill files stop installation; inspect the dry run and use `--replace` only when you intend to replace those Codalith-owned values. Other server entries and unrelated settings are retained.

## Preview, validate, and recover

Preview without writing, probing the endpoint, or starting Docker:

```powershell
.\install.ps1 --agent codex --agent claude --dry-run
```

```bash
sh ./install.sh --agent codex --agent claude --dry-run
```

Validate an existing installation without repairing it:

```powershell
.\install.ps1 --agent codex --agent claude --check
```

```bash
sh ./install.sh --agent codex --agent claude --check
```

The default probe reports a missing or unreachable service as failure. `--no-probe` is an explicit offline configuration mode: it skips server validation and does not prove that an Agent can connect. It cannot be combined with `--start-service`, and `--check` cannot start a service.

Before replacing an existing changed file, the installer saves its original bytes beside that file as `<filename>.codalith-backup-<UTC timestamp>-<8 hex characters>` and reports the path. Existing file permissions are preserved; new configuration files use private permissions where supported. Writes use atomic per-file replacement. Write failures or cancellation trigger rollback; concurrent edits or restoration errors are reported without overwriting those edits, and original backups remain. Preserve these backups until the integration has been verified in the Agent. To revert, stop the affected Agent and restore the relevant original file from its reported backup; for newly created files, remove only the Codalith entry and the installed `codalith-source` skill. Do not replace a shared client configuration with an old backup after unrelated edits have been made to that configuration.

Malformed, ambiguous, symlinked, or unsupported configuration is rejected rather than overwritten. Errors return a nonzero exit code. Review the reported file and correct it before retrying; the installer never prints model-provider credentials.

The equivalent Python entrypoint is available when shell execution is restricted:

```bash
python -X utf8 scripts/install_agents.py --agent codex --agent claude
```

Run `python scripts/install_agents.py --help` for the complete options. This installer is supplied by the source checkout; the repository does not require a remote `curl | sh` bootstrap or a separately published installer package.
