#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Install UE Context Engine as a Claude Code HTTP MCP server.

Usage:
  install-mcp-client.sh <mcp-url> [bearer-token]

Environment:
  UE_CONTEXT_MCP_URL     MCP HTTP endpoint, for example https://mcp.example.com/mcp
  UE_CONTEXT_MCP_TOKEN   Optional bearer token
  UE_CONTEXT_MCP_NAME    Claude Code server name, default: ue-context
  UE_CONTEXT_MCP_SCOPE   Claude Code scope: local, project, or user. Default: user

Examples:
  curl -fsSL https://example.com/install-mcp-client.sh | bash -s -- https://mcp.example.com/mcp
  curl -fsSL https://example.com/install-mcp-client.sh | UE_CONTEXT_MCP_TOKEN=secret bash -s -- https://mcp.example.com/mcp
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

mcp_url="${UE_CONTEXT_MCP_URL:-${1:-}}"
mcp_token="${UE_CONTEXT_MCP_TOKEN:-${2:-}}"
mcp_name="${UE_CONTEXT_MCP_NAME:-ue-context}"
mcp_scope="${UE_CONTEXT_MCP_SCOPE:-user}"

if [[ -z "$mcp_url" ]]; then
  usage >&2
  exit 64
fi

case "$mcp_url" in
  http://*|https://*) ;;
  *)
    echo "UE_CONTEXT_MCP_URL must start with http:// or https://: $mcp_url" >&2
    exit 64
    ;;
esac

case "$mcp_scope" in
  local|project|user) ;;
  *)
    echo "UE_CONTEXT_MCP_SCOPE must be local, project, or user: $mcp_scope" >&2
    exit 64
    ;;
esac

if ! command -v claude >/dev/null 2>&1; then
  echo "Claude Code CLI is required. Install and authenticate Claude Code, then rerun this script." >&2
  exit 69
fi

claude mcp remove "$mcp_name" --scope "$mcp_scope" >/dev/null 2>&1 || true

add_args=(mcp add --scope "$mcp_scope" --transport http "$mcp_name" "$mcp_url")
if [[ -n "$mcp_token" ]]; then
  add_args+=(--header "Authorization: Bearer $mcp_token")
fi

claude "${add_args[@]}"
claude mcp list
