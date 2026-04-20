"""Public contracts for the traceability subsystem.

This module defines the immutable data structures that constitute a
traceability matrix: nodes, links, and the matrix itself.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


def _now_epoch_ms() -> int:
    """Return the current time in milliseconds since the epoch."""
    return int(time.time() * 1000)


def _uuid() -> str:
    """Generate a new UUID4 string."""
    return str(uuid.uuid4())


@dataclass(frozen=True)
class TraceNode:
    """A single node in the traceability graph.

    Attributes:
        node_id: Unique identifier (UUID).
        node_kind: Kind of node, e.g. "doc", "blueprint", "task", "commit", "qa_verdict".
        role: The role that produced this node, e.g. "pm", "chief_engineer", "director", "qa".
        external_id: The ID used by the producing system (task_id, blueprint_id, etc.).
        content_hash: SHA-256 hash of the node's content for integrity verification.
        timestamp_ms: Creation time in epoch milliseconds.
        metadata: Arbitrary key-value metadata.
    """

    node_id: str
    node_kind: str
    role: str
    external_id: str
    content_hash: str
    timestamp_ms: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TraceLink:
    """A directed edge between two TraceNode instances.

    Attributes:
        link_id: Unique identifier (UUID).
        source_node_id: UUID of the upstream node.
        target_node_id: UUID of the downstream node.
        link_kind: Relationship kind, e.g. "derives_from", "implements", "verifies", "evolves_from".
        timestamp_ms: Creation time in epoch milliseconds.
    """

    link_id: str
    source_node_id: str
    target_node_id: str
    link_kind: str
    timestamp_ms: int


@dataclass(frozen=True)
class TraceabilityMatrix:
    """A complete traceability matrix for a single PM iteration.

    Attributes:
        matrix_id: Unique identifier (UUID).
        run_id: The PM run identifier.
        iteration: The PM iteration number.
        nodes: Tuple of all trace nodes.
        links: Tuple of all trace links.
        created_at_ms: Matrix assembly time in epoch milliseconds.
    """

    matrix_id: str
    run_id: str
    iteration: int
    nodes: tuple[TraceNode, ...]
    links: tuple[TraceLink, ...]
    created_at_ms: int

    def query_by_kind(self, kind: str) -> list[TraceNode]:
        """Return all nodes matching the given kind."""
        return [n for n in self.nodes if n.node_kind == kind]

    def query_ancestors(self, node_id: str) -> list[TraceNode]:
        """Return all upstream nodes for the given node_id using BFS."""
        ancestor_ids: set[str] = set()
        frontier: set[str] = {node_id}
        while frontier:
            next_frontier: set[str] = set()
            for link in self.links:
                if link.target_node_id in frontier and link.source_node_id not in ancestor_ids:
                    next_frontier.add(link.source_node_id)
            ancestor_ids |= next_frontier
            frontier = next_frontier
        return [n for n in self.nodes if n.node_id in ancestor_ids]

    def to_dict(self) -> dict[str, Any]:
        """Serialize the matrix to a plain dictionary."""
        return {
            "matrix_id": self.matrix_id,
            "run_id": self.run_id,
            "iteration": self.iteration,
            "nodes": [
                {
                    "node_id": n.node_id,
                    "kind": n.node_kind,
                    "role": n.role,
                    "external_id": n.external_id,
                    "content_hash": n.content_hash,
                    "timestamp_ms": n.timestamp_ms,
                    "metadata": n.metadata,
                }
                for n in self.nodes
            ],
            "links": [
                {
                    "link_id": link.link_id,
                    "source": link.source_node_id,
                    "target": link.target_node_id,
                    "kind": link.link_kind,
                    "timestamp_ms": link.timestamp_ms,
                }
                for link in self.links
            ],
            "created_at_ms": self.created_at_ms,
        }
