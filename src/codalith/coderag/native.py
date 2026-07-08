"""Native CodeRAG instance construction and runtime configuration bridges.

The native ``coderag`` package reads its configuration from environment
variables and module attributes, so Codalith applies its own settings by
patching the relevant hooks before instantiating ``CodeRAG``. All patches are
idempotent and keyed on the applied configuration.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import replace
from hashlib import sha256
from importlib import import_module
from pathlib import Path
from typing import Any, cast

from codalith.corpus.registry import Corpus
from codalith.errors import CodeRAGAdapterError


def native_store_dir(corpus: Corpus) -> Path:
    return corpus.coderag_store


def load_native_instance(corpus: Corpus, watched_dir: Path) -> Any:
    try:
        api_module: Any = import_module("coderag.api")
        config_module: Any = import_module("coderag.config")
        CodeRAG = api_module.CodeRAG
        Config = config_module.Config
    except Exception as exc:
        raise CodeRAGAdapterError("The coderag package is not installed") from exc
    _configure_native_env_aliases()
    _configure_native_chunk_limit()
    _configure_native_index_policy_hash()
    _configure_native_openai_timeout()
    _configure_native_batch_embedding()
    config = Config.from_env()
    config = replace(
        config,
        watched_dir=watched_dir,
        store_dir=native_store_dir(corpus),
        index_all_text=True,
    )
    return CodeRAG(config)


def _configure_native_env_aliases() -> None:
    _set_env_default("CODERAG_PROVIDER", "CODALITH_CODERAG_PROVIDER")
    _set_env_default("CODERAG_OPENAI_MODEL", "CODALITH_CODERAG_EMBEDDING_MODEL")
    _set_env_default("CODERAG_OPENAI_BATCH", "CODALITH_CODERAG_EMBEDDING_BATCH_SIZE")
    _set_env_default("CODERAG_WORKERS", "CODALITH_CODERAG_WORKERS")


def _set_env_default(target: str, source: str) -> None:
    if os.getenv(target):
        return
    value = os.getenv(source)
    if value:
        os.environ[target] = value


def _configure_native_chunk_limit() -> None:
    max_chars, max_bytes = _native_chunk_budget_from_env()
    if max_chars <= 0 and max_bytes <= 0:
        return

    indexer: Any = import_module("coderag.indexer")

    budget = (max_chars, max_bytes)
    if getattr(indexer.chunk_file, "_codalith_chunk_budget", None) == budget:
        return
    original = getattr(indexer.chunk_file, "_codalith_original", indexer.chunk_file)

    def limited_chunk_file(text: str, language: str, config: Any) -> list[Any]:
        return _limit_chunk_texts(original(text, language, config), max_chars, max_bytes)

    limited_chunk_file._codalith_original = original  # type: ignore[attr-defined]
    limited_chunk_file._codalith_chunk_budget = budget  # type: ignore[attr-defined]
    indexer.chunk_file = limited_chunk_file


def _native_chunk_budget_from_env() -> tuple[int, int]:
    max_chars = _parse_positive_env_int("CODALITH_CODERAG_MAX_CHUNK_CHARS")
    max_bytes = _parse_positive_env_int("CODALITH_CODERAG_MAX_CHUNK_BYTES")
    return max_chars, max_bytes


def _parse_positive_env_int(key: str) -> int:
    raw = os.getenv(key)
    if not raw:
        return 0
    try:
        value = int(raw)
    except ValueError as exc:
        raise CodeRAGAdapterError(f"{key} must be an integer") from exc
    return max(0, value)


def _limit_chunk_texts(chunks: list[Any], max_chars: int, max_bytes: int = 0) -> list[Any]:
    limited: list[Any] = []
    for chunk in chunks:
        limited.extend(_split_chunk_by_budget(chunk, max_chars, max_bytes))
    return limited


def _split_chunk_by_budget(chunk: Any, max_chars: int, max_bytes: int) -> list[Any]:
    text = str(chunk.text)
    if _within_chunk_budget(text, max_chars, max_bytes):
        return [chunk]
    start_line = int(getattr(chunk, "start_line", 1))
    lines = text.split("\n")
    if len(lines) == 1:
        return [
            _replace_chunk_text(chunk, part, start_line, start_line)
            for part in _split_text_by_budget(text, max_chars, max_bytes)
        ]

    result: list[Any] = []
    current: list[str] = []
    current_start = start_line
    for offset, line in enumerate(lines):
        line_no = start_line + offset
        candidate = line if not current else "\n".join([*current, line])
        if current and not _within_chunk_budget(candidate, max_chars, max_bytes):
            result.append(
                _replace_chunk_text(chunk, "\n".join(current), current_start, line_no - 1)
            )
            current = [line]
            current_start = line_no
        else:
            current.append(line)

        if current and len(current) == 1 and not _within_chunk_budget(
            current[0], max_chars, max_bytes
        ):
            result.extend(
                _replace_chunk_text(chunk, part, line_no, line_no)
                for part in _split_text_by_budget(current[0], max_chars, max_bytes)
            )
            current = []
            current_start = line_no + 1

    if current:
        result.append(
            _replace_chunk_text(
                chunk,
                "\n".join(current),
                current_start,
                start_line + len(lines) - 1,
            )
        )
    return result


def _within_chunk_budget(text: str, max_chars: int, max_bytes: int) -> bool:
    return (max_chars <= 0 or len(text) <= max_chars) and (
        max_bytes <= 0 or len(text.encode("utf-8")) <= max_bytes
    )


def _split_text_by_budget(text: str, max_chars: int, max_bytes: int) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    current_bytes = 0
    for char in text:
        char_bytes = len(char.encode("utf-8"))
        would_exceed = (
            (max_chars > 0 and len(current) + 1 > max_chars)
            or (max_bytes > 0 and current_bytes + char_bytes > max_bytes)
        )
        if current and would_exceed:
            parts.append("".join(current))
            current = []
            current_bytes = 0
        current.append(char)
        current_bytes += char_bytes
    if current:
        parts.append("".join(current))
    return parts


def _replace_chunk_text(chunk: Any, text: str, start_line: int, end_line: int) -> Any:
    values: dict[str, Any] = {"text": text}
    if hasattr(chunk, "start_line"):
        values["start_line"] = start_line
    if hasattr(chunk, "end_line"):
        values["end_line"] = end_line
    return replace(chunk, **values)


def _configure_native_index_policy_hash() -> None:
    signature = _native_chunk_policy_signature()
    if not signature:
        return

    indexer: Any = import_module("coderag.indexer")

    current = indexer.Indexer._maybe_work
    if getattr(current, "_codalith_chunk_policy_signature", None) == signature:
        return
    original = getattr(current, "_codalith_original", current)
    prefix = _policy_hash_prefix(signature)

    def maybe_work(
        self: Any,
        abs_path: Path,
        rel: str,
        language: str,
        metas: dict[str, dict[str, Any]],
    ) -> Any:
        effective_metas = metas
        existing = metas.get(rel)
        if existing is not None and not str(existing.get("content_hash", "")).startswith(prefix):
            effective_metas = dict(metas)
            forced = dict(existing)
            forced["content_hash"] = ""
            forced["mtime"] = None
            effective_metas[rel] = forced
        item = original(self, abs_path, rel, language, effective_metas)
        if item is None:
            return None
        return replace(item, content_hash=_policy_content_hash(signature, str(item.content_hash)))

    maybe_work._codalith_original = original  # type: ignore[attr-defined]
    maybe_work._codalith_chunk_policy_signature = signature  # type: ignore[attr-defined]
    indexer.Indexer._maybe_work = maybe_work


def _native_chunk_policy_signature() -> str | None:
    max_chars, max_bytes = _native_chunk_budget_from_env()
    if max_chars <= 0 and max_bytes <= 0:
        return None
    return f"chunk-budget:chars={max_chars}:bytes={max_bytes}"


def _policy_hash_prefix(signature: str) -> str:
    return f"codalith-v1:{sha256(signature.encode('utf-8')).hexdigest()[:16]}:"


def _policy_content_hash(signature: str, source_hash: str) -> str:
    return f"{_policy_hash_prefix(signature)}{source_hash}"


def _configure_native_openai_timeout() -> None:
    raw = os.getenv("CODALITH_CODERAG_OPENAI_TIMEOUT_SECONDS")
    if not raw:
        return
    try:
        timeout_seconds = float(raw)
    except ValueError as exc:
        raise CodeRAGAdapterError("CODALITH_CODERAG_OPENAI_TIMEOUT_SECONDS must be a number") from exc
    if timeout_seconds <= 0:
        return
    try:
        retry_attempts = int(os.getenv("CODALITH_CODERAG_OPENAI_RETRY_ATTEMPTS", "3"))
    except ValueError as exc:
        raise CodeRAGAdapterError("CODALITH_CODERAG_OPENAI_RETRY_ATTEMPTS must be an integer") from exc
    retry_attempts = max(1, retry_attempts)

    openai_provider: Any = import_module("coderag.embeddings.openai_provider")

    np_module = openai_provider.np
    retry = openai_provider.retry
    stop_after_attempt = openai_provider.stop_after_attempt
    wait_exponential = openai_provider.wait_exponential

    current = openai_provider.OpenAIEmbeddingProvider._embed_batch
    if (
        getattr(current, "_codalith_openai_timeout_seconds", None) == timeout_seconds
        and getattr(current, "_codalith_openai_retry_attempts", None) == retry_attempts
    ):
        return

    def embed_batch(self: Any, inputs: list[str]) -> Any:
        resp = self._client.embeddings.create(
            model=self._model,
            input=inputs,
            timeout=timeout_seconds,
        )
        return np_module.array([item.embedding for item in resp.data], dtype="float32")

    patched = retry(
        stop=stop_after_attempt(retry_attempts),
        wait=wait_exponential(multiplier=0.5, max=8),
        reraise=True,
    )(embed_batch)
    patched._codalith_openai_timeout_seconds = timeout_seconds
    patched._codalith_openai_retry_attempts = retry_attempts
    openai_provider.OpenAIEmbeddingProvider._embed_batch = patched


def _configure_native_batch_embedding() -> None:
    raw = os.getenv("CODALITH_CODERAG_BATCH_CHUNKS")
    if not raw:
        return
    try:
        batch_chunks = int(raw)
    except ValueError as exc:
        raise CodeRAGAdapterError("CODALITH_CODERAG_BATCH_CHUNKS must be an integer") from exc
    if batch_chunks <= 1:
        return
    raw_concurrency = os.getenv("CODALITH_CODERAG_BATCH_CONCURRENCY")
    try:
        batch_concurrency = int(raw_concurrency) if raw_concurrency else 1
    except ValueError as exc:
        raise CodeRAGAdapterError("CODALITH_CODERAG_BATCH_CONCURRENCY must be an integer") from exc
    batch_concurrency = max(1, batch_concurrency)
    raw_min_batch = os.getenv("CODALITH_CODERAG_EMBED_MIN_BATCH_CHUNKS")
    try:
        min_batch_chunks = int(raw_min_batch) if raw_min_batch else 1
    except ValueError as exc:
        raise CodeRAGAdapterError("CODALITH_CODERAG_EMBED_MIN_BATCH_CHUNKS must be an integer") from exc
    min_batch_chunks = max(1, min_batch_chunks)

    from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
    indexer: Any = import_module("coderag.indexer")

    current = indexer.Indexer._embed_and_write
    if (
        getattr(current, "_codalith_batch_chunks", None) == batch_chunks
        and getattr(current, "_codalith_batch_concurrency", None) == batch_concurrency
    ):
        return

    def batched_embed_and_write(self: Any, work: list[Any], *, reporter: Any) -> Iterator[Any]:
        if not work:
            return
        total = len(work)
        done = 0
        Group = list[tuple[Any, list[Any], int]]

        def groups() -> Iterator[tuple[Group, list[str]]]:
            pending: Group = []
            texts: list[str] = []
            for item in work:
                chunks = indexer.chunk_file(item.text, item.language, self.config)
                pending.append((item, chunks, len(chunks)))
                texts.extend(str(chunk.text) for chunk in chunks)
                if len(texts) >= batch_chunks:
                    yield pending, texts
                    pending = []
                    texts = []
            if pending:
                yield pending, texts

        def write_group(pending: Group, vectors: Any | None) -> Iterator[Any]:
            nonlocal done
            offset = 0
            for item, chunks, count in pending:
                item_vectors = vectors[offset : offset + count] if vectors is not None else None
                offset += count
                added, removed = self._write(item, chunks, item_vectors)
                yield item, added, removed
                done += 1
                reporter.update(f"Embedding {done}/{total} file(s)...")

        if batch_concurrency == 1:
            for pending, texts in groups():
                vectors = _embed_documents_with_split(self.provider, texts, min_batch_chunks)
                yield from write_group(pending, vectors)
            return

        def embed(texts: list[str]) -> Any:
            return _embed_documents_with_split(self.provider, texts, min_batch_chunks)

        group_iter = groups()
        with ThreadPoolExecutor(max_workers=batch_concurrency) as pool:
            inflight: dict[Future[Any], Group] = {}

            def submit_until_full() -> None:
                while len(inflight) < batch_concurrency:
                    try:
                        pending, texts = next(group_iter)
                    except StopIteration:
                        return
                    inflight[pool.submit(embed, texts)] = pending

            submit_until_full()
            while inflight:
                finished, _ = wait(inflight, return_when=FIRST_COMPLETED)
                for future in finished:
                    pending = inflight.pop(future)
                    yield from write_group(pending, future.result())
                submit_until_full()

    cast(Any, batched_embed_and_write)._codalith_batch_chunks = batch_chunks
    cast(Any, batched_embed_and_write)._codalith_batch_concurrency = batch_concurrency
    indexer.Indexer._embed_and_write = batched_embed_and_write


def _embed_documents_with_split(provider: Any, texts: list[str], min_batch_chunks: int = 1) -> Any | None:
    if not texts:
        return None
    try:
        return provider.embed_documents(texts)
    except Exception:
        # Oversized or provider-rejected batches are bisected until they fit;
        # the smallest batch re-raises so real failures still surface.
        if len(texts) <= min_batch_chunks:
            raise
        midpoint = len(texts) // 2
        left = _embed_documents_with_split(provider, texts[:midpoint], min_batch_chunks)
        right = _embed_documents_with_split(provider, texts[midpoint:], min_batch_chunks)
        return [*(left or []), *(right or [])]
