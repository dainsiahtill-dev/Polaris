from __future__ import annotations

import asyncio
import json
from collections import deque
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import WebSocketDisconnect
from polaris.delivery.ws.endpoints.client_message import handle_client_message
from polaris.delivery.ws.endpoints.stream import emit_stream_line
from polaris.delivery.ws.endpoints.websocket_loop import _drain_realtime_log_events, run_main_loop


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send_text(self, data: str) -> None:
        self.messages.append(json.loads(data))


class FakeRealtimeSubscription:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def consume_dropped(self) -> int:
        return 0


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


def test_realtime_fanout_still_pushes_llm_when_v2_protocol_is_active() -> None:
    async def _run() -> dict[str, Any]:
        websocket = FakeWebSocket()
        realtime_subscription = FakeRealtimeSubscription()
        sent, needs_resync = await _drain_realtime_log_events(
            websocket=cast(Any, websocket),
            connection_id="conn-1",
            client="test-client",
            resolved_workspace="C:/workspace",
            v2_protocol="runtime.v2",
            realtime_subscription=cast(Any, realtime_subscription),
            canonical_journal_channels={"llm"},
            stream_signatures=set(),
            stream_signature_order=deque(),
            first_event={
                "event_id": "llm-1",
                "channel": "llm",
                "domain": "llm",
                "message": "live llm",
                "raw": {"stream_event": "content_chunk", "content": "live llm"},
            },
        )
        assert sent is True
        assert needs_resync is False
        return websocket.messages[-1]

    payload = asyncio.run(_run())

    assert payload["type"] == "llm_stream"
    assert payload["channel"] == "llm"
    assert payload["event"]["message"] == "live llm"


def test_runtime_websocket_snapshot_sends_channel_after_journal_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _run() -> list[str]:
        from polaris.delivery.ws.endpoints import websocket_core, websocket_loop

        calls: list[str] = []

        async def _noop_async(*_args: Any, **_kwargs: Any) -> None:
            return None

        async def _register_log(*_args: Any, **_kwargs: Any) -> None:
            return None

        async def _fake_journal_snapshot(*_args: Any, **_kwargs: Any) -> bool:
            calls.append("journal")
            return True

        async def _fake_channel_snapshot(
            _websocket: Any,
            channel: str,
            *_args: Any,
            **_kwargs: Any,
        ) -> bool:
            calls.append(f"channel:{channel}")
            return True

        async def _fake_run_main_loop(**kwargs: Any) -> tuple[int | None, str]:
            await kwargs["send_all_snapshots_func"]()
            await kwargs["send_incrementals_func"]()
            return None, "test"

        monkeypatch.setattr(websocket_core, "STREAM_CHANNELS", ("system", "dialogue"))
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
        monkeypatch.setattr(websocket_core, "send_journal_snapshot", _fake_journal_snapshot)
        monkeypatch.setattr(websocket_core, "send_channel_snapshot", _fake_channel_snapshot)
        monkeypatch.setattr(websocket_core, "send_journal_incremental", _fake_journal_snapshot)
        monkeypatch.setattr(websocket_core, "send_channel_incremental", _fake_channel_snapshot)
        monkeypatch.setattr(websocket_loop, "run_main_loop", _fake_run_main_loop)
        monkeypatch.setattr(websocket_core.RUNTIME_EVENT_FANOUT, "register_connection", _noop_async)
        monkeypatch.setattr(websocket_core.RUNTIME_EVENT_FANOUT, "unregister_connection", _noop_async)
        monkeypatch.setattr(websocket_core.LOG_REALTIME_FANOUT, "register_connection", _register_log)
        monkeypatch.setattr(websocket_core.LOG_REALTIME_FANOUT, "unregister_connection", _noop_async)
        monkeypatch.setattr(websocket_core.REALTIME_SIGNAL_HUB, "ensure_watch", _noop_async)
        monkeypatch.setattr(websocket_core.REALTIME_SIGNAL_HUB, "release_watch", lambda *_args: None)

        await websocket_core.runtime_websocket(cast(Any, FakeRuntimeWebSocket()))
        return calls

    calls = asyncio.run(_run())

    assert calls == ["journal", "channel:dialogue", "journal", "channel:dialogue"]


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
            legacy_subscriptions=set(),
            legacy_channel_states={},
            send_status_func=None,
            send_all_snapshots_func=None,
        )
        assert result == ("sig", 200, None, None, "", [], 0)
        return websocket.messages[-1]

    payload = asyncio.run(_run())

    assert payload["type"] == "ERROR"
    assert payload["payload"]["error"] == "Invalid message"


def test_run_main_loop_disconnects_v2_consumer_on_receive_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DisconnectingWebSocket:
        async def receive_text(self) -> str:
            raise WebSocketDisconnect(code=1001)

    class FakeV2ConsumerManager:
        is_connected = True

        def __init__(self) -> None:
            self.disconnected = False

        async def next_message(self, timeout: float) -> None:
            del timeout
            await asyncio.sleep(60)

        async def disconnect(self) -> None:
            self.disconnected = True
            self.is_connected = False

    async def _run() -> FakeV2ConsumerManager:
        from polaris.delivery.ws.endpoints import websocket_loop

        manager = FakeV2ConsumerManager()

        async def _send_status(**_kwargs: Any) -> tuple[str, dict[str, Any]]:
            return "status-sig", {}

        async def _send_snapshot() -> bool:
            return False

        async def _send_incremental() -> bool:
            return False

        async def _wait_for_update(*_args: Any, **_kwargs: Any) -> int:
            await asyncio.sleep(60)
            return 0

        monkeypatch.setattr(websocket_loop.REALTIME_SIGNAL_HUB, "wait_for_update", _wait_for_update)

        with pytest.raises(WebSocketDisconnect):
            await run_main_loop(
                websocket=cast(Any, DisconnectingWebSocket()),
                state=SimpleNamespace(),
                resolved_workspace="C:/workspace",
                cache_root="C:/runtime",
                roles_filter=set(),
                connection_id="conn-1",
                client="test-client",
                tail_lines=200,
                legacy_subscriptions=set(),
                v2_protocol="runtime.v2",
                v2_consumer_manager=cast(Any, manager),
                v2_client_id="client-1",
                v2_channels=["*"],
                v2_cursor=0,
                canonical_journal_channels={"llm"},
                channel_states={},
                journal_state={},
                legacy_channel_states={},
                stream_signatures=set(),
                stream_signature_order=deque(),
                realtime_subscription=None,
                send_status_func=_send_status,
                send_all_snapshots_func=_send_snapshot,
                send_incrementals_func=_send_incremental,
            )
        return manager

    manager = asyncio.run(_run())

    assert manager.disconnected is True
