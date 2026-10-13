from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import numpy as np
import pytest
from openai import OpenAI

from codalith.semantic.config import Config
from codalith.semantic.embeddings import get_provider
from codalith.semantic.embeddings.openai_provider import OpenAIEmbeddingProvider


def _stub_embeddings(
    provider: OpenAIEmbeddingProvider,
    responses: list[list[tuple[object, object]]],
) -> Mock:
    create = Mock(
        side_effect=[
            SimpleNamespace(
                data=[SimpleNamespace(index=index, embedding=vector) for index, vector in rows]
            )
            for rows in responses
        ]
    )
    provider.__dict__["_client"] = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    return create


def test_provider_factory_honors_configured_batch_size() -> None:
    provider = get_provider(
        Config(
            store_dir=Path("unused"),
            provider="openai",
            model="local-test",
            openai_base_url="http://localhost:11434/v1",
            embed_batch_size=2,
        )
    )
    assert isinstance(provider, OpenAIEmbeddingProvider)
    create = _stub_embeddings(
        provider,
        [
            [(0, [1.0, 0.0]), (1, [2.0, 0.0])],
            [(0, [3.0, 0.0]), (1, [4.0, 0.0])],
            [(0, [5.0, 0.0])],
        ],
    )
    vectors = provider.embed_documents(["a", "b", "c", "d", "e"])
    assert [call.kwargs["input"] for call in create.call_args_list] == [
        ["a", "b"],
        ["c", "d"],
        ["e"],
    ]
    np.testing.assert_array_equal(vectors[:, 0], [1, 2, 3, 4, 5])
    assert vectors.dtype == np.float32
    assert provider.dim == 2


@pytest.mark.parametrize("batch_size", [0, -1])
def test_nonpositive_batch_sizes_still_process_every_document(batch_size: int) -> None:
    provider = OpenAIEmbeddingProvider("local-test", batch_size=batch_size)
    create = _stub_embeddings(provider, [[(0, [1.0])], [(0, [2.0])]])
    assert provider.embed_documents(["a", "b"]).shape == (2, 1)
    assert [call.kwargs["input"] for call in create.call_args_list] == [["a"], ["b"]]


@pytest.mark.parametrize(
    "model", ["qwen3-embedding:0.6b", "qwen3-embedding:4b", "Qwen/Qwen3-Embedding-8B"]
)
def test_qwen_adds_a_general_query_instruction_and_keeps_documents_unchanged(model: str) -> None:
    provider = OpenAIEmbeddingProvider(model)
    create = _stub_embeddings(provider, [[(0, [1.0, 0.0])], [(0, [0.0, 1.0])]])
    provider.embed_documents(["class Cache {};"])
    provider.embed_query("如何清空缓存？")
    assert create.call_args_list[0].kwargs["input"] == ["class Cache {};"]
    assert create.call_args_list[1].kwargs["input"] == [
        "Instruct: Given a source code search query, retrieve relevant code snippets that answer the query."
        "\nQuery: 如何清空缓存？"
    ]


def test_other_models_receive_the_original_query() -> None:
    provider = OpenAIEmbeddingProvider("local-test")
    create = _stub_embeddings(provider, [[(0, [1.0, 0.0])]])
    provider.embed_query("cache invalidation")
    assert create.call_args.kwargs["input"] == ["cache invalidation"]


def test_out_of_order_response_indices_preserve_document_alignment() -> None:
    provider = OpenAIEmbeddingProvider("local-test")
    _stub_embeddings(provider, [[(1, [2.0, 3.0]), (0, [0.0, 1.0])]])
    np.testing.assert_array_equal(
        provider.embed_documents(["first", "second"]), [[0.0, 1.0], [2.0, 3.0]]
    )


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([(0, [1.0, 0.0])], "count"),
        ([(0, [1.0, 0.0]), (0, [2.0, 0.0])], "duplicate"),
        ([(-1, [1.0, 0.0]), (1, [2.0, 0.0])], "invalid input index"),
        ([(0, [1.0, 0.0]), (2, [2.0, 0.0])], "invalid input index"),
        ([(False, [1.0, 0.0]), (1, [2.0, 0.0])], "invalid input index"),
        ([(0, [1.0, 0.0]), (1.0, [2.0, 0.0])], "invalid input index"),
        ([(0, [1.0, 0.0]), ("1", [2.0, 0.0])], "invalid input index"),
        ([(0, []), (1, [])], "non-empty"),
        ([(0, 1.0), (1, 2.0)], "one-dimensional"),
        ([(0, [[1.0]]), (1, [[2.0]])], "one-dimensional"),
        ([(0, [1.0]), (1, [1.0, 2.0])], "invalid vectors"),
        ([(0, ["invalid"]), (1, [1.0])], "invalid vectors"),
        ([(0, [float("nan")]), (1, [1.0])], "non-finite"),
        ([(0, [float("inf")]), (1, [1.0])], "non-finite"),
        ([(0, [float("-inf")]), (1, [1.0])], "non-finite"),
    ],
)
def test_invalid_responses_fail_without_retrying_or_caching_a_dimension(
    rows: list[tuple[object, object]], message: str
) -> None:
    provider = OpenAIEmbeddingProvider("local-test")
    create = _stub_embeddings(provider, [rows])
    with pytest.raises(ValueError, match=message):
        provider.embed_documents(["first", "second"])
    assert create.call_count == 1
    assert provider._dim is None


def test_every_batch_and_query_must_match_the_discovered_dimension() -> None:
    provider = OpenAIEmbeddingProvider("local-test", batch_size=1)
    _stub_embeddings(provider, [[(0, [1.0, 2.0])], [(0, [1.0, 2.0, 3.0])]])
    with pytest.raises(ValueError, match="dimension 3 does not match expected dimension 2"):
        provider.embed_documents(["first", "second"])
    assert provider.dim == 2
    _stub_embeddings(provider, [[(0, [1.0])]])
    with pytest.raises(ValueError, match="dimension 1 does not match expected dimension 2"):
        provider.embed_query("query")


def test_known_openai_dimension_is_validated_without_probing() -> None:
    provider = OpenAIEmbeddingProvider("text-embedding-3-small")
    assert provider.dim == 1536
    _stub_embeddings(provider, [[(0, [1.0, 2.0])]])
    with pytest.raises(ValueError, match="expected dimension 1536"):
        provider.embed_query("query")


def test_empty_documents_have_the_resolved_dimension() -> None:
    provider = OpenAIEmbeddingProvider("local-test")
    create = _stub_embeddings(provider, [[(0, [1.0, 2.0, 3.0])]])
    assert provider.embed_documents([]).shape == (0, 3)
    assert provider.embed_documents([]).shape == (0, 3)
    assert create.call_count == 1


def test_local_client_uses_a_placeholder_key_and_allows_cold_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructor = Mock()
    monkeypatch.setattr("openai.OpenAI", constructor)
    provider = OpenAIEmbeddingProvider("qwen3-embedding:4b", base_url="http://localhost:11434/v1")
    assert not constructor.called
    assert provider._client is constructor.return_value
    assert constructor.call_args.kwargs == {
        "api_key": "not-needed",
        "base_url": "http://localhost:11434/v1",
        "timeout": 600.0,
        "max_retries": 2,
    }


@pytest.mark.parametrize("status", [429, 503])
def test_sdk_retries_transient_failures_and_requests_float_vectors(status: int) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(status, headers={"retry-after-ms": "1"}, json={"error": "busy"})
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "local-test",
                "data": [{"object": "embedding", "index": 0, "embedding": [1.0, 2.0]}],
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            },
        )

    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as http_client,
        OpenAI(api_key="test", base_url="http://local.test/v1", http_client=http_client) as client,
    ):
        provider = OpenAIEmbeddingProvider("local-test")
        provider.__dict__["_client"] = client
        np.testing.assert_array_equal(provider.embed_query("query"), [1.0, 2.0])
    assert len(requests) == 2
    assert json.loads(requests[-1].content) == {
        "model": "local-test",
        "input": ["query"],
        "encoding_format": "float",
    }
    assert requests[-1].extensions["timeout"]["read"] == 600.0
