from __future__ import annotations

import anyio
import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from test_mcp import running_http_server


@pytest.mark.parametrize("bound", [False, True])
def test_uri_tools_record_the_selected_corpus(environment_factory, bound):
    primary = environment_factory(semantic=False)
    other = environment_factory(corpus_id="other", semantic=False)
    primary.registry.corpora["other"] = other.corpus
    service = primary.service()
    try:
        definition = service.symbol("cache_value", target="other").definitions[0]
    finally:
        service.close()

    async def exercise(endpoint):
        address = endpoint + "/targets/other" if bound else endpoint
        async with streamable_http_client(address) as (read, write, _), ClientSession(read, write) as client:
            await client.initialize()
            for name, arguments in (
                ("codalith_read", {"uri": definition.source_uri}),
                ("codalith_graph", {"root_uri": definition.uri}),
            ):
                result = await client.call_tool(name, arguments)
                assert not result.isError, result
            invalid = await client.call_tool("codalith_read", {"uri": "not-a-codalith-uri"})
            assert invalid.isError
            assert invalid.structuredContent["error"]["code"] == "invalid_uri"

    with running_http_server(primary) as endpoint:
        anyio.run(exercise, endpoint)
        with httpx.Client(base_url=endpoint.removesuffix("/mcp"), trust_env=False) as client:
            default = client.get("/api/dashboard/snapshot", params={"target": "sample"}).json()
            selected = client.get("/api/dashboard/snapshot", params={"target": "other"}).json()
    assert default["summary"]["total_queries"] == (0 if bound else 1)
    assert selected["summary"]["total_queries"] == (3 if bound else 2)
    assert selected["summary"]["error_count"] == (1 if bound else 0)
    assert {item["name"] for item in selected["tools"]} == {"codalith_read", "codalith_graph"}
