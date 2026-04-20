"""Journal snapshot and incremental functions for runtime WebSocket endpoint.

This module contains:
- send_journal_snapshot: Send snapshot for journal channels (system/process/llm)
- send_journal_incremental: Send incremental updates for journal channels
"""

from __future__ import annotations

import logging
import os
from collections import deque
from typing import TYPE_CHECKING, Any

from polaris.delivery.ws.endpoints.helpers import (
    channel_max_chars,
    parse_json_line,
    remember_stream_signature,
    resolve_channel_path,
    resolve_journal_event_channel,
    stream_seen,
    stream_signature,
)
from polaris.delivery.ws.endpoints.stream import emit_stream_line

if TYPE_CHECKING:
    from fastapi import WebSocket

logger = logging.getLogger(__name__)


async def send_journal_snapshot(
    websocket: WebSocket,
    workspace: str,
    cache_root: str,
    journal_state: dict[str, Any],
    tail_lines: int,
    enabled_channels: set[str],
    stream_signatures: set[str] | None = None,
    stream_signature_order: list[str] | None = None,
    connection_id: str = "",
    client: str = "",
) -> bool:
    """Send snapshot for journal channels (system/process/llm).

    Args:
        websocket: WebSocket connection.
        workspace: Workspace path.
        cache_root: Runtime cache root.
        journal_state: Journal state dictionary.
        tail_lines: Number of lines to include.
        enabled_channels: Set of enabled channels.
        stream_signatures: Set for deduplication tracking.
        stream_signature_order: List for order tracking.
        connection_id: Connection identifier.
        client: Client address.

    Returns:
        True if any content sent.
    """
    from polaris.cells.runtime.projection.public.service import read_file_tail

    if not enabled_channels:
        return False

    # All canonical channels share the same journal file.
    path = resolve_channel_path(workspace, cache_root, "system")
    if not path or not os.path.isfile(path):
        journal_state["pos"] = 0
        journal_state.pop("_line_buffer", None)
        return False

    content = read_file_tail(path, max_lines=tail_lines, max_chars=channel_max_chars("llm"))
    lines = content.splitlines() if content else []
    sent = False

    # Convert list to deque for signature tracking if needed
    sig_order_deque: deque[str] | None = None
    if stream_signature_order is not None and stream_signatures is not None:
        sig_order_deque = deque(stream_signature_order)

    for line in lines:
        target_channel = resolve_journal_event_channel(line)
        if target_channel not in enabled_channels:
            continue
        parsed = parse_json_line(line)
        signature = stream_signature(channel=target_channel, line=line, payload=parsed)
        if stream_signatures is not None and stream_seen(stream_signatures, signature):
            continue
        if await emit_stream_line(
            websocket,
            target_channel,
            line,
            from_snapshot=True,
            connection_id=connection_id,
            client=client,
            workspace=workspace,
        ):
            sent = True
            if stream_signatures is not None and sig_order_deque is not None:
                remember_stream_signature(stream_signatures, sig_order_deque, signature)

    try:
        journal_state["pos"] = os.path.getsize(path)
        journal_state.pop("_line_buffer", None)
    except (RuntimeError, ValueError) as exc:
        logger.debug("os.path.getsize journal_state failed: %s", exc)
        journal_state["pos"] = 0
    return sent


async def send_journal_incremental(
    websocket: WebSocket,
    workspace: str,
    cache_root: str,
    journal_state: dict[str, Any],
    enabled_channels: set[str],
    stream_signatures: set[str] | None = None,
    stream_signature_order: list[str] | None = None,
    connection_id: str = "",
    client: str = "",
) -> bool:
    """Send incremental updates for journal channels.

    Args:
        websocket: WebSocket connection.
        workspace: Workspace path.
        cache_root: Runtime cache root.
        journal_state: Journal state dictionary.
        enabled_channels: Set of enabled channels.
        stream_signatures: Set for deduplication tracking.
        stream_signature_order: List for order tracking.
        connection_id: Connection identifier.
        client: Client address.

    Returns:
        True if any content sent.
    """
    from polaris.cells.runtime.projection.public.service import read_incremental

    if not enabled_channels:
        return False

    path = resolve_channel_path(workspace, cache_root, "system")
    if not path or not os.path.isfile(path):
        journal_state["pos"] = 0
        journal_state.pop("_line_buffer", None)
        return False

    lines = read_incremental(
        path,
        journal_state,
        max_chars=channel_max_chars("llm"),
        complete_lines_only=True,
    )
    sent = False

    # Convert list to deque for signature tracking if needed
    sig_order_deque: deque[str] | None = None
    if stream_signature_order is not None and stream_signatures is not None:
        sig_order_deque = deque(stream_signature_order)

    for line in lines:
        target_channel = resolve_journal_event_channel(line)
        if target_channel not in enabled_channels:
            continue
        parsed = parse_json_line(line)
        signature = stream_signature(channel=target_channel, line=line, payload=parsed)
        if stream_signatures is not None and stream_seen(stream_signatures, signature):
            continue
        if await emit_stream_line(
            websocket,
            target_channel,
            line,
            from_snapshot=False,
            connection_id=connection_id,
            client=client,
            workspace=workspace,
        ):
            sent = True
            if stream_signatures is not None and sig_order_deque is not None:
                remember_stream_signature(stream_signatures, sig_order_deque, signature)
    return sent


__all__ = [
    "send_journal_incremental",
    "send_journal_snapshot",
]
