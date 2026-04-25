"""Tests for polaris.kernelone.runtime.metrics."""

from __future__ import annotations

from polaris.kernelone.runtime.metrics import (
    EXECUTION_LANES,
    EXECUTION_STATUSES,
    ExecutionMetrics,
    get_metrics,
    reset_metrics,
)


class TestExecutionMetricsDefaults:
    def test_default_active_executions(self) -> None:
        m = ExecutionMetrics()
        assert set(m.active_executions.keys()) == set(EXECUTION_LANES)
        assert all(v == 0 for v in m.active_executions.values())

    def test_default_completed_count(self) -> None:
        m = ExecutionMetrics()
        assert set(m.completed_count.keys()) == set(EXECUTION_STATUSES)
        assert all(v == 0 for v in m.completed_count.values())

    def test_default_total_duration(self) -> None:
        m = ExecutionMetrics()
        assert set(m.total_duration.keys()) == set(EXECUTION_LANES)
        assert all(v == 0.0 for v in m.total_duration.values())

    def test_default_counters_zero(self) -> None:
        m = ExecutionMetrics()
        assert m.messages_dropped == 0
        assert m.processes_killed == 0
        assert m.states_retained == 0
        assert m.states_active == 0


class TestRecordStart:
    def test_known_lane_increments(self) -> None:
        m = ExecutionMetrics()
        m.record_start("async_task")
        assert m.active_executions["async_task"] == 1

    def test_unknown_lane_creates_entry(self) -> None:
        m = ExecutionMetrics()
        m.record_start("custom_lane")
        assert m.active_executions["custom_lane"] == 1

    def test_multiple_starts(self) -> None:
        m = ExecutionMetrics()
        m.record_start("blocking_io")
        m.record_start("blocking_io")
        assert m.active_executions["blocking_io"] == 2


class TestRecordEnd:
    def test_success_decrements_active(self) -> None:
        m = ExecutionMetrics()
        m.record_start("subprocess")
        m.record_end("subprocess", "success", 1.5)
        assert m.active_executions["subprocess"] == 0
        assert m.completed_count["success"] == 1
        assert m.total_duration["subprocess"] == 1.5

    def test_failed_status(self) -> None:
        m = ExecutionMetrics()
        m.record_end("async_task", "failed", 0.5)
        assert m.completed_count["failed"] == 1
        assert m.total_duration["async_task"] == 0.5

    def test_unknown_lane_does_not_go_negative(self) -> None:
        m = ExecutionMetrics()
        m.record_end("unknown", "success", 1.0)
        assert m.active_executions.get("unknown", 0) == 0
        assert m.total_duration["unknown"] == 1.0

    def test_unknown_status_creates_entry(self) -> None:
        m = ExecutionMetrics()
        m.record_end("async_task", "custom_status", 1.0)
        assert m.completed_count["custom_status"] == 1

    def test_duration_accumulates(self) -> None:
        m = ExecutionMetrics()
        m.record_end("blocking_io", "success", 1.0)
        m.record_end("blocking_io", "success", 2.5)
        assert m.total_duration["blocking_io"] == 3.5


class TestRecordMessageDrop:
    def test_increments_counter(self) -> None:
        m = ExecutionMetrics()
        m.record_message_drop()
        m.record_message_drop()
        assert m.messages_dropped == 2


class TestRecordProcessKill:
    def test_increments_counter(self) -> None:
        m = ExecutionMetrics()
        m.record_process_kill()
        assert m.processes_killed == 1


class TestUpdateStates:
    def test_updates_both_fields(self) -> None:
        m = ExecutionMetrics()
        m.update_states(total=100, active=42)
        assert m.states_retained == 100
        assert m.states_active == 42


class TestToPrometheusText:
    def test_contains_all_gauges_and_counters(self) -> None:
        m = ExecutionMetrics()
        m.record_start("async_task")
        m.record_end("async_task", "success", 1.5)
        m.record_message_drop()
        m.record_process_kill()
        m.update_states(total=10, active=3)

        text = m.to_prometheus_text()

        assert "# HELP kernelone_execution_active_current" in text
        assert "# TYPE kernelone_execution_active_current gauge" in text
        assert 'kernelone_execution_active_current{lane="async_task"} 0' in text

        assert "# HELP kernelone_execution_completed_total" in text
        assert "# TYPE kernelone_execution_completed_total counter" in text
        assert 'kernelone_execution_completed_total{status="success"} 1' in text

        assert "# HELP kernelone_execution_duration_seconds_total" in text
        assert "# TYPE kernelone_execution_duration_seconds_total counter" in text
        assert 'kernelone_execution_duration_seconds_total{lane="async_task"} 1.5' in text

        assert "kernelone_messages_dropped_total 1" in text
        assert "kernelone_processes_killed_total 1" in text
        assert "kernelone_states_retained_current 10" in text
        assert "kernelone_states_active_current 3" in text

    def test_empty_metrics_output(self) -> None:
        m = ExecutionMetrics()
        text = m.to_prometheus_text()
        assert "kernelone_execution_active_current" in text
        assert "kernelone_messages_dropped_total 0" in text


class TestGlobalMetrics:
    def test_get_metrics_returns_singleton(self) -> None:
        reset_metrics()
        a = get_metrics()
        b = get_metrics()
        assert a is b

    def test_reset_metrics_creates_new_instance(self) -> None:
        reset_metrics()
        a = get_metrics()
        a.record_start("async_task")
        reset_metrics()
        b = get_metrics()
        assert a is not b
        assert b.active_executions["async_task"] == 0
