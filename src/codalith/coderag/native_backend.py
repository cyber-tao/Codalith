"""Native CodeRAG retrieval backend with store provenance validation."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codalith.coderag.native import load_native_instance, native_store_dir
from codalith.coderag.types import RetrievalHit, module_from_path
from codalith.config import load_config
from codalith.corpus.registry import Corpus
from codalith.corpus.store_manifest import StoreManifest, load_store_manifest
from codalith.corpus.uris import source_uri
from codalith.errors import CodeRAGAdapterError

MAX_FILTER_FETCH = 200


class NativeCodeRAGBackend:
    name = "native"

    def __init__(self) -> None:
        self._instances: dict[str, Any] = {}
        self._indexed_at: dict[str, float] = {}
        self._validated: set[str] = set()

    def search(
        self,
        corpus: Corpus,
        query: str,
        *,
        top_k: int,
        path_prefix: str | None = None,
    ) -> list[RetrievalHit]:
        native = self._instance(corpus, require_store=True)
        fetch_k = top_k
        if path_prefix:
            fetch_k = min(MAX_FILTER_FETCH, max(top_k * 8, 32))
        hits = native.search(query, top_k=fetch_k)
        mapped: list[RetrievalHit] = []
        for hit in hits:
            path = str(hit.path).replace("\\", "/")
            if path_prefix and not path.startswith(path_prefix):
                continue
            mapped.append(_map_hit(corpus, hit, path))
            if len(mapped) >= top_k:
                break
        return mapped

    def reindex(
        self,
        corpus: Corpus,
        *,
        path: str | None = None,
        full: bool = False,
    ) -> dict[str, object]:
        native = self._instance(corpus, require_store=False)
        if _env_flag("CODALITH_NATIVE_CODERAG_PROGRESS"):
            target = Path(path).expanduser() if path else None
            stats = native.indexer.index(target, full=full, progress=True)
        else:
            stats = native.index(path, full=full)
        result = dict(stats.as_dict()) if hasattr(stats, "as_dict") else dict(stats)
        self._indexed_at[corpus.corpus_id] = time.time()
        self._validate_store(corpus)
        return result

    def status(self, corpus: Corpus) -> dict[str, object]:
        native = self._instance(corpus, require_store=True)
        status: dict[str, object] = dict(native.status())
        status["corpus_id"] = corpus.corpus_id
        status["backend"] = self.name
        indexed_at = status.get("indexed_at")
        if not isinstance(indexed_at, int | float):
            indexed_at = self._indexed_at.get(corpus.corpus_id) or _latest_mtime(
                native_store_dir(corpus)
            )
            status["indexed_at"] = indexed_at
        if "updated_at" not in status:
            status["updated_at"] = _iso_timestamp(indexed_at)
        manifest = load_store_manifest(corpus)
        if manifest is not None:
            status["store_manifest"] = _manifest_status(manifest)
        return status

    def _instance(self, corpus: Corpus, *, require_store: bool) -> Any:
        if require_store:
            self._validate_store(corpus)
        instance = self._instances.get(corpus.corpus_id)
        if instance is None:
            instance = load_native_instance(corpus, _corpus_root(corpus))
            self._instances[corpus.corpus_id] = instance
        return instance

    def _validate_store(self, corpus: Corpus) -> None:
        if corpus.corpus_id in self._validated:
            return
        validate_native_store(corpus)
        self._validated.add(corpus.corpus_id)


def _map_hit(corpus: Corpus, hit: Any, path: str) -> RetrievalHit:
    return RetrievalHit(
        source="coderag",
        corpus_id=corpus.corpus_id,
        uri=source_uri(corpus.corpus_id, path, int(hit.start_line), int(hit.end_line)),
        path=path,
        start_line=int(hit.start_line),
        end_line=int(hit.end_line),
        title=f"{path}:{hit.start_line}-{hit.end_line}",
        snippet=str(hit.text),
        score=float(hit.score),
        kind=str(hit.kind),
        language=str(hit.language),
        symbol=hit.symbol,
        module=module_from_path(path, corpus.module_roots),
        reason="CodeRAG hybrid retrieval hit.",
        metadata={"coderag_similarity": float(hit.similarity)},
    )


def _manifest_status(manifest: StoreManifest) -> dict[str, object]:
    return {
        "corpus_id": manifest.corpus_id,
        "source_revision": manifest.source_revision,
        "embedding_model": manifest.embedding_model,
        "embedding_dimension": manifest.embedding_dimension,
        "store_schema_version": manifest.store_schema_version,
        "chunk_policy": manifest.chunk_policy,
        "validated": True,
    }


def validate_native_store(corpus: Corpus) -> StoreManifest | None:
    manifest = load_store_manifest(corpus)
    if manifest is None:
        return None
    metadata_path = native_store_dir(corpus) / "meta.json"
    if not metadata_path.is_file():
        raise CodeRAGAdapterError(
            f"Native store metadata does not exist for {corpus.corpus_id}: {metadata_path}"
        )
    metadata = load_config(metadata_path)
    expected = {
        "embed_model": manifest.embedding_model,
        "embed_dim": manifest.embedding_dimension,
        "schema_version": manifest.store_schema_version,
    }
    mismatches = [
        f"{key}={metadata.get(key)!r} (expected {value!r})"
        for key, value in expected.items()
        if metadata.get(key) != value
    ]
    if mismatches:
        raise CodeRAGAdapterError(
            f"Native store manifest mismatch for {corpus.corpus_id}: "
            + ", ".join(mismatches)
        )
    return manifest


def _corpus_root(corpus: Corpus) -> Path:
    return corpus.indexed_root if corpus.indexed_root.exists() else corpus.source_root


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


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}
