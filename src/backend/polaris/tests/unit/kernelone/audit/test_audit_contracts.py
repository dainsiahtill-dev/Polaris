"""Tests for polaris.kernelone.audit.contracts."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from polaris.kernelone.audit.contracts import (
    KernelAuditEvent,
    KernelAuditEventType,
    KernelAuditRole,
    KernelAuditWriteResult,
    KernelChainVerificationResult,
)
from polaris.kernelone.utils.constants import GENESIS_HASH


class TestKernelAuditEventType:
    def test_values(self) -> None:
        assert KernelAuditEventType.TASK_START == "task_start"
        assert KernelAuditEventType.TASK_COMPLETE == "task_complete"
        assert KernelAuditEventType.SECURITY_VIOLATION == "security_violation"


class TestKernelAuditRole:
    def test_system(self) -> None:
        assert KernelAuditRole.SYSTEM == "system"


class TestKernelAuditEvent:
    def test_defaults(self) -> None:
        now = datetime.now(timezone.utc)
        event = KernelAuditEvent(
            event_id="e1",
            timestamp=now,
            event_type=KernelAuditEventType.TASK_START,
        )
        assert event.version == "2.0"
        assert event.prev_hash == GENESIS_HASH
        assert event.signature == ""
        assert event.source == {}
        assert event.task == {}

    def test_to_dict(self) -> None:
        now = datetime.now(timezone.utc)
        event = KernelAuditEvent(
            event_id="e1",
            timestamp=now,
            event_type=KernelAuditEventType.TOOL_EXECUTION,
            source={"role": "test"},
            task={"task_id": "t1"},
            action={"name": "run"},
            data={"output": "ok"},
            context={"trace_id": "tr1"},
            prev_hash="abc",
            signature="sig",
        )
        d = event.to_dict()
        assert d["event_id"] == "e1"
        assert d["timestamp"] == now.isoformat()
        assert d["event_type"] == "tool_execution"
        assert d["version"] == "2.0"
        assert d["source"] == {"role": "test"}
        assert d["task"] == {"task_id": "t1"}
        assert d["action"] == {"name": "run"}
        assert d["data"] == {"output": "ok"}
        assert d["context"] == {"trace_id": "tr1"}
        assert d["prev_hash"] == "abc"
        assert d["signature"] == "sig"

    def test_from_dict(self) -> None:
        now = datetime.now(timezone.utc)
        payload = {
            "event_id": "e1",
            "timestamp": now.isoformat(),
            "event_type": "task_start",
            "version": "2.0",
            "source": {"role": "test"},
            "task": {"task_id": "t1"},
            "resource": {},
            "action": {},
            "data": {},
            "context": {},
            "prev_hash": "abc",
            "signature": "sig",
        }
        event = KernelAuditEvent.from_dict(payload)
        assert event.event_id == "e1"
        assert event.event_type == KernelAuditEventType.TASK_START
        assert event.prev_hash == "abc"

    def test_from_dict_invalid_timestamp(self) -> None:
        with pytest.raises(ValueError, match="invalid audit payload"):
            KernelAuditEvent.from_dict({"timestamp": ""})
        with pytest.raises(ValueError, match="invalid audit payload"):
            KernelAuditEvent.from_dict({})

    def test_from_dict_with_z_timestamp(self) -> None:
        payload = {
            "event_id": "e1",
            "timestamp": "2024-01-01T00:00:00Z",
            "event_type": "task_start",
        }
        event = KernelAuditEvent.from_dict(payload)
        assert event.timestamp.isoformat().endswith("+00:00")


class TestKernelChainVerificationResult:
    def test_fields(self) -> None:
        now = datetime.now(timezone.utc)
        result = KernelChainVerificationResult(
            is_valid=True,
            first_hash="abc",
            last_hash="xyz",
            total_events=10,
            gap_count=0,
            verified_at=now,
            invalid_events=[],
        )
        assert result.is_valid is True
        assert result.total_events == 10
        assert result.gap_count == 0


class TestKernelAuditWriteResult:
    def test_defaults(self) -> None:
        result = KernelAuditWriteResult(success=True, event_id="e1")
        assert result.warnings == []
        assert result.error is None
        assert result.evidence_paths == []

    def test_with_values(self) -> None:
        result = KernelAuditWriteResult(
            success=False,
            event_id=None,
            warnings=["warn1"],
            error="failed",
            evidence_paths=["/path"],
        )
        assert result.success is False
        assert result.error == "failed"
        assert result.warnings == ["warn1"]
        assert result.evidence_paths == ["/path"]
