"""Client message handling for runtime WebSocket endpoint.

This module contains handlers for:
- v2 Protocol message handling
- Legacy v1 Protocol message handling
- SUBSCRIBE/UNSUBSCRIBE operations
"""

from __future__ import annotations

import json
import logging
import os
import warnings
from typing import TYPE_CHECKING, Any

from polaris.delivery.ws.endpoints.helpers import (
    channel_max_chars,
    resolve_channel_path,
)
from polaris.delivery.ws.endpoints.protocol import handle_v2_message
from polaris.delivery.ws.endpoints.stream import send_json_safe
from polaris.delivery.ws.endpoints.websocket_core import STREAM_CHANNELS
from polaris.delivery.ws.runtime_event_query import handle_event_query

if TYPE_CHECKING:
    from fastapi import WebSocket
    from polaris.infrastructure.messaging.nats.ws_consumer_manager import JetStreamConsumerManager

logger = logging.getLogger(__name__)


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

    # Legacy v1 Protocol Handling (DEPRECATED — will be removed in v2.0)
    warnings.warn(
        "Legacy v1 WebSocket protocol detected. The v1 protocol is deprecated "
        "and will be removed in v2.0. "
        "Please set protocol='runtime.v2' in SUBSCRIBE messages.",
        DeprecationWarning,
        stacklevel=2,
    )

    if msg_type in {"PONG", "PING"}:
        await send_json_safe(
            websocket, {"type": "PONG"}, connection_id=connection_id, client=client, workspace=resolved_workspace
        )
        return status_sig, tail_lines, v2_protocol, v2_consumer_manager, v2_client_id, v2_channels, v2_cursor

    if msg_type in {"STATUS", "GET_STATUS"}:
        updated_sig, _ = await send_status_func(force=True, last_sig=status_sig)
        return updated_sig, tail_lines, v2_protocol, v2_consumer_manager, v2_client_id, v2_channels, v2_cursor

    if msg_type in {"GET_SNAPSHOT", "SNAPSHOT"}:
        updated_sig, _ = await send_status_func(force=True, last_sig=status_sig)
        await send_all_snapshots_func()
        return updated_sig, tail_lines, v2_protocol, v2_consumer_manager, v2_client_id, v2_channels, v2_cursor

    if msg_type == "SUBSCRIBE":
        return await _handle_subscribe(
            message=message,
            websocket=websocket,
            roles_filter=roles_filter,
            tail_lines=tail_lines,
            legacy_subscriptions=legacy_subscriptions,
            legacy_channel_states=legacy_channel_states,
            resolved_workspace=resolved_workspace,
            cache_root=cache_root,
            connection_id=connection_id,
            client=client,
            status_sig=status_sig,
            v2_protocol=v2_protocol,
            v2_consumer_manager=v2_consumer_manager,
            v2_client_id=v2_client_id,
            v2_channels=v2_channels,
            v2_cursor=v2_cursor,
        )

    if msg_type == "UNSUBSCRIBE":
        return await _handle_unsubscribe(
            message=message,
            websocket=websocket,
            legacy_subscriptions=legacy_subscriptions,
            legacy_channel_states=legacy_channel_states,
            resolved_workspace=resolved_workspace,
            connection_id=connection_id,
            client=client,
            status_sig=status_sig,
            tail_lines=tail_lines,
            v2_protocol=v2_protocol,
            v2_consumer_manager=v2_consumer_manager,
            v2_client_id=v2_client_id,
            v2_channels=v2_channels,
            v2_cursor=v2_cursor,
        )

    if msg_type == "EVENT":
        await handle_event_query(
            websocket,
            resolved_workspace,
            cache_root,
            message,
            send_json_safe=send_json_safe,
            connection_id=connection_id,
            client=client,
        )
        return status_sig, tail_lines, v2_protocol, v2_consumer_manager, v2_client_id, v2_channels, v2_cursor

    await send_json_safe(
        websocket,
        {"type": "ERROR", "payload": {"error": f"Unknown message type: {msg_type}"}},
        connection_id=connection_id,
        client=client,
        workspace=resolved_workspace,
    )
    return status_sig, tail_lines, v2_protocol, v2_consumer_manager, v2_client_id, v2_channels, v2_cursor


async def _handle_subscribe(
    message: dict[str, Any],
    websocket: WebSocket,
    roles_filter: set[str],
    tail_lines: int,
    legacy_subscriptions: set[str],
    legacy_channel_states: dict[str, dict[str, Any]],
    resolved_workspace: str,
    cache_root: str,
    connection_id: str,
    client: str,
    status_sig: str,
    v2_protocol: str | None,
    v2_consumer_manager: JetStreamConsumerManager | None,
    v2_client_id: str,
    v2_channels: list[str],
    v2_cursor: int,
) -> tuple[str, int, str | None, JetStreamConsumerManager | None, str, list[str], int]:
    """Handle SUBSCRIBE message."""

    raw_roles = message.get("roles")
    if isinstance(raw_roles, list):
        roles_filter.clear()
        for value in raw_roles:
            role_token = str(value or "").strip().lower()
            if role_token in {"pm", "director", "qa"}:
                roles_filter.add(role_token)

    requested_lines = message.get("tail_lines")
    if isinstance(requested_lines, int) and requested_lines > 0:
        tail_lines = requested_lines

    channels = message.get("channels")
    if not channels:
        channel = message.get("channel")
        channels = [channel] if channel else []
    if isinstance(channels, str):
        channels = [channels]

    if isinstance(channels, list):
        for ch in channels:
            if not isinstance(ch, str):
                continue
            normalized = ch.strip()
            if not normalized:
                continue
            warnings.warn(
                f"Legacy channel '{normalized}' via v1 protocol is deprecated and will be removed in v2.0.",
                DeprecationWarning,
                stacklevel=2,
            )
            if normalized in STREAM_CHANNELS:
                continue
            legacy_subscriptions.add(normalized)
            legacy_channel_states.setdefault(normalized, {"pos": 0})["snapshot"] = True

        await _send_legacy_snapshots(
            websocket=websocket,
            channels=[
                str(ch).strip()
                for ch in channels
                if isinstance(ch, str) and str(ch).strip() and str(ch).strip() not in STREAM_CHANNELS
            ],
            resolved_workspace=resolved_workspace,
            cache_root=cache_root,
            connection_id=connection_id,
            client=client,
            tail_lines=tail_lines,
            legacy_channel_states=legacy_channel_states,
        )

    await send_json_safe(
        websocket,
        {"type": "SUBSCRIBED", "payload": {"roles": sorted(roles_filter), "channels": sorted(legacy_subscriptions)}},
        connection_id=connection_id,
        client=client,
        workspace=resolved_workspace,
    )
    return status_sig, tail_lines, v2_protocol, v2_consumer_manager, v2_client_id, v2_channels, v2_cursor


async def _handle_unsubscribe(
    message: dict[str, Any],
    websocket: WebSocket,
    legacy_subscriptions: set[str],
    legacy_channel_states: dict[str, dict[str, Any]],
    resolved_workspace: str,
    connection_id: str,
    client: str,
    status_sig: str,
    tail_lines: int,
    v2_protocol: str | None,
    v2_consumer_manager: JetStreamConsumerManager | None,
    v2_client_id: str,
    v2_channels: list[str],
    v2_cursor: int,
) -> tuple[str, int, str | None, JetStreamConsumerManager | None, str, list[str], int]:
    """Handle UNSUBSCRIBE message."""
    channels = message.get("channels")
    if not channels:
        channel = message.get("channel")
        channels = [channel] if channel else []
    if isinstance(channels, str):
        channels = [channels]
    if isinstance(channels, list):
        for ch in channels:
            if not isinstance(ch, str):
                continue
            normalized = ch.strip()
            if not normalized:
                continue
            legacy_subscriptions.discard(normalized)
            legacy_channel_states.pop(normalized, None)
    await send_json_safe(
        websocket,
        {"type": "UNSUBSCRIBED", "payload": {"channels": sorted(legacy_subscriptions)}},
        connection_id=connection_id,
        client=client,
        workspace=resolved_workspace,
    )
    return status_sig, tail_lines, v2_protocol, v2_consumer_manager, v2_client_id, v2_channels, v2_cursor


async def _send_legacy_snapshots(
    websocket: WebSocket,
    channels: list[str],
    resolved_workspace: str,
    cache_root: str,
    connection_id: str,
    client: str,
    tail_lines: int,
    legacy_channel_states: dict[str, dict[str, Any]],
) -> bool:
    """Send snapshots for legacy v1 channels (DEPRECATED — will be removed in v2.0)."""
    from polaris.cells.runtime.projection.public.service import CHANNEL_FILES, read_file_tail

    warnings.warn(
        "send_legacy_snapshots: legacy v1 channel snapshots are deprecated and will be removed in v2.0.",
        DeprecationWarning,
        stacklevel=2,
    )
    sent_any = False
    for channel in channels:
        if channel in {"status", "court_status"}:
            continue
        if channel not in CHANNEL_FILES and channel not in {"system", "process", "llm"}:
            continue
        legacy_channel_states.setdefault(channel, {"pos": 0})
        path = resolve_channel_path(resolved_workspace, cache_root, channel)
        if path and os.path.isfile(path):
            limit = channel_max_chars(channel)
            content = read_file_tail(path, max_lines=tail_lines, max_chars=limit)
            lines = content.splitlines() if content else []
            if await send_json_safe(
                websocket,
                {"type": "snapshot", "channel": channel, "lines": lines},
                connection_id=connection_id,
                client=client,
                workspace=resolved_workspace,
            ):
                sent_any = True
    return sent_any


__all__ = ["handle_client_message"]
