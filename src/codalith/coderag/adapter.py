"""Stable internal adapter over CodeRAG retrieval.

The adapter can use the real ``coderag`` package when it is installed, but v0 also ships a
local deterministic fallback so Docker tests and policy validation do not depend on model
downloads or external services.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from codalith.corpus.registry import Corpus, CorpusRegistry
from codalith.errors import CodeRAGAdapterError, CorpusNotFoundError


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    source: str
    corpus_id: str
    uri: str
    path: str
    start_line: int
    end_line: int
    title: str
    snippet: str
    score: float
    kind: str = "window"
    language: str = "text"
    symbol: str | None = None
    module: str | None = None
    reason: str = "CodeRAG retrieval hit."
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class _IndexedFile:
    path: str
    full_path: Path
    text: str
    lines: list[str]


class CodeRAGAdapter:
    def __init__(
        self,
        registry: CorpusRegistry,
        *,
        prefer_native: bool | None = None,
    ) -> None:
        self.registry = registry
        self.prefer_native = (
            prefer_native
            if prefer_native is not None
            else os.getenv("CODALITH_USE_NATIVE_CODERAG", "").lower() in {"1", "true", "yes"}
        )
        self._native: dict[str, Any] = {}
        self._local: dict[str, list[_IndexedFile]] = {}
        self._indexed_at: dict[str, float] = {}

    def search_code(
        self,
        corpus_id: str,
        query: str,
        top_k: int = 8,
        filters: dict[str, Any] | None = None,
    ) -> list[RetrievalHit]:
        corpus = self._corpus(corpus_id)
        if self.prefer_native:
            try:
                return self._native_search(corpus, query, top_k, filters or {})
            except Exception as exc:
                if os.getenv("CODALITH_NATIVE_CODERAG_STRICT"):
                    raise CodeRAGAdapterError(str(exc)) from exc
        return self._local_search(corpus, query, top_k, filters or {})

    def search_files(
        self,
        corpus_id: str,
        pattern: str,
        *,
        target: str = "content",
        file_glob: str | None = None,
        limit: int = 50,
        ignore_case: bool = False,
    ) -> dict[str, Any]:
        corpus = self._corpus(corpus_id)
        files = self._ensure_local_index(corpus)
        if target == "files":
            matches = [
                {"path": item.path}
                for item in files
                if _glob_match(pattern, item.path) or _glob_match(pattern, Path(item.path).name)
            ]
            return {"pattern": pattern, "target": target, "count": len(matches[:limit]), "results": matches[:limit]}
        flags = re.IGNORECASE if ignore_case else 0
        regex = re.compile(pattern, flags)
        results: list[dict[str, Any]] = []
        for item in files:
            if file_glob and not _glob_match(file_glob, item.path):
                continue
            for number, line in enumerate(item.lines, start=1):
                if regex.search(line):
                    results.append({"path": item.path, "line": number, "text": line})
                    if len(results) >= limit:
                        return {
                            "pattern": pattern,
                            "target": target,
                            "count": len(results),
                            "results": results,
                        }
        return {"pattern": pattern, "target": target, "count": len(results), "results": results}

    def get_file(
        self,
        corpus_id: str,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        corpus = self._corpus(corpus_id)
        if self.prefer_native:
            try:
                native = self._native_instance(corpus)
                return str(native.get_file(path, start_line, end_line))
            except Exception as exc:
                if os.getenv("CODALITH_NATIVE_CODERAG_STRICT"):
                    raise CodeRAGAdapterError(str(exc)) from exc
        full = self._root(corpus) / path
        root = self._root(corpus).resolve()
        resolved = full.resolve()
        if root not in resolved.parents and resolved != root:
            raise CodeRAGAdapterError(f"Path escapes corpus root: {path}")
        if not resolved.exists() or not resolved.is_file():
            raise FileNotFoundError(f"Source file does not exist: {path}")
        lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
        if start_line is None and end_line is None:
            return "\n".join(lines)
        start = max(1, start_line or 1)
        end = min(len(lines), end_line or len(lines))
        if end < start:
            return ""
        return "\n".join(lines[start - 1 : end])

    def status(self, corpus_id: str) -> dict[str, Any]:
        corpus = self._corpus(corpus_id)
        if self.prefer_native:
            try:
                status = dict(self._native_instance(corpus).status())
                status["corpus_id"] = corpus_id
                self._add_index_timestamps(corpus, status)
                return status
            except Exception as exc:
                if os.getenv("CODALITH_NATIVE_CODERAG_STRICT"):
                    raise CodeRAGAdapterError(str(exc)) from exc
        files = self._ensure_local_index(corpus)
        indexed_at = self._indexed_at.get(corpus_id)
        return {
            "corpus_id": corpus_id,
            "provider": "local",
            "model": "deterministic-token-search",
            "watched_dir": str(self._root(corpus)),
            "store_dir": str(corpus.coderag_store),
            "total_files": len(files),
            "total_chunks": sum(max(1, (len(item.lines) + 79) // 80) for item in files),
            "indexed_at": indexed_at,
            "updated_at": _iso_timestamp(indexed_at),
        }

    def reindex(self, corpus_id: str, path: str | None = None, full: bool = False) -> dict[str, Any]:
        corpus = self._corpus(corpus_id)
        if self.prefer_native:
            try:
                native = self._native_instance(corpus)
                if os.getenv("CODALITH_NATIVE_CODERAG_PROGRESS", "").lower() in {
                    "1",
                    "true",
                    "yes",
                }:
                    target = Path(path).expanduser() if path else None
                    stats = native.indexer.index(target, full=full, progress=True)
                else:
                    stats = native.index(path, full=full)
                if hasattr(stats, "as_dict"):
                    result = dict(stats.as_dict())
                else:
                    result = dict(stats)
                self._indexed_at[corpus_id] = time.time()
                return result
            except Exception as exc:
                if os.getenv("CODALITH_NATIVE_CODERAG_STRICT"):
                    raise CodeRAGAdapterError(str(exc)) from exc
        self._local[corpus_id] = self._scan(corpus, path)
        self._indexed_at[corpus_id] = time.time()
        return self.status(corpus_id)

    def _native_search(
        self,
        corpus: Corpus,
        query: str,
        top_k: int,
        filters: dict[str, Any],
    ) -> list[RetrievalHit]:
        native = self._native_instance(corpus)
        hits = native.search(query, top_k=top_k)
        mapped: list[RetrievalHit] = []
        for hit in hits:
            path = str(hit.path)
            if filters.get("path_prefix") and not path.startswith(str(filters["path_prefix"])):
                continue
            mapped.append(
                RetrievalHit(
                    source="coderag",
                    corpus_id=corpus.corpus_id,
                    uri=_uri_for_hit(corpus, path, int(hit.start_line), int(hit.end_line)),
                    path=path,
                    start_line=int(hit.start_line),
                    end_line=int(hit.end_line),
                    title=f"{path}:{hit.start_line}-{hit.end_line}",
                    snippet=str(hit.text),
                    score=float(hit.score),
                    kind=str(hit.kind),
                    language=str(hit.language),
                    symbol=hit.symbol,
                    module=_module_from_path(path),
                    reason="CodeRAG hybrid retrieval hit.",
                    metadata={"coderag_similarity": float(hit.similarity)},
                )
            )
        return mapped

    def _native_instance(self, corpus: Corpus) -> Any:
        if corpus.corpus_id in self._native:
            return self._native[corpus.corpus_id]
        try:
            from coderag.api import CodeRAG  # type: ignore[import-not-found]
            from coderag.config import Config  # type: ignore[import-not-found]
        except Exception as exc:
            raise CodeRAGAdapterError("The coderag package is not installed") from exc
        _configure_native_chunk_limit()
        _configure_native_index_policy_hash()
        _configure_native_openai_timeout()
        _configure_native_batch_embedding()
        config = Config.from_env()
        config = _dataclass_replace(
            config,
            watched_dir=self._root(corpus),
            store_dir=_native_store_dir(corpus),
            index_all_text=True,
        )
        native = CodeRAG(config)
        self._native[corpus.corpus_id] = native
        return native

    def _local_search(
        self,
        corpus: Corpus,
        query: str,
        top_k: int,
        filters: dict[str, Any],
    ) -> list[RetrievalHit]:
        files = self._ensure_local_index(corpus)
        tokens = _tokens(query)
        scored: list[RetrievalHit] = []
        for item in files:
            if filters.get("path_prefix") and not item.path.startswith(str(filters["path_prefix"])):
                continue
            for start in range(1, len(item.lines) + 1, 70):
                end = min(len(item.lines), start + 79)
                text = "\n".join(item.lines[start - 1 : end])
                score = _score(tokens, text, item.path)
                if score <= 0:
                    continue
                scored.append(
                    RetrievalHit(
                        source="coderag-local",
                        corpus_id=corpus.corpus_id,
                        uri=_uri_for_hit(corpus, item.path, start, end),
                        path=item.path,
                        start_line=start,
                        end_line=end,
                        title=f"{item.path}:{start}-{end}",
                        snippet=text,
                        score=score,
                        kind="window",
                        language=_language(item.path),
                        module=_module_from_path(item.path),
                        reason="Local deterministic retrieval hit.",
                        metadata={"local_score": score},
                    )
                )
        return sorted(scored, key=lambda hit: hit.score, reverse=True)[:top_k]

    def _ensure_local_index(self, corpus: Corpus) -> list[_IndexedFile]:
        if corpus.corpus_id not in self._local:
            self.reindex(corpus.corpus_id)
        return self._local.get(corpus.corpus_id, [])

    def _scan(self, corpus: Corpus, subpath: str | None = None) -> list[_IndexedFile]:
        root = self._root(corpus)
        if not root.exists():
            return []
        scan_root = (root / subpath).resolve() if subpath else root
        if root.resolve() not in scan_root.parents and scan_root != root.resolve():
            raise CodeRAGAdapterError(f"Path escapes corpus root: {subpath}")
        files: list[_IndexedFile] = []
        paths = [scan_root] if scan_root.is_file() else _iter_text_paths(scan_root)
        for full_path in paths:
            if not full_path.is_file() or not _is_text_candidate(full_path):
                continue
            if any(part in _IGNORED_DIRS for part in full_path.parts):
                continue
            try:
                text = full_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            relative = full_path.relative_to(root).as_posix()
            files.append(
                _IndexedFile(
                    path=relative,
                    full_path=full_path,
                    text=text,
                    lines=text.splitlines(),
                )
            )
        return files

    def _corpus(self, corpus_id: str) -> Corpus:
        if corpus_id in self.registry.engines:
            return self.registry.engines[corpus_id]
        if corpus_id in self.registry.projects:
            return self.registry.projects[corpus_id]
        raise CorpusNotFoundError(f"Unknown corpus: {corpus_id}")

    @staticmethod
    def _root(corpus: Corpus) -> Path:
        return corpus.indexed_root if corpus.indexed_root.exists() else corpus.source_root

    def _add_index_timestamps(self, corpus: Corpus, status: dict[str, Any]) -> None:
        indexed_at = status.get("indexed_at")
        if not isinstance(indexed_at, int | float):
            indexed_at = self._indexed_at.get(corpus.corpus_id) or _latest_mtime(_native_store_dir(corpus))
            status["indexed_at"] = indexed_at
        if "updated_at" not in status:
            status["updated_at"] = _iso_timestamp(indexed_at)


_IGNORED_DIRS = {
    ".git",
    ".coderag",
    "Binaries",
    "Intermediate",
    "Saved",
    "DerivedDataCache",
    "ThirdParty",
}
_TEXT_SUFFIXES = {
    ".h",
    ".hpp",
    ".inl",
    ".cpp",
    ".c",
    ".cs",
    ".uplugin",
    ".uproject",
    ".ini",
    ".json",
    ".md",
    ".txt",
}


def _tokens(query: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}", query)]


def _iter_text_paths(scan_root: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(scan_root):
        dirnames[:] = [dirname for dirname in dirnames if dirname not in _IGNORED_DIRS]
        for filename in filenames:
            yield Path(dirpath) / filename


def _score(tokens: Iterable[str], text: str, path: str) -> float:
    haystack = f"{path}\n{text}".lower()
    score = 0.0
    for token in tokens:
        score += haystack.count(token) * (2.0 if token in path.lower() else 1.0)
    return score


def _is_text_candidate(path: Path) -> bool:
    return path.suffix.lower() in _TEXT_SUFFIXES or path.name in {"Build.cs", "Target.cs"}


def _language(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return {
        ".h": "cpp",
        ".hpp": "cpp",
        ".inl": "cpp",
        ".cpp": "cpp",
        ".c": "c",
        ".cs": "csharp",
        ".md": "markdown",
        ".json": "json",
    }.get(suffix, "text")


def _module_from_path(path: str) -> str | None:
    parts = path.split("/")
    if "Runtime" in parts:
        index = parts.index("Runtime")
        if index + 1 < len(parts):
            return parts[index + 1]
    if "Developer" in parts:
        index = parts.index("Developer")
        if index + 1 < len(parts):
            return parts[index + 1]
    if "Editor" in parts:
        index = parts.index("Editor")
        if index + 1 < len(parts):
            return parts[index + 1]
    return None


def _uri_for_hit(corpus: Corpus, path: str, start: int, end: int) -> str:
    if corpus.kind == "project":
        return f"ue-project://{corpus.corpus_id}/source/{path}#L{start}-L{end}"
    version = corpus.ue_version or corpus.corpus_id.removeprefix("ue-")
    return f"ue://{version}/source/{path}#L{start}-L{end}"


def _native_store_dir(corpus: Corpus) -> Path:
    return Path(os.environ.get("CODERAG_STORE_DIR", str(corpus.coderag_store)))


def _configure_native_chunk_limit() -> None:
    max_chars, max_bytes = _native_chunk_budget_from_env()
    if max_chars <= 0 and max_bytes <= 0:
        return

    import coderag.indexer as indexer  # type: ignore[import-not-found]

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

    import coderag.indexer as indexer

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

    import coderag.embeddings.openai_provider as openai_provider  # type: ignore[import-not-found]

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
    from typing import cast

    import coderag.indexer as indexer

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
        if len(texts) <= min_batch_chunks:
            raise
        midpoint = len(texts) // 2
        left = _embed_documents_with_split(provider, texts[:midpoint], min_batch_chunks)
        right = _embed_documents_with_split(provider, texts[midpoint:], min_batch_chunks)
        return [*(left or []), *(right or [])]


def _glob_match(pattern: str, path: str) -> bool:
    from fnmatch import fnmatchcase

    return fnmatchcase(path, pattern) or fnmatchcase(Path(path).name, pattern)


def _dataclass_replace(obj: Any, **changes: Any) -> Any:
    from dataclasses import replace

    return replace(obj, **changes)


def _latest_mtime(path: Path) -> float | None:
    if not path.exists():
        return None
    latest = path.stat().st_mtime
    for child in path.rglob("*"):
        try:
            latest = max(latest, child.stat().st_mtime)
        except OSError:
            continue
    return latest


def _iso_timestamp(timestamp: object) -> str | None:
    if not isinstance(timestamp, int | float):
        return None
    return datetime.fromtimestamp(float(timestamp), UTC).isoformat()
