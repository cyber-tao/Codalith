"""The single embedded store: LanceDB holds chunk metadata, text (BM25), and vectors (ANN).

One LanceDB database at ``store_dir`` with two tables:

* ``files``: one row per indexed file (``path``, ``content_hash``, ``mtime``, ``size``,
  ``language``, ``indexed_at``).
* ``chunks``: one row per chunk (``id``, ``path``, ``symbol``, ``kind``, ``language``,
  ``start_line``, ``end_line``, ``text``, ``vector``); BM25 runs over ``text`` and vector
  ANN over ``vector``.

``meta.json`` records the embedding model and dimension the vectors were built with. A store
is created empty and written once; an existing store is only ever opened for reading.

The integer ``chunks.id`` is the fusion/hydrate key. Writes are buffered and flushed in
batches because LanceDB is columnar and many tiny appends create severe fragment/version
bloat.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from codalith.semantic.types import Chunk, IndexStats

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)

_CHUNKS = "chunks"
_FILES = "files"
_META_FILE = "meta.json"
_SCHEMA_VERSION = 1
_FLUSH_ROWS = 8192
# LanceDB needs enough rows to train a vector ANN index; below this, brute-force is exact
# and fast, so we skip indexing (also keeps tiny test corpora on the exact path).
_ANN_MIN_ROWS = 256
_HYDRATE_COLS = [
    "id",
    "path",
    "symbol",
    "kind",
    "language",
    "start_line",
    "end_line",
    "text",
]
_FTS_TOKEN = re.compile(r"[A-Za-z0-9_]+")


class SemanticStoreError(Exception):
    """A store is missing, unreadable, read-only, or built with another embedding model."""


@dataclass(frozen=True, slots=True)
class StoreProvenance:
    model: str
    dimension: int

    def describe(self) -> str:
        return f"{self.model} ({self.dimension} dimensions)"


def read_provenance(store_dir: Path) -> StoreProvenance:
    """Read the embedding provenance a store records, without opening LanceDB."""

    path = store_dir / _META_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SemanticStoreError(
            f"Semantic store has no readable provenance {path}: {exc}"
        ) from exc
    model = raw.get("embed_model") if isinstance(raw, dict) else None
    dimension = raw.get("embed_dim") if isinstance(raw, dict) else None
    if (
        not isinstance(model, str)
        or not model
        or not isinstance(dimension, int)
        or isinstance(dimension, bool)
        or dimension <= 0
    ):
        raise SemanticStoreError(f"Semantic store provenance is invalid: {path}")
    return StoreProvenance(model, dimension)


def _fts_query(query: str) -> str:
    """Reduce an arbitrary query to space-separated tokens (defuses FTS operators)."""
    return " ".join(_FTS_TOKEN.findall(query))


class LanceStore:
    """LanceDB-backed chunk + file store with vector ANN and BM25 search."""

    def __init__(self, store_dir: Path, provenance: StoreProvenance) -> None:
        """Use :meth:`create` or :meth:`open`, which check the directory and provenance."""

        import lancedb

        self.provenance = provenance
        self._db = lancedb.connect(str(store_dir))
        self._lock = threading.RLock()
        self._chunks_buf: list[dict[str, Any]] = []
        self._files_buf: list[dict[str, Any]] = []
        self._next_id = 0

    @classmethod
    def create(cls, store_dir: Path, provenance: StoreProvenance) -> LanceStore:
        """Create an empty store; ``store_dir`` must not exist yet."""

        try:
            store_dir.mkdir(parents=True)
        except FileExistsError as exc:
            raise SemanticStoreError(f"Semantic store already exists: {store_dir}") from exc
        (store_dir / _META_FILE).write_text(
            json.dumps(
                {
                    "embed_model": provenance.model,
                    "embed_dim": provenance.dimension,
                    "schema_version": _SCHEMA_VERSION,
                }
            ),
            encoding="utf-8",
        )
        return cls(store_dir, provenance)

    @classmethod
    def open(cls, store_dir: Path, provenance: StoreProvenance) -> LanceStore:
        """Open an existing store for reading. Nothing is written to it."""

        recorded = read_provenance(store_dir)
        if recorded != provenance:
            raise SemanticStoreError(
                f"Semantic store {store_dir} was built with {recorded.describe()}; "
                f"refusing to open it with {provenance.describe()}"
            )
        return cls(store_dir, provenance)

    # --- schema ---

    def _chunks_schema(self) -> Any:
        import pyarrow as pa

        return pa.schema(
            [
                ("id", pa.int64()),
                ("path", pa.string()),
                ("symbol", pa.string()),
                ("kind", pa.string()),
                ("language", pa.string()),
                ("start_line", pa.int32()),
                ("end_line", pa.int32()),
                ("text", pa.string()),
                ("vector", pa.list_(pa.float32(), self.provenance.dimension)),
            ]
        )

    def _files_schema(self) -> Any:
        import pyarrow as pa

        return pa.schema(
            [
                ("path", pa.string()),
                ("content_hash", pa.string()),
                ("mtime", pa.float64()),
                ("size", pa.int64()),
                ("language", pa.string()),
                ("indexed_at", pa.float64()),
            ]
        )

    def _chunks_tbl(self) -> Any:
        if _CHUNKS not in self._db.table_names():
            return self._db.create_table(_CHUNKS, schema=self._chunks_schema())
        return self._db.open_table(_CHUNKS)

    def _files_tbl(self) -> Any:
        if _FILES not in self._db.table_names():
            return self._db.create_table(_FILES, schema=self._files_schema())
        return self._db.open_table(_FILES)

    def close(self) -> None:
        with self._lock:
            self._chunks_buf.clear()
            self._files_buf.clear()

    # --- buffered writes ---

    def _flush(self) -> None:
        if self._chunks_buf:
            self._chunks_tbl().add(self._chunks_buf)
            self._chunks_buf = []
        if self._files_buf:
            self._files_tbl().add(self._files_buf)
            self._files_buf = []

    def flush(self) -> None:
        with self._lock:
            self._flush()

    def write_file(
        self,
        rel: str,
        language: str,
        content_hash: str,
        mtime: float,
        size: int,
        chunks: Sequence[Chunk],
        vectors: np.ndarray | None,
    ) -> int:
        """Buffer one file's rows. Returns the number of chunks added."""
        import numpy as np

        with self._lock:
            mat = None
            if chunks:
                if vectors is None:
                    raise SemanticStoreError(f"Missing chunk vectors for {rel}")
                mat = np.ascontiguousarray(vectors, dtype="float32")
                expected_shape = (len(chunks), self.provenance.dimension)
                if mat.shape != expected_shape or not np.isfinite(mat).all():
                    raise SemanticStoreError(
                        f"Invalid chunk vectors for {rel}: expected finite {expected_shape}"
                    )
            added = 0
            if mat is not None:
                norms = np.linalg.norm(mat, axis=1, keepdims=True)
                mat = mat / np.where(norms == 0.0, 1.0, norms)
                for chunk, vec in zip(chunks, mat, strict=True):
                    self._chunks_buf.append(
                        {
                            "id": self._next_id,
                            "path": rel,
                            "symbol": chunk.symbol or "",
                            "kind": chunk.kind,
                            "language": chunk.language,
                            "start_line": int(chunk.start_line),
                            "end_line": int(chunk.end_line),
                            "text": chunk.text,
                            "vector": vec.tolist(),
                        }
                    )
                    self._next_id += 1
                    added += 1
            self._files_buf.append(
                {
                    "path": rel,
                    "content_hash": content_hash,
                    "mtime": float(mtime),
                    "size": int(size),
                    "language": language,
                    "indexed_at": time.time(),
                }
            )
            if len(self._chunks_buf) >= _FLUSH_ROWS:
                self._flush()
            return added

    def optimize(self) -> None:
        """Flush, compact, (re)build the BM25 index, and build the vector ANN index at scale."""
        with self._lock:
            self._flush()
            if _CHUNKS not in self._db.table_names():
                return
            tbl = self._db.open_table(_CHUNKS)
            try:
                tbl.optimize()
                tbl.cleanup_old_versions()
            except Exception:  # pragma: no cover - compaction is best-effort
                logger.exception("LanceDB optimize failed (continuing).")
            self._build_search_indexes(tbl)

    def _build_search_indexes(self, tbl: Any) -> None:
        """Build the FTS, scalar (id/path), and vector ANN indexes for ``tbl``.

        Each build is independent and best-effort: a failure is logged loudly and leaves
        that lookup on LanceDB's brute-force/scan fallback rather than aborting the others.
        """
        try:
            tbl.create_fts_index("text", replace=True)
        except Exception:  # pragma: no cover
            logger.exception("LanceDB FTS index build failed (lexical scan fallback).")
        # Scalar indexes turn ``id IN (...)`` hydration and path predicates into index
        # lookups instead of full-table scans that grow with the corpus.
        for col in ("id", "path"):
            try:
                tbl.create_scalar_index(col, replace=True)
            except Exception:  # pragma: no cover
                logger.exception(
                    "LanceDB scalar index on %s failed (scan fallback).", col
                )
        n = tbl.count_rows()
        if n < _ANN_MIN_ROWS:
            return
        try:
            nlist = max(1, min(int(4 * math.sqrt(n)), n // 39))
            tbl.create_index(
                metric="cosine",
                vector_column_name="vector",
                num_partitions=nlist,
                replace=True,
            )
        except Exception:  # pragma: no cover - falls back to brute-force search
            logger.exception(
                "LanceDB vector index build failed (brute-force fallback)."
            )

    # --- reads ---

    def file_hashes(self) -> dict[str, str]:
        """Content SHA-256 of every indexed file, keyed by POSIX relative path."""
        self.flush()
        if _FILES not in self._db.table_names():
            return {}
        tbl = self._db.open_table(_FILES)
        rows = (
            tbl.search()
            .select(["path", "content_hash"])
            .limit(max(1, tbl.count_rows()))
            .to_list()
        )
        return {
            str(r["path"]).replace("\\", "/"): str(r["content_hash"] or "") for r in rows
        }

    def vector_search(self, qvec: np.ndarray, k: int) -> list[tuple[int, float]]:
        import numpy as np

        if _CHUNKS not in self._db.table_names():
            return []
        q = np.asarray(qvec, dtype="float32").reshape(-1)
        norm = np.linalg.norm(q)
        if norm:
            q = q / norm
        tbl = self._db.open_table(_CHUNKS)
        if tbl.count_rows() == 0:
            return []
        rows = tbl.search(q.tolist()).metric("cosine").select(["id"]).limit(k).to_list()
        return [(int(r["id"]), 1.0 - float(r["_distance"])) for r in rows]

    def lexical_search(self, query: str, k: int) -> list[tuple[int, float]]:
        if _CHUNKS not in self._db.table_names():
            return []
        match = _fts_query(query)
        if not match:
            return []
        try:
            rows = (
                self._db.open_table(_CHUNKS)
                .search(match, query_type="fts")
                .select(["id"])
                .limit(k)
                .to_list()
            )
        except Exception:  # pragma: no cover - FTS index not built yet / query rejected
            return []
        return [(int(r["id"]), float(r["_score"])) for r in rows]

    def hydrate(self, ids: Sequence[int]) -> dict[int, dict[str, Any]]:
        if not ids or _CHUNKS not in self._db.table_names():
            return {}
        csv = ",".join(str(int(i)) for i in ids)
        rows = (
            self._db.open_table(_CHUNKS)
            .search()
            .where(f"id IN ({csv})")
            .select(_HYDRATE_COLS)
            .limit(len(ids))
            .to_list()
        )
        return {int(r["id"]): r for r in rows}

    def stats(self) -> IndexStats:
        self.flush()
        names = self._db.table_names()
        files = int(self._db.open_table(_FILES).count_rows()) if _FILES in names else 0
        chunks = (
            int(self._db.open_table(_CHUNKS).count_rows()) if _CHUNKS in names else 0
        )
        return IndexStats(total_files=files, total_chunks=chunks)
