"""Simple graph projection over semantic store rows."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GraphEdge:
    from_node: str
    edge: str
    to_node: str
    evidence_uri: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "from": self.from_node,
            "edge": self.edge,
            "to": self.to_node,
            "evidence_uri": self.evidence_uri,
        }
