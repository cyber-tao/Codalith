#!/usr/bin/env sh
# Run the UE Eval Suite through a real Streamable HTTP MCP server.
# Intended to run inside the ue-eval compose service:
#   docker compose --profile eval-ue run --rm ue-eval sh scripts/ue-mcp-eval.sh
# Uses the product UE corpus registry; the suite dataset stays under eval/datasets/.
set -eu

OUTPUT_DIR="reports/mcp-eval/ue_eval_suite"
ENDPOINT="http://127.0.0.1:8765/mcp"
UE_REGISTRY="${CODALITH_UE_CORPUS_REGISTRY:-configs/ue_5_7_4_registry.json}"

uv sync
uv run python -c "from jobs.coderag_acceptance import ensure_coderag_installed; ensure_coderag_installed('openai')"
mkdir -p "$OUTPUT_DIR"

CODALITH_CORPUS_REGISTRY="$UE_REGISTRY" \
  uv run codalith-mcp-http --host 127.0.0.1 --port 8765 --endpoint /mcp \
  > "$OUTPUT_DIR/server.out.log" 2> "$OUTPUT_DIR/server.err.log" &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT

attempt=0
until uv run python -c "import http.client; c = http.client.HTTPConnection('127.0.0.1', 8765, timeout=2); c.request('GET', '/mcp', headers={'Accept': 'text/event-stream'}); raise SystemExit(0 if c.getresponse().status == 200 else 1)" 2> /dev/null; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 60 ]; then
    echo "MCP HTTP server did not become ready" >&2
    cat "$OUTPUT_DIR/server.err.log" >&2
    exit 1
  fi
  sleep 1
done

uv run codalith-mcp-eval \
  --endpoint "$ENDPOINT" \
  --dataset eval/datasets/ue_eval_suite.jsonl \
  --output-dir "$OUTPUT_DIR" \
  --label ue_eval_suite \
  --version "${CODALITH_UE_VERSION:-5.7.4}" \
  --max-source-spans 20 \
  --require-pass
