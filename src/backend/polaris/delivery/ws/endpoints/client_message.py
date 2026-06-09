"""Client message handling for runtime WebSocket endpoint.

This module handles canonical runtime.v2 WebSocket messages.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from polaris.delivery.ws.endpoints.protocol import handle_v2_message
from polaris.delivery.ws.endpoints.stream import send_json_safe
from polaris.delivery.ws.runtime_event_query import handle_event_query

if TYPE_CHECKING:
    from fastapi import WebSocket
    from polaris.infrastructure.messaging.nats.ws_consumer_manager import JetStreamConsumerManager


async def handle_client_message(
    raw: str,
    status_sig: str,
    websocket: WebSocket,
    state: Any,
    resolved_workspace: str,
    cache_root: str,
    roles_filter: set[str],
    connection_id: str,
    client: str,
    tail_lines: int,
    v2_protocol: str | None,
    v2_consumer_manager: JetStreamConsumerManager | None,
    v2_client_id: str,
    v2_channels: list[str],
    v2_cursor: int,
    legacy_subscriptions: set[str],
    legacy_channel_states: dict[str, dict[str, Any]],
    send_status_func: Any,
    send_all_snapshots_func: Any,
) -> tuple[str, int, str | None, JetStreamConsumerManager | None, str, list[str], int]:
    """Handle client message and return updated state."""
    try:
        message = json.loads(raw)
    except json.JSONDecodeError as e:
        await send_json_safe(
            websocket,
            {"type": "ERROR", "payload": {"error": "Invalid JSON", "details": str(e)}},
            connection_id=connection_id,
            client=client,
            workspace=resolved_workspace,
        )
        return status_sig, tail_lines, v2_protocol, v2_consumer_manager, v2_client_id, v2_channels, v2_cursor
    except (RuntimeError, ValueError) as e:
        await send_json_safe(
            websocket,
            {"type": "ERROR", "payload": {"error": f"Parse error: {e!s}"}},
            connection_id=connection_id,
            client=client,
            workspace=resolved_workspace,
        )
        return status_sig, tail_lines, v2_protocol, v2_consumer_manager, v2_client_id, v2_channels, v2_cursor

    if not isinstance(message, dict):
        await send_json_safe(
            websocket,
            {
                "type": "ERROR",
                "payload": {
                    "error": "Invalid message",
                    "details": "WebSocket client messages must be JSON objects.",
                },
            },
            connection_id=connection_id,
            client=client,
            workspace=resolved_workspace,
        )
        return status_sig, tail_lines, v2_protocol, v2_consumer_manager, v2_client_id, v2_channels, v2_cursor

    msg_type = str(message.get("type") or "").strip().upper()
    protocol_version = str(message.get("protocol") or "").strip()

    # Compatibility: allow ACK without explicit protocol once v2 is active.
    if not protocol_version and msg_type == "ACK" and v2_consumer_manager is not None:
        protocol_version = "runtime.v2"

    # v2 Protocol Handling
    if protocol_version == "runtime.v2":
        v2_tail_ref = [tail_lines]
        v2_consumer_ref: list[JetStreamConsumerManager | None] = [v2_consumer_manager]
        v2_client_id_ref = [v2_client_id]
        v2_channels_ref: list[list[str]] = [v2_channels]
        v2_cursor_ref = [v2_cursor]

        status_sig, protocol_activated = await handle_v2_message(
            message=message,
            websocket=websocket,
            status_sig=status_sig,
            connection_id=connection_id,
            client=client,
            workspace=resolved_workspace,
            cache_root=cache_root,
            roles_filter=roles_filter,
            tail_lines_ref=v2_tail_ref,
            consumer_manager_ref=v2_consumer_ref,
            client_id_ref=v2_client_id_ref,
            channels_ref=v2_channels_ref,
            cursor_ref=v2_cursor_ref,
            state=state,
            handle_event_query_func=handle_event_query,
        )

        tail_lines = v2_tail_ref[0]
        v2_consumer_manager = v2_consumer_ref[0]
        v2_client_id = v2_client_id_ref[0]
        v2_channels = v2_channels_ref[0]
        v2_cursor = v2_cursor_ref[0]
        if protocol_activated:
            v2_protocol = "runtime.v2"
        return status_sig, tail_lines, v2_protocol, v2_consumer_manager, v2_client_id, v2_channels, v2_cursor

    await send_json_safe(
        websocket,
        {
            "type": "ERROR",
            "payload": {
                "code": "RUNTIME_V2_REQUIRED",
                "error": "WebSocket runtime messages must use protocol='runtime.v2'.",
            },
        },
        connection_id=connection_id,
        client=client,
        workspace=resolved_workspace,
    )
    return status_sig, tail_lines, v2_protocol, v2_consumer_manager, v2_client_id, v2_channels, v2_cursor


__all__ = ["handle_client_message"]
