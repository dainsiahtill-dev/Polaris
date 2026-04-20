"""Tests for ToolCallTracker.

Test coverage:
- Normal: Create, track, transition, query tool calls
- Boundary: Concurrent operations, multiple tool calls
- Error: Missing tool calls, invalid transitions
"""

import asyncio

import pytest
from polaris.kernelone.tool_state import (
    ToolErrorKind,
    ToolState,
    ToolStateStatus,
)
from polaris.kernelone.tool_state.tracker import ToolCallTracker


class TestToolCallTrackerCreation:
    """Tests for ToolCallTracker creation methods."""

    @pytest.fixture
    def tracker(self) -> ToolCallTracker:
        """Create a fresh tracker for each test."""
        return ToolCallTracker()

    @pytest.mark.asyncio
    async def test_create_tool_call(self, tracker: ToolCallTracker) -> None:
        """Test creating a new tool call."""
        state = await tracker.create("read_file", execution_lane="direct")

        assert state.tool_name == "read_file"
        assert state.status == ToolStateStatus.PENDING
        assert state.execution_lane == "direct"
        assert state.tool_call_id is not None

    @pytest.mark.asyncio
    async def test_create_with_custom_id(self, tracker: ToolCallTracker) -> None:
        """Test creating tool call with custom ID."""
        state = await tracker.create(
            "write_file",
            tool_call_id="custom_call_123",
        )

        assert state.tool_call_id == "custom_call_123"

    @pytest.mark.asyncio
    async def test_create_with_metadata(self, tracker: ToolCallTracker) -> None:
        """Test creating tool call with metadata."""
        state = await tracker.create(
            "test_tool",
            metadata={"priority": "high", "tags": ["urgent"]},
        )

        assert state.metadata == {"priority": "high", "tags": ["urgent"]}

    @pytest.mark.asyncio
    async def test_create_with_correlation_id(self, tracker: ToolCallTracker) -> None:
        """Test creating tool call with correlation ID."""
        state = await tracker.create(
            "test_tool",
            correlation_id="parent_op_456",
        )

        assert state.correlation_id == "parent_op_456"

    @pytest.mark.asyncio
    async def test_create_duplicate_id_raises(self, tracker: ToolCallTracker) -> None:
        """Test that creating with duplicate ID raises error."""
        await tracker.create("tool1", tool_call_id="same_id")

        with pytest.raises(ValueError) as exc_info:
            await tracker.create("tool2", tool_call_id="same_id")

        assert "already exists" in str(exc_info.value)


class TestToolCallTrackerQuery:
    """Tests for ToolCallTracker query methods."""

    @pytest.fixture
    def tracker(self) -> ToolCallTracker:
        return ToolCallTracker()

    @pytest.mark.asyncio
    async def test_get_existing(self, tracker: ToolCallTracker) -> None:
        """Test getting an existing tool state."""
        await tracker.create("test_tool", tool_call_id="call_1")

        result = await tracker.get("call_1")

        assert result is not None
        assert result.tool_call_id == "call_1"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, tracker: ToolCallTracker) -> None:
        """Test getting a nonexistent tool state returns None."""
        result = await tracker.get("nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_list_all(self, tracker: ToolCallTracker) -> None:
        """Test listing all tool states."""
        await tracker.create("tool1", tool_call_id="call_1")
        await tracker.create("tool2", tool_call_id="call_2")
        await tracker.create("tool3", tool_call_id="call_3")

        result = await tracker.list_all()

        assert len(result) == 3
        ids = {s.tool_call_id for s in result}
        assert ids == {"call_1", "call_2", "call_3"}

    @pytest.mark.asyncio
    async def test_list_by_status(self, tracker: ToolCallTracker) -> None:
        """Test listing tool states by status."""
        await tracker.create("tool1", tool_call_id="call_1")
        await tracker.create("tool2", tool_call_id="call_2")
        await tracker.transition("call_1", ToolStateStatus.RUNNING)

        pending = await tracker.list_by_status(ToolStateStatus.PENDING)
        running = await tracker.list_by_status(ToolStateStatus.RUNNING)

        assert len(pending) == 1
        assert pending[0].tool_call_id == "call_2"
        assert len(running) == 1
        assert running[0].tool_call_id == "call_1"

    @pytest.mark.asyncio
    async def test_list_running(self, tracker: ToolCallTracker) -> None:
        """Test listing running tool states."""
        await tracker.create("tool1", tool_call_id="call_1")
        await tracker.create("tool2", tool_call_id="call_2")
        await tracker.transition("call_1", ToolStateStatus.RUNNING)

        result = await tracker.list_running()

        assert len(result) == 1
        assert result[0].tool_call_id == "call_1"

    @pytest.mark.asyncio
    async def test_list_pending(self, tracker: ToolCallTracker) -> None:
        """Test listing pending tool states."""
        await tracker.create("tool1", tool_call_id="call_1")
        await tracker.create("tool2", tool_call_id="call_2")
        await tracker.transition("call_1", ToolStateStatus.RUNNING)

        result = await tracker.list_pending()

        assert len(result) == 1
        assert result[0].tool_call_id == "call_2"

    @pytest.mark.asyncio
    async def test_list_terminal(self, tracker: ToolCallTracker) -> None:
        """Test listing terminal tool states."""
        await tracker.create("tool1", tool_call_id="call_1")
        await tracker.create("tool2", tool_call_id="call_2")
        await tracker.transition("call_1", ToolStateStatus.RUNNING)
        await tracker.transition("call_1", ToolStateStatus.COMPLETED)

        result = await tracker.list_terminal()

        assert len(result) == 1
        assert result[0].tool_call_id == "call_1"
        assert result[0].is_terminal

    @pytest.mark.asyncio
    async def test_count(self, tracker: ToolCallTracker) -> None:
        """Test counting tool states."""
        await tracker.create("tool1", tool_call_id="call_1")
        await tracker.create("tool2", tool_call_id="call_2")

        assert await tracker.count() == 2

    @pytest.mark.asyncio
    async def test_count_by_status(self, tracker: ToolCallTracker) -> None:
        """Test counting by status."""
        await tracker.create("tool1", tool_call_id="call_1")
        await tracker.create("tool2", tool_call_id="call_2")
        await tracker.transition("call_1", ToolStateStatus.RUNNING)

        pending_count = await tracker.count_by_status(ToolStateStatus.PENDING)
        running_count = await tracker.count_by_status(ToolStateStatus.RUNNING)

        assert pending_count == 1
        assert running_count == 1


class TestToolCallTrackerTransition:
    """Tests for ToolCallTracker transition methods."""

    @pytest.fixture
    def tracker(self) -> ToolCallTracker:
        return ToolCallTracker()

    @pytest.mark.asyncio
    async def test_transition(self, tracker: ToolCallTracker) -> None:
        """Test basic state transition."""
        await tracker.create("test_tool", tool_call_id="call_1")

        result = await tracker.transition("call_1", ToolStateStatus.RUNNING)

        assert result is not None
        assert result.status == ToolStateStatus.RUNNING

    @pytest.mark.asyncio
    async def test_transition_nonexistent(self, tracker: ToolCallTracker) -> None:
        """Test transitioning nonexistent tool call returns None."""
        result = await tracker.transition("nonexistent", ToolStateStatus.RUNNING)

        assert result is None

    @pytest.mark.asyncio
    async def test_start(self, tracker: ToolCallTracker) -> None:
        """Test start helper method."""
        await tracker.create("test_tool", tool_call_id="call_1")

        result = await tracker.start("call_1")

        assert result is not None
        assert result.status == ToolStateStatus.RUNNING
        assert result.started_at is not None

    @pytest.mark.asyncio
    async def test_complete(self, tracker: ToolCallTracker) -> None:
        """Test complete helper method."""
        await tracker.create("test_tool", tool_call_id="call_1")
        await tracker.transition("call_1", ToolStateStatus.RUNNING)

        result = await tracker.complete("call_1", result={"output": "test"})

        assert result is not None
        assert result.status == ToolStateStatus.COMPLETED
        assert result.result == {"output": "test"}

    @pytest.mark.asyncio
    async def test_fail(self, tracker: ToolCallTracker) -> None:
        """Test fail helper method."""
        await tracker.create("test_tool", tool_call_id="call_1")
        await tracker.transition("call_1", ToolStateStatus.RUNNING)

        result = await tracker.fail(
            "call_1",
            "Something went wrong",
            error_kind=ToolErrorKind.RUNTIME,
        )

        assert result is not None
        assert result.status == ToolStateStatus.ERROR
        assert result.error_kind == ToolErrorKind.RUNTIME
        assert result.error_message == "Something went wrong"

    @pytest.mark.asyncio
    async def test_timeout(self, tracker: ToolCallTracker) -> None:
        """Test timeout helper method."""
        await tracker.create("test_tool", tool_call_id="call_1")
        await tracker.transition("call_1", ToolStateStatus.RUNNING)

        result = await tracker.timeout("call_1", timeout_seconds=30)

        assert result is not None
        assert result.status == ToolStateStatus.TIMEOUT
        assert result.error_kind == ToolErrorKind.TIMEOUT
        assert result.error_message is not None
        assert "30s" in result.error_message

    @pytest.mark.asyncio
    async def test_cancel(self, tracker: ToolCallTracker) -> None:
        """Test cancel helper method."""
        await tracker.create("test_tool", tool_call_id="call_1")

        result = await tracker.cancel("call_1")

        assert result is not None
        assert result.status == ToolStateStatus.CANCELLED
        assert result.error_kind == ToolErrorKind.CANCELLED


class TestToolCallTrackerRetry:
    """Tests for ToolCallTracker retry functionality."""

    @pytest.fixture
    def tracker(self) -> ToolCallTracker:
        return ToolCallTracker()

    @pytest.mark.asyncio
    async def test_retry(self, tracker: ToolCallTracker) -> None:
        """Test retry resets state to pending."""
        await tracker.create("test_tool", tool_call_id="call_1")
        await tracker.transition("call_1", ToolStateStatus.RUNNING)
        await tracker.fail("call_1", "Failed")

        result = await tracker.retry("call_1")

        assert result is not None
        assert result.status == ToolStateStatus.PENDING
        assert result.retry_count == 1

    @pytest.mark.asyncio
    async def test_retry_nonexistent(self, tracker: ToolCallTracker) -> None:
        """Test retry on nonexistent returns None."""
        result = await tracker.retry("nonexistent")

        assert result is None


class TestToolCallTrackerRemoval:
    """Tests for ToolCallTracker removal methods."""

    @pytest.fixture
    def tracker(self) -> ToolCallTracker:
        return ToolCallTracker()

    @pytest.mark.asyncio
    async def test_remove_existing(self, tracker: ToolCallTracker) -> None:
        """Test removing an existing tool state."""
        await tracker.create("test_tool", tool_call_id="call_1")

        result = await tracker.remove("call_1")

        assert result is True
        assert await tracker.get("call_1") is None

    @pytest.mark.asyncio
    async def test_remove_nonexistent(self, tracker: ToolCallTracker) -> None:
        """Test removing nonexistent returns False."""
        result = await tracker.remove("nonexistent")

        assert result is False

    @pytest.mark.asyncio
    async def test_clear(self, tracker: ToolCallTracker) -> None:
        """Test clearing all tool states."""
        await tracker.create("tool1", tool_call_id="call_1")
        await tracker.create("tool2", tool_call_id="call_2")
        await tracker.create("tool3", tool_call_id="call_3")

        count = await tracker.clear()

        assert count == 3
        assert await tracker.count() == 0


class TestToolCallTrackerCallbacks:
    """Tests for ToolCallTracker callback system."""

    @pytest.fixture
    def tracker(self) -> ToolCallTracker:
        return ToolCallTracker()

    @pytest.mark.asyncio
    async def test_sync_callback(self, tracker: ToolCallTracker) -> None:
        """Test sync callback is called on transition."""
        received: list[ToolState] = []

        def callback(state: ToolState) -> None:
            received.append(state)

        tracker.add_callback(callback)
        await tracker.create("test_tool", tool_call_id="call_1")
        await tracker.transition("call_1", ToolStateStatus.RUNNING)

        assert len(received) == 1
        assert received[0].tool_call_id == "call_1"
        assert received[0].status == ToolStateStatus.RUNNING

    @pytest.mark.asyncio
    async def test_async_callback(self, tracker: ToolCallTracker) -> None:
        """Test async callback is called on transition."""
        received: list[ToolState] = []

        async def callback(state: ToolState) -> None:
            received.append(state)

        tracker.add_callback(callback)
        await tracker.create("test_tool", tool_call_id="call_1")
        await tracker.transition("call_1", ToolStateStatus.RUNNING)

        assert len(received) == 1
        assert received[0].tool_call_id == "call_1"

    @pytest.mark.asyncio
    async def test_callback_exception_handled(self, tracker: ToolCallTracker) -> None:
        """Test that callback exceptions don't crash transition."""

        def bad_callback(state: ToolState) -> None:
            raise ValueError("Test error")

        tracker.add_callback(bad_callback)
        await tracker.create("test_tool", tool_call_id="call_1")

        # Should not raise
        await tracker.transition("call_1", ToolStateStatus.RUNNING)

    @pytest.mark.asyncio
    async def test_remove_callback(self, tracker: ToolCallTracker) -> None:
        """Test removing a callback."""
        received: list[ToolState] = []

        def callback(state: ToolState) -> None:
            received.append(state)

        tracker.add_callback(callback)
        await tracker.create("test_tool", tool_call_id="call_1")

        tracker.remove_callback(callback)
        await tracker.transition("call_1", ToolStateStatus.RUNNING)

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_remove_nonexistent_callback(self, tracker: ToolCallTracker) -> None:
        """Test removing nonexistent callback returns False."""

        def dummy(state: ToolState) -> None:
            pass

        result = tracker.remove_callback(dummy)

        assert result is False


class TestToolCallTrackerConcurrency:
    """Tests for ToolCallTracker concurrency safety."""

    @pytest.mark.asyncio
    async def test_concurrent_creates(self) -> None:
        """Test concurrent create operations."""
        tracker = ToolCallTracker()

        async def create_tool(i: int) -> ToolState:
            return await tracker.create(f"tool_{i}", tool_call_id=f"call_{i}")

        results = await asyncio.gather(*[create_tool(i) for i in range(10)])

        assert len(results) == 10
        assert await tracker.count() == 10

    @pytest.mark.asyncio
    async def test_concurrent_transitions(self) -> None:
        """Test concurrent transition operations."""
        tracker = ToolCallTracker()

        await tracker.create("test_tool", tool_call_id="call_1")

        async def transition_state() -> None:
            await asyncio.sleep(0.001)  # Simulate some work
            await tracker.transition("call_1", ToolStateStatus.RUNNING)

        # First transition succeeds, second fails (invalid transition)
        results = await asyncio.gather(
            transition_state(),
            transition_state(),
            return_exceptions=True,
        )

        # At least one should succeed
        successes = [r for r in results if not isinstance(r, Exception)]
        assert len(successes) >= 1

    @pytest.mark.asyncio
    async def test_concurrent_queries(self) -> None:
        """Test concurrent query operations."""
        tracker = ToolCallTracker()

        # Create some tool calls
        for i in range(10):
            await tracker.create(f"tool_{i}", tool_call_id=f"call_{i}")

        async def query_tools() -> list[ToolState]:
            all_tools = await tracker.list_all()
            await tracker.list_running()
            await tracker.list_pending()
            return all_tools

        # All queries should complete without errors
        results = await asyncio.gather(*[query_tools() for _ in range(10)])

        for result in results:
            assert len(result) == 10


class TestToolCallTrackerIntegration:
    """Integration tests for complete tool call lifecycle."""

    @pytest.fixture
    def tracker(self) -> ToolCallTracker:
        return ToolCallTracker()

    @pytest.mark.asyncio
    async def test_complete_tool_lifecycle(self, tracker: ToolCallTracker) -> None:
        """Test a complete tool call lifecycle."""
        # Create
        state = await tracker.create(
            "read_file",
            tool_call_id="read_call",
            correlation_id="parent_123",
        )
        assert state is not None
        assert state.is_pending

        # Start
        started_state: ToolState | None = await tracker.start("read_call")
        assert started_state is not None
        assert started_state.is_running

        # Complete
        completed_state: ToolState | None = await tracker.complete(
            "read_call",
            result={"content": "file contents"},
        )
        assert completed_state is not None
        assert completed_state.is_completed
        assert started_state.result == {"content": "file contents"}

        # Verify terminal
        assert state.is_terminal
        running = await tracker.list_running()
        assert len(running) == 0

    @pytest.mark.asyncio
    async def test_failed_tool_lifecycle(self, tracker: ToolCallTracker) -> None:
        """Test a failed tool call lifecycle."""
        await tracker.create("write_file", tool_call_id="write_call")
        await tracker.start("write_call")

        state = await tracker.fail(
            "write_call",
            "Permission denied",
            error_kind=ToolErrorKind.PERMISSION,
        )

        assert state is not None
        assert state.is_failed
        assert state.error_kind == ToolErrorKind.PERMISSION
        assert state.error_message == "Permission denied"

    @pytest.mark.asyncio
    async def test_timeout_tool_lifecycle(self, tracker: ToolCallTracker) -> None:
        """Test a timeout tool call lifecycle."""
        await tracker.create("slow_tool", tool_call_id="slow_call")
        await tracker.start("slow_call")

        state = await tracker.timeout("slow_call", timeout_seconds=60)

        assert state is not None
        assert state.status == ToolStateStatus.TIMEOUT
        assert state.error_kind == ToolErrorKind.TIMEOUT
        assert state.error_message is not None
        assert "60s" in state.error_message

    @pytest.mark.asyncio
    async def test_retry_after_failure(self, tracker: ToolCallTracker) -> None:
        """Test retry after failure."""
        await tracker.create("unreliable_tool", tool_call_id="unreliable_call")
        await tracker.start("unreliable_call")
        await tracker.fail("unreliable_call", "Transient error")

        # First retry
        state = await tracker.retry("unreliable_call")
        assert state is not None
        assert state.retry_count == 1
        assert state.is_pending

        # Start again
        await tracker.start("unreliable_call")
        await tracker.fail("unreliable_call", "Still failing")

        # Second retry
        state = await tracker.retry("unreliable_call")
        assert state is not None
        assert state.retry_count == 2

    @pytest.mark.asyncio
    async def test_cancel_pending(self, tracker: ToolCallTracker) -> None:
        """Test cancelling a pending tool call."""
        await tracker.create("cancellable_tool", tool_call_id="cancel_call")

        state = await tracker.cancel("cancel_call")

        assert state is not None
        assert state.status == ToolStateStatus.CANCELLED
        assert state.error_kind == ToolErrorKind.CANCELLED

    @pytest.mark.asyncio
    async def test_multiple_tools_concurrent(self, tracker: ToolCallTracker) -> None:
        """Test tracking multiple tools concurrently."""
        # Create multiple tools
        for i in range(5):
            await tracker.create(f"tool_{i}", tool_call_id=f"call_{i}")

        # Start some
        for i in [0, 2, 4]:
            await tracker.start(f"call_{i}")

        # Complete one
        await tracker.complete("call_0", result="done")

        # Fail another
        await tracker.fail("call_2", "error")

        # Check counts
        # States after operations:
        # call_0: COMPLETED (terminal)
        # call_1: PENDING (never started)
        # call_2: ERROR (terminal)
        # call_3: PENDING (never started)
        # call_4: RUNNING (started but never completed)
        running = await tracker.list_running()
        completed = await tracker.list_terminal()
        pending = await tracker.list_pending()

        assert len(running) == 1  # Only call_4 is still running
        assert len(pending) == 2  # call_1 and call_3 are pending
        assert len(completed) == 2  # call_0 completed, call_2 failed
