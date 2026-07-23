from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import WebSocketDisconnect
from polaris.delivery.ws.endpoints.client_message import handle_client_message
from polaris.delivery.ws.endpoints.stream import _WS_FRAME_MAX_BYTES, emit_stream_line, send_json
from polaris.delivery.ws.endpoints.websocket_loop import run_main_loop
from polaris.infrastructure.messaging.nats.nats_types import RuntimeEventEnvelope


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.raw_frames: list[str] = []

    async def send_text(self, data: str) -> None:
        self.raw_frames.append(data)
        self.messages.append(json.loads(data))


class FakeRuntimeWebSocket(FakeWebSocket):
    def __init__(self) -> None:
        super().__init__()
        self.query_params = {"token": "ok"}
        self.client = SimpleNamespace(host="127.0.0.1", port=12345)
        self.app = SimpleNamespace(
            state=SimpleNamespace(
                auth=SimpleNamespace(check=lambda token: token == "Bearer ok"),
                app_state=SimpleNamespace(settings=SimpleNamespace(workspace="C:/workspace", ramdisk_root="")),
            )
        )
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000) -> None:
        self.messages.append({"type": "closed", "code": code})


async def _send_status(**_kwargs: Any) -> tuple[str, dict[str, Any]]:
    return "status-sig", {"type": "status"}


def _run_loop_kwargs(manager: Any, websocket: Any) -> dict[str, Any]:
    return {
        "websocket": cast(Any, websocket),
        "state": SimpleNamespace(),
        "resolved_workspace": "C:/workspace",
        "cache_root": "C:/runtime",
        "roles_filter": set(),
        "connection_id": "conn-1",
        "client": "test-client",
        "tail_lines": 200,
        "v2_protocol": "runtime.v2",
        "v2_consumer_manager": cast(Any, manager),
        "v2_client_id": "client-1",
        "v2_channels": ["*"],
        "v2_cursor": 0,
        "send_status_func": _send_status,
    }


def test_emit_stream_line_routes_canonical_dialogue_source_to_dialogue_event() -> None:
    async def _run() -> dict[str, Any]:
        websocket = FakeWebSocket()
        sent = await emit_stream_line(
            cast(Any, websocket),
            "system",
            json.dumps(
                {
                    "channel": "system",
                    "domain": "system",
                    "source": "dialogue",
                    "message": "ignored canonical wrapper",
                    "raw": {
                        "event_id": "dialogue-1",
                        "speaker": "PM",
                        "type": "say",
                        "text": "hello from dialogue",
                    },
                },
                ensure_ascii=False,
            ),
            from_snapshot=False,
        )
        assert sent is True
        return websocket.messages[-1]

    payload = asyncio.run(_run())

    assert payload["type"] == "dialogue_event"
    assert payload["channel"] == "dialogue"
    assert payload["event"]["text"] == "hello from dialogue"


def test_send_json_elides_oversized_runtime_frame_under_ws_limit() -> None:
    # Reproduces r05: a factory ``stage_completed`` event embeds the full Director
    # ``StageResult.output`` twice → frame > 1 MiB → the websockets client drops the
    # connection (close 1009). send_json must bound the frame and keep control fields.
    async def _run() -> tuple[str, dict[str, Any]]:
        websocket = FakeWebSocket()
        huge_output = "GENERATED SOURCE LINE\n" * 80_000
        payload = {
            "type": "EVENT",
            "channel": "event.factory:run-1",
            "cursor": 11,
            "event": {
                "run_id": "run-1",
                "channel": "event.factory:run-1",
                "kind": "stage_completed",
                "payload": {
                    "type": "stage_completed",
                    "stage": "director",
                    "message": huge_output,
                    "result": {"stage": "director", "status": "success", "output": huge_output},
                    "timestamp": "2026-06-27T00:00:00Z",
                },
            },
        }
        sent = await send_json(cast(Any, websocket), payload)
        assert sent is True
        return websocket.raw_frames[-1], websocket.messages[-1]

    raw_frame, parsed = asyncio.run(_run())

    assert len(raw_frame.encode("utf-8")) <= _WS_FRAME_MAX_BYTES
    assert parsed["type"] == "EVENT"
    assert parsed["channel"] == "event.factory:run-1"
    assert parsed["cursor"] == 11
    inner = parsed["event"]["payload"]
    assert inner["type"] == "stage_completed"
    assert inner["result"]["status"] == "success"
    assert "ws-elided" in inner["result"]["output"]


def test_send_json_leaves_small_frame_byte_identical() -> None:
    async def _run() -> str:
        websocket = FakeWebSocket()
        await send_json(cast(Any, websocket), {"type": "status", "ok": True, "n": 3})
        return websocket.raw_frames[-1]

    raw_frame = asyncio.run(_run())
    assert json.loads(raw_frame) == {"type": "status", "ok": True, "n": 3}
    assert len(raw_frame.encode("utf-8")) <= _WS_FRAME_MAX_BYTES


def test_runtime_websocket_entrypoint_exposes_only_runtime_v2_loop_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _run() -> dict[str, Any]:
        from polaris.delivery.ws.endpoints import websocket_core, websocket_loop

        seen_kwargs: dict[str, Any] = {}

        async def _noop_async(*_args: Any, **_kwargs: Any) -> None:
            return None

        async def _fake_run_main_loop(**kwargs: Any) -> tuple[int | None, str]:
            seen_kwargs.update(kwargs)
            return None, "test"

        monkeypatch.setattr(
            websocket_core,
            "resolve_workspace_runtime_context",
            lambda **_kwargs: SimpleNamespace(
                workspace="C:/workspace",
                workspace_key="workspace-key",
                runtime_root="C:/runtime",
                runtime_base="C:/runtime-base",
                source="settings",
            ),
        )
        monkeypatch.setattr(websocket_core, "_log_connection_event", _noop_async)
        monkeypatch.setattr(websocket_loop, "run_main_loop", _fake_run_main_loop)

        ws = FakeRuntimeWebSocket()
        await websocket_core.runtime_websocket(cast(Any, ws))
        assert ws.accepted is True
        return seen_kwargs

    kwargs = asyncio.run(_run())

    forbidden_kwargs = {
        "legacy_subscriptions",
        "legacy_channel_states",
        "canonical_journal_channels",
        "channel_states",
        "journal_state",
        "stream_signatures",
        "stream_signature_order",
        "realtime_subscription",
        "send_all_snapshots_func",
        "send_incrementals_func",
    }
    assert forbidden_kwargs.isdisjoint(kwargs)


def test_runtime_websocket_rejects_foreign_workspace_before_subscription(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _run() -> tuple[list[dict[str, Any]], bool]:
        from polaris.delivery.ws.endpoints import websocket_core, websocket_loop

        main_loop_called = False

        async def _noop_async(*_args: Any, **_kwargs: Any) -> None:
            return None

        async def _unexpected_main_loop(**_kwargs: Any) -> tuple[int | None, str]:
            nonlocal main_loop_called
            main_loop_called = True
            return None, "unexpected"

        def _resolve_context(*, configured_workspace: str, **_kwargs: Any) -> Any:
            normalized = configured_workspace.replace("\\", "/")
            return SimpleNamespace(
                workspace=normalized,
                workspace_key=normalized.rsplit("/", 1)[-1],
                runtime_root=f"{normalized}/runtime",
                runtime_base=f"{normalized}/runtime-base",
                source="settings",
            )

        monkeypatch.setattr(websocket_core, "resolve_workspace_runtime_context", _resolve_context)
        monkeypatch.setattr(websocket_core, "_log_connection_event", _noop_async)
        monkeypatch.setattr(websocket_loop, "run_main_loop", _unexpected_main_loop)

        ws = FakeRuntimeWebSocket()
        await websocket_core.runtime_websocket(cast(Any, ws), workspace="C:/stale-instance")
        return ws.messages, main_loop_called

    messages, main_loop_called = asyncio.run(_run())

    assert main_loop_called is False
    assert {"type": "closed", "code": 1008} in messages


def test_handle_client_message_rejects_non_object_json_without_closing() -> None:
    async def _run() -> dict[str, Any]:
        websocket = FakeWebSocket()
        result = await handle_client_message(
            raw="[]",
            status_sig="sig",
            websocket=cast(Any, websocket),
            state=SimpleNamespace(),
            resolved_workspace="C:/workspace",
            cache_root="C:/runtime",
            roles_filter=set(),
            connection_id="conn-1",
            client="test-client",
            tail_lines=200,
            v2_protocol=None,
            v2_consumer_manager=None,
            v2_client_id="",
            v2_channels=[],
            v2_cursor=0,
        )
        assert result == ("sig", 200, None, None, "", [], 0)
        return websocket.messages[-1]

    payload = asyncio.run(_run())

    assert payload["type"] == "ERROR"
    assert payload["payload"]["error"] == "Invalid message"


def test_handle_client_message_rejects_legacy_subscribe_protocol() -> None:
    async def _run() -> tuple[dict[str, Any], tuple[str, int, str | None, Any, str, list[str], int]]:
        websocket = FakeWebSocket()
        result = await handle_client_message(
            raw=json.dumps({"type": "SUBSCRIBE", "channels": ["llm"]}),
            status_sig="sig",
            websocket=cast(Any, websocket),
            state=SimpleNamespace(),
            resolved_workspace="C:/workspace",
            cache_root="C:/runtime",
            roles_filter=set(),
            connection_id="conn-1",
            client="test-client",
            tail_lines=200,
            v2_protocol=None,
            v2_consumer_manager=None,
            v2_client_id="",
            v2_channels=[],
            v2_cursor=0,
        )
        return websocket.messages[-1], result

    payload, result = asyncio.run(_run())

    assert result == ("sig", 200, None, None, "", [], 0)
    assert payload["type"] == "ERROR"
    assert payload["payload"]["code"] == "RUNTIME_V2_REQUIRED"


def test_run_main_loop_disconnects_v2_consumer_on_receive_disconnect() -> None:
    class DisconnectingWebSocket:
        async def receive_text(self) -> str:
            raise WebSocketDisconnect(code=1001)

    class FakeV2ConsumerManager:
        is_connected = True

        def __init__(self) -> None:
            self.disconnected = False

        async def next_message(self, timeout: float | None = None) -> None:
            del timeout
            await asyncio.sleep(60)

        async def disconnect(self) -> None:
            self.disconnected = True
            self.is_connected = False

    async def _run() -> FakeV2ConsumerManager:
        manager = FakeV2ConsumerManager()
        with pytest.raises(WebSocketDisconnect):
            await run_main_loop(**_run_loop_kwargs(manager, DisconnectingWebSocket()))
        return manager

    manager = asyncio.run(_run())

    assert manager.disconnected is True


def test_run_main_loop_sends_resync_required_when_v2_events_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    class IdleWebSocket:
        async def receive_text(self) -> str:
            await asyncio.sleep(60)
            return ""

    class DroppedEventsConsumerManager:
        is_connected = True

        def __init__(self) -> None:
            self.disconnected = False
            self._next_calls = 0
            self._consume_calls = 0

        async def next_message(self, timeout: float | None = None) -> RuntimeEventEnvelope | None:
            del timeout
            self._next_calls += 1
            if self._next_calls == 1:
                return None
            if self._next_calls == 2:
                return RuntimeEventEnvelope(
                    workspace_key="workspace",
                    channel="director",
                    kind="task.updated",
                    cursor=7,
                    payload={"task_id": "task-1"},
                )
            await asyncio.sleep(60)
            return None

        def consume_dropped(self) -> int:
            self._consume_calls += 1
            if self._consume_calls == 1:
                return 1
            return 0

        async def disconnect(self) -> None:
            self.disconnected = True
            self.is_connected = False

    async def _run() -> tuple[int | None, str, list[dict[str, Any]], DroppedEventsConsumerManager]:
        from polaris.delivery.ws.endpoints import websocket_loop

        manager = DroppedEventsConsumerManager()
        sent_payloads: list[dict[str, Any]] = []

        async def _record_send_json(_websocket: Any, payload: dict[str, Any], **_kwargs: Any) -> bool:
            sent_payloads.append(payload)
            return payload.get("type") != "EVENT"

        monkeypatch.setattr(websocket_loop, "send_json_safe", _record_send_json)

        close_code, close_reason = await run_main_loop(**_run_loop_kwargs(manager, IdleWebSocket()))
        return close_code, close_reason, sent_payloads, manager

    close_code, close_reason, sent_payloads, manager = asyncio.run(_run())

    assert close_code == 1011
    assert close_reason == "runtime_v2_send_failed"
    assert manager.disconnected is True
    assert {
        "type": "RESYNC_REQUIRED",
        "protocol": "runtime.v2",
        "cursor": 0,
        "reason": "events_dropped",
    } in sent_payloads


def test_run_main_loop_sends_resync_before_failed_event_and_keeps_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    class IdleWebSocket:
        async def receive_text(self) -> str:
            await asyncio.sleep(60)
            return ""

    class DroppedThenEventConsumerManager:
        is_connected = True

        def __init__(self) -> None:
            self.disconnected = False
            self._next_calls = 0
            self._consume_calls = 0

        async def next_message(self, timeout: float | None = None) -> RuntimeEventEnvelope | None:
            del timeout
            self._next_calls += 1
            if self._next_calls == 1:
                return None
            if self._next_calls == 2:
                return RuntimeEventEnvelope(
                    workspace_key="workspace",
                    channel="director",
                    kind="task.updated",
                    cursor=42,
                    payload={"task_id": "task-1"},
                )
            await asyncio.sleep(60)
            return None

        def consume_dropped(self) -> int:
            self._consume_calls += 1
            if self._consume_calls == 1:
                return 1
            return 0

        async def disconnect(self) -> None:
            self.disconnected = True
            self.is_connected = False

    async def _run() -> tuple[int | None, str, list[dict[str, Any]], DroppedThenEventConsumerManager]:
        from polaris.delivery.ws.endpoints import websocket_loop

        manager = DroppedThenEventConsumerManager()
        sent_payloads: list[dict[str, Any]] = []

        async def _record_send_json(_websocket: Any, payload: dict[str, Any], **_kwargs: Any) -> bool:
            sent_payloads.append(payload)
            return payload.get("type") != "EVENT"

        monkeypatch.setattr(websocket_loop, "send_json_safe", _record_send_json)

        close_code, close_reason = await run_main_loop(**_run_loop_kwargs(manager, IdleWebSocket()))
        return close_code, close_reason, sent_payloads, manager

    close_code, close_reason, sent_payloads, manager = asyncio.run(_run())

    assert close_code == 1011
    assert close_reason == "runtime_v2_send_failed"
    assert manager.disconnected is True

    resync_messages = [payload for payload in sent_payloads if payload.get("type") == "RESYNC_REQUIRED"]
    event_messages = [payload for payload in sent_payloads if payload.get("type") == "EVENT"]
    assert resync_messages
    assert event_messages
    assert resync_messages[0]["cursor"] == 0
    assert event_messages[0]["cursor"] == 42
    assert sent_payloads.index(resync_messages[0]) < sent_payloads.index(event_messages[0])


def test_run_main_loop_uses_single_blocking_v2_consumer_task(monkeypatch: pytest.MonkeyPatch) -> None:
    class IdleWebSocket:
        async def receive_text(self) -> str:
            await asyncio.sleep(60)
            return ""

    class QueuedEventConsumerManager:
        is_connected = True

        def __init__(self) -> None:
            self.disconnected = False
            self.next_calls = 0
            self.queue: asyncio.Queue[RuntimeEventEnvelope] = asyncio.Queue()

        async def next_message(self, timeout: float | None = None) -> RuntimeEventEnvelope | None:
            assert timeout is None
            self.next_calls += 1
            return await self.queue.get()

        def consume_dropped(self) -> int:
            return 0

        async def disconnect(self) -> None:
            self.disconnected = True
            self.is_connected = False

    async def _run() -> tuple[int | None, str, list[dict[str, Any]], QueuedEventConsumerManager]:
        from polaris.delivery.ws.endpoints import websocket_loop

        manager = QueuedEventConsumerManager()
        sent_payloads: list[dict[str, Any]] = []

        async def _record_send_json(_websocket: Any, payload: dict[str, Any], **_kwargs: Any) -> bool:
            sent_payloads.append(payload)
            return payload.get("type") != "EVENT"

        async def _publish_event() -> None:
            await asyncio.sleep(0)
            await manager.queue.put(
                RuntimeEventEnvelope(
                    workspace_key="workspace",
                    channel="process",
                    kind="probe.event",
                    cursor=99,
                    payload={"marker": "v2-blocking-consumer"},
                )
            )

        monkeypatch.setattr(websocket_loop, "send_json_safe", _record_send_json)
        publisher_task = asyncio.create_task(_publish_event())
        try:
            close_code, close_reason = await asyncio.wait_for(
                run_main_loop(**_run_loop_kwargs(manager, IdleWebSocket())),
                timeout=2,
            )
        finally:
            await publisher_task
        return close_code, close_reason, sent_payloads, manager

    close_code, close_reason, sent_payloads, manager = asyncio.run(_run())

    assert close_code == 1011
    assert close_reason == "runtime_v2_send_failed"
    assert manager.disconnected is True
    assert manager.next_calls == 1
    assert [
        payload.get("event", {}).get("payload", {}).get("marker")
        for payload in sent_payloads
        if payload.get("type") == "EVENT"
    ] == ["v2-blocking-consumer"]


def test_run_main_loop_does_not_advance_v2_cursor_when_send_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    class IdleWebSocket:
        async def receive_text(self) -> str:
            await asyncio.sleep(60)
            return ""

    class OneEventConsumerManager:
        is_connected = True

        def __init__(self) -> None:
            self.disconnected = False
            self.sent_event = False

        async def next_message(self, timeout: float | None = None) -> RuntimeEventEnvelope | None:
            del timeout
            if self.sent_event:
                await asyncio.sleep(60)
                return None
            self.sent_event = True
            return RuntimeEventEnvelope(
                workspace_key="workspace",
                channel="director",
                kind="task.updated",
                cursor=42,
                payload={"task_id": "task-1"},
            )

        async def disconnect(self) -> None:
            self.disconnected = True
            self.is_connected = False

    async def _run() -> tuple[int | None, str, OneEventConsumerManager]:
        from polaris.delivery.ws.endpoints import websocket_loop

        manager = OneEventConsumerManager()

        async def _send_json_fails(*_args: Any, **_kwargs: Any) -> bool:
            return False

        monkeypatch.setattr(websocket_loop, "send_json_safe", _send_json_fails)

        close_code, close_reason = await run_main_loop(**_run_loop_kwargs(manager, IdleWebSocket()))
        return close_code, close_reason, manager

    close_code, close_reason, manager = asyncio.run(_run())

    assert close_code == 1011
    assert close_reason == "runtime_v2_send_failed"
    assert manager.disconnected is True
