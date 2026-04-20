"""Channel snapshot and incremental functions for runtime WebSocket endpoint.

This module contains:
- send_channel_snapshot: Send snapshot for a single channel
- send_channel_incremental: Send incremental updates for a single channel
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from polaris.delivery.ws.endpoints.helpers import (
    channel_max_chars,
    is_llm_channel,
    resolve_channel_path,
    sanitize_snapshot_lines,
)
from polaris.delivery.ws.endpoints.stream import emit_stream_line

if TYPE_CHECKING:
    from fastapi import WebSocket

logger = logging.getLogger(__name__)


async def send_channel_snapshot(
    websocket: WebSocket,
    channel: str,
    workspace: str,
    cache_root: str,
    channel_state: dict[str, Any],
    tail_lines: int,
    connection_id: str = "",
    client: str = "",
) -> bool:
    """Send snapshot for a single channel.

    Args:
        websocket: WebSocket connection.
        channel: Channel name.
        workspace: Workspace path.
        cache_root: Runtime cache root.
        channel_state: Channel state dictionary.
        tail_lines: Number of lines to include.
        connection_id: Connection identifier.
        client: Client address.

    Returns:
        True if any content sent.
    """
    from polaris.cells.runtime.projection.public.service import read_file_tail

    path = resolve_channel_path(workspace, cache_root, channel)
    if not path or not os.path.isfile(path):
        channel_state["pos"] = 0
        channel_state.pop("_line_buffer", None)
        return False

    limit = channel_max_chars(channel)
    content = read_file_tail(path, max_lines=tail_lines, max_chars=limit)
    lines = content.splitlines() if content else []
    lines = sanitize_snapshot_lines(channel, lines)

    sent = False
    for line in lines:
        if await emit_stream_line(
            websocket,
            channel,
            line,
            from_snapshot=True,
            connection_id=connection_id,
            client=client,
            workspace=workspace,
        ):
            sent = True

    try:
        channel_state["pos"] = os.path.getsize(path)
        channel_state.pop("_line_buffer", None)
    except (RuntimeError, ValueError) as exc:
        logger.debug("os.path.getsize channel_state failed: %s", exc)
        channel_state["pos"] = 0
    return sent


async def send_channel_incremental(
    websocket: WebSocket,
    channel: str,
    workspace: str,
    cache_root: str,
    channel_state: dict[str, Any],
    connection_id: str = "",
    client: str = "",
) -> bool:
    """Send incremental updates for a single channel.

    Args:
        websocket: WebSocket connection.
        channel: Channel name.
        workspace: Workspace path.
        cache_root: Runtime cache root.
        channel_state: Channel state dictionary.
        connection_id: Connection identifier.
        client: Client address.

    Returns:
        True if any content sent.
    """
    from polaris.cells.runtime.projection.public.service import read_incremental

    path = resolve_channel_path(workspace, cache_root, channel)
    if not path or not os.path.isfile(path):
        channel_state["pos"] = 0
        channel_state.pop("_line_buffer", None)
        return False

    limit = channel_max_chars(channel)
    lines = read_incremental(
        path,
        channel_state,
        max_chars=limit,
        complete_lines_only=is_llm_channel(channel),
    )
    sent = False
    for line in lines:
        if await emit_stream_line(
            websocket,
            channel,
            line,
            from_snapshot=False,
            connection_id=connection_id,
            client=client,
            workspace=workspace,
        ):
            sent = True
    return sent


__all__ = [
    "send_channel_incremental",
    "send_channel_snapshot",
]
