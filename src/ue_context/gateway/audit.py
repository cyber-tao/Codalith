"""JSONL audit logging for source reads."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class AuditRecord:
    timestamp: str
    user_id: str
    session_id: str
    tool: str
    uri: str
    corpus_id: str
    path: str
    start_line: int
    end_line: int
    line_count: int
    client: str
    decision: str
    reason: str | None = None

    @classmethod
    def create(
        cls,
        *,
        tool: str,
        uri: str,
        corpus_id: str,
        path: str,
        start_line: int,
        end_line: int,
        line_count: int,
        decision: str,
        reason: str | None = None,
        user_id: str = "local-user",
        session_id: str = "local-session",
        client: str = "codex",
    ) -> AuditRecord:
        return cls(
            timestamp=datetime.now(UTC).isoformat(),
            user_id=user_id,
            session_id=session_id,
            tool=tool,
            uri=uri,
            corpus_id=corpus_id,
            path=path,
            start_line=start_line,
            end_line=end_line,
            line_count=line_count,
            client=client,
            decision=decision,
            reason=reason,
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class AuditLogger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def write(self, record: AuditRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.as_dict(), sort_keys=True) + "\n")
