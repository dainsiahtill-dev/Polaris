"""Tests for TaskMarketMetrics — counters, latency histograms, queue depth, Prometheus format."""

from __future__ import annotations

from polaris.cells.runtime.task_market.internal.metrics import (
    TaskMarketMetrics,
    reset_task_market_metrics_for_testing,
)


def test_metrics_record_operation_increments_counter() -> None:
    metrics = TaskMarketMetrics(enabled=True)
    metrics.record_operation("publish", 5.0, stage="pending_exec", ok=True)
    metrics.record_operation("publish", 10.0, stage="pending_exec", ok=True)
    metrics.record_operation("publish", 3.0, stage="pending_design", ok=False)

    text = metrics.get_prometheus_metrics()
    assert 'task_market_operations_total{operation="publish",stage="pending_exec",ok="true"} 2' in text
    assert 'task_market_operations_total{operation="publish",stage="pending_design",ok="false"} 1' in text


def test_metrics_record_latency_populates_histogram() -> None:
    metrics = TaskMarketMetrics(enabled=True)
    metrics.record_operation("claim", 5.0)
    metrics.record_operation("claim", 50.0)
    metrics.record_operation("claim", 500.0)

    text = metrics.get_prometheus_metrics()
    assert 'task_market_operation_duration_ms_bucket{operation="claim",le="5"}' in text
    assert 'task_market_operation_duration_ms_bucket{operation="claim",le="+Inf"} 3' in text
    assert 'task_market_operation_duration_ms_count{operation="claim"} 3' in text
    assert "task_market_operation_duration_ms_sum" in text


def test_metrics_prometheus_format_empty_when_disabled() -> None:
    metrics = TaskMarketMetrics(enabled=False)
    metrics.record_operation("publish", 5.0)
    assert metrics.get_prometheus_metrics() == ""


def test_metrics_queue_depth_gauge() -> None:
    metrics = TaskMarketMetrics(enabled=True)
    metrics.set_queue_depth("pending_design", 5)
    metrics.set_queue_depth("pending_exec", 10)

    text = metrics.get_prometheus_metrics()
    assert 'task_market_queue_depth{stage="pending_design"} 5' in text
    assert 'task_market_queue_depth{stage="pending_exec"} 10' in text


def test_metrics_outbox_relay_counters() -> None:
    metrics = TaskMarketMetrics(enabled=True)
    metrics.record_outbox_relay(sent=5, failed=2)

    text = metrics.get_prometheus_metrics()
    assert "task_market_outbox_relay_sent_total 5" in text
    assert "task_market_outbox_relay_failed_total 2" in text


def test_metrics_consumer_poll() -> None:
    metrics = TaskMarketMetrics(enabled=True)
    metrics.record_consumer_poll("director", 15.0)
    metrics.record_consumer_poll("director", 25.0)
    metrics.record_consumer_poll("qa", 8.0)

    text = metrics.get_prometheus_metrics()
    assert 'task_market_consumer_poll_total{role="director"} 2' in text
    assert 'task_market_consumer_poll_total{role="qa"} 1' in text
    assert "task_market_consumer_poll_duration_ms" in text


def test_metrics_reset_clears_all() -> None:
    metrics = TaskMarketMetrics(enabled=True)
    metrics.record_operation("publish", 5.0)
    metrics.set_queue_depth("pending_exec", 3)
    metrics.record_outbox_relay(sent=1, failed=0)
    metrics.record_consumer_poll("ce", 1.0)

    metrics.reset()

    text = metrics.get_prometheus_metrics()
    # After reset, operation counters, latency histograms, queue depths, and consumer polls
    # should have no data lines (only HELP/TYPE headers). Outbox relay counters show 0.
    assert 'operation="publish"' not in text
    assert 'stage="pending_exec"' not in text
    assert 'stage="ce"' not in text
    assert "task_market_outbox_relay_sent_total 0" in text
    assert "task_market_outbox_relay_failed_total 0" in text


def test_metrics_time_operation_context_manager() -> None:
    metrics = TaskMarketMetrics(enabled=True)
    with metrics.time_operation("acknowledge", stage="pending_qa") as timer:
        pass  # simulate work

    assert timer.duration_ms >= 0.0
    text = metrics.get_prometheus_metrics()
    assert "task_market_operations_total" in text
    assert "task_market_operation_duration_ms" in text


def test_metrics_thread_safety() -> None:
    """Rapid concurrent record_operation calls should not lose data."""
    import threading

    metrics = TaskMarketMetrics(enabled=True)
    n = 100
    barrier = threading.Barrier(n)

    def worker() -> None:
        barrier.wait()
        for _ in range(50):
            metrics.record_operation("publish", 1.0, stage="pending_exec", ok=True)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    text = metrics.get_prometheus_metrics()
    assert 'task_market_operations_total{operation="publish",stage="pending_exec",ok="true"} 5000' in text


def test_reset_task_market_metrics_for_testing() -> None:
    metrics = reset_task_market_metrics_for_testing()
    assert metrics.enabled is True
    text = metrics.get_prometheus_metrics()
    # Fresh singleton should have no operation data lines.
    assert "operation=" not in text
    assert "stage=" not in text


def test_service_records_metrics_on_publish(tmp_path) -> None:
    """Integration: publish_work_item records a publish metric."""
    from polaris.cells.runtime.task_market.internal.metrics import reset_task_market_metrics_for_testing
    from polaris.cells.runtime.task_market.internal.service import TaskMarketService
    from polaris.cells.runtime.task_market.public.contracts import PublishTaskWorkItemCommandV1

    reset_task_market_metrics_for_testing()
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-m1",
            run_id="run-m1",
            task_id="task-m1",
            stage="pending_exec",
            source_role="pm",
            payload={"title": "metrics test"},
        )
    )

    from polaris.cells.runtime.task_market.internal.metrics import get_task_market_metrics

    metrics = get_task_market_metrics()
    text = metrics.get_prometheus_metrics()
    assert 'operation="publish"' in text
    assert 'stage="pending_exec"' in text


def test_service_records_metrics_on_fail(tmp_path) -> None:
    """Integration: fail_task_stage records a fail metric with ok=True (since the failure is handled)."""
    from polaris.cells.runtime.task_market.internal.metrics import reset_task_market_metrics_for_testing
    from polaris.cells.runtime.task_market.internal.service import TaskMarketService
    from polaris.cells.runtime.task_market.public.contracts import (
        ClaimTaskWorkItemCommandV1,
        FailTaskStageCommandV1,
        PublishTaskWorkItemCommandV1,
    )

    reset_task_market_metrics_for_testing()
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-m2",
            run_id="run-m2",
            task_id="task-m2",
            stage="pending_exec",
            source_role="pm",
            payload={"title": "fail metrics"},
            max_attempts=1,
        )
    )
    claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="d-1",
            worker_role="director",
            visibility_timeout_seconds=60,
        )
    )
    service.fail_task_stage(
        FailTaskStageCommandV1(
            workspace=str(workspace),
            task_id="task-m2",
            lease_token=claim.lease_token,
            error_code="exec_error",
            error_message="boom",
        )
    )

    from polaris.cells.runtime.task_market.internal.metrics import get_task_market_metrics

    metrics = get_task_market_metrics()
    text = metrics.get_prometheus_metrics()
    assert 'operation="fail"' in text
