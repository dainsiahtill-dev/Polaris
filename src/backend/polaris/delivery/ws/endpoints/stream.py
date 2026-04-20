"""Stream sending functions for runtime WebSocket endpoint.

This module contains core functions for:
- Sending JSON payloads via WebSocket
- Emitting stream lines with appropriate formatting

Channel and journal snapshot/incremental functions are in separate modules:
- channel_stream.py: send_channel_snapshot, send_channel_incremental
- journal_stream.py: send_journal_snapshot, send_journal_incremental
"""

from __future__ import annotations

import json
import logging
from contextlib import suppress
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from polaris.cells.audit.diagnosis.public.service import write_ws_connection_event
from polaris.delivery.ws.endpoints.helpers import (
    is_llm_channel,
    is_process_channel,
    parse_json_line,
)
from polaris.delivery.ws.endpoints.models import WebSocketSendError

logger = logging.getLogger(__name__)


# =============================================================================
# JSON Sending Functions
# =============================================================================


async def send_json(
    websocket: WebSocket,
    payload: dict[str, Any],
    connection_id: str = "",
    client: str = "",
    workspace: str = "",
) -> bool:
    """Send JSON payload to WebSocket.

    Note: WebSocket.send_text() is coroutine-safe on a single connection,
    no external locking needed.

    Args:
        websocket: WebSocket connection
        payload: Data to send
        connection_id: For audit logging
        client: Client address for audit logging
        workspace: Workspace for audit logging

    Returns:
        True if send succeeded, False otherwise

    Raises:
        WebSocketSendError: On send failure with categorization
    """
    error_context = {
        "connection_id": connection_id,
        "client": client,
        "workspace": workspace,
    }

    try:
        safe_payload = jsonable_encoder(payload)
        json_text = json.dumps(safe_payload, ensure_ascii=False)
        await websocket.send_text(json_text)
        return True

    except TypeError as e:
        error_type = "serialization_error"
        logger.warning(f"WebSocket serialization error: {e}", extra=error_context)
        raise WebSocketSendError(error_type, f"JSON serialization failed: {e}", e) from e

    except ConnectionResetError as e:
        error_type = "connection_reset"
        logger.info(f"WebSocket connection reset: {e}", extra=error_context)
        raise WebSocketSendError(error_type, "Connection reset by peer", e) from e

    except BrokenPipeError as e:
        error_type = "broken_pipe"
        logger.info(f"WebSocket broken pipe: {e}", extra=error_context)
        raise WebSocketSendError(error_type, "Connection broken", e) from e

    except WebSocketDisconnect as e:
        error_type = "websocket_disconnect"
        logger.info(f"WebSocket disconnected during send: {e}", extra=error_context)
        raise WebSocketSendError(error_type, f"Client disconnected: {e}", e) from e

    except RuntimeError as e:
        error_msg = str(e).lower()
        error_type = "connection_closed" if "closed" in error_msg or "close" in error_msg else "runtime_error"
        logger.warning(f"WebSocket runtime error: {e}", extra=error_context)
        raise WebSocketSendError(error_type, str(e), e) from e

    except (RuntimeError, ValueError) as e:
        error_type = "unknown_error"
        logger.error(f"WebSocket send error ({type(e).__name__}): {e}", extra=error_context)
        raise WebSocketSendError(error_type, f"Unknown error: {e}", e) from e


async def send_json_safe(
    websocket: WebSocket,
    payload: dict[str, Any],
    connection_id: str = "",
    client: str = "",
    workspace: str = "",
    cache_root: str = "",
) -> bool:
    """Send JSON with error logging but no exception propagation.

    Returns False on any error. Used when we don't want to disrupt the loop
    for send failures.
    """
    try:
        return await send_json(websocket, payload, connection_id, client, workspace)
    except WebSocketSendError as e:
        with suppress(Exception):
            await write_ws_connection_event(
                workspace=workspace,
                cache_root=cache_root,
                endpoint="/v2/ws/runtime",
                connection_id=connection_id,
                event="send_error",
                details={"client": client, "error_type": e.error_type, "error": e.message},
            )
        return False
    except (RuntimeError, ValueError) as exc:
        logger.warning("Unexpected error in send_json_safe: %s", exc)
        return False


# =============================================================================
# Stream Line Emission
# =============================================================================


async def emit_stream_line(
    websocket: WebSocket,
    channel: str,
    line: str,
    *,
    from_snapshot: bool,
    connection_id: str = "",
    client: str = "",
    workspace: str = "",
) -> bool:
    """Emit a stream line to WebSocket with appropriate format.

    Args:
        websocket: WebSocket connection.
        channel: Channel name.
        line: Raw line content.
        from_snapshot: True if from snapshot, False if incremental.
        connection_id: Connection identifier.
        client: Client address.
        workspace: Workspace path.

    Returns:
        True if sent successfully.
    """
    parsed = parse_json_line(line)

    if channel == "dialogue":
        return await send_json_safe(
            websocket,
            {
                "type": "dialogue_event",
                "channel": channel,
                "event": parsed or {"text": str(line or "").strip()},
                "snapshot": from_snapshot,
            },
            connection_id=connection_id,
            client=client,
            workspace=workspace,
        )

    if channel == "runtime_events":
        return await send_json_safe(
            websocket,
            {
                "type": "runtime_event",
                "channel": channel,
                "event": parsed or {"text": str(line or "").strip()},
                "snapshot": from_snapshot,
            },
            connection_id=connection_id,
            client=client,
            workspace=workspace,
        )

    if is_llm_channel(channel):
        payload: dict[str, Any] = {
            "type": "llm_stream",
            "channel": channel,
            "line": str(line or ""),
            "snapshot": from_snapshot,
        }
        if parsed is not None:
            payload["event"] = parsed
        return await send_json_safe(websocket, payload, connection_id=connection_id, client=client, workspace=workspace)

    if is_process_channel(channel):
        payload = {"type": "process_stream", "channel": channel, "line": str(line or ""), "snapshot": from_snapshot}
        if parsed is not None:
            payload["event"] = parsed
        return await send_json_safe(websocket, payload, connection_id=connection_id, client=client, workspace=workspace)

    return False


__all__ = [
    "emit_stream_line",
    "send_json",
    "send_json_safe",
]
