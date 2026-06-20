"""Integration tests for the runtime.v2 WebSocket architecture."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

mock_auth = MagicMock()
mock_auth.check = MagicMock(return_value=True)

mock_app_state = MagicMock()
mock_app_state.settings.workspace = "/tmp/test_workspace"
mock_app_state.settings.ramdisk_root = ""


@pytest.fixture
def app(tmp_path):
    """Create FastAPI app with the runtime.v2 WebSocket endpoint."""
    from fastapi import FastAPI
    from polaris.delivery.ws.runtime_endpoint import router as ws_router

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    mock_app_state.settings.workspace = str(workspace)

    app = FastAPI()
    app.state.app_state = mock_app_state
    app.state.auth = mock_auth
    app.include_router(ws_router, prefix="/v2")

    return app


@pytest.fixture
def client(app):
    """Create test client."""
    with TestClient(app) as test_client:
        yield test_client


def _receive_until_type(websocket, expected_types: set[str], max_messages: int = 16) -> dict[str, Any]:
    expected = {str(item).upper() for item in expected_types}
    for _ in range(max(1, int(max_messages))):
        message = websocket.receive_json()
        msg_type = str(message.get("type") or "").strip().upper()
        if msg_type in expected:
            return message
    raise AssertionError(f"Expected message types {sorted(expected)} not received")


class TestWebSocketLifecycle:
    def test_websocket_connection_accepted(self, client) -> None:
        with client.websocket_connect("/v2/ws/runtime?token=valid") as websocket:
            data = websocket.receive_json()
            assert data.get("type") == "status"

    def test_websocket_auth_rejected(self, client) -> None:
        mock_auth.check.return_value = False

        try:
            with (
                pytest.raises(WebSocketDisconnect) as exc_info,
                client.websocket_connect("/v2/ws/runtime?token=invalid") as websocket,
            ):
                websocket.receive_json()
            assert exc_info.value.code == 1008
        finally:
            mock_auth.check.return_value = True

    def test_websocket_ping_pong_requires_runtime_v2_protocol(self, client) -> None:
        with client.websocket_connect("/v2/ws/runtime?token=valid") as websocket:
            _receive_until_type(websocket, {"STATUS"})

            websocket.send_json({"type": "PING", "protocol": "runtime.v2"})

            response = _receive_until_type(websocket, {"PONG"})
            assert response.get("type") == "PONG"
            assert response.get("protocol") == "runtime.v2"

    def test_legacy_subscribe_without_protocol_is_rejected(self, client) -> None:
        with client.websocket_connect("/v2/ws/runtime?token=valid") as websocket:
            _receive_until_type(websocket, {"STATUS"})

            websocket.send_json({"type": "SUBSCRIBE", "channels": ["custom_channel"]})

            response = _receive_until_type(websocket, {"ERROR"})
            assert response.get("payload", {}).get("code") == "RUNTIME_V2_REQUIRED"

    def test_runtime_v2_subscribe_uses_jetstream_consumer(self, client, monkeypatch: pytest.MonkeyPatch) -> None:
        from polaris.delivery.ws.endpoints import protocol

        class _FakeJetStreamConsumerManager:
            is_connected = False

            def __init__(self, **kwargs: Any) -> None:
                self.kwargs = kwargs
                self.is_connected = False

            async def connect(self) -> bool:
                self.is_connected = True
                return True

            async def next_message(self, timeout: float | None = None) -> None:
                assert timeout is None
                await asyncio.sleep(60)

            def consume_dropped(self) -> int:
                return 0

            async def disconnect(self) -> None:
                self.is_connected = False

        monkeypatch.setattr(protocol, "JetStreamConsumerManager", _FakeJetStreamConsumerManager)

        with client.websocket_connect("/v2/ws/runtime?token=valid") as websocket:
            _receive_until_type(websocket, {"STATUS"})

            websocket.send_json(
                {
                    "type": "SUBSCRIBE",
                    "protocol": "runtime.v2",
                    "client_id": "test-client",
                    "channels": ["llm"],
                    "tail": 0,
                }
            )

            response = _receive_until_type(websocket, {"SUBSCRIBED"})
            assert response.get("protocol") == "runtime.v2"
            assert response.get("payload", {}).get("jetstream") is True


class TestWebSocketErrorHandling:
    def test_invalid_json_handled(self, client) -> None:
        with client.websocket_connect("/v2/ws/runtime?token=valid") as websocket:
            _receive_until_type(websocket, {"STATUS"})

            websocket.send_text("not valid json{}")

            response = _receive_until_type(websocket, {"ERROR"})
            assert "Invalid JSON" in str(response.get("payload", {}).get("error", ""))

    def test_unknown_runtime_v2_message_type(self, client) -> None:
        with client.websocket_connect("/v2/ws/runtime?token=valid") as websocket:
            _receive_until_type(websocket, {"STATUS"})

            websocket.send_json({"type": "UNKNOWN_TYPE", "protocol": "runtime.v2"})

            response = _receive_until_type(websocket, {"ERROR"})
            assert "Unknown message type" in str(response.get("payload", {}).get("error", ""))


class TestRealtimeSingleRail:
    def test_runtime_connection_does_not_register_process_local_fanout_or_watch(self, client) -> None:
        from polaris.infrastructure.realtime.process_local.message_event_fanout import RUNTIME_EVENT_FANOUT
        from polaris.infrastructure.realtime.process_local.signal_hub import REALTIME_SIGNAL_HUB

        initial_connections = len(RUNTIME_EVENT_FANOUT.list_connections())
        initial_watches = len(REALTIME_SIGNAL_HUB.list_watches())

        with client.websocket_connect("/v2/ws/runtime?token=valid") as websocket:
            _receive_until_type(websocket, {"STATUS"})
            assert len(RUNTIME_EVENT_FANOUT.list_connections()) == initial_connections
            assert len(REALTIME_SIGNAL_HUB.list_watches()) == initial_watches

        assert len(RUNTIME_EVENT_FANOUT.list_connections()) == initial_connections
        assert len(REALTIME_SIGNAL_HUB.list_watches()) == initial_watches


class TestErrorObservability:
    @pytest.mark.asyncio
    async def test_audit_events_written(self) -> None:
        from polaris.cells.audit.diagnosis.internal.connection_audit_service import write_ws_connection_event

        await write_ws_connection_event(
            workspace="/test",
            cache_root="/cache",
            endpoint="/v2/ws/runtime",
            connection_id="test-conn",
            event="test_event",
            details={"test": "data"},
        )
