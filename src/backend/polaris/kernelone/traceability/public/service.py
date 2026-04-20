"""Public service port for the traceability subsystem.

This module defines the abstract interface (port) that consumers must use
to interact with traceability capabilities. The concrete implementation lives
in ``polaris.kernelone.traceability.internal.service_impl``.
"""

from __future__ import annotations

from typing import Any, Protocol

from polaris.kernelone.traceability.public.contracts import (
    TraceabilityMatrix,
    TraceLink,
    TraceNode,
)


class TraceabilityService(Protocol):
    """Port interface for building, persisting, and querying traceability matrices.

    Implementations must be side-effect free with regard to the caller's
    business logic: failures inside the service must be contained and must
    not propagate exceptions that would abort an ongoing PM/CE/Director/QA
    workflow.
    """

    def register_node(
        self,
        *,
        node_kind: str,
        role: str,
        external_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> TraceNode:
        """Register a new trace node.

        Args:
            node_kind: The kind of node (e.g. "doc", "task", "commit").
            role: The role that created the node.
            external_id: External system identifier.
            content: Raw content used to compute a SHA-256 content_hash.
            metadata: Optional additional metadata.

        Returns:
            The newly created TraceNode.
        """
        ...

    def link(
        self,
        source: TraceNode,
        target: TraceNode,
        link_kind: str = "derives_from",
    ) -> TraceLink:
        """Create a directed link between two nodes.

        Args:
            source: Upstream node.
            target: Downstream node.
            link_kind: Relationship type.

        Returns:
            The newly created TraceLink.
        """
        ...

    def find_node(self, external_id: str, node_kind: str) -> TraceNode | None:
        """Find an already-registered node by external_id and kind.

        Args:
            external_id: The external system identifier used during registration.
            node_kind: The kind of node to look for.

        Returns:
            The matching TraceNode, or None if not found.
        """
        ...

    def list_nodes(self) -> list[TraceNode]:
        """Return a shallow copy of all registered nodes."""
        ...

    def list_links(self) -> list[TraceLink]:
        """Return a shallow copy of all registered links."""
        ...

    def build_matrix(self, run_id: str, iteration: int) -> TraceabilityMatrix:
        """Assemble the current nodes and links into an immutable matrix.

        Args:
            run_id: The PM run identifier.
            iteration: The iteration number.

        Returns:
            A frozen TraceabilityMatrix instance.
        """
        ...

    def persist(self, matrix: TraceabilityMatrix, path: str) -> None:
        """Atomically persist the matrix to ``path`` as JSON.

        The implementation must write to a temporary file and then replace
        the target atomically to avoid corrupting an existing matrix file.

        Args:
            matrix: The matrix to persist.
            path: Destination file path.
        """
        ...

    def reset(self) -> None:
        """Clear all accumulated nodes and links, preparing for a new iteration."""
        ...


def create_traceability_service(workspace: str) -> TraceabilityService:
    """Factory that returns the concrete traceability service implementation.

    Args:
        workspace: The workspace root used to derive default runtime paths.

    Returns:
        A fully configured TraceabilityService instance.
    """
    from polaris.kernelone.traceability.internal.service_impl import (
        TraceabilityServiceImpl,
    )

    return TraceabilityServiceImpl(workspace=workspace)
