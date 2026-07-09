"""Simple graph projection over semantic store rows."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, TypedDict


class GraphNodeDict(TypedDict):
    id: str
    kind: str
    label: str


class GraphQueryResult(TypedDict):
    nodes: list[GraphNodeDict]
    edges: list[dict[str, object]]


@dataclass(frozen=True, slots=True)
class GraphEdge:
    from_node: str
    edge_type: str
    to_node: str
    evidence_uri: str | None = None
    extractor: str = "manual"
    confidence: float = 1.0
    metadata: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "from": self.from_node,
            "edge_type": self.edge_type,
            "to": self.to_node,
            "evidence_uri": self.evidence_uri,
            "extractor": self.extractor,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


class GraphStore(Protocol):
    def list_graph_edges(
        self,
        corpus_id: str,
        *,
        node: str | None = None,
        edge_types: Iterable[str] | None = None,
        limit: int = 200,
    ) -> list[GraphEdge]: ...


def query_graph(
    store: GraphStore,
    *,
    corpus_id: str,
    node: str,
    edge_types: Iterable[str] | None = None,
    depth: int = 1,
    max_nodes: int = 80,
) -> GraphQueryResult:
    """Return a bounded undirected neighborhood around a semantic node."""

    bounded_depth = max(1, min(depth, 4))
    bounded_nodes = max(1, max_nodes)
    queue: deque[tuple[str, int]] = deque((candidate, 0) for candidate in node_candidates(node))
    visited: set[str] = set()
    nodes: dict[str, GraphNodeDict] = {}
    edges: dict[tuple[str, str, str], GraphEdge] = {}

    while queue and len(nodes) < bounded_nodes:
        current, current_depth = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        for edge in store.list_graph_edges(
            corpus_id,
            node=current,
            edge_types=edge_types,
            limit=bounded_nodes * 4,
        ):
            key = (edge.from_node, edge.edge_type, edge.to_node)
            edges[key] = edge
            _add_node(nodes, edge.from_node)
            _add_node(nodes, edge.to_node)
            if len(nodes) >= bounded_nodes or current_depth >= bounded_depth - 1:
                continue
            for neighbor in (edge.from_node, edge.to_node):
                if neighbor not in visited:
                    queue.append((neighbor, current_depth + 1))

    return {
        "nodes": list(nodes.values())[:bounded_nodes],
        "edges": [edge.as_dict() for edge in edges.values()],
    }


def aggregate_graph_neighborhood(
    store: GraphStore,
    *,
    corpus_ids: Iterable[str],
    seed_nodes: Iterable[str],
    edge_types: Iterable[str] | None = None,
    depth: int = 1,
    max_nodes: int = 80,
    max_edges: int | None = None,
    include_corpus_id: bool = False,
) -> GraphQueryResult:
    """Merge neighborhoods for multiple seed nodes across corpora.

    Edges are deduplicated by ``(from, edge_type, to)``. When
    ``include_corpus_id`` is true, each edge dict gains a ``corpus_id`` field
    (last corpus wins on collision).
    """
    nodes: dict[str, GraphNodeDict] = {}
    edges: dict[tuple[object, object, object], dict[str, object]] = {}
    seeds = [node for node in seed_nodes if node]
    for corpus_id in corpus_ids:
        for node in seeds:
            result = query_graph(
                store,
                corpus_id=corpus_id,
                node=node,
                edge_types=edge_types,
                depth=depth,
                max_nodes=max_nodes,
            )
            for result_node in result["nodes"]:
                nodes[str(result_node["id"])] = result_node
            for edge in result["edges"]:
                key = (edge.get("from"), edge.get("edge_type"), edge.get("to"))
                payload = dict(edge)
                if include_corpus_id:
                    payload["corpus_id"] = corpus_id
                edges[key] = payload
                if max_edges is not None and len(edges) >= max_edges:
                    return {
                        "nodes": list(nodes.values())[:max_nodes],
                        "edges": list(edges.values()),
                    }
    return {
        "nodes": list(nodes.values())[:max_nodes],
        "edges": list(edges.values()),
    }


def node_candidates(node: str) -> list[str]:
    """Expand a bare name into namespaced candidate node ids."""
    normalized = node.strip()
    if not normalized:
        return []
    if ":" in normalized:
        return [normalized]
    return [
        normalized,
        f"module:{normalized}",
        f"symbol:{normalized}",
        f"macro:{normalized}",
    ]


def edge_from_row(row: Mapping[str, object]) -> GraphEdge:
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    confidence = row.get("confidence")
    return GraphEdge(
        from_node=str(row["from_node"]),
        edge_type=str(row["edge_type"]),
        to_node=str(row["to_node"]),
        evidence_uri=str(row["evidence_uri"]) if row.get("evidence_uri") is not None else None,
        extractor=str(row["extractor"]),
        confidence=float(confidence) if isinstance(confidence, int | float | str) else 1.0,
        metadata=metadata,
    )


def _add_node(nodes: dict[str, GraphNodeDict], node: str) -> None:
    if node in nodes:
        return
    kind, _, label = node.partition(":")
    nodes[node] = {
        "id": node,
        "kind": kind if label else "node",
        "label": label or kind,
    }
