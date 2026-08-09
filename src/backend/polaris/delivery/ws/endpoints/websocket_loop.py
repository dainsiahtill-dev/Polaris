"""Main loop implementation for the runtime.v2 WebSocket endpoint."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Callable

from polaris.delivery.ws.endpoints.client_message import handle_client_message
from polaris.delivery.ws.endpoints.models import WebSocketSendError, is_websocket_disconnect_runtime_error
from polaris.delivery.ws.endpoints.stream import send_json_safe
from polaris.infrastructure.messaging.nats.nats_types import RuntimeEventEnvelope

if TYPE_CHECKING:
    from fastapi import WebSocket
    from polaris.infrastructure.messaging.nats.ws_consumer_manager import JetStreamConsumerManager

logger = logging.getLogger(__name__)


_STATUS_REFRESH_COALESCE_SECONDS = 0.1

_TASK_RUNTIME_STATUS_EVENT_TYPES = frozenset(
    {
        "owner_rework_execution_prepared",
        "factory_stale_session_fenced",
        "runtime_reset_removed",
        "reexecution_reset",
        "reexecution_imported",
        "created",
        "materialized",
        "factory_run_bound",
        "updated",
        "failed",
        "cancelled",
        "reopened",
        "claimed",
        "completed",
        "suspended",
        "dependency_blockers_refreshed",
        "dependencies_unblocked",
        "terminal_session_reconciled",
        "reverse_dependency_linked",
        "downstream_dependency_reblocked",
    }
)
_FACTORY_STATUS_EVENT_KINDS = frozenset(
    {
        "factory_run_admitted",
        "workspace_run_lease_released",
        "project_completion_control_plane_blocked",
        "recovered",
        "retry_requested",
        "paused",
        "resumed",
        "metadata_updated",
        "started",
        "cancelled",
        "completed",
        "failed",
        "workspace_stale_owner_recovered",
        "factory_run_quarantined",
        "factory_stage_persistence_committed",
        "stage_started",
        "stage_completed",
    }
)
_ROLE_STATUS_CHANNELS = frozenset({"pm", "chief_engineer", "director", "qa"})
_ROLE_STATUS_EVENT_KINDS = frozenset(
    {
        "task.created",
        "task.updated",
        "task.started",
        "task.claimed",
        "task.in_progress",
        "task.completed",
        "task.failed",
        "task.cancelled",
        "stage.started",
        "stage.completed",
        "stage.failed",
        "stage.cancelled",
    }
)


def _canonical_event_token(value: Any) -> str:
    if type(value) is not str:
        return ""
    return value.strip().lower().replace("-", "_")


def _canonical_event_kind(value: Any) -> str:
    if type(value) is not str:
        return ""
    return value.strip().lower()


def _task_runtime_event_type(payload: dict[str, Any]) -> str:
    nested = payload.get("payload")
    if not isinstance(nested, dict):
        return ""
    return _canonical_event_token(nested.get("event_type"))


def _runtime_event_affects_status(payload: dict[str, Any]) -> bool:
    """Return whether one canonical runtime.v2 event changes status facts.

    Free-text lifecycle inference is forbidden. Logs, token chunks, and
    arbitrary ``event.factory:*`` traffic must not rebuild the full status
    projection merely because they contain words such as ``completed``.
    """

    channel = _canonical_event_kind(payload.get("channel"))
    kind = _canonical_event_kind(payload.get("kind"))
    if channel.startswith("status."):
        return True

    if kind == "task_runtime_execution":
        return channel.startswith("event.factory:") and (
            _task_runtime_event_type(payload) in _TASK_RUNTIME_STATUS_EVENT_TYPES
        )

    if channel.startswith("event.factory:"):
        return kind in _FACTORY_STATUS_EVENT_KINDS

    return channel in _ROLE_STATUS_CHANNELS and kind in _ROLE_STATUS_EVENT_KINDS


def _runtime_envelope_affects_status(event: RuntimeEventEnvelope) -> bool:
    return _runtime_event_affects_status(
        {
            "channel": event.channel,
            "kind": event.kind,
            "payload": event.payload,
        }
    )


async def _coalesced_status_refresh(
    *,
    build_status_func: Any,
    last_sig: str,
    generation_getter: Callable[[], int],
) -> tuple[int, str, dict[str, Any] | None]:
    """Run one bounded refresh after a short latest-wins collection window."""

    await asyncio.sleep(_STATUS_REFRESH_COALESCE_SECONDS)
    generation = generation_getter()
    status_sig, payload = await build_status_func()
    return generation, status_sig, payload if status_sig != last_sig else None


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
    build_status_func: Any,
) -> tuple[int | None, str]:
    """Run the runtime.v2 WebSocket event loop.

    Product realtime has a single rail:
    Nats-JetStream -> JetStreamConsumerManager -> runtime.v2 WebSocket EVENT.
    File snapshots, process-local fanout, signal hub watchers, and timer-based
    incremental scans are deliberately not event sources in this loop.
    """

    active = True
    status_sig, initial_status = await build_status_func()
    if not await send_json_safe(
        websocket,
        initial_status,
        connection_id=connection_id,
        client=client,
        workspace=resolved_workspace,
    ):
        raise WebSocketSendError("send_failed", "Failed to send status")

    receive_task: asyncio.Task[str] = asyncio.create_task(websocket.receive_text())
    close_code: int | None = None
    close_reason = ""
    v2_consume_task: asyncio.Task[RuntimeEventEnvelope | None] | None = None
    status_refresh_generation = 0
    status_refresh_task: asyncio.Task[tuple[int, str, dict[str, Any] | None]] | None = None

    def current_status_refresh_generation() -> int:
        return status_refresh_generation

    def schedule_status_refresh() -> None:
        nonlocal status_refresh_task
        if status_refresh_task is not None:
            return
        status_refresh_task = asyncio.create_task(
            _coalesced_status_refresh(
                build_status_func=build_status_func,
                last_sig=status_sig,
                generation_getter=current_status_refresh_generation,
            )
        )

    try:
        while active:
            wait_set: set[asyncio.Task[Any]] = {receive_task}

            if status_refresh_task is not None:
                wait_set.add(status_refresh_task)

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
                            status_refresh_generation += 1
                            schedule_status_refresh()
                    else:
                        close_code = 1011
                        close_reason = "runtime_v2_send_failed"
                        active = False
                        # The client did not receive this causal EVENT and the
                        # cursor did not advance. Never emit a same-turn STATUS
                        # snapshot that may already observe that durable event;
                        # finally cancels the pending refresh task.
                        continue

            # When EVENT and a previously scheduled STATUS refresh become ready
            # in the same loop turn, EVENT must be published first. The refresh
            # may already observe that durable event; sending it first would let
            # STATUS overtake its causal cursor. Processing EVENT above also
            # advances the generation, so a stale refresh is discarded and one
            # latest follow-up is scheduled.
            if status_refresh_task is not None and status_refresh_task in done:
                completed_refresh = status_refresh_task
                status_refresh_task = None
                refreshed_generation, refreshed_status_sig, refreshed_status = completed_refresh.result()
                if status_refresh_generation > refreshed_generation:
                    schedule_status_refresh()
                else:
                    if refreshed_status is not None and not await send_json_safe(
                        websocket,
                        refreshed_status,
                        connection_id=connection_id,
                        client=client,
                        workspace=resolved_workspace,
                    ):
                        raise WebSocketSendError("send_failed", "Failed to send status")
                    status_sig = refreshed_status_sig

    finally:
        active = False
        for task in (receive_task, v2_consume_task, status_refresh_task):
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
