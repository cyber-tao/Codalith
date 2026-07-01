#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Install Codalith as an HTTP MCP server for common AI coding clients.

Usage:
  install-mcp-client.sh --url <mcp-url> [options]
  install-mcp-client.sh <mcp-url> [bearer-token]

Options:
  --client <name>       claude, codex, vscode, copilot, cursor, or all. Default: all
  --url <url>           MCP HTTP endpoint, for example https://mcp.example.com/mcp
  --token <token>       Optional bearer token written to client configuration
  --name <name>         MCP server name. Default: codalith
  --scope <scope>       user, local, project, or workspace. Default: user
  --config-path <path>  Override the config file path for file-based clients
  -h, --help            Show this help

Environment:
  CODALITH_MCP_CLIENT
  CODALITH_MCP_URL
  CODALITH_MCP_TOKEN
  CODALITH_MCP_NAME
  CODALITH_MCP_SCOPE
  CODALITH_MCP_CONFIG_PATH

Examples:
  curl -fsSL https://example.com/install-mcp-client.sh | bash -s -- --url https://mcp.example.com/mcp
  curl -fsSL https://example.com/install-mcp-client.sh | bash -s -- --client codex --url https://mcp.example.com/mcp
  curl -fsSL https://example.com/install-mcp-client.sh | CODALITH_MCP_TOKEN=secret bash -s -- https://mcp.example.com/mcp
USAGE
}

client="${CODALITH_MCP_CLIENT:-all}"
mcp_url="${CODALITH_MCP_URL:-}"
mcp_token="${CODALITH_MCP_TOKEN:-}"
mcp_name="${CODALITH_MCP_NAME:-codalith}"
mcp_scope="${CODALITH_MCP_SCOPE:-user}"
config_path="${CODALITH_MCP_CONFIG_PATH:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --client)
      client="${2:-}"
      shift 2
      ;;
    --url)
      mcp_url="${2:-}"
      shift 2
      ;;
    --token)
      mcp_token="${2:-}"
      shift 2
      ;;
    --name)
      mcp_name="${2:-}"
      shift 2
      ;;
    --scope)
      mcp_scope="${2:-}"
      shift 2
      ;;
    --config-path)
      config_path="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 64
      ;;
    *)
      if [[ -z "$mcp_url" ]]; then
        mcp_url="$1"
      elif [[ -z "$mcp_token" ]]; then
        mcp_token="$1"
      else
        echo "Unexpected positional argument: $1" >&2
        exit 64
      fi
      shift
      ;;
  esac
done

if [[ -z "$mcp_url" ]]; then
  usage >&2
  exit 64
fi

case "$mcp_url" in
  http://*|https://*) ;;
  *)
    echo "CODALITH_MCP_URL must start with http:// or https://: $mcp_url" >&2
    exit 64
    ;;
esac

case "$mcp_name" in
  *[!A-Za-z0-9_-]*|"")
    echo "CODALITH_MCP_NAME may contain only letters, numbers, underscores, and hyphens." >&2
    exit 64
    ;;
esac

normalize_client() {
  case "$1" in
    claude|claude-code) echo "claude" ;;
    codex) echo "codex" ;;
    vscode|vs-code|copilot|github-copilot) echo "vscode" ;;
    cursor) echo "cursor" ;;
    all) echo "all" ;;
    *)
      echo "Unsupported client: $1" >&2
      exit 64
      ;;
  esac
}

client="$(normalize_client "$client")"

run_or_skip() {
  local selected="$1"
  local description="$2"
  shift 2
  if [[ "$client" == "all" ]]; then
    if "$@"; then
      echo "Configured $description."
    else
      echo "Skipped $description." >&2
    fi
    return 0
  fi
  "$@"
  echo "Configured $description."
}

require_command() {
  local command_name="$1"
  command -v "$command_name" >/dev/null 2>&1
}

python_bin() {
  if command -v python3 >/dev/null 2>&1; then
    echo "python3"
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    echo "python"
    return 0
  fi
  return 1
}

install_claude() {
  require_command claude || {
    echo "Claude Code CLI is not installed." >&2
    return 1
  }
  local scope="$mcp_scope"
  case "$scope" in
    user|local|project) ;;
    workspace) scope="local" ;;
    *)
      echo "Claude Code scope must be user, local, or project." >&2
      return 1
      ;;
  esac
  claude mcp remove "$mcp_name" --scope "$scope" >/dev/null 2>&1 || true
  local add_args=(mcp add --scope "$scope" --transport http "$mcp_name" "$mcp_url")
  if [[ -n "$mcp_token" ]]; then
    add_args+=(--header "Authorization: Bearer $mcp_token")
  fi
  claude "${add_args[@]}"
}

install_codex() {
  local scope="$mcp_scope"
  local target="$config_path"
  case "$scope" in
    user|"")
      target="${target:-$HOME/.codex/config.toml}"
      ;;
    local|project|workspace)
      target="${target:-.codex/config.toml}"
      ;;
    *)
      echo "Codex scope must be user or project/workspace/local." >&2
      return 1
      ;;
  esac
  mkdir -p "$(dirname "$target")"
  touch "$target"
  local tmp
  tmp="$(mktemp)"
  awk -v section="[mcp_servers.$mcp_name]" '
    $0 == section { skip = 1; next }
    skip && /^\[/ { skip = 0 }
    !skip { print }
  ' "$target" > "$tmp"
  {
    cat "$tmp"
    printf '\n[mcp_servers.%s]\n' "$mcp_name"
    printf 'url = "%s"\n' "$mcp_url"
    if [[ -n "$mcp_token" ]]; then
      printf 'http_headers = { "Authorization" = "Bearer %s" }\n' "$mcp_token"
    fi
  } > "$target"
  rm -f "$tmp"
}

install_vscode() {
  local scope="$mcp_scope"
  if [[ "$scope" == "workspace" || "$scope" == "project" || "$scope" == "local" ]]; then
    write_json_mcp ".vscode/mcp.json" "servers"
    return
  fi
  require_command code || {
    echo "VS Code CLI 'code' is not installed." >&2
    return 1
  }
  local server_config
  server_config="$(json_server_config "vscode-cli")"
  code --add-mcp "$server_config"
}

install_cursor() {
  local scope="$mcp_scope"
  local target="$config_path"
  case "$scope" in
    user|"")
      target="${target:-$HOME/.cursor/mcp.json}"
      ;;
    local|project|workspace)
      target="${target:-.cursor/mcp.json}"
      ;;
    *)
      echo "Cursor scope must be user or project/workspace/local." >&2
      return 1
      ;;
  esac
  write_json_mcp "$target" "mcpServers"
}

json_server_config() {
  local mode="$1"
  local py
  py="$(python_bin)" || return 1
  "$py" - "$mode" "$mcp_name" "$mcp_url" "$mcp_token" <<'PY'
import json
import sys

mode, name, url, token = sys.argv[1:5]
server = {"type": "http", "url": url}
if token:
    server["headers"] = {"Authorization": f"Bearer {token}"}
if mode == "vscode-cli":
    server["name"] = name
print(json.dumps(server, separators=(",", ":")))
PY
}

write_json_mcp() {
  local target="$1"
  local root_key="$2"
  local py
  py="$(python_bin)" || {
    echo "Python is required to update $target safely." >&2
    return 1
  }
  mkdir -p "$(dirname "$target")"
  "$py" - "$target" "$root_key" "$mcp_name" "$mcp_url" "$mcp_token" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
root_key, name, url, token = sys.argv[2:6]
if path.exists() and path.read_text(encoding="utf-8").strip():
    data = json.loads(path.read_text(encoding="utf-8"))
else:
    data = {}
servers = data.setdefault(root_key, {})
server = {"type": "http", "url": url}
if token:
    server["headers"] = {"Authorization": f"Bearer {token}"}
servers[name] = server
path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

case "$client" in
  claude)
    run_or_skip "$client" "Claude Code MCP" install_claude
    ;;
  codex)
    run_or_skip "$client" "Codex MCP" install_codex
    ;;
  vscode)
    run_or_skip "$client" "VS Code/Copilot MCP" install_vscode
    ;;
  cursor)
    run_or_skip "$client" "Cursor MCP" install_cursor
    ;;
  all)
    run_or_skip "$client" "Claude Code MCP" install_claude
    run_or_skip "$client" "Codex MCP" install_codex
    run_or_skip "$client" "VS Code/Copilot MCP" install_vscode
    run_or_skip "$client" "Cursor MCP" install_cursor
    ;;
esac
