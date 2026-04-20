"""v2 Protocol message handlers for runtime WebSocket endpoint.

This module contains handlers for:
- SUBSCRIBE: Channel subscription
- UNSUBSCRIBE: Channel unsubscription
- ACK: Cursor acknowledgment
- PING/PONG: Heartbeat
- GET_STATUS: Status request
- EVENT: Event query
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from polaris.cells.runtime.projection.public.service import (
    build_director_status,
    build_pm_status_async,
    build_status_payload_sync,
)
from polaris.delivery.ws.endpoints.helpers import (
    filter_status_payload_by_roles,
    resolve_runtime_v2_workspace_key,
    status_signature,
)
from polaris.delivery.ws.endpoints.stream import send_json_safe
from polaris.infrastructure.messaging.nats.ws_consumer_manager import JetStreamConsumerManager

logger = logging.getLogger(__name__)


async def handle_v2_message(
    message: dict[str, Any],
    websocket: Any,
    status_sig: str,
    connection_id: str,
    client: str,
    workspace: str,
    cache_root: str,
    roles_filter: set[str],
    tail_lines_ref: list[int],
    consumer_manager_ref: list[JetStreamConsumerManager | None],
    client_id_ref: list[str],
    channels_ref: list[list[str]],
    cursor_ref: list[int],
    state: Any,
    handle_event_query_func: Any,
) -> tuple[str, bool]:
    """Handle v2 protocol messages.

    This function manages the v2 protocol state and delegates to specialized
    handlers for each message type. Uses list wrappers for mutable state
    to allow updates to propagate to the caller.

    Args:
        message: Parsed message dictionary.
        websocket: WebSocket connection.
        status_sig: Current status signature.
        connection_id: Connection identifier.
        client: Client address.
        workspace: Workspace path.
        cache_root: Runtime cache root.
        roles_filter: Role filter set (mutable reference).
        tail_lines_ref: List containing tail_lines count [mutable].
        consumer_manager_ref: List containing consumer manager [mutable].
        client_id_ref: List containing client ID [mutable].
        channels_ref: List containing channels [mutable].
        cursor_ref: List containing cursor [mutable].
        state: AppState for status building.
        handle_event_query_func: Function to handle EVENT query.

    Returns:
        Tuple of (updated status_sig, protocol_activated).
    """
    msg_type = str(message.get("type") or "").strip().upper()
    protocol_activated = False

    # PING/PONG heartbeat
    if msg_type == "PING":
        await send_json_safe(
            websocket,
            {"type": "PONG", "protocol": "runtime.v2"},
            connection_id=connection_id,
            client=client,
            workspace=workspace,
        )
        return status_sig, protocol_activated

    # ACK - cursor acknowledgment
    if msg_type == "ACK":
        cursor = message.get("cursor", 0)
        if isinstance(cursor, int) and cursor > 0 and consumer_manager_ref[0]:
            await consumer_manager_ref[0].ack_cursor(cursor)
            cursor_ref[0] = consumer_manager_ref[0].get_current_cursor()
            logger.debug(f"ACK received for cursor {cursor}, updated to {cursor_ref[0]}")
        return status_sig, protocol_activated

    # SUBSCRIBE - channel subscription
    if msg_type == "SUBSCRIBE":
        # Mark protocol as activated
        protocol_activated = True

        # Extract subscription params
        client_id_ref[0] = str(message.get("client_id") or f"ws-{connection_id[:8]}").strip()

        requested_channels = message.get("channels", [])
        if isinstance(requested_channels, str):
            requested_channels = [requested_channels]
        if not requested_channels:
            requested_channels = ["*"]  # Subscribe to all by default

        channels_ref[0] = [str(ch).strip() for ch in requested_channels if isinstance(ch, str)]

        # Get initial cursor
        cursor_ref[0] = int(message.get("cursor", 0) or 0)

        # Get tail count
        tail_request = message.get("tail", 200)
        if isinstance(tail_request, int) and tail_request > 0:
            tail_lines_ref[0] = tail_request

        requested_workspace = str(message.get("workspace") or "").strip()
        workspace_key = resolve_runtime_v2_workspace_key(
            connection_workspace=workspace,
            requested_workspace=requested_workspace,
        )

        # Create JetStream consumer manager
        try:
            consumer_manager = JetStreamConsumerManager(
                workspace_key=workspace_key,
                client_id=client_id_ref[0],
                channels=channels_ref[0],
                initial_cursor=cursor_ref[0],
                tail=tail_lines_ref[0],
            )
            connected = await consumer_manager.connect()

            if connected:
                consumer_manager_ref[0] = consumer_manager
                logger.info(
                    f"v2 protocol activated: client_id={client_id_ref[0]}, "
                    f"channels={channels_ref[0]}, cursor={cursor_ref[0]}"
                )
            else:
                consumer_manager_ref[0] = None
                logger.warning("JetStream consumer failed, operating in legacy mode")
        except (RuntimeError, ValueError) as e:
            logger.error(f"Failed to create v2 consumer manager: {e}")
            consumer_manager_ref[0] = None

        # Send confirmation
        # Canonical v2 protocol: includes strategy_receipt hint for context propagation.
        # Clients should use StrategyReceipt format (polaris.kernelone.context.strategy_contracts)
        # for context enrichment on subsequent role dialogue calls.
        await send_json_safe(
            websocket,
            {
                "type": "SUBSCRIBED",
                "protocol": "runtime.v2",
                "payload": {
                    "client_id": client_id_ref[0],
                    "channels": channels_ref[0],
                    "cursor": cursor_ref[0],
                    "jetstream": consumer_manager_ref[0] is not None and consumer_manager_ref[0].is_connected,
                    # Canonical strategy_receipt context hint (v2 protocol, no legacy equivalent)
                    "strategy_receipt": {
                        "_hint": "use StrategyReceipt from polaris.kernelone.context.strategy_contracts "
                        "for canonical context enrichment in role dialogue calls",
                        "_canonical_protocol": "runtime.v2",
                    },
                },
            },
            connection_id=connection_id,
            client=client,
            workspace=workspace,
        )
        return status_sig, protocol_activated

    # UNSUBSCRIBE
    if msg_type == "UNSUBSCRIBE":
        channels = message.get("channels", [])
        if isinstance(channels, str):
            channels = [channels]
        for ch in channels:
            if ch in channels_ref[0]:
                channels_ref[0].remove(ch)

        if consumer_manager_ref[0]:
            await consumer_manager_ref[0].disconnect()
            consumer_manager_ref[0] = None

        await send_json_safe(
            websocket,
            {
                "type": "UNSUBSCRIBED",
                "protocol": "runtime.v2",
                "payload": {"channels": channels_ref[0]},
            },
            connection_id=connection_id,
            client=client,
            workspace=workspace,
        )
        return status_sig, protocol_activated

    # GET_STATUS
    if msg_type in {"STATUS", "GET_STATUS"}:
        pm_status = await build_pm_status_async(state)
        director_status = await build_director_status(state, workspace, cache_root)
        payload = await asyncio.to_thread(
            build_status_payload_sync,
            state,
            workspace,
            cache_root,
            pm_status,
            director_status,
        )
        if not isinstance(payload, dict):
            payload = {"type": "status"}
        payload.setdefault("type", "status")
        payload["protocol"] = "runtime.v2"
        payload["cursor"] = cursor_ref[0]
        await send_json_safe(
            websocket,
            payload,
            connection_id=connection_id,
            client=client,
            workspace=workspace,
        )
        return status_signature(payload), protocol_activated

    # EVENT query
    if msg_type == "EVENT":
        await handle_event_query_func(
            websocket,
            workspace,
            cache_root,
            message,
            send_json_safe=send_json_safe,
            connection_id=connection_id,
            client=client,
        )
        return status_sig, protocol_activated

    # Unknown message type
    await send_json_safe(
        websocket,
        {
            "type": "ERROR",
            "protocol": "runtime.v2",
            "payload": {"error": f"Unknown message type: {msg_type}"},
        },
        connection_id=connection_id,
        client=client,
        workspace=workspace,
    )
    return status_sig, protocol_activated


async def build_status_payload(
    state: Any,
    workspace: str,
    cache_root: str,
    roles: set[str],
) -> dict[str, Any]:
    """Build status payload with role filtering.

    Args:
        state: AppState instance.
        workspace: Workspace path.
        cache_root: Runtime cache root.
        roles: Role filter set.

    Returns:
        Status payload dictionary.
    """
    from polaris.cells.runtime.projection.public.service import (
        build_director_status,
        build_pm_status_async,
        build_status_payload_sync,
    )

    pm_status = await build_pm_status_async(state)
    director_status = await build_director_status(state, workspace, cache_root)
    payload = await asyncio.to_thread(
        build_status_payload_sync,
        state,
        workspace,
        cache_root,
        pm_status,
        director_status,
    )
    if not isinstance(payload, dict):
        payload = {"type": "status"}
    payload.setdefault("type", "status")
    return filter_status_payload_by_roles(payload, roles)


__all__ = [
    "build_status_payload",
    "handle_v2_message",
]
