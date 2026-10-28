"""Content-addressed vectors and immutable, version-filtered Lance releases."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, cast

import numpy as np

from codalith.semantic.config import Config
from codalith.semantic.embeddings import EmbeddingProvider
from codalith.semantic.search import fusion_weights, reciprocal_rank_fusion
from codalith.semantic.store import _fts_query

_ANN_MIN_ROWS = 256


class SharedVectors:
    def __init__(self, root: Path, dimension: int, *, writable: bool = True) -> None:
        import lancedb
        import pyarrow as pa

        if writable:
            root.mkdir(parents=True, exist_ok=True)
        elif not root.is_dir():
            raise ValueError("Shared semantic pool is missing")
        self.root = root
        self.dimension = dimension
        self.db = lancedb.connect(str(root))
        try:
            self.table = self.db.open_table("chunks")
        except (FileNotFoundError, ValueError):
            if not writable:
                raise
            self.table = self.db.create_table(
                "chunks",
                schema=pa.schema(
                    [
                        ("id", pa.int64()),
                        ("input_hash", pa.string()),
                        ("text", pa.string()),
                        ("vector", pa.list_(pa.float32(), dimension)),
                    ]
                ),
            )
        if self.table.schema.field("vector").type.list_size != dimension:
            raise ValueError("Shared vector dimension does not match the namespace")

    def append(self, records: Iterable[Mapping[str, Any]]) -> None:
        import pyarrow as pa

        unique: dict[int, dict[str, Any]] = {}
        for record in records:
            row = dict(record)
            row_id = int(row["id"])
            if row_id in unique and unique[row_id]["input_hash"] != row["input_hash"]:
                raise ValueError("Conflicting shared vector ids in one batch")
            unique[row_id] = row
        rows = list(unique.values())
        if not rows:
            return
        ids = ",".join(str(int(row["id"])) for row in rows)
        existing = (
            self.table.search()
            .where(f"id IN ({ids})")
            .select(["id", "input_hash"])
            .limit(len(rows))
            .to_list()
        )
        stored = {int(row["id"]): str(row["input_hash"]) for row in existing}
        for row in rows:
            if int(row["id"]) in stored and stored[int(row["id"])] != row["input_hash"]:
                raise ValueError("Shared vector id already belongs to another input")
        rows = [row for row in rows if int(row["id"]) not in stored]
        if not rows:
            return
        for row in rows:
            for field in self.table.schema:
                if field.name.startswith("s_"):
                    row[field.name] = False
        self.table.add(pa.Table.from_pylist(rows, schema=self.table.schema))

    def publish(
        self,
        snapshot_id: str,
        members: set[int],
        check_cancel: Callable[[], None],
    ) -> tuple[int, str, str]:
        import lance
        import pyarrow as pa
        from lancedb.index import FTS, Bitmap

        check_cancel()
        column = "s_" + hashlib.sha256(snapshot_id.encode()).hexdigest()[:24]
        native = lance.dataset(str(self.root / "chunks.lance"))
        if column not in native.schema.names:

            def membership(batch: Any) -> Any:
                values = [value.as_py() in members for value in batch.column("id")]
                return pa.record_batch([pa.array(values, type=pa.bool_())], names=[column])

            transform = cast(Any, lance.batch_udf)(
                output_schema=pa.schema([(column, pa.bool_())]),
            )(membership)
            native.add_columns(transform, read_columns=["id"])
        self.table = self.db.open_table("chunks")
        self.table.checkout_latest()
        self.table.create_index(column, config=Bitmap(), replace=True)
        indices = self.table.list_indices()
        text_indices = [item.name for item in indices if "text" in item.columns]
        check_cancel()
        if text_indices:
            native = lance.dataset(str(self.root / "chunks.lance"))
            try:
                cast(Any, native.optimize).optimize_indices(
                    num_indices_to_merge=0,
                    index_names=text_indices,
                    retrain=False,
                )
            except (NotImplementedError, ValueError) as exc:
                if "support" not in str(exc).lower():
                    raise
                self.table.create_index("text", config=FTS(), replace=True)
        else:
            self.table.create_index("text", config=FTS(), replace=True)
        self.table.checkout_latest()
        count = self.table.count_rows()
        vector_indices = [
            item.name for item in self.table.list_indices() if "vector" in item.columns
        ]
        if count >= _ANN_MIN_ROWS:
            if vector_indices:
                native = lance.dataset(str(self.root / "chunks.lance"))
                try:
                    cast(Any, native.optimize).optimize_indices(
                        num_indices_to_merge=0,
                        index_names=vector_indices,
                        retrain=False,
                    )
                except (NotImplementedError, ValueError) as exc:
                    if "support" not in str(exc).lower():
                        raise
                    self.table.create_index(
                        metric="cosine",
                        num_partitions=max(1, int(count**0.5)),
                        vector_column_name="vector",
                        replace=True,
                    )
            else:
                self.table.create_index(
                    metric="cosine",
                    num_partitions=max(1, int(count**0.5)),
                    vector_column_name="vector",
                    replace=True,
                )
        check_cancel()
        self.table = self.db.open_table("chunks")
        self.table.checkout_latest()
        version = int(self.table.version)
        tag = "snapshot_" + hashlib.sha256(snapshot_id.encode()).hexdigest()[:24]
        native = lance.dataset(str(self.root / "chunks.lance"))
        if tag not in native.tags.list():
            native.tags.create(tag, version)
        return version, column, tag

    def search(
        self,
        query: str,
        *,
        provider: EmbeddingProvider,
        config: Config,
        version: int,
        column: str,
        limit: int,
    ) -> list[tuple[int, float, float, str]]:
        table = self.db.open_table("chunks")
        table.checkout(version)
        fetch = max(limit, config.fetch_k)
        vector = np.asarray(provider.embed_query(query), dtype="float32").reshape(-1)
        dense = (
            table.search(vector.tolist())
            .metric("cosine")
            .where(
                f"{column} = true",
                prefilter=True,
            )
            .select(["id", "text"])
            .limit(fetch)
            .to_list()
        )
        lexical_query = _fts_query(query)
        lexical = (
            (
                table.search(lexical_query, query_type="fts")
                .where(
                    f"{column} = true",
                    prefilter=True,
                )
                .select(["id", "text"])
                .limit(fetch)
                .to_list()
            )
            if lexical_query
            else []
        )
        weights = fusion_weights(query, config)
        ranked = reciprocal_rank_fusion(
            [[int(row["id"]) for row in dense], [int(row["id"]) for row in lexical]],
            k=config.rrf_k,
            weights=weights,
        )[:limit]
        rows = {int(row["id"]): row for row in [*dense, *lexical]}
        similarities = {
            int(row["id"]): max(0.0, min(1.0, 1.0 - float(row["_distance"]))) for row in dense
        }
        return [
            (chunk_id, score, similarities.get(chunk_id, 0.0), str(rows[chunk_id]["text"]))
            for chunk_id, score in ranked
        ]
