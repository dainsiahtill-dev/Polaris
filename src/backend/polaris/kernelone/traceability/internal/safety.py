"""Safety wrappers for traceability integration.

These utilities ensure that traceability is a true **bypass observer**:
failures inside the traceability subsystem are captured, logged, and
suppressed so they can never block or corrupt the main PM/CE/Director/QA
execution flow.
"""

from __future__ import annotations

import logging
from typing import Any

from polaris.kernelone.traceability.public.contracts import (
    TraceabilityMatrix,
    TraceLink,
    TraceNode,
)
from polaris.kernelone.traceability.public.service import TraceabilityService

logger = logging.getLogger(__name__)


def safe_register_node(
    trace_service: TraceabilityService | None,
    *,
    node_kind: str,
    role: str,
    external_id: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> TraceNode | None:
    """Safely register a trace node, suppressing any exception.

    Args:
        trace_service: The traceability service instance, or None.
        node_kind: The kind of node.
        role: The role that produced the node.
        external_id: External system identifier.
        content: Raw content for the SHA-256 hash.
        metadata: Optional metadata dictionary.

    Returns:
        The created TraceNode, or None if the service is None or an error occurred.
    """
    if trace_service is None:
        return None
    try:
        return trace_service.register_node(
            node_kind=node_kind,
            role=role,
            external_id=external_id,
            content=content,
            metadata=metadata,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Traceability register_node failed (kind=%s, external_id=%s): %s",
            node_kind,
            external_id,
            exc,
            exc_info=False,
        )
        return None


def safe_link(
    trace_service: TraceabilityService | None,
    source: TraceNode | None,
    target: TraceNode | None,
    link_kind: str = "derives_from",
) -> TraceLink | None:
    """Safely create a trace link, suppressing any exception.

    Args:
        trace_service: The traceability service instance, or None.
        source: Upstream node, or None.
        target: Downstream node, or None.
        link_kind: Relationship type.

    Returns:
        The created TraceLink, or None if inputs are missing or an error occurred.
    """
    if trace_service is None or source is None or target is None:
        return None
    try:
        return trace_service.link(source, target, link_kind)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Traceability link failed (%s -> %s): %s",
            source.external_id,
            target.external_id,
            exc,
            exc_info=False,
        )
        return None


def safe_find_node(
    trace_service: TraceabilityService | None,
    external_id: str,
    node_kind: str,
) -> TraceNode | None:
    """Safely find a trace node, suppressing any exception.

    Args:
        trace_service: The traceability service instance, or None.
        external_id: External system identifier.
        node_kind: The kind of node.

    Returns:
        The matching TraceNode, or None if not found or an error occurred.
    """
    if trace_service is None:
        return None
    try:
        return trace_service.find_node(external_id, node_kind)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Traceability find_node failed (kind=%s, external_id=%s): %s",
            node_kind,
            external_id,
            exc,
            exc_info=False,
        )
        return None


def safe_persist_matrix(
    trace_service: TraceabilityService | None,
    matrix: TraceabilityMatrix | None,
    path: str,
) -> bool:
    """Safely persist a traceability matrix, suppressing any exception.

    Args:
        trace_service: The traceability service instance, or None.
        matrix: The matrix to persist, or None.
        path: Destination file path.

    Returns:
        True if persistence succeeded, False otherwise.
    """
    if trace_service is None or matrix is None:
        return False
    try:
        trace_service.persist(matrix, path)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Traceability persist failed (path=%s): %s",
            path,
            exc,
            exc_info=False,
        )
        return False


def safe_reset(trace_service: TraceabilityService | None) -> bool:
    """Safely reset the traceability service, suppressing any exception.

    Args:
        trace_service: The traceability service instance, or None.

    Returns:
        True if reset succeeded, False otherwise.
    """
    if trace_service is None:
        return False
    try:
        trace_service.reset()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Traceability reset failed: %s",
            exc,
            exc_info=False,
        )
        return False
