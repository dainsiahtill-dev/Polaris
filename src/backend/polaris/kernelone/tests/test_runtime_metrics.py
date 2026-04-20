"""Tests for KernelOne runtime metrics module."""

from __future__ import annotations

from polaris.kernelone.runtime.metrics import (
    EXECUTION_LANES,
    EXECUTION_STATUSES,
    ExecutionMetrics,
    get_metrics,
    reset_metrics,
)


class TestExecutionMetrics:
    """Tests for ExecutionMetrics dataclass."""

    def setup_method(self) -> None:
        """Reset metrics before each test."""
        reset_metrics()

    def teardown_method(self) -> None:
        """Reset metrics after each test."""
        reset_metrics()

    def test_record_start_increments_active_count(self) -> None:
        """Verify record_start increments the active count for the lane."""
        metrics = ExecutionMetrics()

        metrics.record_start("async_task")
        assert metrics.active_executions["async_task"] == 1

        metrics.record_start("subprocess")
        assert metrics.active_executions["subprocess"] == 1

    def test_record_start_with_unknown_lane(self) -> None:
        """Verify record_start works with unknown lanes."""
        metrics = ExecutionMetrics()

        metrics.record_start("custom_lane")
        assert metrics.active_executions["custom_lane"] == 1

    def test_record_end_decrements_active_count(self) -> None:
        """Verify record_end decrements the active count for the lane."""
        metrics = ExecutionMetrics()

        metrics.record_start("async_task")
        metrics.record_end("async_task", "success", 1.5)

        assert metrics.active_executions["async_task"] == 0

    def test_record_end_increments_completed_count(self) -> None:
        """Verify record_end increments the completed count for the status."""
        metrics = ExecutionMetrics()

        metrics.record_end("async_task", "success", 1.0)
        metrics.record_end("async_task", "failed", 0.5)

        assert metrics.completed_count["success"] == 1
        assert metrics.completed_count["failed"] == 1

    def test_record_end_accumulates_duration(self) -> None:
        """Verify record_end accumulates execution duration per lane."""
        metrics = ExecutionMetrics()

        metrics.record_end("subprocess", "success", 2.0)
        metrics.record_end("subprocess", "success", 3.0)

        assert metrics.total_duration["subprocess"] == 5.0

    def test_record_end_handles_unknown_status(self) -> None:
        """Verify record_end works with unknown status values."""
        metrics = ExecutionMetrics()

        metrics.record_end("async_task", "unknown_status", 1.0)

        assert metrics.completed_count["unknown_status"] == 1

    def test_record_end_does_not_go_negative(self) -> None:
        """Verify active count does not go below zero."""
        metrics = ExecutionMetrics()

        # End without start
        metrics.record_end("async_task", "success", 1.0)
        assert metrics.active_executions["async_task"] == 0

    def test_record_message_drop(self) -> None:
        """Verify record_message_drop increments counter."""
        metrics = ExecutionMetrics()

        metrics.record_message_drop()
        metrics.record_message_drop()
        metrics.record_message_drop()

        assert metrics.messages_dropped == 3

    def test_record_process_kill(self) -> None:
        """Verify record_process_kill increments counter."""
        metrics = ExecutionMetrics()

        metrics.record_process_kill()
        metrics.record_process_kill()

        assert metrics.processes_killed == 2

    def test_update_states(self) -> None:
        """Verify update_states sets state counts."""
        metrics = ExecutionMetrics()

        metrics.update_states(total=100, active=25)

        assert metrics.states_retained == 100
        assert metrics.states_active == 25

    def test_to_prometheus_text_format(self) -> None:
        """Verify Prometheus text format output structure."""
        metrics = ExecutionMetrics()
        metrics.record_start("async_task")
        metrics.record_end("async_task", "success", 2.0)
        metrics.messages_dropped = 5
        metrics.processes_killed = 2
        metrics.update_states(total=50, active=10)

        text = metrics.to_prometheus_text()

        # Check active executions gauge
        assert 'kernelone_execution_active_current{lane="async_task"} 0' in text
        assert "# TYPE kernelone_execution_active_current gauge" in text

        # Check completed executions counter
        assert 'kernelone_execution_completed_total{status="success"} 1' in text
        assert "# TYPE kernelone_execution_completed_total counter" in text

        # Check duration counter
        assert 'kernelone_execution_duration_seconds_total{lane="async_task"} 2.0' in text

        # Check messages dropped
        assert "kernelone_messages_dropped_total 5" in text

        # Check processes killed
        assert "kernelone_processes_killed_total 2" in text

        # Check state gauges
        assert "kernelone_states_retained_current 50" in text
        assert "kernelone_states_active_current 10" in text

    def test_all_lanes_initialized(self) -> None:
        """Verify all lanes are initialized in active_executions."""
        metrics = ExecutionMetrics()

        for lane in EXECUTION_LANES:
            assert lane in metrics.active_executions
            assert metrics.active_executions[lane] == 0

    def test_all_statuses_initialized(self) -> None:
        """Verify all statuses are initialized in completed_count."""
        metrics = ExecutionMetrics()

        for status in EXECUTION_STATUSES:
            assert status in metrics.completed_count
            assert metrics.completed_count[status] == 0

    def test_execution_lifecycle(self) -> None:
        """Verify complete execution lifecycle metrics."""
        metrics = ExecutionMetrics()

        # Submit async task
        metrics.record_start("async_task")
        assert metrics.active_executions["async_task"] == 1

        # Complete successfully
        metrics.record_end("async_task", "success", 1.5)
        assert metrics.active_executions["async_task"] == 0
        assert metrics.completed_count["success"] == 1
        assert metrics.total_duration["async_task"] == 1.5

    def test_timeout_lifecycle(self) -> None:
        """Verify timeout execution lifecycle metrics."""
        metrics = ExecutionMetrics()

        metrics.record_start("subprocess")
        assert metrics.active_executions["subprocess"] == 1

        metrics.record_end("subprocess", "timed_out", 300.0)
        assert metrics.active_executions["subprocess"] == 0
        assert metrics.completed_count["timed_out"] == 1
        assert metrics.total_duration["subprocess"] == 300.0

    def test_multiple_concurrent_executions(self) -> None:
        """Verify metrics handle multiple concurrent executions."""
        metrics = ExecutionMetrics()

        # Start 3 async tasks
        metrics.record_start("async_task")
        metrics.record_start("async_task")
        metrics.record_start("async_task")
        assert metrics.active_executions["async_task"] == 3

        # Complete 2
        metrics.record_end("async_task", "success", 1.0)
        metrics.record_end("async_task", "failed", 2.0)
        assert metrics.active_executions["async_task"] == 1
        assert metrics.completed_count["success"] == 1
        assert metrics.completed_count["failed"] == 1
        assert metrics.total_duration["async_task"] == 3.0


class TestGlobalMetricsInstance:
    """Tests for global metrics instance management."""

    def setup_method(self) -> None:
        """Reset metrics before each test."""
        reset_metrics()

    def teardown_method(self) -> None:
        """Reset metrics after each test."""
        reset_metrics()

    def test_get_metrics_returns_singleton(self) -> None:
        """Verify get_metrics returns the same instance."""
        metrics1 = get_metrics()
        metrics2 = get_metrics()

        assert metrics1 is metrics2

    def test_reset_metrics_creates_new_instance(self) -> None:
        """Verify reset_metrics creates a new instance."""
        metrics1 = get_metrics()
        metrics1.record_start("async_task")

        reset_metrics()

        metrics2 = get_metrics()
        assert metrics2 is not metrics1
        assert metrics2.active_executions["async_task"] == 0

    def test_metrics_persist_between_calls(self) -> None:
        """Verify metrics persist when getting the singleton."""
        metrics1 = get_metrics()
        metrics1.record_start("async_task")

        metrics2 = get_metrics()
        assert metrics2.active_executions["async_task"] == 1
