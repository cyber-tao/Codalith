"""Read-only integrity checks scoped to one published shared snapshot."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from typing import TYPE_CHECKING, Any

import numpy as np

from codalith.corpus.generations import STRUCTURE_TABLES
from codalith.errors import IndexInvalidError
from codalith.indexing.semantic import engine_config
from codalith.semantic.chunking import chunk_file
from codalith.semantic.chunking.languages import detect_language
from codalith.semantic.config import Config
from codalith.semantic.indexer import has_indexable_text
from codalith.semantic.shared import SharedVectors

if TYPE_CHECKING:
    from codalith.indexing.shared import SharedPool

_BATCH_ROWS = 400


def verify_shared_snapshot(pool: SharedPool, snapshot: dict[str, Any]) -> None:
    try:
        _verify_metadata(snapshot)
        with pool._connect(readonly=True) as connection:
            _verify_structure(connection, snapshot)
            _verify_sources(pool, connection, snapshot)
            members = _vector_members(connection, snapshot)
        _verify_vectors(pool, snapshot, members)
    except IndexInvalidError:
        raise
    except Exception as exc:
        raise IndexInvalidError(
            f"Shared snapshot {snapshot.get('snapshot_id', 'unknown')} integrity check failed: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


def _verify_metadata(snapshot: dict[str, Any]) -> None:
    from codalith.indexing.shared import _digest

    for name in ("snapshot_id", "semantic_namespace"):
        if re.fullmatch(r"[a-f0-9]{32}", snapshot[name]) is None:
            raise IndexInvalidError(f"Shared snapshot has invalid {name}")
    suffix = hashlib.sha256(snapshot["snapshot_id"].encode()).hexdigest()[:24]
    if (
        snapshot["membership_column"] != "s_" + suffix
        or snapshot["lance_tag"] != "snapshot_" + suffix
    ):
        raise IndexInvalidError("Shared snapshot release identity mismatch")
    profile = snapshot["profile"]
    identity = _digest([
        snapshot["commit"], snapshot["source_selection_hash"], profile["adapter"],
        snapshot["adapter_version"], snapshot["semantic_namespace"],
    ])[:32]
    if (
        identity != snapshot["snapshot_id"] or snapshot["generation_id"] != identity
        or snapshot["source_commit"] != snapshot["commit"]
        or profile["revision"] != snapshot["commit"]
        or profile["corpus_id"] != snapshot["target_id"]
    ):
        raise IndexInvalidError("Shared snapshot indexing contract identity mismatch")


def _verify_structure(connection: sqlite3.Connection, snapshot: dict[str, Any]) -> None:
    from codalith.indexing.shared import _digest, _structure_fingerprint

    snapshot_id = snapshot["snapshot_id"]
    connection.execute("CREATE TEMP TABLE verified_snapshot(id TEXT PRIMARY KEY)")
    connection.execute("INSERT INTO verified_snapshot VALUES(?)", (snapshot_id,))
    keys = {
        "files": "path", "symbols": "symbol_id", "references": "reference_id",
        "module_dependencies": "dependency_id",
    }
    for table in STRUCTURE_TABLES:
        count = connection.execute(
            f"SELECT count(*) FROM members_{table} WHERE snapshot_id=?", (snapshot_id,)
        ).fetchone()[0]
        if count != snapshot["counts"][table]:
            raise IndexInvalidError(f"Shared snapshot {table} member count mismatch")
        missing = connection.execute(
            f"SELECT 1 FROM members_{table} m LEFT JOIN facts_{table} f USING(fact_id) "
            "WHERE m.snapshot_id=? AND f.fact_id IS NULL LIMIT 1", (snapshot_id,),
        ).fetchone()
        if missing is not None:
            raise IndexInvalidError(f"Shared snapshot {table} member has no fact")
        connection.execute(
            f'CREATE TEMP VIEW "verified_{table}" AS SELECT f.* FROM facts_{table} f '
            f"JOIN members_{table} m USING(fact_id) "
            "WHERE m.snapshot_id=(SELECT id FROM verified_snapshot)"
        )
        duplicate = connection.execute(
            f'SELECT 1 FROM "verified_{table}" GROUP BY {keys[table]} HAVING count(*) > 1 LIMIT 1'
        ).fetchone()
        if duplicate is not None:
            raise IndexInvalidError(f"Shared snapshot has duplicate {table} identities")
        for row in connection.execute(f'SELECT * FROM "verified_{table}"'):
            values = list(row)[1:]
            if table in {"references", "module_dependencies"}:
                values[0] = 0
            fact_id = _digest([value.hex() if isinstance(value, bytes) else value for value in values])
            if fact_id != row["fact_id"]:
                raise IndexInvalidError(f"Shared snapshot {table} fact hash mismatch")
            if table in {"references", "module_dependencies"} and row[keys[table]] != int(fact_id[:15], 16):
                raise IndexInvalidError(f"Shared snapshot {table} fact identity mismatch")

    for table in ("symbols", "references", "module_dependencies"):
        if connection.execute(
            f'SELECT 1 FROM "verified_{table}" v LEFT JOIN verified_files f USING(path) '
            "WHERE f.path IS NULL LIMIT 1"
        ).fetchone() is not None:
            raise IndexInvalidError(f"Shared snapshot {table} references a missing file")
    for table, field in (
        ("symbols", "parent_symbol_id"),
        ("references", "source_symbol_id"),
        ("references", "target_symbol_id"),
    ):
        if connection.execute(
            f'SELECT 1 FROM "verified_{table}" v LEFT JOIN verified_symbols s '
            f"ON s.symbol_id=v.{field} WHERE v.{field} IS NOT NULL "
            "AND s.symbol_id IS NULL LIMIT 1"
        ).fetchone() is not None:
            raise IndexInvalidError(f"Shared snapshot {table}.{field} references a missing symbol")

    if not snapshot.get("structure_fingerprint"):
        raise IndexInvalidError("Shared snapshot lacks a structural membership fingerprint; rebuild target")
    if _structure_fingerprint(connection, snapshot_id) != snapshot["structure_fingerprint"]:
        raise IndexInvalidError("Shared snapshot structural membership fingerprint mismatch")


def _verify_sources(
    pool: SharedPool, connection: sqlite3.Connection, snapshot: dict[str, Any]
) -> None:
    snapshot_id = snapshot["snapshot_id"]
    count = connection.execute(
        "SELECT count(*) FROM snapshot_sources WHERE snapshot_id=?", (snapshot_id,)
    ).fetchone()[0]
    if count != snapshot["counts"]["files"]:
        raise IndexInvalidError("Shared snapshot source member count mismatch")
    cursor = connection.execute(
        "SELECT s.path,s.sha256,o.git_oid,o.size_bytes,f.sha256 AS indexed_sha256,"
        "f.size_bytes AS indexed_size FROM snapshot_sources s "
        "LEFT JOIN source_objects o USING(sha256) LEFT JOIN verified_files f ON f.path=s.path "
        "WHERE s.snapshot_id=? ORDER BY s.path", (snapshot_id,),
    )
    fingerprint = hashlib.sha256()
    config = engine_config(pool._corpus(snapshot), pool.root / "semantic", pool.policy)
    while batch := cursor.fetchmany(_BATCH_ROWS):
        rows = []
        for row in batch:
            if (
                row["git_oid"] is None or row["sha256"] != row["indexed_sha256"]
                or row["size_bytes"] != row["indexed_size"]
            ):
                raise IndexInvalidError("Shared snapshot source object and file facts disagree")
            pool.policy.normalize_path(row["path"])
            rows.append(dict(row))
        inputs = pool._source_inputs(rows, lambda: None)
        try:
            for row, (path, data) in zip(rows, inputs, strict=True):
                if len(data) != row["size_bytes"]:
                    raise IndexInvalidError("Shared snapshot source object size mismatch")
                _verify_occurrences(connection, snapshot_id, path, data, config)
                fingerprint.update(path.encode("utf-8"))
                fingerprint.update(b"\0")
                fingerprint.update(row["sha256"].encode("ascii"))
        finally:
            inputs.close()
    if fingerprint.hexdigest() != snapshot["source_fingerprint"]:
        raise IndexInvalidError("Shared snapshot source fingerprint mismatch")


def _verify_occurrences(
    connection: sqlite3.Connection, snapshot_id: str, path: str, data: bytes, config: Config
) -> None:
    expected = {
        (hashlib.sha256(chunk.text.encode()).hexdigest(), chunk.start_line, chunk.end_line,
         chunk.symbol, chunk.kind, chunk.language)
        for chunk in chunk_file(data.decode("utf-8", errors="replace"), detect_language(path), config)
    } if has_indexable_text(data) else set()
    actual = {
        tuple(row) for row in connection.execute(
            "SELECT c.input_hash,o.start_line,o.end_line,o.symbol,o.kind,o.language "
            "FROM occurrences o JOIN chunks c ON c.id=o.chunk_id WHERE o.snapshot_id=? AND o.path=?",
            (snapshot_id, path),
        )
    }
    if actual != expected:
        raise IndexInvalidError(f"Shared snapshot chunk occurrences differ from source: {path}")


def _vector_members(connection: sqlite3.Connection, snapshot: dict[str, Any]) -> dict[int, str]:
    snapshot_id = snapshot["snapshot_id"]
    invalid = connection.execute(
        "SELECT 1 FROM occurrences o LEFT JOIN chunks c ON c.id=o.chunk_id "
        "LEFT JOIN verified_files f ON f.path=o.path WHERE o.snapshot_id=? AND "
        "(c.id IS NULL OR c.namespace<>? OR c.stored<>1 OR f.path IS NULL "
        "OR o.start_line<1 OR o.end_line<o.start_line OR o.end_line>f.line_count) LIMIT 1",
        (snapshot_id, snapshot["semantic_namespace"]),
    ).fetchone()
    if invalid is not None:
        raise IndexInvalidError("Shared snapshot has invalid chunk occurrences")
    members = {
        int(row["id"]): str(row["input_hash"])
        for row in connection.execute(
            "SELECT DISTINCT c.id,c.input_hash FROM chunks c JOIN occurrences o ON o.chunk_id=c.id "
            "WHERE o.snapshot_id=?", (snapshot_id,),
        )
    }
    if len(members) != snapshot["chunks"]:
        raise IndexInvalidError("Shared snapshot chunk member count mismatch")
    return members


def _verify_vectors(pool: SharedPool, snapshot: dict[str, Any], members: dict[int, str]) -> None:
    import lance

    dimension = snapshot["dimension"]
    root = pool.root / "semantic" / snapshot["semantic_namespace"]
    vectors = SharedVectors(root, dimension, writable=False)
    version = vectors._retained_release(
        snapshot["lance_tag"], snapshot["membership_column"], set(members), lambda: None,
    )
    if version != snapshot["table_version"]:
        raise IndexInvalidError("Shared snapshot retained tag/version mismatch")
    dataset = lance.dataset(str(root / "chunks.lance"), version=version)
    for batch in dataset.scanner(
        columns=["id", "input_hash", "text", "vector"],
        filter=f"{snapshot['membership_column']} = true", batch_size=_BATCH_ROWS,
    ).to_batches():
        for row in batch.to_pylist():
            if (
                row["input_hash"] != members[row["id"]]
                or hashlib.sha256(row["text"].encode("utf-8")).hexdigest() != row["input_hash"]
            ):
                raise IndexInvalidError("Shared snapshot vector input hash mismatch")
            vector = np.asarray(row["vector"], dtype="float32")
            if vector.shape != (dimension,) or not np.isfinite(vector).all():
                raise IndexInvalidError("Shared snapshot vector is invalid")
