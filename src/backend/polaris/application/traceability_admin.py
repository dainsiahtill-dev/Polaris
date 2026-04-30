"""Traceability service facade for application layer.

This module provides a thin facade that wraps KernelOne's traceability
safety functions, allowing delivery-layer code to safely integrate with
the traceability subsystem without importing Cell internals.

Architecture
------------
::

    delivery  ->  application.traceability_admin  ->  kernelone.traceability.public

Usage
-----
::

    from polaris.application.traceability_admin import TraceabilityAdminService

    service = TraceabilityAdminService()
    service.safe_register_node(
        node_kind="task",
        role="chief_engineer",
        external_id="task-123",
        content="Implement feature X",
    )
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.kernelone.traceability.public.contracts import (
        TraceabilityMatrix,
        TraceLink,
        TraceNode,
    )

logger = logging.getLogger(__name__)


class TraceabilityAdminError(Exception):
    """Raised when traceability operations fail after all safety fallbacks."""

    pass


class TraceabilityAdminService:
    """Facade for KernelOne traceability subsystem.

    This service provides a safe, non-blocking interface to the traceability
    system. All operations are wrapped with exception suppression to ensure
    traceability failures never block the main execution flow.

    Attributes:
        _trace_service: The underlying TraceabilityService instance (optional).

    Example:
        >>> service = TraceabilityAdminService()
        >>> node = service.safe_register_node(
        ...     node_kind="task",
        ...     role="chief_engineer",
        ...     external_id="task-123",
        ...     content="Implement feature X",
        ... )
        >>> if node is not None:
        ...     print(f"Registered: {node.external_id}")
    """

    def __init__(
        self,
        trace_service: Any = None,
    ) -> None:
        """Initialize the traceability admin service.

        Args:
            trace_service: Optional TraceabilityService instance.
                          If None, a default service will be created.
        """
        self._trace_service = trace_service

    def _get_service(self) -> Any | None:
        """Get the underlying traceability service lazily.

        Returns:
            TraceabilityService instance or None if unavailable.
        """
        if self._trace_service is not None:
            return self._trace_service

        try:
            from polaris.kernelone.traceability.public.service import (
                create_traceability_service,
            )

            self._trace_service = create_traceability_service()  # type: ignore[call-arg]
            return self._trace_service
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "TraceabilityService unavailable: %s",
                exc,
            )
            return None

    def safe_register_node(
        self,
        *,
        node_kind: str,
        role: str,
        external_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> TraceNode | None:
        """Safely register a trace node.

        This method wraps TraceabilityService.register_node() with
        exception suppression. Failures are logged but never raised.

        Args:
            node_kind: The kind of node (e.g., "task", "artifact", "decision").
            role: The role that produced the node.
            external_id: External system identifier for the node.
            content: Raw content used for SHA-256 hash computation.
            metadata: Optional metadata dictionary.

        Returns:
            The created TraceNode, or None if registration failed.
        """
        service = self._get_service()
        if service is None:
            return None
        try:
            return service.register_node(
                node_kind=node_kind,
                role=role,
                external_id=external_id,
                content=content,
                metadata=metadata,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "safe_register_node failed (kind=%s, external_id=%s): %s",
                node_kind,
                external_id,
                exc,
            )
            return None

    def safe_link(
        self,
        source: TraceNode | None,
        target: TraceNode | None,
        link_kind: str = "derives_from",
    ) -> TraceLink | None:
        """Safely create a trace link between two nodes.

        This method wraps TraceabilityService.link() with
        exception suppression.

        Args:
            source: Upstream node, or None to skip.
            target: Downstream node, or None to skip.
            link_kind: Relationship type (default: "derives_from").

        Returns:
            The created TraceLink, or None if linking failed.
        """
        if source is None or target is None:
            return None
        service = self._get_service()
        if service is None:
            return None
        try:
            return service.link(source, target, link_kind)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "safe_link failed: %s",
                exc,
            )
            return None

    def safe_find_node(
        self,
        external_id: str,
        node_kind: str,
    ) -> TraceNode | None:
        """Safely find a trace node by external ID.

        This method wraps TraceabilityService.find_node() with
        exception suppression.

        Args:
            external_id: External system identifier.
            node_kind: The kind of node to search for.

        Returns:
            The matching TraceNode, or None if not found.
        """
        service = self._get_service()
        if service is None:
            return None
        try:
            return service.find_node(external_id, node_kind)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "safe_find_node failed (kind=%s, external_id=%s): %s",
                node_kind,
                external_id,
                exc,
            )
            return None

    def safe_persist_matrix(
        self,
        matrix: TraceabilityMatrix | None,
        path: str,
    ) -> bool:
        """Safely persist a traceability matrix to disk.

        This method wraps TraceabilityService.persist() with
        exception suppression.

        Args:
            matrix: The matrix to persist, or None to skip.
            path: Destination file path (UTF-8 encoded).

        Returns:
            True if persistence succeeded, False otherwise.
        """
        service = self._get_service()
        if service is None or matrix is None:
            return False
        try:
            service.persist(matrix, path)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "safe_persist_matrix failed (path=%s): %s",
                path,
                exc,
            )
            return False

    def safe_reset(self) -> bool:
        """Safely reset the traceability service state.

        This method wraps TraceabilityService.reset() with
        exception suppression.

        Returns:
            True if reset succeeded, False otherwise.
        """
        service = self._get_service()
        if service is None:
            return False
        try:
            service.reset()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "safe_reset failed: %s",
                exc,
            )
            return False


__all__ = [
    "TraceabilityAdminError",
    "TraceabilityAdminService",
]
