"""Main loop implementation for the runtime.v2 WebSocket endpoint."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from polaris.delivery.ws.endpoints.client_message import handle_client_message
from polaris.delivery.ws.endpoints.models import is_websocket_disconnect_runtime_error
from polaris.delivery.ws.endpoints.stream import send_json_safe
from polaris.infrastructure.messaging.nats.nats_types import RuntimeEventEnvelope

if TYPE_CHECKING:
    from fastapi import WebSocket
    from polaris.infrastructure.messaging.nats.ws_consumer_manager import JetStreamConsumerManager

logger = logging.getLogger(__name__)


_STATUS_EVENT_DOMAINS = {"director", "pm", "chief_engineer", "chief-engineer", "qa", "factory", "task", "stage"}
_STATUS_EVENT_TRANSITIONS = {
    "started",
    "starting",
    "running",
    "claimed",
    "in_progress",
    "completed",
    "failed",
    "cancelled",
    "canceled",
    "updated",
    "generated",
    "validated",
}


def _text_parts(value: Any) -> list[str]:
    if isinstance(value, dict):
        parts: list[str] = []
        for key in ("channel", "kind", "name", "event", "type", "domain", "actor", "role", "stage", "status", "state"):
            item = value.get(key)
            if item is not None:
                parts.append(str(item))
        return parts
    if value is None:
        return []
    return [str(value)]


def _runtime_event_affects_status(payload: dict[str, Any]) -> bool:
    """Return True when a realtime payload should trigger a status snapshot.

    Runtime event streams carry both high-volume logs and lifecycle changes.
    Only lifecycle-like events should rebuild the status projection; this keeps
    the update event-driven without turning token streams into status churn.
    """

    channel = str(payload.get("channel") or "").strip().lower()
    if channel.startswith("status."):
        return True
    if channel.startswith("event.") and any(domain in channel for domain in _STATUS_EVENT_DOMAINS):
        return True

    parts = _text_parts(payload)
    for nested_key in ("payload", "meta", "data", "event"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            parts.extend(_text_parts(nested))

    normalized = " ".join(parts).lower().replace("-", "_").replace(".", "_")
    has_domain = any(domain.replace("-", "_") in normalized for domain in _STATUS_EVENT_DOMAINS)
    has_transition = any(transition in normalized for transition in _STATUS_EVENT_TRANSITIONS)
    return has_domain and has_transition


def _runtime_envelope_affects_status(event: RuntimeEventEnvelope) -> bool:
    return _runtime_event_affects_status(event.to_dict())


async def run_main_loop(
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
    send_status_func: Any,
) -> tuple[int | None, str]:
    """Run the runtime.v2 WebSocket event loop.

    Product realtime has a single rail:
    Nats-JetStream -> JetStreamConsumerManager -> runtime.v2 WebSocket EVENT.
    File snapshots, process-local fanout, signal hub watchers, and timer-based
    incremental scans are deliberately not event sources in this loop.
    """

    active = True
    status_sig, _ = await send_status_func(force=True)

    receive_task: asyncio.Task[str] = asyncio.create_task(websocket.receive_text())
    close_code: int | None = None
    close_reason = ""
    v2_consume_task: asyncio.Task[RuntimeEventEnvelope | None] | None = None

    try:
        while active:
            wait_set: set[asyncio.Task[Any]] = {receive_task}

            if v2_consumer_manager and v2_consumer_manager.is_connected:
                if v2_consume_task is None:
                    v2_consume_task = asyncio.create_task(v2_consumer_manager.next_message(timeout=None))
                wait_set.add(v2_consume_task)
            elif v2_consume_task is not None:
                v2_consume_task.cancel()
                v2_consume_task = None

            done, _ = await asyncio.wait(wait_set, return_when=asyncio.FIRST_COMPLETED)

            if receive_task in done:
                try:
                    raw = receive_task.result()
                except RuntimeError as exc:
                    if is_websocket_disconnect_runtime_error(exc):
                        close_code = 1001
                        close_reason = f"client_disconnect:{exc!s}"
                        active = False
                        continue
                    raise
                previous_v2_consumer_manager = v2_consumer_manager
                (
                    status_sig,
                    tail_lines,
                    v2_protocol,
                    v2_consumer_manager,
                    v2_client_id,
                    v2_channels,
                    v2_cursor,
                ) = await handle_client_message(
                    raw=raw,
                    status_sig=status_sig,
                    websocket=websocket,
                    state=state,
                    resolved_workspace=resolved_workspace,
                    cache_root=cache_root,
                    roles_filter=roles_filter,
                    connection_id=connection_id,
                    client=client,
                    tail_lines=tail_lines,
                    v2_protocol=v2_protocol,
                    v2_consumer_manager=v2_consumer_manager,
                    v2_client_id=v2_client_id,
                    v2_channels=v2_channels,
                    v2_cursor=v2_cursor,
                )
                if previous_v2_consumer_manager is not v2_consumer_manager and v2_consume_task is not None:
                    v2_consume_task.cancel()
                    v2_consume_task = None
                receive_task = asyncio.create_task(websocket.receive_text())

            if v2_consume_task is not None and v2_consume_task in done:
                v2_event = v2_consume_task.result()
                v2_consume_task = None
                if v2_consumer_manager and v2_consumer_manager.is_connected:
                    consume_dropped = getattr(v2_consumer_manager, "consume_dropped", None)
                    if callable(consume_dropped):
                        dropped = 0
                        try:
                            dropped = int(consume_dropped())
                        except (RuntimeError, ValueError, TypeError):
                            logger.debug("Failed to consume JetStream dropped-events signal", exc_info=True)
                        if dropped > 0:
                            logger.info(
                                "JetStream events dropped for %s: %s, sending resync",
                                connection_id,
                                dropped,
                                extra={"client": client, "workspace": resolved_workspace},
                            )
                            await send_json_safe(
                                websocket,
                                {
                                    "type": "RESYNC_REQUIRED",
                                    "protocol": "runtime.v2",
                                    "cursor": v2_cursor,
                                    "reason": "events_dropped",
                                },
                                connection_id=connection_id,
                                client=client,
                                workspace=resolved_workspace,
                            )
                if v2_event and isinstance(v2_event, RuntimeEventEnvelope):
                    event_cursor = v2_event.cursor
                    v2_sent = await send_json_safe(
                        websocket,
                        {
                            "type": "EVENT",
                            "protocol": "runtime.v2",
                            "cursor": event_cursor,
                            "event": v2_event.to_dict(),
                        },
                        connection_id=connection_id,
                        client=client,
                        workspace=resolved_workspace,
                    )
                    if v2_sent:
                        v2_cursor = event_cursor
                        if _runtime_envelope_affects_status(v2_event):
                            status_sig, _ = await send_status_func(force=False, last_sig=status_sig)
                    else:
                        close_code = 1011
                        close_reason = "runtime_v2_send_failed"
                        active = False

    finally:
        active = False
        for task in (receive_task, v2_consume_task):
            if task is None:
                continue
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        if v2_consumer_manager:
            with suppress(Exception):
                await v2_consumer_manager.disconnect()

    return close_code, close_reason


__all__ = ["run_main_loop"]
