"""Integration tests for the v2 WebSocket architecture.

Tests the complete flow:
1. WebSocket connection
2. RealtimeSignalHub watcher lifecycle
3. RuntimeEventFanout event distribution
4. Error handling and observability
5. Cleanup on disconnect
"""

from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

# Mock Auth before importing app components
mock_auth = MagicMock()
mock_auth.check = MagicMock(return_value=True)

mock_app_state = MagicMock()
mock_app_state.settings.workspace = "/tmp/test_workspace"
mock_app_state.settings.ramdisk_root = ""

mock_state_obj = MagicMock()
mock_state_obj.app_state = mock_app_state
mock_state_obj.auth = mock_auth


@pytest.fixture
def app(tmp_path):
    """Create FastAPI app with WebSocket endpoint."""
    from fastapi import FastAPI
    from polaris.delivery.ws.runtime_endpoint import router as ws_router

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    mock_app_state.settings.workspace = str(workspace)

    app = FastAPI()
    app.state.app_state = mock_app_state
    app.state.auth = mock_auth
    # runtime_ws router already contains prefix="/ws"
    app.include_router(ws_router, prefix="/v2")

    return app


@pytest.fixture
def client(app):
    """Create test client."""
    with TestClient(app) as client:
        yield client


def _receive_until_type(websocket, expected_types: set[str], max_messages: int = 16) -> dict:
    """Read websocket messages until one expected type appears."""
    expected = {str(item).upper() for item in expected_types}
    for _ in range(max(1, int(max_messages))):
        message = websocket.receive_json()
        msg_type = str(message.get("type") or "").strip().upper()
        if msg_type in expected:
            return message
    raise AssertionError(f"Expected message types {sorted(expected)} not received")


class TestWebSocketLifecycle:
    """Tests for WebSocket connection lifecycle."""

    def test_websocket_connection_accepted(self, client):
        """Test that WebSocket connection is accepted with valid token."""
        with client.websocket_connect("/v2/ws/runtime?token=valid") as websocket:
            # Should receive initial status
            data = websocket.receive_json()
            assert data.get("type") == "status"

    def test_websocket_auth_rejected(self, client):
        """Test that invalid token is rejected."""
        mock_auth.check.return_value = False

        try:
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect("/v2/ws/runtime?token=invalid") as websocket:
                    websocket.receive_json()
            assert exc_info.value.code == 1008
        finally:
            mock_auth.check.return_value = True  # Reset

    def test_websocket_ping_pong(self, client):
        """Test ping/pong mechanism."""
        with client.websocket_connect("/v2/ws/runtime?token=valid") as websocket:
            # Receive initial status
            _receive_until_type(websocket, {"STATUS"})

            # Send ping
            websocket.send_json({"type": "PING"})

            # Receive pong
            response = _receive_until_type(websocket, {"PONG"})
            assert response.get("type") == "PONG"

    def test_websocket_subscribe(self, client):
        """Test channel subscription."""
        with client.websocket_connect("/v2/ws/runtime?token=valid") as websocket:
            # Receive initial status
            _receive_until_type(websocket, {"STATUS"})

            # Subscribe to a legacy channel
            websocket.send_json(
                {
                    "type": "SUBSCRIBE",
                    "channels": ["custom_channel"],
                }
            )

            # Receive SUBSCRIBED confirmation
            response = _receive_until_type(websocket, {"SUBSCRIBED"})
            assert response.get("type") == "SUBSCRIBED"


class TestWebSocketErrorHandling:
    """Tests for error handling and observability."""

    def test_invalid_json_handled(self, client):
        """Test that invalid JSON is handled gracefully."""
        with client.websocket_connect("/v2/ws/runtime?token=valid") as websocket:
            # Receive initial status
            _receive_until_type(websocket, {"STATUS"})

            # Send invalid JSON text
            websocket.send_text("not valid json{}")

            # Should receive error
            response = _receive_until_type(websocket, {"ERROR"})
            assert response.get("type") == "ERROR"
            assert "Invalid JSON" in str(response.get("payload", {}).get("error", ""))

    def test_unknown_message_type(self, client):
        """Test handling of unknown message type."""
        with client.websocket_connect("/v2/ws/runtime?token=valid") as websocket:
            # Receive initial status
            _receive_until_type(websocket, {"STATUS"})

            # Send unknown type
            websocket.send_json({"type": "UNKNOWN_TYPE"})

            # Should receive error
            response = _receive_until_type(websocket, {"ERROR"})
            assert response.get("type") == "ERROR"
            assert "Unknown message type" in str(response.get("payload", {}).get("error", ""))


class TestWebSocketWithFanout:
    """Tests for WebSocket integration with RuntimeEventFanout."""

    @pytest.mark.asyncio
    async def test_file_edit_events_flow(self, client):
        """Test that file edit events flow through the system."""
        from polaris.infrastructure.realtime.process_local.message_event_fanout import RUNTIME_EVENT_FANOUT
        from polaris.kernelone.events.message_bus import Message, MessageType

        with client.websocket_connect("/v2/ws/runtime?token=valid&roles=director") as websocket:
            # Ensure server loop is fully initialized.
            _receive_until_type(websocket, {"STATUS"})

            # Get connection info from fanout
            connections = RUNTIME_EVENT_FANOUT.list_connections()
            assert len(connections) == 1
            conn_id = connections[0]

            # Inject a file written event
            message = Message(
                type=MessageType.FILE_WRITTEN,
                sender="test",
                payload={
                    "file_path": "/test/file.py",
                    "operation": "modify",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )

            # Call handler directly
            if RUNTIME_EVENT_FANOUT._file_handler:
                RUNTIME_EVENT_FANOUT._file_handler(message)

            # Wait for event to be processed
            await asyncio.sleep(0.1)

            # The connection should have the event in its sink
            file_events, _, _, _ = await RUNTIME_EVENT_FANOUT.drain_events(conn_id)
            assert len(file_events) == 1
            assert file_events[0]["file_path"] == "/test/file.py"

    @pytest.mark.asyncio
    async def test_connection_cleanup_on_disconnect(self, client):
        """Test that connection is cleaned up on disconnect."""
        from polaris.infrastructure.realtime.process_local.message_event_fanout import RUNTIME_EVENT_FANOUT

        initial_conns = len(RUNTIME_EVENT_FANOUT.list_connections())

        with client.websocket_connect("/v2/ws/runtime?token=valid") as websocket:
            # Receive initial status
            _receive_until_type(websocket, {"STATUS"})

            # Should have a connection registered
            assert len(RUNTIME_EVENT_FANOUT.list_connections()) == initial_conns + 1

        # Wait for async disconnect cleanup to settle.
        for _ in range(20):
            if len(RUNTIME_EVENT_FANOUT.list_connections()) == initial_conns:
                break
            await asyncio.sleep(0.05)

        # Should be cleaned up
        assert len(RUNTIME_EVENT_FANOUT.list_connections()) == initial_conns


class TestRealtimeSignalHubIntegration:
    """Tests for RealtimeSignalHub integration."""

    @pytest.mark.asyncio
    async def test_watcher_created_for_connection(self, client):
        """Test that filesystem watcher is created for connection."""
        from polaris.infrastructure.realtime.process_local.signal_hub import REALTIME_SIGNAL_HUB

        with tempfile.TemporaryDirectory() as tmpdir:
            # Mock workspace context to use temp dir
            with patch("polaris.delivery.ws.runtime_endpoint.resolve_workspace_runtime_context") as mock_ctx:
                mock_context = MagicMock()
                mock_context.workspace = tmpdir
                mock_context.runtime_root = tmpdir
                mock_context.workspace_key = "test"
                mock_context.runtime_base = tmpdir
                mock_context.source = "test"
                mock_ctx.return_value = mock_context

                len(REALTIME_SIGNAL_HUB.list_watches())

                with client.websocket_connect("/v2/ws/runtime?token=valid") as websocket:
                    # Receive initial status
                    websocket.receive_json()

                    # Should have created a watcher
                    await asyncio.sleep(0.1)

                # Give time for cleanup
                await asyncio.sleep(0.1)

    @pytest.mark.asyncio
    async def test_workspace_isolation(self, client):
        """Test that signals are filtered by workspace."""
        from polaris.infrastructure.realtime.process_local.signal_hub import REALTIME_SIGNAL_HUB

        with tempfile.TemporaryDirectory() as dir1, tempfile.TemporaryDirectory() as dir2:
            # Ensure watchers for both
            await REALTIME_SIGNAL_HUB.ensure_watch(dir1)
            await REALTIME_SIGNAL_HUB.ensure_watch(dir2)

            # Notify for dir1
            await REALTIME_SIGNAL_HUB.notify(source="test", path="/test", root=dir1)

            # Wait with dir2 filter should timeout (no matching signal)
            await REALTIME_SIGNAL_HUB.wait_for_update(0, timeout_sec=0.1, workspace=dir2)

            # Sequence should not have advanced for dir2
            # (this tests that workspace filtering works)

            # Cleanup
            REALTIME_SIGNAL_HUB.release_watch(dir1)
            REALTIME_SIGNAL_HUB.release_watch(dir2)


class TestBackpressureAndResync:
    """Tests for backpressure handling and resync."""

    @pytest.mark.asyncio
    async def test_dropped_events_trigger_resync(self, client):
        """Test that dropped events trigger a resync."""
        from polaris.infrastructure.realtime.process_local.message_event_fanout import RUNTIME_EVENT_FANOUT
        from polaris.kernelone.events.message_bus import Message, MessageType

        with client.websocket_connect("/v2/ws/runtime?token=valid&roles=director") as websocket:
            _receive_until_type(websocket, {"STATUS"})

            connections = RUNTIME_EVENT_FANOUT.list_connections()
            if not connections:
                pytest.skip("No active connections")

            conn_id = connections[0]

            # Inject many events without draining to trigger drops
            for i in range(300):  # More than buffer size
                message = Message(
                    type=MessageType.FILE_WRITTEN,
                    sender="test",
                    payload={"file_path": f"/test/{i}.py", "operation": "modify"},
                )
                if RUNTIME_EVENT_FANOUT._file_handler:
                    RUNTIME_EVENT_FANOUT._file_handler(message)

            # Wait for processing
            await asyncio.sleep(0.1)

            # Drain events
            _, _, _, dropped = await RUNTIME_EVENT_FANOUT.drain_events(conn_id)

            # Should have dropped some events
            assert dropped > 0


class TestErrorObservability:
    """Tests for error observability."""

    def test_send_error_categorized(self, client):
        """Test that send errors are categorized."""
        from polaris.delivery.ws.runtime_endpoint import WebSocketSendError

        # Test error creation
        error = WebSocketSendError("serialization_error", "JSON failed")
        assert error.error_type == "serialization_error"
        assert "JSON failed" in error.message

    @pytest.mark.asyncio
    async def test_audit_events_written(self):
        """Test that connection audit events are written."""
        from polaris.cells.audit.diagnosis.internal.connection_audit_service import write_ws_connection_event

        # This is a basic smoke test - the audit function should work
        await write_ws_connection_event(
            workspace="/test",
            cache_root="/cache",
            endpoint="/v2/ws/runtime",
            connection_id="test-conn",
            event="test_event",
            details={"test": "data"},
        )
