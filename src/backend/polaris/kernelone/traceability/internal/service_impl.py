"""Concrete implementation of the traceability service port."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from polaris.kernelone.traceability.public.contracts import (
    TraceabilityMatrix,
    TraceLink,
    TraceNode,
    _now_epoch_ms,
    _uuid,
)

logger = logging.getLogger(__name__)


class TraceabilityServiceImpl:
    """Default implementation of ``TraceabilityService``.

    This class builds an in-memory graph of trace nodes and links and
    supports atomic JSON persistence. It is intentionally simple and
    does not depend on any application-level modules.
    """

    def __init__(self, workspace: str) -> None:
        """Initialize a new traceability service bound to a workspace.

        Args:
            workspace: The workspace root directory.
        """
        self._workspace = workspace
        self._nodes: list[TraceNode] = []
        self._links: list[TraceLink] = []

    def register_node(
        self,
        *,
        node_kind: str,
        role: str,
        external_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> TraceNode:
        """Register a new trace node."""
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        node = TraceNode(
            node_id=_uuid(),
            node_kind=node_kind,
            role=role,
            external_id=external_id,
            content_hash=content_hash,
            timestamp_ms=_now_epoch_ms(),
            metadata=metadata or {},
        )
        self._nodes.append(node)
        return node

    def link(
        self,
        source: TraceNode,
        target: TraceNode,
        link_kind: str = "derives_from",
    ) -> TraceLink:
        """Create a directed link between two nodes."""
        trace_link = TraceLink(
            link_id=_uuid(),
            source_node_id=source.node_id,
            target_node_id=target.node_id,
            link_kind=link_kind,
            timestamp_ms=_now_epoch_ms(),
        )
        self._links.append(trace_link)
        return trace_link

    def find_node(self, external_id: str, node_kind: str) -> TraceNode | None:
        """Find an already-registered node by external_id and kind."""
        token = str(external_id or "").strip()
        kind = str(node_kind or "").strip()
        for node in self._nodes:
            if node.external_id == token and node.node_kind == kind:
                return node
        return None

    def list_nodes(self) -> list[TraceNode]:
        """Return a shallow copy of all registered nodes."""
        return list(self._nodes)

    def list_links(self) -> list[TraceLink]:
        """Return a shallow copy of all registered links."""
        return list(self._links)

    def build_matrix(self, run_id: str, iteration: int) -> TraceabilityMatrix:
        """Assemble the current nodes and links into an immutable matrix."""
        return TraceabilityMatrix(
            matrix_id=_uuid(),
            run_id=run_id,
            iteration=iteration,
            nodes=tuple(self._nodes),
            links=tuple(self._links),
            created_at_ms=_now_epoch_ms(),
        )

    def persist(self, matrix: TraceabilityMatrix, path: str) -> None:
        """Atomically persist the matrix to ``path`` as JSON."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(matrix.to_dict(), f, ensure_ascii=False, indent=2)
        tmp.replace(p)

    def reset(self) -> None:
        """Clear all accumulated nodes and links."""
        self._nodes.clear()
        self._links.clear()
