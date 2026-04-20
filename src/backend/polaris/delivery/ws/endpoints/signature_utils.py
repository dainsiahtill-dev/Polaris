"""Signature utility functions for runtime WebSocket endpoint.

This module contains:
- Stream signature computation and tracking
- Payload signature computation
- Deduplication helpers
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections import deque

logger = logging.getLogger(__name__)


# =============================================================================
# Payload Signature Helpers
# =============================================================================


def status_signature(payload: dict[str, Any]) -> str:
    """Compute deterministic signature for status payload.

    Args:
        payload: Status payload dictionary.

    Returns:
        JSON string signature for comparison.
    """
    try:
        return json.dumps(payload, sort_keys=True, ensure_ascii=False)
    except (RuntimeError, ValueError) as exc:
        logger.debug("json.dumps payload failed: %s", exc)
        return str(payload)


def filter_status_payload_by_roles(
    payload: dict[str, Any],
    roles: set[str],
) -> dict[str, Any]:
    """Filter status payload by role permissions.

    Args:
        payload: Full status payload.
        roles: Role filter set.

    Returns:
        Filtered payload with only permitted role data.
    """
    if not roles:
        return payload
    filtered = dict(payload)
    if "pm" not in roles:
        filtered["pm_status"] = None
    if "director" not in roles:
        filtered["director_status"] = None
    return filtered


# =============================================================================
# Stream Signature Helpers
# =============================================================================


def stream_signature(
    *,
    channel: str,
    line: str,
    payload: dict[str, Any] | None,
) -> str:
    """Compute deterministic signature for stream line.

    Args:
        channel: Channel name.
        line: Raw line string.
        payload: Parsed payload dictionary, if available.

    Returns:
        Signature string for deduplication.
    """
    channel_token = str(channel or "").strip().lower()
    if isinstance(payload, dict):
        event_id = str(payload.get("event_id") or "").strip()
        if event_id:
            return f"{channel_token}:event:{event_id}"
        run_id = str(payload.get("run_id") or "").strip()
        seq = payload.get("seq")
        if run_id and seq is not None:
            return f"{channel_token}:run:{run_id}:seq:{seq}"
    compact = str(line or "").strip()
    if len(compact) > 512:
        compact = compact[-512:]
    return f"{channel_token}:line:{compact}"


def stream_seen(signatures: set[str], signature: str) -> bool:
    """Check if signature has been seen.

    Args:
        signatures: Set of seen signatures.
        signature: Signature to check.

    Returns:
        True if already seen.
    """
    if not signature:
        return False
    return signature in signatures


def remember_stream_signature(
    signatures: set[str],
    signature_order: deque[str],
    signature: str,
    *,
    max_size: int = 4096,
) -> None:
    """Add signature to tracking set with bounded memory.

    Args:
        signatures: Set of seen signatures.
        signature_order: Deque tracking insertion order.
        signature: Signature to add.
        max_size: Maximum number of signatures to track.
    """
    if not signature:
        return
    if signature in signatures:
        return
    signatures.add(signature)
    signature_order.append(signature)
    while len(signature_order) > max(128, int(max_size)):
        stale = signature_order.popleft()
        signatures.discard(stale)


__all__ = [
    "filter_status_payload_by_roles",
    "remember_stream_signature",
    "status_signature",
    "stream_seen",
    "stream_signature",
]
