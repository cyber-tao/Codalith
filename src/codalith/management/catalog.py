"""Durable management records and ordered job events."""

from __future__ import annotations

import builtins
import json
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


class Catalog:
    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "management.sqlite"
        self._lock = threading.RLock()
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS records (
                    kind TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(kind,id)
                ) WITHOUT ROWID;
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL, at TEXT NOT NULL,
                    type TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_job ON events(job_id,seq);
                CREATE TABLE IF NOT EXISTS requests (
                    key TEXT PRIMARY KEY, plan_id TEXT NOT NULL, job_id TEXT NOT NULL
                ) WITHOUT ROWID;
            """)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            connection.execute("PRAGMA busy_timeout=30000")
            with connection:
                yield connection
        finally:
            connection.close()

    def get(self, kind: str, identifier: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM records WHERE kind=? AND id=?", (kind, identifier)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown {kind}: {identifier}")
        value: dict[str, Any] = json.loads(row[0])
        return value

    def list(self, kind: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM records WHERE kind=? ORDER BY id", (kind,)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def put(self, kind: str, identifier: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self.connect() as connection:
            connection.execute(
                "INSERT INTO records(kind,id,payload) VALUES(?,?,?) "
                "ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload",
                (kind, identifier, json.dumps(payload, sort_keys=True)),
            )
        return payload

    def update_job(
        self, job_id: str, update: Callable[[dict[str, Any]], None], *, event: str = "updated"
    ) -> dict[str, Any]:
        with self._lock, self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM records WHERE kind='job' AND id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown job: {job_id}")
            payload: dict[str, Any] = json.loads(row[0])
            previous = json.loads(row[0])
            update(payload)
            payload["updated_at"] = timestamp()
            connection.execute(
                "UPDATE records SET payload=? WHERE kind='job' AND id=?",
                (
                    json.dumps(payload, sort_keys=True),
                    job_id,
                ),
            )
            event_payload = {key: value for key, value in payload.items() if key != "items"}
            event_payload["items"] = [
                item
                for index, item in enumerate(payload.get("items", []))
                if index >= len(previous.get("items", [])) or item != previous["items"][index]
            ]
            connection.execute(
                "INSERT INTO events(job_id,at,type,payload) VALUES(?,?,?,?)",
                (
                    job_id,
                    payload["updated_at"],
                    event,
                    json.dumps(event_payload, sort_keys=True),
                ),
            )
        return payload

    def create_job(self, job: dict[str, Any], key: str) -> dict[str, Any]:
        with self._lock, self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT plan_id,job_id FROM requests WHERE key=?", (key,)
            ).fetchone()
            if previous is not None:
                if previous[0] != job["plan_id"]:
                    raise ValueError("Idempotency key already belongs to another plan")
                row = connection.execute(
                    "SELECT payload FROM records WHERE kind='job' AND id=?", (previous[1],)
                ).fetchone()
                assert row is not None
                result: dict[str, Any] = json.loads(row[0])
                return result
            connection.execute(
                "INSERT INTO requests VALUES(?,?,?)",
                (
                    key,
                    job["plan_id"],
                    job["job_id"],
                ),
            )
            connection.execute(
                "INSERT INTO records VALUES('job',?,?)",
                (
                    job["job_id"],
                    json.dumps(job, sort_keys=True),
                ),
            )
            connection.execute(
                "INSERT INTO events(job_id,at,type,payload) VALUES(?,?,?,?)",
                (
                    job["job_id"],
                    timestamp(),
                    "created",
                    json.dumps(job, sort_keys=True),
                ),
            )
        return job

    def claim_job(self, job_id: str, expected_attempt: int) -> bool:
        with self._lock, self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM records WHERE kind='job' AND id=?", (job_id,)
            ).fetchone()
            if row is None:
                return False
            payload: dict[str, Any] = json.loads(row[0])
            if payload["status"] != "queued" or payload["attempt"] != expected_attempt:
                return False
            now = timestamp()
            payload.update(status="running", phase="starting", started_at=now, updated_at=now)
            connection.execute(
                "UPDATE records SET payload=? WHERE kind='job' AND id=?",
                (
                    json.dumps(payload, sort_keys=True),
                    job_id,
                ),
            )
            event_payload = {name: value for name, value in payload.items() if name != "items"}
            connection.execute(
                "INSERT INTO events(job_id,at,type,payload) VALUES(?,?,?,?)",
                (
                    job_id,
                    now,
                    "started",
                    json.dumps(event_payload, sort_keys=True),
                ),
            )
            return True

    def events(
        self, job_id: str, after_seq: int | None = None, *, tail: int | None = None
    ) -> builtins.list[dict[str, Any]]:
        if tail is not None and (after_seq is not None or not 1 <= tail <= 500):
            raise ValueError("Event tail must be between 1 and 500 and cannot use after_seq")
        if after_seq is not None and after_seq < 0:
            raise ValueError("Event after_seq cannot be negative")
        self.get("job", job_id)
        with self.connect() as connection:
            if tail is not None:
                rows = connection.execute(
                    "SELECT seq,at,type,payload FROM events WHERE job_id=? "
                    "ORDER BY seq DESC LIMIT ?",
                    (job_id, tail),
                ).fetchall()[::-1]
            else:
                rows = connection.execute(
                    "SELECT seq,at,type,payload FROM events WHERE job_id=? AND seq>? "
                    "ORDER BY seq LIMIT 500",
                    (job_id, after_seq or 0),
                ).fetchall()
        return [
            {
                "seq": row[0],
                "job_id": job_id,
                "at": row[1],
                "type": row[2],
                "payload": json.loads(row[3]),
            }
            for row in rows
        ]
