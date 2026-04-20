"""Tests for Typed Event Schemas.

Test coverage:
- Normal: Event creation, factory methods
- Boundary: Event name validation, payload constraints
- Error: Invalid event names, missing required fields
"""

from datetime import datetime, timezone

import pytest
from polaris.kernelone.events.typed.schemas import (
    EventCategory,
    InstanceDisposed,
    InstanceStarted,
    ToolCompleted,
    ToolError,
    ToolErrorKind,
    ToolInvoked,
    ToolTimeout,
    TurnCompleted,
    TurnStarted,
    get_all_event_names,
    get_event_type,
)


class TestEventBase:
    """Tests for EventBase functionality."""

    def test_event_has_uuid(self) -> None:
        """Test that events have unique IDs by default."""
        event1 = InstanceStarted.create(instance_id="test", instance_type="kernel")
        event2 = InstanceStarted.create(instance_id="test", instance_type="kernel")

        assert event1.event_id != event2.event_id
        assert len(event1.event_id) == 32  # UUID hex length

    def test_event_has_timestamp(self) -> None:
        """Test that events have UTC timestamps."""
        event = InstanceStarted.create(instance_id="test", instance_type="kernel")

        assert event.timestamp.tzinfo == timezone.utc
        assert isinstance(event.timestamp, datetime)

    def test_event_is_frozen(self) -> None:
        """Test that events are immutable (frozen)."""
        event = InstanceStarted.create(instance_id="test", instance_type="kernel")

        with pytest.raises(Exception):  # Pydantic FrozenInstanceError
            event.event_id = "changed"  # type: ignore

    def test_event_extra_fields_rejected(self) -> None:
        """Test that extra fields are forbidden."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            InstanceStarted(
                event_id="test",
                event_name="instance_started",
                category=EventCategory.LIFECYCLE,
                extra_field="should_fail",  # type: ignore[call-arg]
            )


class TestInstanceLifecycleEvents:
    """Tests for instance lifecycle events."""

    def test_instance_started_factory(self) -> None:
        """Test InstanceStarted factory method."""
        event = InstanceStarted.create(
            instance_id="inst_123",
            instance_type="kernel",
            run_id="run_456",
            workspace="/tmp/test",
            config={"timeout": 300},
        )

        assert event.event_name == "instance_started"
        assert event.category == EventCategory.LIFECYCLE
        assert event.payload.instance_id == "inst_123"
        assert event.payload.instance_type == "kernel"
        assert event.payload.config["timeout"] == 300

    def test_instance_disposed_factory(self) -> None:
        """Test InstanceDisposed factory method."""
        event = InstanceDisposed.create(
            directory="/tmp/test",
            reason="shutdown",
            duration_ms=60000,
            run_id="run_456",
        )

        assert event.event_name == "instance_disposed"
        assert event.category == EventCategory.LIFECYCLE
        assert event.payload.directory == "/tmp/test"
        assert event.payload.reason == "shutdown"
        assert event.payload.duration_ms == 60000


class TestToolEvents:
    """Tests for tool execution events."""

    def test_tool_invoked_factory(self) -> None:
        """Test ToolInvoked factory method."""
        event = ToolInvoked.create(
            tool_name="read_file",
            tool_call_id="call_abc123",
            arguments={"path": "test.py"},
            execution_lane="direct",
            correlation_id="corr_123",
        )

        assert event.event_name == "tool_invoked"
        assert event.category == EventCategory.TOOL
        assert event.payload.tool_name == "read_file"
        assert event.payload.tool_call_id == "call_abc123"
        assert event.payload.arguments == {"path": "test.py"}
        assert event.correlation_id == "corr_123"

    def test_tool_completed_factory(self) -> None:
        """Test ToolCompleted factory method."""
        event = ToolCompleted.create(
            tool_name="read_file",
            tool_call_id="call_abc123",
            result={"content": "file contents"},
            duration_ms=50,
            output_size=100,
        )

        assert event.event_name == "tool_completed"
        assert event.category == EventCategory.TOOL
        assert event.payload.tool_name == "read_file"
        assert event.payload.result == {"content": "file contents"}
        assert event.payload.duration_ms == 50

    def test_tool_error_factory(self) -> None:
        """Test ToolError factory method."""
        event = ToolError.create(
            tool_name="read_file",
            tool_call_id="call_abc123",
            error="File not found",
            error_type=ToolErrorKind.NOT_FOUND,
            stack_trace="Traceback...",
            duration_ms=10,
        )

        assert event.event_name == "tool_error"
        assert event.category == EventCategory.TOOL
        assert event.payload.error == "File not found"
        assert event.payload.error_kind == "not_found"
        assert event.payload.stack_trace == "Traceback..."

    def test_tool_timeout_factory(self) -> None:
        """Test ToolTimeout factory method."""
        event = ToolTimeout.create(
            tool_name="long_running",
            tool_call_id="call_xyz",
            timeout_seconds=30,
            duration_ms=30001,
        )

        assert event.event_name == "tool_timeout"
        assert event.category == EventCategory.TOOL
        assert event.payload.timeout_seconds == 30
        assert event.payload.duration_ms == 30001


class TestTurnEvents:
    """Tests for turn events."""

    def test_turn_started_factory(self) -> None:
        """Test TurnStarted factory method."""
        event = TurnStarted.create(
            turn_id="turn_001",
            agent="director",
            prompt="Fix the bug",
            tools=["read_file", "search_replace"],
        )

        assert event.event_name == "turn_started"
        assert event.category == EventCategory.TURN
        assert event.payload.turn_id == "turn_001"
        assert event.payload.agent == "director"
        assert len(event.payload.tools) == 2

    def test_turn_completed_factory(self) -> None:
        """Test TurnCompleted factory method."""
        event = TurnCompleted.create(
            turn_id="turn_001",
            agent="director",
            tool_calls_count=5,
            duration_ms=1000,
            tokens_used=500,
        )

        assert event.event_name == "turn_completed"
        assert event.category == EventCategory.TURN
        assert event.payload.tool_calls_count == 5
        assert event.payload.duration_ms == 1000


class TestEventHelpers:
    """Tests for event type helper functions."""

    def test_get_event_type_valid(self) -> None:
        """Test get_event_type with valid names."""
        event_type = get_event_type("tool_invoked")
        assert event_type is not None
        assert event_type == ToolInvoked

    def test_get_event_type_invalid(self) -> None:
        """Test get_event_type with invalid name."""
        event_type = get_event_type("nonexistent_event")
        assert event_type is None

    def test_get_all_event_names(self) -> None:
        """Test get_all_event_names returns all registered events."""
        names = get_all_event_names()

        assert "tool_invoked" in names
        assert "tool_completed" in names
        assert "tool_error" in names
        assert "instance_started" in names
        assert "instance_disposed" in names
        assert len(names) >= 10  # At least 10 event types


class TestEventSerialization:
    """Tests for event serialization."""

    def test_event_serialization_roundtrip(self) -> None:
        """Test that events serialize and deserialize correctly."""
        original = ToolInvoked.create(
            tool_name="read_file",
            tool_call_id="call_123",
            arguments={"path": "test.py"},
        )

        # Serialize
        data = original.model_dump(mode="json")

        # Deserialize
        restored = ToolInvoked(**data)

        assert restored.event_id == original.event_id
        assert restored.event_name == original.event_name
        assert restored.payload.tool_name == original.payload.tool_name

    def test_event_payload_serialization(self) -> None:
        """Test that complex payloads serialize correctly."""
        event = ToolCompleted.create(
            tool_name="read_file",
            tool_call_id="call_123",
            result={
                "files": [{"path": "a.py"}, {"path": "b.py"}],
                "metadata": {"total": 2},
            },
        )

        data = event.model_dump(mode="json")
        payload_data = data["payload"]

        assert "files" in payload_data["result"]
        assert payload_data["result"]["metadata"]["total"] == 2


class TestContextWindowStatus:
    """Tests for ContextWindowStatus event."""

    def test_context_window_status_creation(self) -> None:
        """Test ContextWindowStatus event creation."""
        from polaris.kernelone.events.typed.schemas import ContextWindowStatus

        event = ContextWindowStatus.create(
            current_tokens=80000,
            max_tokens=128000,
        )

        assert event.event_name == "context_window_status"
        assert event.category == EventCategory.CONTEXT
        assert event.payload.current_tokens == 80000
        assert event.payload.max_tokens == 128000
        assert event.payload.remaining_tokens == 48000
        assert event.payload.usage_percentage == pytest.approx(62.5, rel=0.01)

    def test_context_window_status_critical_threshold(self) -> None:
        """Test that is_critical is set correctly at threshold."""
        from polaris.kernelone.events.typed.schemas import ContextWindowStatus

        # 85% usage - should be critical (above 80%)
        event = ContextWindowStatus.create(
            current_tokens=108800,
            max_tokens=128000,
            critical_threshold=80.0,
        )

        assert event.payload.usage_percentage == pytest.approx(85.0, rel=0.01)
        assert event.payload.is_critical is True
        assert event.payload.is_exhausted is False

    def test_context_window_status_exhausted(self) -> None:
        """Test that is_exhausted is set when at or over limit."""
        from polaris.kernelone.events.typed.schemas import ContextWindowStatus

        # 100% usage
        event = ContextWindowStatus.create(
            current_tokens=128000,
            max_tokens=128000,
        )

        assert event.payload.is_exhausted is True
        assert event.payload.remaining_tokens == 0

    def test_context_window_status_over_limit(self) -> None:
        """Test behavior when over limit."""
        from polaris.kernelone.events.typed.schemas import ContextWindowStatus

        # Over limit - remaining_tokens clamped to 0, percentage capped at 100
        event = ContextWindowStatus.create(
            current_tokens=130000,
            max_tokens=128000,
        )

        assert event.payload.is_exhausted is True
        assert event.payload.remaining_tokens == 0  # Clamped to 0
        # Note: usage_percentage is capped at 100 by schema constraint
        assert event.payload.usage_percentage == 100.0

    def test_context_window_status_with_segment_breakdown(self) -> None:
        """Test context window status with segment breakdown."""
        from polaris.kernelone.events.typed.schemas import ContextWindowStatus

        breakdown = {
            "system": 5000,
            "history": 60000,
            "tools": 10000,
            "retrieval": 5000,
        }

        event = ContextWindowStatus.create(
            current_tokens=80000,
            max_tokens=128000,
            segment_breakdown=breakdown,
        )

        assert event.payload.segment_breakdown == breakdown
        assert sum(breakdown.values()) == 80000

    def test_context_window_status_not_critical(self) -> None:
        """Test that is_critical is False below threshold."""
        from polaris.kernelone.events.typed.schemas import ContextWindowStatus

        # 50% usage - should not be critical
        event = ContextWindowStatus.create(
            current_tokens=64000,
            max_tokens=128000,
            critical_threshold=80.0,
        )

        assert event.payload.usage_percentage == pytest.approx(50.0, rel=0.01)
        assert event.payload.is_critical is False

    def test_get_event_type_context_window_status(self) -> None:
        """Test get_event_type returns ContextWindowStatus."""
        event_type = get_event_type("context_window_status")
        from polaris.kernelone.events.typed.schemas import ContextWindowStatus

        assert event_type is ContextWindowStatus
