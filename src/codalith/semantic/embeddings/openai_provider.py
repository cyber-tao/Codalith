"""OpenAI and OpenAI-compatible embedding provider.

Each chunk is embedded independently and requests are batched. The client and dimension are
resolved lazily.

Setting ``base_url`` points the same client at a self-hosted OpenAI-compatible server
(Ollama, vLLM, LM Studio, LocalAI, text-embeddings-inference, ...); such servers usually
don't require an API key, so one is optional when ``base_url`` is given.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from functools import cached_property
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "text-embedding-3-small"

# Known output dimensions; anything else is probed on first use.
_KNOWN_DIMS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}

_REQUEST_TIMEOUT_SECONDS = 600.0
_QWEN_QUERY_INSTRUCTION = (
    "Given a source code search query, retrieve relevant code snippets that answer the query."
)


class OpenAIEmbeddingProvider:
    name = "openai"

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        *,
        batch_size: int = 64,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._batch_size = max(1, batch_size)
        self._dim = _KNOWN_DIMS.get(model)

    @cached_property
    def _client(self) -> Any:
        from openai import OpenAI

        if not self._api_key and not self._base_url:
            raise RuntimeError(
                "OpenAI embeddings require an API key (CODALITH_EMBEDDING_API_KEY) or an "
                "OpenAI-compatible endpoint (CODALITH_EMBEDDING_BASE_URL)."
            )
        # Local OpenAI-compatible servers often ignore the key but the SDK still
        # requires a non-empty string, so pass a harmless placeholder when absent.
        return OpenAI(
            api_key=self._api_key or "not-needed",
            base_url=self._base_url,
            timeout=_REQUEST_TIMEOUT_SECONDS,
            max_retries=2,
        )

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = self.embed_query("probe").shape[0]
        return self._dim

    def _embed_batch(self, inputs: list[str]) -> np.ndarray:
        resp = self._client.embeddings.create(
            model=self._model,
            input=inputs,
            encoding_format="float",
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        if len(resp.data) != len(inputs):
            raise ValueError("Embedding response count does not match the input count")
        ordered: list[Any] = [None] * len(inputs)
        for item in resp.data:
            index = item.index
            if type(index) is not int or not 0 <= index < len(inputs):
                raise ValueError("Embedding response contains an invalid input index")
            if ordered[index] is not None:
                raise ValueError("Embedding response contains a duplicate input index")
            ordered[index] = item.embedding
        try:
            vectors = np.asarray(ordered, dtype="float32")
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Embedding response contains invalid vectors") from exc
        if vectors.ndim != 2 or vectors.shape[1] == 0:
            raise ValueError("Embedding response must contain non-empty one-dimensional vectors")
        dimension = vectors.shape[1]
        if self._dim is not None and dimension != self._dim:
            raise ValueError(
                f"Embedding response dimension {dimension} does not match expected dimension {self._dim}"
            )
        if not np.isfinite(vectors).all():
            raise ValueError("Embedding response contains non-finite values")
        self._dim = dimension
        return vectors

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype="float32")
        out: list[np.ndarray] = []
        for i in range(0, len(texts), self._batch_size):
            out.append(self._embed_batch(list(texts[i : i + self._batch_size])))
        return np.vstack(out).astype("float32")

    def embed_query(self, text: str) -> np.ndarray:
        if "qwen3-embedding" in self._model.lower():
            text = f"Instruct: {_QWEN_QUERY_INSTRUCTION}\nQuery: {text}"
        return self._embed_batch([text])[0]

    def close(self) -> None:
        client = self.__dict__.pop("_client", None)
        if client is not None:
            client.close()

    def prepare(self) -> None:
        _ = self._client
