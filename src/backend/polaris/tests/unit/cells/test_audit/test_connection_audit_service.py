"""Tests for polaris.cells.audit.diagnosis.internal.connection_audit_service.

Covers WS connection audit event emission with mocked KernelAuditRuntime.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from polaris.cells.audit.diagnosis.internal.connection_audit_service import (
    write_ws_connection_event,
    write_ws_connection_event_sync,
)


class TestWriteWsConnectionEventSync:
    """Synchronous WS connection event writer tests."""

    @patch("polaris.cells.audit.diagnosis.internal.connection_audit_service.KernelAuditRuntime")
    def test_emits_event_with_all_fields(self, mock_runtime_cls: Any) -> None:
        mock_runtime = MagicMock()
        mock_runtime_cls.get_instance.return_value = mock_runtime

        result = write_ws_connection_event_sync(
            workspace="/ws",
            cache_root="/cache",
            endpoint="/v2/ws",
            connection_id="conn-1",
            event="connected",
            details={"client": "test"},
        )

        # Returns empty string on success
        assert result == ""
        mock_runtime_cls.get_instance.assert_called_once_with(Path("/cache").resolve())
        mock_runtime.emit_event.assert_called_once()
        call_kwargs = mock_runtime.emit_event.call_args.kwargs
        assert call_kwargs["role"] == "system"
        assert call_kwargs["workspace"] == "/ws"
        assert call_kwargs["task_id"] == "ws-conn-1"
        assert call_kwargs["data"]["endpoint"] == "/v2/ws"
        assert call_kwargs["data"]["event"] == "connected"
        assert call_kwargs["data"]["details"] == {"client": "test"}

    @patch("polaris.cells.audit.diagnosis.internal.connection_audit_service.KernelAuditRuntime")
    def test_empty_workspace_returns_empty(self, mock_runtime_cls: Any) -> None:
        result = write_ws_connection_event_sync(
            workspace="",
            cache_root="/cache",
            endpoint="/v2/ws",
            connection_id="conn-1",
            event="connected",
        )
        assert result == ""
        mock_runtime_cls.get_instance.assert_not_called()

    @patch("polaris.cells.audit.diagnosis.internal.connection_audit_service.KernelAuditRuntime")
    def test_empty_details_normalized(self, mock_runtime_cls: Any) -> None:
        mock_runtime = MagicMock()
        mock_runtime_cls.get_instance.return_value = mock_runtime

        write_ws_connection_event_sync(
            workspace="/ws",
            cache_root="/cache",
            endpoint="/v2/ws",
            connection_id="conn-1",
            event="connected",
            details=None,
        )

        call_kwargs = mock_runtime.emit_event.call_args.kwargs
        assert call_kwargs["data"]["details"] == {}

    @patch("polaris.cells.audit.diagnosis.internal.connection_audit_service.KernelAuditRuntime")
    def test_non_serializable_details_coerced(self, mock_runtime_cls: Any) -> None:
        mock_runtime = MagicMock()
        mock_runtime_cls.get_instance.return_value = mock_runtime

        class Unserializable:
            def __str__(self) -> str:
                return "unserializable-value"

        write_ws_connection_event_sync(
            workspace="/ws",
            cache_root="/cache",
            endpoint="/v2/ws",
            connection_id="conn-1",
            event="connected",
            details={"obj": Unserializable()},  # type: ignore[dict-item]
        )

        call_kwargs = mock_runtime.emit_event.call_args.kwargs
        # _normalize_details falls back to str(value) on TypeError (not OSError)
        assert call_kwargs["data"]["details"]["obj"] == "unserializable-value"

    @patch("polaris.cells.audit.diagnosis.internal.connection_audit_service.KernelAuditRuntime")
    def test_runtime_oserror_is_swallowed(self, mock_runtime_cls: Any) -> None:
        mock_runtime_cls.get_instance.side_effect = OSError("disk full")

        result = write_ws_connection_event_sync(
            workspace="/ws",
            cache_root="/cache",
            endpoint="/v2/ws",
            connection_id="conn-1",
            event="connected",
        )
        # Best-effort: failure should not propagate
        assert result == ""


class TestWriteWsConnectionEvent:
    """Async WS connection event writer tests."""

    @pytest.mark.asyncio
    @patch("polaris.cells.audit.diagnosis.internal.connection_audit_service.KernelAuditRuntime")
    async def test_async_wrapper_delegates(self, mock_runtime_cls: Any) -> None:
        mock_runtime = MagicMock()
        mock_runtime_cls.get_instance.return_value = mock_runtime

        result = await write_ws_connection_event(
            workspace="/ws",
            cache_root="/cache",
            endpoint="/v2/ws",
            connection_id="conn-1",
            event="disconnected",
        )

        assert result == ""
        mock_runtime.emit_event.assert_called_once()
