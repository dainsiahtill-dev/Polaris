"""Tests for audit contracts module."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from polaris.kernelone.audit.contracts import (
    GENESIS_HASH,
    KernelAuditEvent,
    KernelAuditEventType,
    KernelAuditRole,
    KernelAuditWriteResult,
    KernelChainVerificationResult,
)
from polaris.kernelone.utils.constants import GENESIS_HASH as CONSTANT_GENESIS_HASH


class TestKernelAuditEventType:
    """Test suite for KernelAuditEventType enum."""

    def test_all_event_types_exist(self) -> None:
        """Test that all expected event types are defined."""
        expected_types = [
            "task_start",
            "task_complete",
            "task_failed",
            "tool_execution",
            "llm_call",
            "dialogue",
            "verification",
            "policy_check",
            "audit_verdict",
            "file_change",
            "security_violation",
            "internal_audit_failure",
        ]
        for type_name in expected_types:
            assert hasattr(KernelAuditEventType, type_name.upper())

    def test_event_type_values(self) -> None:
        """Test event type string values."""
        assert KernelAuditEventType.TASK_START.value == "task_start"
        assert KernelAuditEventType.TASK_COMPLETE.value == "task_complete"
        assert KernelAuditEventType.TOOL_EXECUTION.value == "tool_execution"
        assert KernelAuditEventType.LLM_CALL.value == "llm_call"
        assert KernelAuditEventType.SECURITY_VIOLATION.value == "security_violation"

    def test_event_type_is_string_enum(self) -> None:
        """Test that event types are string enums."""
        event_type = KernelAuditEventType.TASK_START
        assert isinstance(event_type, str)
        assert event_type == "task_start"


class TestKernelAuditRole:
    """Test suite for KernelAuditRole enum."""

    def test_system_role_value(self) -> None:
        """Test that SYSTEM role has correct value."""
        assert KernelAuditRole.SYSTEM.value == "system"


class TestGenesisHash:
    """Test suite for GENESIS_HASH constant."""

    def test_genesis_hash_is_64_zeros(self) -> None:
        """Test that GENESIS_HASH is 64 zero characters."""
        assert GENESIS_HASH == "0" * 64
        assert len(GENESIS_HASH) == 64

    def test_genesis_hash_equals_constant(self) -> None:
        """Test that imported GENESIS_HASH matches constant module."""
        assert GENESIS_HASH == CONSTANT_GENESIS_HASH


class TestKernelAuditEventCreation:
    """Test suite for KernelAuditEvent creation."""

    def test_minimal_event_creation(self) -> None:
        """Test creating event with minimal required fields."""
        event = KernelAuditEvent(
            event_id="test-123",
            timestamp=datetime.now(timezone.utc),
            event_type=KernelAuditEventType.TASK_START,
        )
        assert event.event_id == "test-123"
        assert event.event_type == KernelAuditEventType.TASK_START
        assert event.version == "2.0"  # default
        assert event.prev_hash == GENESIS_HASH  # default
        assert event.signature == ""  # default

    def test_full_event_creation(self) -> None:
        """Test creating event with all fields."""
        timestamp = datetime.now(timezone.utc)
        event = KernelAuditEvent(
            event_id="full-event",
            timestamp=timestamp,
            event_type=KernelAuditEventType.LLM_CALL,
            version="2.0",
            source={"role": "director", "workspace": "/tmp/test"},
            task={"task_id": "task-1", "run_id": "run-1"},
            resource={"type": "llm", "path": "gpt-4"},
            action={"name": "llm_call", "result": "success"},
            data={"prompt_tokens": 100, "completion_tokens": 50},
            context={"trace_id": "trace-123"},
            prev_hash="abc123",
            signature="sig-xyz",
        )
        assert event.event_id == "full-event"
        assert event.source["role"] == "director"
        assert event.task["task_id"] == "task-1"
        assert event.resource["type"] == "llm"
        assert event.action["result"] == "success"
        assert event.data["prompt_tokens"] == 100
        assert event.context["trace_id"] == "trace-123"
        assert event.prev_hash == "abc123"
        assert event.signature == "sig-xyz"


class TestKernelAuditEventToDict:
    """Test suite for KernelAuditEvent.to_dict method."""

    def test_to_dict_includes_all_fields(self) -> None:
        """Test that to_dict serializes all fields correctly."""
        timestamp = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        event = KernelAuditEvent(
            event_id="dict-test",
            timestamp=timestamp,
            event_type=KernelAuditEventType.DIALOGUE,
            version="2.0",
            source={"role": "pm"},
            task={"task_id": "t1"},
            resource={"type": "dialogue"},
            action={"name": "message"},
            data={"summary": "test"},
            context={"trace_id": "tr1"},
            prev_hash="hash123",
            signature="sig123",
        )
        result = event.to_dict()

        assert result["event_id"] == "dict-test"
        assert result["timestamp"] == "2024-01-15T10:30:00+00:00"
        assert result["event_type"] == "dialogue"
        assert result["version"] == "2.0"
        assert result["source"] == {"role": "pm"}
        assert result["task"] == {"task_id": "t1"}
        assert result["resource"] == {"type": "dialogue"}
        assert result["action"] == {"name": "message"}
        assert result["data"] == {"summary": "test"}
        assert result["context"] == {"trace_id": "tr1"}
        assert result["prev_hash"] == "hash123"
        assert result["signature"] == "sig123"

    def test_to_dict_returns_new_dict(self) -> None:
        """Test that to_dict returns a new dict (not a reference)."""
        event = KernelAuditEvent(
            event_id="ref-test",
            timestamp=datetime.now(timezone.utc),
            event_type=KernelAuditEventType.TASK_START,
        )
        result = event.to_dict()
        result["event_id"] = "modified"
        assert event.event_id == "ref-test"


class TestKernelAuditEventFromDict:
    """Test suite for KernelAuditEvent.from_dict method."""

    def test_from_dict_minimal(self) -> None:
        """Test creating event from minimal dict."""
        payload = {
            "event_id": "from-dict-1",
            "timestamp": "2024-01-15T10:30:00+00:00",
            "event_type": "task_start",
        }
        event = KernelAuditEvent.from_dict(payload)

        assert event.event_id == "from-dict-1"
        assert event.timestamp == datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        assert event.event_type == KernelAuditEventType.TASK_START

    def test_from_dict_with_z_suffix(self) -> None:
        """Test creating event from dict with Z timestamp suffix."""
        payload = {
            "event_id": "z-suffix",
            "timestamp": "2024-01-15T10:30:00Z",
            "event_type": "llm_call",
        }
        event = KernelAuditEvent.from_dict(payload)
        assert event.event_id == "z-suffix"

    def test_from_dict_full(self) -> None:
        """Test creating event from full dict."""
        payload = {
            "event_id": "full-from-dict",
            "timestamp": "2024-01-15T10:30:00+00:00",
            "event_type": "tool_execution",
            "version": "2.0",
            "source": {"role": "director"},
            "task": {"task_id": "t1", "run_id": "r1"},
            "resource": {"type": "tool"},
            "action": {"name": "read_file"},
            "data": {"file": "/tmp/test"},
            "context": {"trace_id": "tr1"},
            "prev_hash": "prev-hash-123",
            "signature": "sig-xyz",
        }
        event = KernelAuditEvent.from_dict(payload)

        assert event.event_id == "full-from-dict"
        assert event.source["role"] == "director"
        assert event.task["run_id"] == "r1"
        assert event.prev_hash == "prev-hash-123"
        assert event.signature == "sig-xyz"

    def test_from_dict_missing_timestamp_raises(self) -> None:
        """Test that missing timestamp raises ValueError."""
        payload = {
            "event_id": "no-timestamp",
            "event_type": "task_start",
        }
        with pytest.raises(ValueError, match="timestamp is required"):
            KernelAuditEvent.from_dict(payload)

    def test_from_dict_empty_timestamp_raises(self) -> None:
        """Test that empty timestamp raises ValueError."""
        payload = {
            "event_id": "empty-timestamp",
            "timestamp": "",
            "event_type": "task_start",
        }
        with pytest.raises(ValueError, match="timestamp is required"):
            KernelAuditEvent.from_dict(payload)

    def test_from_dict_whitespace_timestamp_raises(self) -> None:
        """Test that whitespace-only timestamp raises ValueError."""
        payload = {
            "event_id": "whitespace-timestamp",
            "timestamp": "   ",
            "event_type": "task_start",
        }
        with pytest.raises(ValueError, match="timestamp is required"):
            KernelAuditEvent.from_dict(payload)

    def test_from_dict_invalid_event_type_raises(self) -> None:
        """Test that invalid event_type raises ValueError."""
        payload = {
            "event_id": "no-type",
            "timestamp": "2024-01-15T10:30:00+00:00",
            "event_type": "",  # Empty string is not a valid enum value
        }
        with pytest.raises(ValueError, match="not a valid KernelAuditEventType"):
            KernelAuditEvent.from_dict(payload)

    def test_from_dict_default_values(self) -> None:
        """Test that missing optional fields get defaults."""
        payload = {
            "event_id": "defaults",
            "timestamp": "2024-01-15T10:30:00+00:00",
            "event_type": "task_start",
        }
        event = KernelAuditEvent.from_dict(payload)
        assert event.version == "2.0"
        assert event.source == {}
        assert event.task == {}
        assert event.resource == {}
        assert event.action == {}
        assert event.data == {}
        assert event.context == {}
        assert event.prev_hash == GENESIS_HASH
        assert event.signature == ""


class TestKernelChainVerificationResult:
    """Test suite for KernelChainVerificationResult dataclass."""

    def test_creation(self) -> None:
        """Test creating verification result."""
        result = KernelChainVerificationResult(
            is_valid=True,
            first_hash="abc123",
            last_hash="xyz789",
            total_events=100,
            gap_count=0,
            verified_at=datetime.now(timezone.utc),
        )
        assert result.is_valid is True
        assert result.total_events == 100
        assert result.gap_count == 0

    def test_with_invalid_events(self) -> None:
        """Test verification result with invalid events list."""
        result = KernelChainVerificationResult(
            is_valid=False,
            first_hash="abc123",
            last_hash="xyz789",
            total_events=100,
            gap_count=2,
            verified_at=datetime.now(timezone.utc),
            invalid_events=[
                {"event_id": "bad-1", "reason": "hash mismatch"},
                {"event_id": "bad-2", "reason": "missing signature"},
            ],
        )
        assert result.is_valid is False
        assert len(result.invalid_events) == 2

    def test_is_frozen(self) -> None:
        """Test that dataclass is frozen."""
        from dataclasses import FrozenInstanceError

        result = KernelChainVerificationResult(
            is_valid=True,
            first_hash="abc",
            last_hash="xyz",
            total_events=1,
            gap_count=0,
            verified_at=datetime.now(timezone.utc),
        )
        with pytest.raises(FrozenInstanceError):
            result.is_valid = False  # type: ignore[fisc-setting]


class TestKernelAuditWriteResult:
    """Test suite for KernelAuditWriteResult dataclass."""

    def test_success_result(self) -> None:
        """Test creating successful write result."""
        result = KernelAuditWriteResult(
            success=True,
            event_id="evt-123",
            warnings=[],
            evidence_paths=["/path/to/evidence"],
        )
        assert result.success is True
        assert result.event_id == "evt-123"
        assert result.warnings == []
        assert result.error is None

    def test_result_with_warnings(self) -> None:
        """Test creating result with warnings."""
        result = KernelAuditWriteResult(
            success=True,
            event_id="evt-456",
            warnings=["task_id_missing:derived", "trace_id_missing:derived"],
        )
        assert result.success is True
        assert len(result.warnings) == 2

    def test_result_with_error(self) -> None:
        """Test creating result with error."""
        result = KernelAuditWriteResult(
            success=False,
            event_id=None,
            error="Write failed: disk full",
        )
        assert result.success is False
        assert result.event_id is None
        assert "disk full" in result.error


class TestEventRoundTrip:
    """Test suite for event serialization round-trip."""

    def test_to_dict_from_dict_roundtrip(self) -> None:
        """Test that event survives to_dict -> from_dict round-trip."""
        original = KernelAuditEvent(
            event_id="roundtrip-test",
            timestamp=datetime(2024, 6, 15, 14, 30, 0, tzinfo=timezone.utc),
            event_type=KernelAuditEventType.TOOL_EXECUTION,
            version="2.0",
            source={"role": "director", "workspace": "/workspace/test"},
            task={"task_id": "task-abc", "run_id": "run-xyz"},
            resource={"type": "tool", "name": "read_file"},
            action={"name": "read_file", "result": "success"},
            data={"path": "/tmp/test.txt", "bytes_read": 1024},
            context={"trace_id": "trace-123", "span_id": "span-456"},
            prev_hash="prev-hash-value",
            signature="hmac-sig-abc",
        )

        serialized = original.to_dict()
        restored = KernelAuditEvent.from_dict(serialized)

        assert restored.event_id == original.event_id
        assert restored.timestamp == original.timestamp
        assert restored.event_type == original.event_type
        assert restored.version == original.version
        assert restored.source == original.source
        assert restored.task == original.task
        assert restored.resource == original.resource
        assert restored.action == original.action
        assert restored.data == original.data
        assert restored.context == original.context
        assert restored.prev_hash == original.prev_hash
        assert restored.signature == original.signature
