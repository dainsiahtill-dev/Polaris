"""Main loop implementation for runtime WebSocket endpoint.

This module contains the main event loop for WebSocket handling,
including signal-triggered updates and event processing.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from polaris.delivery.ws.endpoints.client_message import handle_client_message
from polaris.delivery.ws.endpoints.helpers import (
    JOURNAL_CHANNELS,
    stream_seen,
    stream_signature,
    wants_role,
)
from polaris.delivery.ws.endpoints.signature_utils import remember_stream_signature
from polaris.delivery.ws.endpoints.stream import (
    emit_stream_line,
    send_json_safe,
)
from polaris.infrastructure.messaging.nats.nats_types import RuntimeEventEnvelope
from polaris.infrastructure.realtime.process_local.message_event_fanout import RUNTIME_EVENT_FANOUT
from polaris.infrastructure.realtime.process_local.signal_hub import REALTIME_SIGNAL_HUB

if TYPE_CHECKING:
    from fastapi import WebSocket
    from polaris.infrastructure.messaging.nats.ws_consumer_manager import JetStreamConsumerManager
    from polaris.infrastructure.realtime.process_local.log_fanout import RealtimeLogSubscription

logger = logging.getLogger(__name__)


async def run_main_loop(
    websocket: WebSocket,
    state: Any,
    resolved_workspace: str,
    cache_root: str,
    roles_filter: set[str],
    connection_id: str,
    client: str,
    tail_lines: int,
    legacy_subscriptions: set[str],
    v2_protocol: str | None,
    v2_consumer_manager: JetStreamConsumerManager | None,
    v2_client_id: str,
    v2_channels: list[str],
    v2_cursor: int,
    canonical_journal_channels: set[str],
    channel_states: dict[str, dict[str, Any]],
    journal_state: dict[str, Any],
    legacy_channel_states: dict[str, dict[str, Any]],
    stream_signatures: set[str],
    stream_signature_order: Any,
    realtime_subscription: RealtimeLogSubscription | None,
    send_status_func: Any,
    send_all_snapshots_func: Any,
    send_incrementals_func: Any,
) -> tuple[int | None, str]:
    """Run the main WebSocket event loop.

    Returns:
        Tuple of (close_code, close_reason) on exit.
    """

    from polaris.delivery.ws.endpoints.protocol import build_status_payload

    active = True
    status_sig, _ = await send_status_func(force=True)
    await send_all_snapshots_func()

    receive_task: asyncio.Task[str] = asyncio.create_task(websocket.receive_text())
    signal_seq = 0
    signal_task: asyncio.Task[int] = asyncio.create_task(
        REALTIME_SIGNAL_HUB.wait_for_update(signal_seq, timeout_sec=0.5, workspace=cache_root)
    )
    realtime_task: asyncio.Task[dict[str, Any]] | None = None
    if realtime_subscription is not None:
        realtime_task = asyncio.create_task(realtime_subscription.queue.get())

    close_code: int | None = None
    close_reason = ""

    v2_poll_task: asyncio.Task[RuntimeEventEnvelope | None] | None = None

    try:
        while active:
            wait_set: set[asyncio.Task[Any]] = {receive_task, signal_task}
            if realtime_task is not None:
                wait_set.add(realtime_task)

            # Add v2 consumer task if active
            v2_poll_task = None
            if v2_consumer_manager and v2_consumer_manager.is_connected:
                v2_poll_task = asyncio.create_task(v2_consumer_manager.next_message(timeout=0.1))
                wait_set.add(v2_poll_task)

            done, _ = await asyncio.wait(wait_set, return_when=asyncio.FIRST_COMPLETED)

            sent_any = False
            needs_resync = False

            if receive_task in done:
                raw = receive_task.result()
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
                    legacy_subscriptions=legacy_subscriptions,
                    legacy_channel_states=legacy_channel_states,
                    send_status_func=send_status_func,
                    send_all_snapshots_func=send_all_snapshots_func,
                )
                receive_task = asyncio.create_task(websocket.receive_text())

            signal_triggered = False
            if signal_task in done:
                next_signal_seq = signal_task.result()
                signal_triggered = next_signal_seq != signal_seq
                signal_seq = next_signal_seq
                signal_task = asyncio.create_task(
                    REALTIME_SIGNAL_HUB.wait_for_update(signal_seq, timeout_sec=0.5, workspace=cache_root)
                )

                if signal_triggered:
                    if "court_status" in legacy_subscriptions:
                        status_payload = await build_status_payload(state, resolved_workspace, cache_root, roles_filter)
                        court_ok = await send_json_safe(
                            websocket,
                            {"type": "court_status", "court_state": status_payload.get("court_state")},
                            connection_id=connection_id,
                            client=client,
                            workspace=resolved_workspace,
                        )
                        sent_any = sent_any or court_ok
                    sent_any = sent_any or await send_incrementals_func()

            if realtime_task is not None and realtime_task in done:
                first_realtime_event = realtime_task.result()
                realtime_task = (
                    asyncio.create_task(realtime_subscription.queue.get()) if realtime_subscription else None
                )
                realtime_sent, realtime_resync = await _drain_realtime_log_events(
                    websocket=websocket,
                    connection_id=connection_id,
                    client=client,
                    resolved_workspace=resolved_workspace,
                    v2_protocol=v2_protocol,
                    realtime_subscription=realtime_subscription,
                    canonical_journal_channels=canonical_journal_channels,
                    stream_signatures=stream_signatures,
                    stream_signature_order=stream_signature_order,
                    first_event=first_realtime_event,
                )
                sent_any = sent_any or realtime_sent
                needs_resync = needs_resync or realtime_resync

            # Handle v2 JetStream messages
            if v2_poll_task is not None and v2_poll_task in done:
                v2_event = v2_poll_task.result()
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
                        sent_any = True
                    else:
                        close_code = 1011
                        close_reason = "runtime_v2_send_failed"
                        active = False

            if v2_consumer_manager and v2_consumer_manager.is_connected:
                consume_dropped = getattr(v2_consumer_manager, "consume_dropped", None)
                if callable(consume_dropped):
                    dropped = 0
                    try:
                        dropped = int(consume_dropped())
                    except (RuntimeError, ValueError, TypeError):
                        logger.debug("Failed to consume JetStream dropped-events signal", exc_info=True)
                    if dropped > 0:
                        needs_resync = True
                        logger.info(
                            "JetStream events dropped for %s: %s, triggering resync",
                            connection_id,
                            dropped,
                            extra={"client": client, "workspace": resolved_workspace},
                        )

            if not active:
                continue

            fanout_sent, fanout_resync = await _drain_fanout_events(
                websocket=websocket,
                connection_id=connection_id,
                client=client,
                resolved_workspace=resolved_workspace,
                roles_filter=roles_filter,
            )
            sent_any = sent_any or fanout_sent
            needs_resync = needs_resync or fanout_resync

            # Handle resync if needed
            if needs_resync:
                status_sig, _ = await send_status_func(force=True, last_sig=status_sig)
                await send_all_snapshots_func()
                sent_any = True
                if v2_consumer_manager and v2_consumer_manager.is_connected:
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

            # Cancel leaked v2 poll task
            if v2_poll_task is not None and v2_poll_task not in done:
                v2_poll_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await v2_poll_task

    finally:
        active = False
        for task in (receive_task, signal_task, realtime_task, v2_poll_task):
            if task is None:
                continue
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        if v2_consumer_manager:
            with suppress(Exception):
                await v2_consumer_manager.disconnect()

    return close_code, close_reason


async def _drain_fanout_events(
    websocket: WebSocket,
    connection_id: str,
    client: str,
    resolved_workspace: str,
    roles_filter: set[str],
) -> tuple[bool, bool]:
    """Drain events from RuntimeEventFanout."""
    from datetime import datetime, timezone

    sent_any = False
    needs_resync = False

    if not wants_role(roles_filter, "director"):
        await RUNTIME_EVENT_FANOUT.drain_events(connection_id)
        return False, False

    file_events, task_trace_events, sequential_events, total_dropped = await RUNTIME_EVENT_FANOUT.drain_events(
        connection_id
    )

    for item in file_events:
        payload = {"type": "file_edit", "event": item, "timestamp": datetime.now(timezone.utc).isoformat()}
        if await send_json_safe(
            websocket, payload, connection_id=connection_id, client=client, workspace=resolved_workspace
        ):
            sent_any = True

    for item in task_trace_events:
        if await send_json_safe(
            websocket, item, connection_id=connection_id, client=client, workspace=resolved_workspace
        ):
            sent_any = True

    for item in sequential_events:
        if await send_json_safe(
            websocket, item, connection_id=connection_id, client=client, workspace=resolved_workspace
        ):
            sent_any = True

    if total_dropped > 0:
        needs_resync = True
        logger.info(
            f"Events dropped for {connection_id}: {total_dropped}, triggering resync",
            extra={"client": client, "workspace": resolved_workspace},
        )

    return sent_any, needs_resync


async def _drain_realtime_log_events(
    websocket: WebSocket,
    connection_id: str,
    client: str,
    resolved_workspace: str,
    v2_protocol: str | None,
    realtime_subscription: RealtimeLogSubscription | None,
    canonical_journal_channels: set[str],
    stream_signatures: set[str],
    stream_signature_order: Any,
    first_event: dict[str, Any] | None = None,
) -> tuple[bool, bool]:
    """Drain in-process canonical journal events from realtime fanout."""
    if realtime_subscription is None:
        return False, False
    del v2_protocol

    sent_any = False
    needs_resync = False
    pending: list[dict[str, Any]] = []
    if isinstance(first_event, dict):
        pending.append(first_event)

    # Drain backlog
    while True:
        try:
            item = realtime_subscription.queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        if isinstance(item, dict):
            pending.append(item)
        if len(pending) >= 512:
            break

    for event_payload in pending:
        channel = str(event_payload.get("channel") or "").strip().lower()
        if channel not in JOURNAL_CHANNELS:
            domain = str(event_payload.get("domain") or "").strip().lower()
            channel = "llm" if domain == "llm" else ("process" if domain == "process" else "system")
        if channel not in canonical_journal_channels:
            continue

        line = json.dumps(event_payload, ensure_ascii=False)
        signature = stream_signature(channel=channel, line=line, payload=event_payload)
        if stream_seen(stream_signatures, signature):
            continue

        if await emit_stream_line(
            websocket,
            channel,
            line,
            from_snapshot=False,
            connection_id=connection_id,
            client=client,
            workspace=resolved_workspace,
        ):
            sent_any = True
            remember_stream_signature(stream_signatures, stream_signature_order, signature)

    dropped = realtime_subscription.consume_dropped()
    if dropped > 0:
        needs_resync = True
        logger.info(
            "Realtime log events dropped for %s: %s, triggering resync",
            connection_id,
            dropped,
            extra={"client": client, "workspace": resolved_workspace},
        )
    return sent_any, needs_resync


__all__ = ["run_main_loop"]
