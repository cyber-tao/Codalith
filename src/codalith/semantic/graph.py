"""Simple graph projection over semantic store rows."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import NotRequired, Protocol, TypedDict


class GraphNodeDict(TypedDict):
    id: str
    kind: str
    label: str


class GraphEdgeDict(TypedDict):
    from_node: str
    edge_type: str
    to_node: str
    evidence_uris: list[str]
    extractor: str
    confidence: float
    metadata: dict[str, object]
    corpus_id: NotRequired[str]


class GraphQueryResult(TypedDict):
    nodes: list[GraphNodeDict]
    edges: list[GraphEdgeDict]


@dataclass(frozen=True, slots=True)
class GraphEdge:
    from_node: str
    edge_type: str
    to_node: str
    evidence_uris: tuple[str, ...] = ()
    extractor: str = "manual"
    confidence: float = 1.0
    metadata: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> GraphEdgeDict:
        return {
            "from_node": self.from_node,
            "edge_type": self.edge_type,
            "to_node": self.to_node,
            "evidence_uris": list(self.evidence_uris),
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
            missing_nodes = {
                endpoint
                for endpoint in (edge.from_node, edge.to_node)
                if endpoint not in nodes
            }
            if len(nodes) + len(missing_nodes) > bounded_nodes:
                continue
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

    Edges are deduplicated by ``(corpus_id, from, edge_type, to)``. When
    ``include_corpus_id`` is true, each edge dict gains a ``corpus_id`` field
    so overlay provenance remains explicit.
    """
    nodes: dict[str, GraphNodeDict] = {}
    edges: dict[tuple[object, object, object, object], GraphEdgeDict] = {}
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
            result_nodes = {
                str(result_node["id"]): result_node for result_node in result["nodes"]
            }
            for edge in result["edges"]:
                from_node = str(edge.get("from_node", ""))
                to_node = str(edge.get("to_node", ""))
                missing_nodes = {
                    endpoint
                    for endpoint in (from_node, to_node)
                    if endpoint and endpoint not in nodes
                }
                if (
                    not from_node
                    or not to_node
                    or len(nodes) + len(missing_nodes) > max_nodes
                ):
                    continue
                if max_edges is not None and len(edges) >= max_edges:
                    return {
                        "nodes": list(nodes.values()),
                        "edges": list(edges.values()),
                    }
                for endpoint in (from_node, to_node):
                    node_payload = result_nodes.get(endpoint)
                    if node_payload is not None:
                        nodes[endpoint] = node_payload
                    else:
                        _add_node(nodes, endpoint)
                key = (corpus_id, from_node, edge.get("edge_type"), to_node)
                payload = edge.copy()
                if include_corpus_id:
                    payload["corpus_id"] = corpus_id
                edges[key] = payload
    return {
        "nodes": list(nodes.values()),
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
    evidence = row.get("evidence_uris")
    evidence_uris = (
        tuple(str(item) for item in evidence)
        if isinstance(evidence, list | tuple)
        else ()
    )
    return GraphEdge(
        from_node=str(row["from_node"]),
        edge_type=str(row["edge_type"]),
        to_node=str(row["to_node"]),
        evidence_uris=evidence_uris,
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
