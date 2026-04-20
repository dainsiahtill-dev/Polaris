"""Tests for Tool State Machine.

Test coverage:
- Normal: State transitions, factory creation, serialization
- Boundary: Invalid transitions, terminal state handling, max retries
- Error: Invalid state changes, missing tool call handling
"""

from datetime import datetime, timezone

import pytest
from polaris.kernelone.tool_state import (
    InvalidToolStateTransitionError,
    ToolErrorKind,
    ToolPendingSubState,
    ToolRunningSubState,
    ToolState,
    ToolStateStatus,
    create_tool_state,
)


class TestToolStateStatus:
    """Tests for ToolStateStatus enum."""

    def test_all_statuses_defined(self) -> None:
        """Test all expected statuses are defined."""
        assert ToolStateStatus.PENDING.value == "pending"
        assert ToolStateStatus.RUNNING.value == "running"
        assert ToolStateStatus.COMPLETED.value == "completed"
        assert ToolStateStatus.ERROR.value == "error"
        assert ToolStateStatus.BLOCKED.value == "blocked"
        assert ToolStateStatus.TIMEOUT.value == "timeout"
        assert ToolStateStatus.CANCELLED.value == "cancelled"


class TestToolPendingSubState:
    """Tests for ToolPendingSubState enum."""

    def test_all_substates_defined(self) -> None:
        """Test all expected sub-states are defined."""
        assert ToolPendingSubState.QUEUED.value == "queued"
        assert ToolPendingSubState.SCHEDULED.value == "scheduled"
        assert ToolPendingSubState.WAITING_INPUT.value == "waiting_input"


class TestToolRunningSubState:
    """Tests for ToolRunningSubState enum."""

    def test_all_substates_defined(self) -> None:
        """Test all expected sub-states are defined."""
        assert ToolRunningSubState.INITIALIZING.value == "initializing"
        assert ToolRunningSubState.EXECUTING.value == "executing"
        assert ToolRunningSubState.FINALIZING.value == "finalizing"


class TestToolErrorKind:
    """Tests for ToolErrorKind enum."""

    def test_all_error_kinds_defined(self) -> None:
        """Test all expected error kinds are defined."""
        assert ToolErrorKind.EXCEPTION.value == "exception"
        assert ToolErrorKind.VALIDATION.value == "validation"
        assert ToolErrorKind.PERMISSION.value == "permission"
        assert ToolErrorKind.NOT_FOUND.value == "not_found"
        assert ToolErrorKind.RUNTIME.value == "runtime"
        assert ToolErrorKind.TIMEOUT.value == "timeout"
        assert ToolErrorKind.CANCELLED.value == "cancelled"
        assert ToolErrorKind.NETWORK.value == "network"
        assert ToolErrorKind.RATE_LIMIT.value == "rate_limit"
        assert ToolErrorKind.UNKNOWN.value == "unknown"


class TestToolStateCreation:
    """Tests for ToolState creation."""

    def test_create_with_required_fields(self) -> None:
        """Test creating ToolState with required fields only."""
        state = ToolState(
            tool_call_id="call_abc123",
            tool_name="read_file",
        )

        assert state.tool_call_id == "call_abc123"
        assert state.tool_name == "read_file"
        assert state.status == ToolStateStatus.PENDING
        assert state.sub_state == ToolPendingSubState.QUEUED

    def test_create_with_all_fields(self) -> None:
        """Test creating ToolState with all optional fields."""
        state = ToolState(
            tool_call_id="call_xyz",
            tool_name="write_file",
            execution_lane="batch",
            correlation_id="corr_123",
            metadata={"priority": "high"},
        )

        assert state.execution_lane == "batch"
        assert state.correlation_id == "corr_123"
        assert state.metadata == {"priority": "high"}

    def test_create_with_default_values(self) -> None:
        """Test default values are set correctly."""
        state = ToolState(
            tool_call_id="call_default",
            tool_name="test_tool",
        )

        assert state.retry_count == 0
        assert state.max_retries == 3
        assert state.output_size == 0
        assert state.error_kind is None
        assert state.error_message is None
        assert state.started_at is None
        assert state.completed_at is None


class TestToolStateProperties:
    """Tests for ToolState properties."""

    def test_is_terminal_for_completed(self) -> None:
        """Test is_terminal is True for COMPLETED state."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.status = ToolStateStatus.COMPLETED
        assert state.is_terminal is True

    def test_is_terminal_for_error(self) -> None:
        """Test is_terminal is True for ERROR state."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.status = ToolStateStatus.ERROR
        assert state.is_terminal is True

    def test_is_terminal_for_timeout(self) -> None:
        """Test is_terminal is True for TIMEOUT state."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.status = ToolStateStatus.TIMEOUT
        assert state.is_terminal is True

    def test_is_terminal_for_cancelled(self) -> None:
        """Test is_terminal is True for CANCELLED state."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.status = ToolStateStatus.CANCELLED
        assert state.is_terminal is True

    def test_is_terminal_for_blocked(self) -> None:
        """Test is_terminal is True for BLOCKED state."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.status = ToolStateStatus.BLOCKED
        assert state.is_terminal is True

    def test_is_terminal_false_for_pending(self) -> None:
        """Test is_terminal is False for PENDING state."""
        state = ToolState(tool_call_id="test", tool_name="test")
        assert state.is_terminal is False

    def test_is_terminal_false_for_running(self) -> None:
        """Test is_terminal is False for RUNNING state."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.status = ToolStateStatus.RUNNING
        assert state.is_terminal is False

    def test_is_pending(self) -> None:
        """Test is_pending property."""
        state = ToolState(tool_call_id="test", tool_name="test")
        assert state.is_pending is True
        state.status = ToolStateStatus.RUNNING
        assert state.is_pending is False

    def test_is_running(self) -> None:
        """Test is_running property."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.status = ToolStateStatus.RUNNING
        assert state.is_running is True
        state.status = ToolStateStatus.COMPLETED
        assert state.is_running is False

    def test_is_completed(self) -> None:
        """Test is_completed property."""
        state = ToolState(tool_call_id="test", tool_name="test")
        assert state.is_completed is False
        state.status = ToolStateStatus.COMPLETED
        assert state.is_completed is True

    def test_is_failed(self) -> None:
        """Test is_failed property for error states."""
        state = ToolState(tool_call_id="test", tool_name="test")

        # Non-failure states
        assert state.is_failed is False
        state.status = ToolStateStatus.PENDING
        assert state.is_failed is False
        state.status = ToolStateStatus.RUNNING
        assert state.is_failed is False
        state.status = ToolStateStatus.COMPLETED
        assert state.is_failed is False

        # Failure states
        state.status = ToolStateStatus.ERROR
        assert state.is_failed is True
        state.status = ToolStateStatus.TIMEOUT
        assert state.is_failed is True
        state.status = ToolStateStatus.CANCELLED
        assert state.is_failed is True
        state.status = ToolStateStatus.BLOCKED
        assert state.is_failed is True


class TestToolStateTransition:
    """Tests for ToolState state transitions."""

    def test_pending_to_running(self) -> None:
        """Test transition from PENDING to RUNNING."""
        state = ToolState(tool_call_id="test", tool_name="test")
        assert state.status == ToolStateStatus.PENDING
        assert state.started_at is None

        state.transition(ToolStateStatus.RUNNING)

        assert state.status == ToolStateStatus.RUNNING
        assert state.started_at is not None
        assert state.sub_state == ToolRunningSubState.INITIALIZING

    def test_running_to_completed(self) -> None:
        """Test transition from RUNNING to COMPLETED."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.transition(ToolStateStatus.RUNNING)

        state.transition(
            ToolStateStatus.COMPLETED,
            result={"content": "test result"},
        )

        assert state.status == ToolStateStatus.COMPLETED
        assert state.completed_at is not None
        assert state.result == {"content": "test result"}

    def test_running_to_error(self) -> None:
        """Test transition from RUNNING to ERROR."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.transition(ToolStateStatus.RUNNING)

        state.transition(
            ToolStateStatus.ERROR,
            error_kind=ToolErrorKind.EXCEPTION,
            error_message="Something went wrong",
            error_stack="Traceback...",
        )

        assert state.status == ToolStateStatus.ERROR
        assert state.error_kind == ToolErrorKind.EXCEPTION
        assert state.error_message == "Something went wrong"
        assert state.error_stack == "Traceback..."
        assert state.completed_at is not None

    def test_running_to_timeout(self) -> None:
        """Test transition from RUNNING to TIMEOUT."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.transition(ToolStateStatus.RUNNING)

        state.transition(
            ToolStateStatus.TIMEOUT,
            error_message="Operation timed out after 30s",
        )

        assert state.status == ToolStateStatus.TIMEOUT
        assert state.error_kind == ToolErrorKind.TIMEOUT
        assert state.error_message == "Operation timed out after 30s"

    def test_running_to_cancelled(self) -> None:
        """Test transition from RUNNING to CANCELLED."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.transition(ToolStateStatus.RUNNING)

        state.transition(
            ToolStateStatus.CANCELLED,
            error_message="Cancelled by user",
        )

        assert state.status == ToolStateStatus.CANCELLED
        assert state.error_kind == ToolErrorKind.CANCELLED

    def test_pending_to_cancelled(self) -> None:
        """Test transition from PENDING to CANCELLED."""
        state = ToolState(tool_call_id="test", tool_name="test")

        state.transition(
            ToolStateStatus.CANCELLED,
            error_message="Cancelled before execution",
        )

        assert state.status == ToolStateStatus.CANCELLED
        assert state.completed_at is not None

    def test_running_to_blocked(self) -> None:
        """Test transition from RUNNING to BLOCKED."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.transition(ToolStateStatus.RUNNING)

        state.transition(
            ToolStateStatus.BLOCKED,
            error_kind=ToolErrorKind.PERMISSION,
            error_message="Permission denied",
        )

        assert state.status == ToolStateStatus.BLOCKED
        assert state.error_kind == ToolErrorKind.PERMISSION
        assert state.completed_at is not None


class TestInvalidTransitions:
    """Tests for invalid state transitions."""

    def test_cannot_transition_from_completed(self) -> None:
        """Test that transitions from COMPLETED are invalid."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.transition(ToolStateStatus.RUNNING)
        state.transition(ToolStateStatus.COMPLETED)

        with pytest.raises(InvalidToolStateTransitionError) as exc_info:
            state.transition(ToolStateStatus.ERROR)

        assert exc_info.value.current_status == ToolStateStatus.COMPLETED
        assert exc_info.value.target_status == ToolStateStatus.ERROR

    def test_cannot_transition_from_error(self) -> None:
        """Test that transitions from ERROR are invalid."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.transition(ToolStateStatus.RUNNING)
        state.transition(
            ToolStateStatus.ERROR,
            error_message="Failed",
        )

        with pytest.raises(InvalidToolStateTransitionError):
            state.transition(ToolStateStatus.COMPLETED)

    def test_cannot_direct_pending_to_completed(self) -> None:
        """Test that PENDING cannot directly go to COMPLETED."""
        state = ToolState(tool_call_id="test", tool_name="test")

        with pytest.raises(InvalidToolStateTransitionError):
            state.transition(ToolStateStatus.COMPLETED)

    def test_cannot_direct_pending_to_error(self) -> None:
        """Test that PENDING cannot directly go to ERROR."""
        state = ToolState(tool_call_id="test", tool_name="test")

        with pytest.raises(InvalidToolStateTransitionError):
            state.transition(ToolStateStatus.ERROR)

    def test_error_message_contains_valid_transitions(self) -> None:
        """Test that InvalidToolStateTransitionError message shows valid transitions."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.transition(ToolStateStatus.RUNNING)
        state.transition(ToolStateStatus.COMPLETED)

        with pytest.raises(InvalidToolStateTransitionError) as exc_info:
            state.transition(ToolStateStatus.ERROR)

        error_msg = str(exc_info.value)
        assert "Invalid state transition" in error_msg
        assert "completed" in error_msg
        assert "error" in error_msg


class TestToolStateRetry:
    """Tests for ToolState retry functionality."""

    def test_retry_resets_state(self) -> None:
        """Test that retry resets state to PENDING."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.transition(ToolStateStatus.RUNNING)
        state.transition(
            ToolStateStatus.ERROR,
            error_message="Failed",
        )
        assert state.retry_count == 0

        state.retry()

        assert state.status == ToolStateStatus.PENDING
        assert state.sub_state == ToolPendingSubState.QUEUED
        assert state.retry_count == 1
        assert state.started_at is None
        assert state.completed_at is None
        assert state.error_message is None

    def test_retry_increments_count(self) -> None:
        """Test that retry increments retry_count."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.transition(ToolStateStatus.RUNNING)
        state.transition(ToolStateStatus.ERROR)

        assert state.retry_count == 0
        state.retry()
        assert state.retry_count == 1
        state.retry()
        assert state.retry_count == 2

    def test_retry_clears_result(self) -> None:
        """Test that retry clears the result."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.transition(ToolStateStatus.RUNNING)
        state.transition(ToolStateStatus.COMPLETED, result="old result")

        state.retry()

        assert state.result is None
        assert state.output_size == 0

    def test_retry_cannot_exceed_max_retries(self) -> None:
        """Test that retry raises error when max retries exceeded."""
        state = ToolState(
            tool_call_id="test",
            tool_name="test",
            max_retries=2,
        )
        state.transition(ToolStateStatus.RUNNING)
        state.transition(ToolStateStatus.ERROR)

        state.retry()
        state.retry()

        with pytest.raises(ValueError) as exc_info:
            state.retry()

        assert "Max retries (2) exceeded" in str(exc_info.value)

    def test_retry_cannot_from_running(self) -> None:
        """Test that retry from RUNNING state is invalid."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.transition(ToolStateStatus.RUNNING)

        with pytest.raises(InvalidToolStateTransitionError):
            state.retry()


class TestToolStateDuration:
    """Tests for ToolState duration calculations."""

    def test_duration_ms_none_when_not_completed(self) -> None:
        """Test duration_ms is None when not completed."""
        state = ToolState(tool_call_id="test", tool_name="test")
        assert state.duration_ms is None

        state.transition(ToolStateStatus.RUNNING)
        assert state.duration_ms is None

    def test_duration_ms_calculated_after_completion(self) -> None:
        """Test duration_ms is calculated after completion."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.transition(ToolStateStatus.RUNNING)
        # Manually set started_at for testing
        state.started_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        state.completed_at = datetime(2024, 1, 1, 12, 0, 1, tzinfo=timezone.utc)

        assert state.duration_ms == 1000

    def test_pending_duration_ms(self) -> None:
        """Test pending_duration_ms calculation."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.created_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        state.transition(ToolStateStatus.RUNNING)
        state.started_at = datetime(2024, 1, 1, 12, 0, 2, tzinfo=timezone.utc)

        assert state.pending_duration_ms == 2000

    def test_total_duration_ms(self) -> None:
        """Test total_duration_ms calculation."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.created_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        state.transition(ToolStateStatus.RUNNING)
        state.completed_at = datetime(2024, 1, 1, 12, 0, 5, tzinfo=timezone.utc)

        assert state.total_duration_ms == 5000


class TestToolStateHistory:
    """Tests for ToolState history tracking."""

    def test_history_records_initial_state(self) -> None:
        """Test that history starts with initial state."""
        state = ToolState(tool_call_id="test", tool_name="test")

        assert len(state.history) == 1
        assert state.history[0][0] == ToolStateStatus.PENDING

    def test_history_records_transitions(self) -> None:
        """Test that history records all transitions."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.transition(ToolStateStatus.RUNNING)
        state.transition(ToolStateStatus.COMPLETED)

        assert len(state.history) == 3
        assert state.history[0][0] == ToolStateStatus.PENDING
        assert state.history[1][0] == ToolStateStatus.RUNNING
        assert state.history[2][0] == ToolStateStatus.COMPLETED

    def test_history_contains_timestamps(self) -> None:
        """Test that history entries contain timestamps."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.transition(ToolStateStatus.RUNNING)

        assert len(state.history[1]) == 3  # (status, timestamp, note)
        assert isinstance(state.history[1][1], datetime)

    def test_history_contains_notes(self) -> None:
        """Test that history entries can contain notes."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.transition(ToolStateStatus.RUNNING)
        state.transition(
            ToolStateStatus.ERROR,
            error_message="Test error",
        )

        assert state.history[2][2] == "Test error"


class TestToolStateSerialization:
    """Tests for ToolState serialization."""

    def test_to_dict(self) -> None:
        """Test converting ToolState to dictionary."""
        state = ToolState(
            tool_call_id="call_abc",
            tool_name="read_file",
            execution_lane="direct",
            correlation_id="corr_123",
        )
        state.transition(ToolStateStatus.RUNNING)

        result = state.to_dict()

        assert result["tool_call_id"] == "call_abc"
        assert result["tool_name"] == "read_file"
        assert result["status"] == "running"
        assert result["sub_state"] == "initializing"
        assert result["execution_lane"] == "direct"
        assert result["correlation_id"] == "corr_123"

    def test_to_dict_with_error(self) -> None:
        """Test to_dict includes error details."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.transition(ToolStateStatus.RUNNING)
        state.transition(
            ToolStateStatus.ERROR,
            error_kind=ToolErrorKind.VALIDATION,
            error_message="Invalid input",
        )

        result = state.to_dict()

        assert result["error_kind"] == "validation"
        assert result["error_message"] == "Invalid input"

    def test_to_dict_with_result(self) -> None:
        """Test to_dict includes result."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.transition(ToolStateStatus.RUNNING)
        state.transition(
            ToolStateStatus.COMPLETED,
            result={"content": "test data"},
        )

        result = state.to_dict()

        assert result["result"] == {"content": "test data"}
        assert result["output_size"] > 0

    def test_from_dict(self) -> None:
        """Test creating ToolState from dictionary."""
        data = {
            "tool_call_id": "call_xyz",
            "tool_name": "write_file",
            "status": "completed",
            "result": "success",
            "output_size": 7,
            "execution_lane": "batch",
        }

        state = ToolState.from_dict(data)

        assert state.tool_call_id == "call_xyz"
        assert state.tool_name == "write_file"
        assert state.status == ToolStateStatus.COMPLETED
        assert state.result == "success"

    def test_from_dict_roundtrip(self) -> None:
        """Test round-trip serialization."""
        original = ToolState(
            tool_call_id="call_roundtrip",
            tool_name="test_tool",
            execution_lane="direct",
        )
        original.transition(ToolStateStatus.RUNNING)
        original.transition(
            ToolStateStatus.COMPLETED,
            result={"data": "test"},
        )

        restored = ToolState.from_dict(original.to_dict())

        assert restored.tool_call_id == original.tool_call_id
        assert restored.tool_name == original.tool_name
        assert restored.status == original.status
        assert restored.result == original.result


class TestCreateToolState:
    """Tests for create_tool_state factory function."""

    def test_creates_pending_state(self) -> None:
        """Test that factory creates PENDING state."""
        state = create_tool_state(
            tool_name="test_tool",
            tool_call_id="custom_id",
        )

        assert state.status == ToolStateStatus.PENDING
        assert state.sub_state == ToolPendingSubState.QUEUED
        assert state.tool_name == "test_tool"
        assert state.tool_call_id == "custom_id"

    def test_generates_id_if_not_provided(self) -> None:
        """Test that ID is generated if not provided."""
        state = create_tool_state(tool_name="test_tool")

        assert state.tool_call_id is not None
        assert len(state.tool_call_id) == 12  # uuid4 hex truncated

    def test_sets_execution_lane(self) -> None:
        """Test that execution_lane is set correctly."""
        state = create_tool_state(
            tool_name="test_tool",
            execution_lane="batch",
        )

        assert state.execution_lane == "batch"

    def test_sets_correlation_id(self) -> None:
        """Test that correlation_id is set correctly."""
        state = create_tool_state(
            tool_name="test_tool",
            correlation_id="parent_op_123",
        )

        assert state.correlation_id == "parent_op_123"

    def test_sets_metadata(self) -> None:
        """Test that metadata is set correctly."""
        state = create_tool_state(
            tool_name="test_tool",
            metadata={"priority": "high", "tags": ["urgent"]},
        )

        assert state.metadata == {"priority": "high", "tags": ["urgent"]}


class TestOutputSizeCalculation:
    """Tests for output size calculation."""

    def test_output_size_string_result(self) -> None:
        """Test output_size calculation for string result."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.transition(ToolStateStatus.RUNNING)
        state.transition(ToolStateStatus.COMPLETED, result="Hello, World!")

        assert state.output_size == len("Hello, World!")

    def test_output_size_dict_result(self) -> None:
        """Test output_size calculation for dict result."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.transition(ToolStateStatus.RUNNING)
        state.transition(
            ToolStateStatus.COMPLETED,
            result={"content": "test", "count": 42},
        )

        import json

        expected = len(json.dumps({"content": "test", "count": 42}))
        assert state.output_size == expected

    def test_output_size_zero_for_no_result(self) -> None:
        """Test output_size is zero when no result."""
        state = ToolState(tool_call_id="test", tool_name="test")
        state.transition(ToolStateStatus.RUNNING)
        state.transition(ToolStateStatus.COMPLETED)

        assert state.output_size == 0
