from __future__ import annotations

import http.client
import json
import threading

from codalith.gateway.http_server import StreamableHTTPConfig, create_http_server


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
            "params": {"protocolVersion": "2025-11-25"},
        }
        response, payload = _post(host, port, initialize)

        assert response.status == 200
        assert response.getheader("MCP-Session-Id")
        assert payload["result"]["protocolVersion"] == "2025-11-25"

        session_id = response.getheader("MCP-Session-Id")
        tools_response, tools_payload = _post(
            host,
            port,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            session_id=session_id,
        )

        assert tools_response.status == 200
        assert tools_payload["result"]["tools"]

        missing_session, _ = _post(
            host,
            port,
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
        )
        assert missing_session.status == 400

        get_response, get_body = _get(host, port, session_id=session_id)
        assert get_response.status == 200
        assert get_response.getheader("Content-Type", "").startswith("text/event-stream")
        assert "event: ping" in get_body

        forbidden, _ = _post(
            host,
            port,
            {"jsonrpc": "2.0", "id": 4, "method": "tools/list"},
            origin="http://evil.example",
            session_id=session_id,
        )
        assert forbidden.status == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _post(
    host: str,
    port: int,
    payload: dict[str, object],
    *,
    origin: str = "http://127.0.0.1",
    session_id: str | None = None,
) -> tuple[http.client.HTTPResponse, dict[str, object]]:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Origin": origin,
        "MCP-Protocol-Version": "2025-11-25",
    }
    if session_id:
        headers["MCP-Session-Id"] = session_id
    connection = http.client.HTTPConnection(host, port, timeout=5)
    connection.request("POST", "/mcp", body=json.dumps(payload), headers=headers)
    response = connection.getresponse()
    body = response.read().decode("utf-8")
    connection.close()
    return response, json.loads(body) if body else {}


def _get(
    host: str,
    port: int,
    *,
    session_id: str | None = None,
) -> tuple[http.client.HTTPResponse, str]:
    headers = {
        "Accept": "text/event-stream",
        "Origin": "http://localhost",
        "MCP-Protocol-Version": "2025-11-25",
    }
    if session_id:
        headers["MCP-Session-Id"] = session_id
    connection = http.client.HTTPConnection(host, port, timeout=5)
    connection.request("GET", "/mcp", headers=headers)
    response = connection.getresponse()
    body = response.read().decode("utf-8")
    connection.close()
    return response, body
