from __future__ import annotations

import http.client
import json
import threading

from codalith.gateway.http_server import (
    StreamableHTTPConfig,
    create_http_server,
    http_log_enabled,
)


def test_http_log_enabled_truthy_values(monkeypatch):
    for value in ("1", "true", "TRUE", "yes", "on", "verbose"):
        monkeypatch.setenv("CODALITH_HTTP_LOG", value)
        assert http_log_enabled() is True
    for value in ("", "0", "false", "no", "off"):
        monkeypatch.setenv("CODALITH_HTTP_LOG", value)
        assert http_log_enabled() is False
    monkeypatch.delenv("CODALITH_HTTP_LOG", raising=False)
    assert http_log_enabled() is False


def test_streamable_http_post_get_session_and_origin(tools):
    server = create_http_server(tools, StreamableHTTPConfig(port=0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address

    try:
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1"},
            },
        }
        response, payload = _post(host, port, initialize)

        assert response.status == 200
        assert response.getheader("MCP-Session-Id")
        assert payload["result"]["protocolVersion"] == "2025-11-25"

        session_id = response.getheader("MCP-Session-Id")
        initialized_response, _ = _post(
            host,
            port,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            session_id=session_id,
        )
        assert initialized_response.status == 202
        tools_response, tools_payload = _post(
            host,
            port,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            session_id=session_id,
        )

        assert tools_response.status == 200
        assert tools_payload["result"]["tools"]

        status_response, status_payload = _post(
            host,
            port,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "codalith_index_status",
                    "arguments": {"corpus": "sample"},
                },
            },
            session_id=session_id,
        )
        assert status_response.status == 200
        assert "error" not in status_payload
        assert (
            status_payload["result"]["structuredContent"]["semantic"]["base"]["corpus_id"]
            == "sample-codebase"
        )

        graph_response, graph_payload = _post(
            host,
            port,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "codalith_graph",
                    "arguments": {"node": "EventBus", "corpus": "sample"},
                },
            },
            session_id=session_id,
        )
        assert graph_response.status == 200
        assert "nodes" in graph_payload["result"]["structuredContent"]

        missing_session, _ = _post(
            host,
            port,
            {"jsonrpc": "2.0", "id": 5, "method": "tools/list"},
        )
        assert missing_session.status == 400

        invalid_session, _ = _post(
            host,
            port,
            {"jsonrpc": "2.0", "id": 5, "method": "tools/list"},
            session_id="not-a-registered-session",
        )
        assert invalid_session.status == 404

        forbidden, _ = _post(
            host,
            port,
            {"jsonrpc": "2.0", "id": 6, "method": "tools/list"},
            origin="http://evil.example",
            session_id=session_id,
        )
        assert forbidden.status == 403
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_streamable_http_requires_configured_bearer_token(tools, monkeypatch):
    monkeypatch.setenv("CODALITH_HTTP_BEARER_TOKEN", "secret-token")
    server = create_http_server(tools, StreamableHTTPConfig(port=0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address

    try:
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1"},
            },
        }
        anonymous, _ = _post(host, port, initialize)
        assert anonymous.status == 401

        authorized, payload = _post(host, port, initialize, bearer_token="secret-token")
        assert authorized.status == 200
        assert payload["result"]["serverInfo"]["name"] == "codalith"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_streamable_http_rejects_oversized_requests(tools):
    server = create_http_server(
        tools,
        StreamableHTTPConfig(port=0, max_request_bytes=32),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address

    try:
        response, payload = _post(
            host,
            port,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"padding": "x" * 100},
            },
        )
        assert response.status == 413
        assert payload["error"] == "request_too_large"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _post(
    host: str,
    port: int,
    payload: dict[str, object],
    *,
    origin: str = "http://127.0.0.1",
    session_id: str | None = None,
    bearer_token: str | None = None,
) -> tuple[http.client.HTTPResponse, dict[str, object]]:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Origin": origin,
        "MCP-Protocol-Version": "2025-11-25",
    }
    if session_id:
        headers["MCP-Session-Id"] = session_id
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    connection = http.client.HTTPConnection(host, port, timeout=5)
    connection.request("POST", "/mcp", body=json.dumps(payload), headers=headers)
    response = connection.getresponse()
    body = response.read().decode("utf-8")
    connection.close()
    if not body:
        return response, {}
    try:
        return response, json.loads(body)
    except json.JSONDecodeError:
        return response, {"raw": body}
