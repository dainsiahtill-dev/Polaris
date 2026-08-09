from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from fastapi import WebSocket
from polaris.delivery.ws.endpoints.websocket_loop import run_main_loop
from polaris.infrastructure.messaging.nats.nats_types import RuntimeEventEnvelope


class _ControlledWebSocket:
    def __init__(self, disconnect: asyncio.Event) -> None:
        self._disconnect = disconnect

    async def receive_text(self) -> str:
        await self._disconnect.wait()
        raise RuntimeError('WebSocket is not connected. Need to call "accept" first.')


class _QueuedConsumerManager:
    is_connected = True

    def __init__(self) -> None:
        self.queue: asyncio.Queue[RuntimeEventEnvelope] = asyncio.Queue()
        self.disconnected = False

    async def next_message(self, timeout: float | None = None) -> RuntimeEventEnvelope | None:
        assert timeout is None
        return await self.queue.get()

    def consume_dropped(self) -> int:
        return 0

    async def disconnect(self) -> None:
        self.disconnected = True
        self.is_connected = False


def _event(cursor: int) -> RuntimeEventEnvelope:
    return RuntimeEventEnvelope(
        workspace_key="workspace",
        run_id="factory-run-1",
        channel="event.factory:factory-run-1",
        kind="task_runtime_execution",
        cursor=cursor,
        payload={
            "type": "task_runtime_execution",
            "event_type": "updated",
            "task_id": f"TASK-{cursor}",
        },
    )


def _loop_kwargs(
    *,
    websocket: _ControlledWebSocket,
    manager: _QueuedConsumerManager,
    build_status_func: Any,
) -> dict[str, Any]:
    return {
        "websocket": cast(WebSocket, websocket),
        "state": object(),
        "resolved_workspace": "C:/Temp/workspace",
        "cache_root": "C:/Temp/runtime",
        "roles_filter": set(),
        "connection_id": "ws-status-coalescing",
        "client": "127.0.0.1:50000",
        "tail_lines": 200,
        "v2_protocol": "runtime.v2",
        "v2_consumer_manager": cast(Any, manager),
        "v2_client_id": "client-1",
        "v2_channels": ["event.factory:factory-run-1"],
        "v2_cursor": 0,
        "build_status_func": build_status_func,
    }


@pytest.mark.asyncio
async def test_status_refresh_is_latest_wins_and_does_not_block_event_consumption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.delivery.ws.endpoints import websocket_loop

    disconnect = asyncio.Event()
    websocket = _ControlledWebSocket(disconnect)
    manager = _QueuedConsumerManager()
    sent_cursors: list[int] = []
    sent_status_markers: list[str] = []
    all_events_sent = asyncio.Event()
    first_refresh_started = asyncio.Event()
    release_first_refresh = asyncio.Event()
    second_refresh_done = asyncio.Event()
    second_status_sent = asyncio.Event()
    nonforce_calls = 0
    active_refreshes = 0
    max_active_refreshes = 0

    async def _record_send_json(
        _websocket: Any,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> bool:
        if payload.get("type") == "EVENT":
            sent_cursors.append(int(payload["cursor"]))
            if len(sent_cursors) == 20:
                all_events_sent.set()
        elif payload.get("type") == "status":
            sent_status_markers.append(str(payload["marker"]))
            if payload["marker"] == "status-3":
                second_status_sent.set()
        return True

    async def _build_status() -> tuple[str, dict[str, Any]]:
        nonlocal nonforce_calls, active_refreshes, max_active_refreshes
        if nonforce_calls == 0 and not first_refresh_started.is_set():
            nonforce_calls += 1
            return "initial", {"type": "status", "marker": "initial"}
        nonforce_calls += 1
        active_refreshes += 1
        max_active_refreshes = max(max_active_refreshes, active_refreshes)
        try:
            if nonforce_calls == 2:
                first_refresh_started.set()
                await release_first_refresh.wait()
            else:
                second_refresh_done.set()
            marker = f"status-{nonforce_calls}"
            return marker, {"type": "status", "marker": marker}
        finally:
            active_refreshes -= 1

    monkeypatch.setattr(websocket_loop, "send_json_safe", _record_send_json)
    monkeypatch.setattr(websocket_loop, "_STATUS_REFRESH_COALESCE_SECONDS", 0.0)

    await manager.queue.put(_event(1))
    loop_task = asyncio.create_task(
        run_main_loop(**_loop_kwargs(websocket=websocket, manager=manager, build_status_func=_build_status))
    )
    await asyncio.wait_for(first_refresh_started.wait(), timeout=1)
    for cursor in range(2, 21):
        await manager.queue.put(_event(cursor))

    await asyncio.wait_for(all_events_sent.wait(), timeout=1)
    assert nonforce_calls == 2
    release_first_refresh.set()
    await asyncio.wait_for(second_refresh_done.wait(), timeout=1)
    await asyncio.wait_for(second_status_sent.wait(), timeout=1)
    disconnect.set()

    close_code, close_reason = await asyncio.wait_for(loop_task, timeout=1)

    assert close_code == 1001
    assert close_reason.startswith("client_disconnect:")
    assert sent_cursors == list(range(1, 21))
    assert sent_status_markers == ["initial", "status-3"]
    assert nonforce_calls == 3
    assert max_active_refreshes == 1
    assert manager.disconnected is True


@pytest.mark.asyncio
async def test_disconnect_cancels_pending_status_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    from polaris.delivery.ws.endpoints import websocket_loop

    disconnect = asyncio.Event()
    websocket = _ControlledWebSocket(disconnect)
    manager = _QueuedConsumerManager()
    refresh_started = asyncio.Event()
    refresh_cancelled = asyncio.Event()

    async def _send_json_ok(*_args: Any, **_kwargs: Any) -> bool:
        return True

    initial_build = True

    async def _build_status() -> tuple[str, dict[str, Any]]:
        nonlocal initial_build
        if initial_build:
            initial_build = False
            return "initial", {"type": "status"}
        refresh_started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            refresh_cancelled.set()
            raise
        return "unreachable", {"type": "status"}

    monkeypatch.setattr(websocket_loop, "send_json_safe", _send_json_ok)
    monkeypatch.setattr(websocket_loop, "_STATUS_REFRESH_COALESCE_SECONDS", 0.0)

    await manager.queue.put(_event(1))
    loop_task = asyncio.create_task(
        run_main_loop(**_loop_kwargs(websocket=websocket, manager=manager, build_status_func=_build_status))
    )
    await asyncio.wait_for(refresh_started.wait(), timeout=1)
    disconnect.set()

    close_code, close_reason = await asyncio.wait_for(loop_task, timeout=1)

    assert close_code == 1001
    assert close_reason.startswith("client_disconnect:")
    assert refresh_cancelled.is_set()
    assert manager.disconnected is True


@pytest.mark.asyncio
async def test_simultaneous_ready_event_precedes_status_that_can_observe_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A STATUS snapshot may not overtake the causal runtime EVENT cursor."""

    from polaris.delivery.ws.endpoints import websocket_loop

    disconnect = asyncio.Event()
    websocket = _ControlledWebSocket(disconnect)
    manager = _QueuedConsumerManager()
    sends: list[str] = []
    build_count = 0
    simultaneous_forced = False

    async def _record_send_json(
        _websocket: Any,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> bool:
        payload_type = str(payload.get("type") or "")
        if payload_type == "EVENT":
            sends.append(f"EVENT:{payload['cursor']}")
        elif payload_type == "status":
            marker = str(payload.get("marker") or "")
            sends.append(f"STATUS:{marker}")
            if marker == "contains-event-2":
                disconnect.set()
        return True

    async def _build_status() -> tuple[str, dict[str, Any]]:
        nonlocal build_count
        build_count += 1
        marker = "initial" if build_count == 1 else "contains-event-2"
        return marker, {"type": "status", "marker": marker}

    real_wait = asyncio.wait

    async def _wait_with_simultaneous_status_and_event(
        tasks: set[asyncio.Task[Any]],
        *,
        timeout: float | None = None,
        return_when: str = asyncio.FIRST_COMPLETED,
    ) -> tuple[set[asyncio.Task[Any]], set[asyncio.Task[Any]]]:
        nonlocal simultaneous_forced
        # After EVENT:1 schedules a status refresh, force the refresh and the
        # already-queued EVENT:2 consume to complete before the owner loop
        # resumes. This deterministically exercises the simultaneous-ready path.
        non_receive = {
            task
            for task in tasks
            if getattr(task.get_coro(), "__name__", "") != "receive_text"
        }
        if not simultaneous_forced and len(tasks) == 3 and len(non_receive) == 2:
            simultaneous_forced = True
            await asyncio.gather(*non_receive)
            return non_receive, tasks - non_receive
        return await real_wait(tasks, timeout=timeout, return_when=return_when)

    monkeypatch.setattr(websocket_loop, "send_json_safe", _record_send_json)
    monkeypatch.setattr(websocket_loop, "_STATUS_REFRESH_COALESCE_SECONDS", 0.0)
    monkeypatch.setattr(websocket_loop.asyncio, "wait", _wait_with_simultaneous_status_and_event)

    await manager.queue.put(_event(1))
    await manager.queue.put(_event(2))
    close_code, close_reason = await asyncio.wait_for(
        run_main_loop(**_loop_kwargs(websocket=websocket, manager=manager, build_status_func=_build_status)),
        timeout=1,
    )

    assert close_code == 1001
    assert close_reason.startswith("client_disconnect:")
    assert sends == [
        "STATUS:initial",
        "EVENT:1",
        "EVENT:2",
        "STATUS:contains-event-2",
    ]


@pytest.mark.asyncio
async def test_failed_simultaneous_event_suppresses_status_that_can_observe_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed causal EVENT send must suppress the same-turn STATUS."""

    from polaris.delivery.ws.endpoints import websocket_loop

    disconnect = asyncio.Event()
    websocket = _ControlledWebSocket(disconnect)
    manager = _QueuedConsumerManager()
    sends: list[str] = []
    build_count = 0
    simultaneous_forced = False

    async def _record_send_json(
        _websocket: Any,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> bool:
        payload_type = str(payload.get("type") or "")
        if payload_type == "EVENT":
            cursor = int(payload["cursor"])
            sends.append(f"EVENT:{cursor}")
            return cursor != 2
        if payload_type == "status":
            sends.append(f"STATUS:{payload.get('marker')}")
        return True

    async def _build_status() -> tuple[str, dict[str, Any]]:
        nonlocal build_count
        build_count += 1
        marker = "initial" if build_count == 1 else "contains-event-2"
        return marker, {"type": "status", "marker": marker}

    real_wait = asyncio.wait

    async def _wait_with_simultaneous_status_and_event(
        tasks: set[asyncio.Task[Any]],
        *,
        timeout: float | None = None,
        return_when: str = asyncio.FIRST_COMPLETED,
    ) -> tuple[set[asyncio.Task[Any]], set[asyncio.Task[Any]]]:
        nonlocal simultaneous_forced
        non_receive = {
            task
            for task in tasks
            if getattr(task.get_coro(), "__name__", "") != "receive_text"
        }
        if not simultaneous_forced and len(tasks) == 3 and len(non_receive) == 2:
            simultaneous_forced = True
            await asyncio.gather(*non_receive)
            return non_receive, tasks - non_receive
        return await real_wait(tasks, timeout=timeout, return_when=return_when)

    monkeypatch.setattr(websocket_loop, "send_json_safe", _record_send_json)
    monkeypatch.setattr(websocket_loop, "_STATUS_REFRESH_COALESCE_SECONDS", 0.0)
    monkeypatch.setattr(websocket_loop.asyncio, "wait", _wait_with_simultaneous_status_and_event)

    await manager.queue.put(_event(1))
    await manager.queue.put(_event(2))
    close_code, close_reason = await asyncio.wait_for(
        run_main_loop(**_loop_kwargs(websocket=websocket, manager=manager, build_status_func=_build_status)),
        timeout=1,
    )

    assert close_code == 1011
    assert close_reason == "runtime_v2_send_failed"
    assert sends == ["STATUS:initial", "EVENT:1", "EVENT:2"]
    assert manager.disconnected is True
