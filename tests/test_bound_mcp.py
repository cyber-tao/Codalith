from __future__ import annotations

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from test_mcp import running_http_server


def test_bound_http_enforces_target_for_tools_resources_and_catalog(semantic_environment) -> None:
    async def exercise(endpoint: str) -> None:
        async with streamable_http_client(endpoint + "/targets/sample") as (read, write, _), ClientSession(read, write) as client:
            initialized = await client.initialize()
            assert "Default target: sample" in initialized.instructions
            status = await client.call_tool("codalith_status", {})
            assert status.structuredContent["target"] == "sample"
            catalog = await client.call_tool("codalith_catalog", {})
            assert [row["target_id"] for row in catalog.structuredContent["versions"]] == ["sample"]
            for name, arguments in [
                ("codalith_status", {"target": "other"}),
                ("codalith_read", {"uri": "codalith://other/source/a.py#L1-L2"}),
                ("codalith_graph", {"root_uri": "codalith://other/symbol/abc"}),
                ("codalith_compare", {"from_corpus": "sample", "to_corpus": "sample"}),
            ]:
                response = await client.call_tool(name, arguments)
                assert response.isError
                assert response.structuredContent["error"]["code"] == "invalid_arguments"
            pack = await client.call_tool("codalith_context", {"query": "CachedValue"})
            assert pack.structuredContent["sources"]
            uri = pack.structuredContent["sources"][0]["uri"]
            result = await client.read_resource(uri)
            assert result.contents

    with running_http_server(semantic_environment) as endpoint:
        with httpx.Client(trust_env=False) as client:
            catalog = client.get(endpoint.removesuffix("/mcp") + "/api/catalog").json()
            assert catalog["versions"][0]["target_id"] == "sample"
            assert client.post(endpoint + "/targets/unknown", json={}).status_code == 404
        anyio.run(exercise, endpoint)


def test_empty_management_registry_starts_and_reports_not_ready(tmp_path) -> None:
    from starlette.testclient import TestClient

    from codalith.corpus.registry import CorpusRegistry
    from codalith.corpus.source_policy import SourcePolicy
    from codalith.mcp.http import HTTPConfig, create_http_app
    from codalith.query.service import QueryService

    path = tmp_path / "registry.toml"
    path.write_text("schema_version = 2\n[management]\nenabled = true\n")
    registry = CorpusRegistry.from_file(path)
    policy = SourcePolicy.from_file("configs/source-policy.toml")
    with TestClient(create_http_app(QueryService(registry, policy), HTTPConfig()), base_url="http://127.0.0.1") as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 503
        assert client.get("/api/catalog").json() == {"versions": []}
