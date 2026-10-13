"""The single embedded store: LanceDB holds chunk metadata, text (BM25), and vectors (ANN).

One LanceDB database at ``store_dir`` with two tables:

* ``files``: one row per indexed file (``path``, ``content_hash``, ``mtime``, ``size``,
  ``language``, ``indexed_at``).
* ``chunks``: one row per chunk (``id``, ``path``, ``symbol``, ``kind``, ``language``,
  ``start_line``, ``end_line``, ``text``, ``vector``); BM25 runs over ``text`` and vector
  ANN over ``vector``.

The integer ``chunks.id`` is the fusion/hydrate key. Writes are buffered and flushed in
batches because LanceDB is columnar and many tiny appends create severe fragment/version
bloat. Reads query committed data only.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from codalith.semantic.types import Chunk, IndexStats

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


def _fts_query(query: str) -> str:
    """Reduce an arbitrary query to space-separated tokens (defuses FTS operators)."""
    return " ".join(_FTS_TOKEN.findall(query))


class LanceStore:
    """LanceDB-backed chunk + file store with vector ANN and BM25 search."""

    def __init__(self, store_dir: Path, dim: int) -> None:
        import lancedb

        self.dim = dim
        self._dir = Path(store_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(str(self._dir))
        self._lock = threading.RLock()
        self._chunks_buf: list[dict[str, Any]] = []
        self._files_buf: list[dict[str, Any]] = []
        self._next_id = 0
        if _CHUNKS in self._db.table_names():
            self._next_id = self._max_id() + 1

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
                ("vector", pa.list_(pa.float32(), self.dim)),
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

    def _max_id(self) -> int:
        tbl = self._db.open_table(_CHUNKS)
        n = tbl.count_rows()
        if n == 0:
            return -1
        rows = tbl.search().select(["id"]).limit(n).to_list()
        return max((int(r["id"]) for r in rows), default=-1)

    # --- provenance / lifecycle ---

    def bootstrap(self, embed_dim: int, embed_model: str) -> bool:
        """Record the embedding model/dim; clear the store if they changed.

        Returns True when a rebuild is required (model/dim changed).
        """
        meta_path = self._dir / _META_FILE
        prev: dict[str, Any] = {}
        if meta_path.exists():
            try:
                prev = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):  # pragma: no cover - corrupt meta
                prev = {}
        changed = bool(prev) and (
            int(prev.get("embed_dim", -1)) != embed_dim
            or prev.get("embed_model") != embed_model
        )
        if changed:
            logger.warning(
                "Embedding model changed (%s/%s -> %s/%s); clearing index.",
                prev.get("embed_model"),
                prev.get("embed_dim"),
                embed_model,
                embed_dim,
            )
            with self._lock:
                for name in (_CHUNKS, _FILES):
                    if name in self._db.table_names():
                        self._db.drop_table(name)
                self._chunks_buf.clear()
                self._files_buf.clear()
                self._next_id = 0
        meta_path.write_text(
            json.dumps(
                {
                    "embed_model": embed_model,
                    "embed_dim": embed_dim,
                    "schema_version": _SCHEMA_VERSION,
                }
            ),
            encoding="utf-8",
        )
        return changed

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
        with self._lock:
            added = 0
            if chunks and vectors is not None:
                mat = np.ascontiguousarray(vectors, dtype="float32")
                norms = np.linalg.norm(mat, axis=1, keepdims=True)
                mat = mat / np.where(norms == 0.0, 1.0, norms)
                for chunk, vec in zip(chunks, mat, strict=False):
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

    # --- file metadata ---

    def all_file_metas(self) -> dict[str, dict[str, Any]]:
        """Every file's metadata, in one scan."""
        self.flush()
        if _FILES not in self._db.table_names():
            return {}
        tbl = self._db.open_table(_FILES)
        rows = (
            tbl.search()
            .select(["path", "content_hash", "mtime", "size"])
            .limit(max(1, tbl.count_rows()))
            .to_list()
        )
        return {r["path"]: r for r in rows}

    # --- retrieval ---

    def vector_search(self, qvec: np.ndarray, k: int) -> list[tuple[int, float]]:
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

    # --- stats ---

    def stats(self) -> IndexStats:
        self.flush()
        names = self._db.table_names()
        files = int(self._db.open_table(_FILES).count_rows()) if _FILES in names else 0
        chunks = (
            int(self._db.open_table(_CHUNKS).count_rows()) if _CHUNKS in names else 0
        )
        return IndexStats(total_files=files, total_chunks=chunks)
