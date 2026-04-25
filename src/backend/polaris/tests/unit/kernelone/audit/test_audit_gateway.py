"""Tests for polaris.kernelone.audit.gateway."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from polaris.kernelone.audit.gateway import (
    AuditGateway,
    _to_kernel_event_type,
    _to_kernel_role,
    _to_legacy_event,
    emit_audit_event,
    get_gateway,
)
from polaris.kernelone.audit.contracts import KernelAuditEventType, KernelAuditRole


class TestToKernelEventType:
    def test_enum_passes_through(self) -> None:
        assert _to_kernel_event_type(KernelAuditEventType.TASK_START) == KernelAuditEventType.TASK_START

    def test_string_converted(self) -> None:
        assert _to_kernel_event_type("task_start") == KernelAuditEventType.TASK_START

    def test_object_with_value(self) -> None:
        obj = MagicMock()
        obj.value = "task_complete"
        assert _to_kernel_event_type(obj) == KernelAuditEventType.TASK_COMPLETE


class TestToKernelRole:
    def test_enum_converted(self) -> None:
        assert _to_kernel_role(KernelAuditRole.SYSTEM) == "system"

    def test_string_passes_through(self) -> None:
        assert _to_kernel_role("pm") == "pm"

    def test_object_with_value(self) -> None:
        obj = MagicMock()
        obj.value = "architect"
        assert _to_kernel_role(obj) == "architect"


class TestToLegacyEvent:
    def test_identity(self) -> None:
        event = MagicMock()
        assert _to_legacy_event(event) is event


class TestAuditGateway:
    def test_singleton_per_path(self, tmp_path: Path) -> None:
        with patch.object(AuditGateway, "_initialized", False):
            # Reset instances for test
            AuditGateway._instances.clear()
        g1 = AuditGateway.get_instance(tmp_path)
        g2 = AuditGateway.get_instance(tmp_path)
        assert g1 is g2

    def test_shutdown_all(self, tmp_path: Path) -> None:
        with patch.object(AuditGateway, "_initialized", False):
            AuditGateway._instances.clear()
        gateway = AuditGateway.get_instance(tmp_path)
        AuditGateway.shutdown_all()
        assert len(AuditGateway._instances) == 0

    def test_reset_instance(self, tmp_path: Path) -> None:
        with patch.object(AuditGateway, "_initialized", False):
            AuditGateway._instances.clear()
        gateway = AuditGateway.get_instance(tmp_path)
        AuditGateway.reset_instance(tmp_path)
        assert str(tmp_path.resolve()) not in AuditGateway._instances

    def test_runtime_root_property(self, tmp_path: Path) -> None:
        with patch.object(AuditGateway, "_initialized", False):
            AuditGateway._instances.clear()
        gateway = AuditGateway.get_instance(tmp_path)
        assert gateway.runtime_root == tmp_path.resolve()

    def test_emit_event_delegates(self, tmp_path: Path) -> None:
        with patch.object(AuditGateway, "_initialized", False):
            AuditGateway._instances.clear()
        gateway = AuditGateway.get_instance(tmp_path)
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.event_id = "e1"
        mock_result.warnings = []
        mock_result.error = None
        gateway._runtime.emit_event.return_value = mock_result

        result = gateway.emit_event(
            KernelAuditEventType.TASK_START,
            "system",
            ".",
            task_id="t1",
            run_id="r1",
        )
        assert result["success"] is True
        assert result["event_id"] == "e1"

    def test_emit_llm_event(self, tmp_path: Path) -> None:
        with patch.object(AuditGateway, "_initialized", False):
            AuditGateway._instances.clear()
        gateway = AuditGateway.get_instance(tmp_path)
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.event_id = "e2"
        mock_result.warnings = []
        mock_result.error = None
        gateway._runtime.emit_llm_event.return_value = mock_result

        result = gateway.emit_llm_event(
            role="pm",
            workspace=".",
            model="gpt-4",
            prompt_tokens=10,
            completion_tokens=5,
        )
        assert result["success"] is True

    def test_emit_dialogue(self, tmp_path: Path) -> None:
        with patch.object(AuditGateway, "_initialized", False):
            AuditGateway._instances.clear()
        gateway = AuditGateway.get_instance(tmp_path)
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.event_id = "e3"
        mock_result.warnings = []
        mock_result.error = None
        gateway._runtime.emit_dialogue.return_value = mock_result

        result = gateway.emit_dialogue(
            role="pm",
            workspace=".",
            dialogue_type="chat",
            message_summary="hello",
        )
        assert result["success"] is True

    def test_query_methods(self, tmp_path: Path) -> None:
        with patch.object(AuditGateway, "_initialized", False):
            AuditGateway._instances.clear()
        gateway = AuditGateway.get_instance(tmp_path)
        mock_event = MagicMock()
        gateway._runtime.query_by_run_id.return_value = [mock_event]
        gateway._runtime.query_by_task_id.return_value = [mock_event]
        gateway._runtime.query_by_trace_id.return_value = [mock_event]

        assert gateway.query_by_run_id("r1") == [mock_event]
        assert gateway.query_by_task_id("t1") == [mock_event]
        assert gateway.query_by_trace_id("tr1") == [mock_event]

    def test_verify_chain(self, tmp_path: Path) -> None:
        with patch.object(AuditGateway, "_initialized", False):
            AuditGateway._instances.clear()
        gateway = AuditGateway.get_instance(tmp_path)
        mock_result = MagicMock()
        mock_result.is_valid = True
        mock_result.first_hash = "abc"
        mock_result.last_hash = "xyz"
        mock_result.total_events = 10
        mock_result.gap_count = 0
        mock_result.invalid_events = []
        gateway._runtime.verify_chain.return_value = mock_result

        result = gateway.verify_chain()
        assert result["chain_valid"] is True
        assert result["first_event_hash"] == "abc"
        assert result["total_events"] == 10

    def test_store_property(self, tmp_path: Path) -> None:
        with patch.object(AuditGateway, "_initialized", False):
            AuditGateway._instances.clear()
        gateway = AuditGateway.get_instance(tmp_path)
        gateway._runtime.raw_store = "raw_store_value"
        assert gateway.store == "raw_store_value"


class TestGetGateway:
    def test_with_runtime_root(self, tmp_path: Path) -> None:
        with patch.object(AuditGateway, "_initialized", False):
            AuditGateway._instances.clear()
        gateway = get_gateway(tmp_path)
        assert isinstance(gateway, AuditGateway)

    def test_without_runtime_root(self, tmp_path: Path) -> None:
        with patch.object(AuditGateway, "_initialized", False):
            AuditGateway._instances.clear()
        with patch("polaris.kernelone.audit.gateway.get_runtime_base", return_value=str(tmp_path)):
            gateway = get_gateway()
            assert isinstance(gateway, AuditGateway)


class TestEmitAuditEvent:
    def test_delegates(self, tmp_path: Path) -> None:
        with patch.object(AuditGateway, "_initialized", False):
            AuditGateway._instances.clear()
        with patch("polaris.kernelone.audit.gateway.get_gateway") as mock_get:
            mock_gateway = MagicMock()
            mock_gateway.emit_event.return_value = {"success": True}
            mock_get.return_value = mock_gateway

            result = emit_audit_event(KernelAuditEventType.TASK_START, "system", ".")
            assert result["success"] is True
