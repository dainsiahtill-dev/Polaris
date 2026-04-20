"""Tests for P1-003: agent_runtime_base exponential backoff error handling.

These tests verify the _run_loop() error recovery strategy:
1. Normal execution:偶发错误有重试
2. Consecutive errors: 连续错误有退避
3. Threshold exceeded: 超过阈值优雅停止

All tests use MockClock injection instead of patch("time.sleep") to avoid
the brittle patching patterns that required importing the `time` module.
"""

from __future__ import annotations

import threading
from unittest import mock as ucmock

from polaris.cells.roles.runtime.internal.agent_runtime_base import (
    AgentMessage,
    AgentState,
    AgentStatus,
    RoleAgent,
)
from polaris.kernelone.common.clock import MockClock


class _TestableRoleAgent(RoleAgent):
    """Concrete test agent that records call patterns without file I/O."""

    def __init__(
        self,
        workspace: str,
        agent_name: str,
        cycle_behavior: str = "success",
        clock: MockClock | None = None,
    ) -> None:
        """Initialize testable agent with mocked memory."""
        super().__init__(workspace, agent_name, enable_context_compression=False, clock=clock)
        self.cycle_behavior = cycle_behavior
        self._cycle_count = 0
        self._call_log: list[str] = []
        self._mock_run_cycle: ucmock.MagicMock | None = None
        # Skip actual initialization to avoid file I/O
        self._state = AgentState()
        self._initialized = True

    def setup_toolbox(self) -> None:
        pass  # No tools needed for testing

    def handle_message(self, message: AgentMessage) -> AgentMessage | None:
        return None

    def run_cycle(self) -> bool:
        """Record cycle execution and return behavior based on configuration."""
        self._cycle_count += 1
        self._call_log.append(f"cycle_{self._cycle_count}")

        if self._mock_run_cycle:
            return self._mock_run_cycle()

        if self.cycle_behavior == "success":
            return True
        elif self.cycle_behavior == "idle":
            return False
        else:  # "always_error"
            raise RuntimeError("Simulated cycle error")

    def initialize(self) -> None:
        """Skip actual initialization for unit tests."""
        self._state = AgentState()
        self._initialized = True


class TestExponentialBackoffConstants:
    """Tests for backoff configuration constants."""

    def test_default_backoff_constants(self) -> None:
        """Verify default backoff configuration values."""
        agent = _TestableRoleAgent("/test", "test_agent")
        assert agent.MAX_CONSECUTIVE_ERRORS == 10
        assert agent.INITIAL_BACKOFF_SECONDS == 1.0
        assert agent.MAX_BACKOFF_SECONDS == 60.0
        assert agent.BACKOFF_MULTIPLIER == 2.0

    def test_backoff_constants_are_modifiable(self) -> None:
        """Verify constants can be overridden for testing."""
        agent = _TestableRoleAgent("/test", "test_agent")
        original = agent.MAX_CONSECUTIVE_ERRORS
        agent.MAX_CONSECUTIVE_ERRORS = 5
        assert agent.MAX_CONSECUTIVE_ERRORS == 5
        agent.MAX_CONSECUTIVE_ERRORS = original  # Restore


class TestNormalExecution:
    """Tests for normal execution scenarios."""

    def test_successful_cycles_reset_error_state(self) -> None:
        """Verify successful cycles reset consecutive_failures counter."""
        mock = MockClock(auto_sleep=True)
        agent = _TestableRoleAgent("/test", "test_agent", cycle_behavior="success", clock=mock)

        # Mock successful cycles - stop after 5 cycles to avoid infinite loop
        success_count = [0]

        def successful_cycles() -> bool:
            success_count[0] += 1
            if success_count[0] >= 5:
                agent._running = False  # Stop the loop
            return True

        agent._mock_run_cycle = ucmock.MagicMock(side_effect=successful_cycles)
        agent._running = True

        with (
            ucmock.patch.object(agent, "_heartbeat"),
            ucmock.patch.object(agent.memory, "save_state"),
        ):
            agent._run_loop()

        state = agent.get_state()
        assert state is not None
        assert state.consecutive_failures == 0
        assert agent._cycle_count >= 5


class TestOccasionalErrors:
    """Tests for occasional/transient error handling."""

    def test_transient_error_allows_retry(self) -> None:
        """Verify transient errors are retried (偶发错误有重试)."""
        mock = MockClock(auto_sleep=True)
        agent = _TestableRoleAgent("/test", "test_agent", clock=mock)

        call_count = [0]

        def fail_once_then_succeed() -> bool:
            call_count[0] += 1
            if call_count[0] <= 2:
                raise ValueError("Transient error")
            # After recovering, stop to avoid infinite loop
            agent._running = False
            return True

        agent._mock_run_cycle = ucmock.MagicMock(side_effect=fail_once_then_succeed)
        agent.MAX_CONSECUTIVE_ERRORS = 10  # High threshold for this test
        agent._running = True

        error_callbacks: list[Exception] = []

        def on_error(exc: Exception) -> None:
            error_callbacks.append(exc)

        agent.register_callback("on_error", on_error)

        with (
            ucmock.patch.object(agent, "_heartbeat"),
            ucmock.patch.object(agent.memory, "save_state"),
        ):
            agent._run_loop()

        # Should have received error callbacks
        assert len(error_callbacks) == 2, f"Expected 2 errors, got {len(error_callbacks)}"
        # Should have eventually succeeded
        assert call_count[0] >= 3, f"Expected 3+ calls, got {call_count[0]}"
        # State should be reset after success
        state = agent.get_state()
        assert state is not None
        assert state.consecutive_failures == 0

    def test_error_callback_invoked_on_exception(self) -> None:
        """Verify on_error callback is called when exceptions occur."""
        mock = MockClock(auto_sleep=True)
        agent = _TestableRoleAgent("/test", "test_agent", clock=mock)

        error_count = [0]

        def always_error() -> bool:
            error_count[0] += 1
            if error_count[0] >= 3:
                agent._running = False  # Stop after 3 errors
            raise RuntimeError("Test error")

        agent._mock_run_cycle = ucmock.MagicMock(side_effect=always_error)
        agent.MAX_CONSECUTIVE_ERRORS = 3
        agent._running = True

        error_callbacks: list[Exception] = []

        def on_error(exc: Exception) -> None:
            error_callbacks.append(exc)

        agent.register_callback("on_error", on_error)

        with (
            ucmock.patch.object(agent, "_heartbeat"),
            ucmock.patch.object(agent.memory, "save_state"),
        ):
            agent._run_loop()

        assert len(error_callbacks) == 3
        assert all(isinstance(e, RuntimeError) for e in error_callbacks)


class TestConsecutiveErrors:
    """Tests for consecutive error backoff behavior."""

    def test_consecutive_errors_trigger_backoff(self) -> None:
        """Verify consecutive errors trigger backoff (连续错误有退避)."""
        mock = MockClock()
        agent = _TestableRoleAgent("/test", "test_agent", clock=mock)

        error_count = [0]

        def always_error() -> bool:
            error_count[0] += 1
            if error_count[0] >= 10:
                agent._running = False  # Stop after 10 errors
            raise RuntimeError("Error")

        agent._mock_run_cycle = ucmock.MagicMock(side_effect=always_error)
        agent.MAX_CONSECUTIVE_ERRORS = 10  # Don't stop, just observe backoff
        agent._running = True

        with (
            ucmock.patch.object(agent, "_heartbeat"),
            ucmock.patch.object(agent.memory, "save_state"),
        ):
            agent._run_loop()

        # Should have multiple sleep calls with increasing durations
        sleep_calls = mock.sleep_calls
        assert len(sleep_calls) >= 3, f"Expected at least 3 sleep calls, got {len(sleep_calls)}"
        # Verify exponential growth: each should be roughly double the previous
        assert sleep_calls[0] == 1.0  # Initial backoff
        assert sleep_calls[1] == 2.0  # 1 * 2
        assert sleep_calls[2] == 4.0  # 2 * 2

    def test_backoff_caps_at_maximum(self) -> None:
        """Verify backoff caps at MAX_BACKOFF_SECONDS."""
        mock = MockClock()
        agent = _TestableRoleAgent("/test", "test_agent", clock=mock)

        error_count = [0]

        def always_error() -> bool:
            error_count[0] += 1
            if error_count[0] >= 10:
                agent._running = False
            raise RuntimeError("Error")

        agent._mock_run_cycle = ucmock.MagicMock(side_effect=always_error)
        agent.MAX_CONSECUTIVE_ERRORS = 10
        agent._running = True

        with (
            ucmock.patch.object(agent, "_heartbeat"),
            ucmock.patch.object(agent.memory, "save_state"),
        ):
            agent._run_loop()

        # After 1, 2, 4, 8, 16, 32, next should be capped at 60
        sleep_calls = mock.sleep_calls
        assert any(t == 60.0 for t in sleep_calls), f"Expected backoff to cap at 60.0, got: {sleep_calls}"

    def test_backoff_sequence_exponential(self) -> None:
        """Verify backoff follows: 1s -> 2s -> 4s -> 8s -> ... (指数退避).

        Note: When threshold is reached, we check before sleeping, so the last
        error doesn't trigger a sleep. For MAX_CONSECUTIVE_ERRORS=5, we get
        sleeps for errors 1-4: 1, 2, 4, 8. Error 5 causes immediate stop.
        """
        mock = MockClock()
        agent = _TestableRoleAgent("/test", "test_agent", clock=mock)

        error_count = [0]

        def always_error() -> bool:
            error_count[0] += 1
            if error_count[0] >= 5:
                agent._running = False
            raise RuntimeError("Error")

        agent._mock_run_cycle = ucmock.MagicMock(side_effect=always_error)
        agent.MAX_CONSECUTIVE_ERRORS = 5
        agent._running = True

        with (
            ucmock.patch.object(agent, "_heartbeat"),
            ucmock.patch.object(agent.memory, "save_state"),
        ):
            agent._run_loop()

        # Expected: 1, 2, 4, 8 (5th error causes stop without sleep)
        expected = [1.0, 2.0, 4.0, 8.0]
        assert mock.sleep_calls == expected, f"Expected {expected}, got {mock.sleep_calls}"


class TestThresholdExceeded:
    """Tests for graceful stop when threshold is exceeded."""

    def test_max_consecutive_errors_stops_gracefully(self) -> None:
        """Verify exceeding threshold causes graceful stop (超过阈值优雅停止)."""
        mock = MockClock(auto_sleep=True)
        agent = _TestableRoleAgent("/test", "test_agent", clock=mock)

        def always_error() -> bool:
            raise RuntimeError("Fatal error")

        agent._mock_run_cycle = ucmock.MagicMock(side_effect=always_error)
        agent.MAX_CONSECUTIVE_ERRORS = 3
        agent._running = True

        with (
            ucmock.patch.object(agent, "_heartbeat"),
            ucmock.patch.object(agent.memory, "save_state"),
        ):
            agent._run_loop()

        # Should have stopped
        assert agent.status == AgentStatus.STOPPED
        # Should have exactly 3 cycles before stopping
        assert agent._cycle_count == 3

    def test_state_preserved_after_graceful_stop(self) -> None:
        """Verify agent state is preserved after graceful stop."""
        mock = MockClock(auto_sleep=True)
        agent = _TestableRoleAgent("/test", "test_agent", clock=mock)

        def always_error() -> bool:
            raise RuntimeError("Fatal error")

        agent._mock_run_cycle = ucmock.MagicMock(side_effect=always_error)
        agent.MAX_CONSECUTIVE_ERRORS = 2
        agent._running = True

        with (
            ucmock.patch.object(agent, "_heartbeat"),
            ucmock.patch.object(agent.memory, "save_state"),
        ):
            agent._run_loop()

        state = agent.get_state()
        assert state is not None
        assert state.consecutive_failures == 2
        assert state.last_error == "Fatal error"
        assert state.status == AgentStatus.STOPPED

    def test_status_set_to_error_on_exception(self) -> None:
        """Verify status is set to ERROR during exception handling."""
        mock = MockClock(auto_sleep=True)
        agent = _TestableRoleAgent("/test", "test_agent", clock=mock)

        error_count = [0]

        def always_error() -> bool:
            error_count[0] += 1
            if error_count[0] >= 3:
                agent._running = False
            raise RuntimeError("Test error")

        agent._mock_run_cycle = ucmock.MagicMock(side_effect=always_error)
        agent.MAX_CONSECUTIVE_ERRORS = 3
        agent._running = True

        status_changes: list[AgentStatus] = []

        def on_status_change(status: AgentStatus) -> None:
            status_changes.append(status)

        agent.register_callback("on_status_change", on_status_change)

        with (
            ucmock.patch.object(agent, "_heartbeat"),
            ucmock.patch.object(agent.memory, "save_state"),
        ):
            agent._run_loop()

        assert AgentStatus.ERROR in status_changes
        assert AgentStatus.STOPPED in status_changes


class TestRecoveryScenarios:
    """Integration tests for error recovery scenarios."""

    def test_recovery_after_error_reset(self) -> None:
        """Verify agent can recover and reset error count after success."""
        mock = MockClock(auto_sleep=True)
        agent = _TestableRoleAgent("/test", "test_agent", clock=mock)

        call_count = [0]

        def mix_errors_and_success() -> bool:
            call_count[0] += 1
            if call_count[0] <= 3:
                raise ValueError(f"Error {call_count[0]}")
            # Recover and stop
            agent._running = False
            return True

        agent._mock_run_cycle = ucmock.MagicMock(side_effect=mix_errors_and_success)
        agent.MAX_CONSECUTIVE_ERRORS = 10

        with (
            ucmock.patch.object(agent, "_heartbeat"),
            ucmock.patch.object(agent.memory, "save_state"),
        ):
            agent._run_loop()

        state = agent.get_state()
        assert state is not None
        # After successful cycle, consecutive_failures should be 0
        assert state.consecutive_failures == 0

    def test_successful_cycle_resets_backoff(self) -> None:
        """Verify successful cycle resets backoff to initial value."""
        mock = MockClock()
        agent = _TestableRoleAgent("/test", "test_agent", clock=mock)

        call_count = [0]

        def fail_then_succeed() -> bool:
            call_count[0] += 1
            if call_count[0] <= 2:
                raise RuntimeError("Error")
            # Recover and stop
            agent._running = False
            return True

        agent._mock_run_cycle = ucmock.MagicMock(side_effect=fail_then_succeed)
        agent.MAX_CONSECUTIVE_ERRORS = 10
        agent._running = True

        with (
            ucmock.patch.object(agent, "_heartbeat"),
            ucmock.patch.object(agent.memory, "save_state"),
        ):
            agent._run_loop()

        # After 2 errors (backoff 1, 2) and 1 success, stopped
        # So only 2 sleep calls
        sleep_calls = mock.sleep_calls
        assert len(sleep_calls) == 2, f"Expected 2 sleep calls, got {len(sleep_calls)}"
        assert sleep_calls[0] == 1.0
        assert sleep_calls[1] == 2.0


class TestEdgeCases:
    """Edge case tests for error handling."""

    def test_single_error_does_not_stop(self) -> None:
        """Verify single error doesn't trigger immediate stop."""
        mock = MockClock(auto_sleep=True)
        agent = _TestableRoleAgent("/test", "test_agent", clock=mock)

        call_count = [0]

        def fail_once() -> bool:
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("Single error")
            # Stop after recovery
            agent._running = False
            return True

        agent._mock_run_cycle = ucmock.MagicMock(side_effect=fail_once)
        agent.MAX_CONSECUTIVE_ERRORS = 10
        agent._running = True

        with (
            ucmock.patch.object(agent, "_heartbeat"),
            ucmock.patch.object(agent.memory, "save_state"),
        ):
            agent._run_loop()

        # Should have continued after single error
        assert agent.status == AgentStatus.STOPPED  # Stopped because no more work
        assert agent._cycle_count >= 2  # At least error + success

    def test_error_at_threshold_boundary(self) -> None:
        """Verify behavior at exactly MAX_CONSECUTIVE_ERRORS threshold."""
        mock = MockClock(auto_sleep=True)
        agent = _TestableRoleAgent("/test", "test_agent", clock=mock)

        def always_error() -> bool:
            raise RuntimeError("Error at boundary")

        agent._mock_run_cycle = ucmock.MagicMock(side_effect=always_error)
        agent.MAX_CONSECUTIVE_ERRORS = 2
        agent._running = True

        with (
            ucmock.patch.object(agent, "_heartbeat"),
            ucmock.patch.object(agent.memory, "save_state"),
        ):
            agent._run_loop()

        # Should stop after exactly 2 errors (threshold)
        assert agent.status == AgentStatus.STOPPED
        assert agent._cycle_count == 2

    def test_paused_state_skips_cycle(self) -> None:
        """Verify paused agent skips cycle execution."""
        mock = MockClock(auto_sleep=True)
        agent = _TestableRoleAgent("/test", "test_agent", clock=mock)

        agent._mock_run_cycle = ucmock.MagicMock(return_value=True)
        agent._paused = True

        with ucmock.patch.object(agent, "_heartbeat"):
            agent._running = True
            agent._stop_event.clear()

            # Run in a separate thread
            thread = threading.Thread(target=agent._run_loop)
            thread.start()
            # Advance time while paused
            mock.advance(0.2)
            agent._paused = False
            mock.advance(0.1)
            agent._running = False
            thread.join(timeout=2.0)

        # Paused for ~200ms with 0.5s sleep interval, should have skipped cycles
        assert agent._cycle_count == 0
