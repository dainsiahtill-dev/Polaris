"""Tests for P2-4: MetricsCollector instance-variable-based metrics."""

from __future__ import annotations

import pytest
from polaris.kernelone.context.metrics.collector import MetricsCollector, MetricsSnapshot


class TestMetricsCollectorProperties:
    """Tests for MetricsCollector dynamic property calculations."""

    def test_initial_values_are_zero(self) -> None:
        """Initial state has zero metrics."""
        collector = MetricsCollector()
        assert collector.receipt_write_total == 0
        assert collector.receipt_write_failures == 0
        assert collector.receipt_write_failure_rate == 0.0
        assert collector.sqlite_write_p95_ms == 0.0

    def test_failure_rate_calculation(self) -> None:
        """receipt_write_failure_rate = failures / total."""
        collector = MetricsCollector()
        collector.increment_receipt_write_total(10)
        collector.increment_receipt_write_failures(2)
        assert collector.receipt_write_failure_rate == pytest.approx(0.2)

    def test_failure_rate_zero_when_no_total(self) -> None:
        """Failure rate is 0.0 when total is 0 (avoid division by zero)."""
        collector = MetricsCollector()
        assert collector.receipt_write_failure_rate == 0.0

    def test_failure_rate_full_when_all_fail(self) -> None:
        """100% failure rate when all writes fail."""
        collector = MetricsCollector()
        collector.increment_receipt_write_total(5)
        collector.increment_receipt_write_failures(5)
        assert collector.receipt_write_failure_rate == pytest.approx(1.0)


class TestMetricsCollectorCounters:
    """Tests for MetricsCollector counter operations."""

    def test_increment_total(self) -> None:
        """increment_receipt_write_total adds to counter."""
        collector = MetricsCollector()
        collector.increment_receipt_write_total()
        assert collector.receipt_write_total == 1
        collector.increment_receipt_write_total(5)
        assert collector.receipt_write_total == 6

    def test_increment_failures(self) -> None:
        """increment_receipt_write_failures adds to counter."""
        collector = MetricsCollector()
        collector.increment_receipt_write_failures()
        assert collector.receipt_write_failures == 1
        collector.increment_receipt_write_failures(3)
        assert collector.receipt_write_failures == 4

    def test_counters_independent(self) -> None:
        """Total and failures are tracked independently."""
        collector = MetricsCollector()
        collector.increment_receipt_write_total(10)
        collector.increment_receipt_write_failures(3)
        assert collector.receipt_write_total == 10
        assert collector.receipt_write_failures == 3
        assert collector.receipt_write_failure_rate == pytest.approx(0.3)


class TestMetricsCollectorLatency:
    """Tests for MetricsCollector SQLite latency tracking."""

    def test_record_latency_updates_p95(self) -> None:
        """record_sqlite_write_latency updates p95 calculation."""
        collector = MetricsCollector()
        # Add 20 samples from 1ms to 20ms
        for i in range(1, 21):
            collector.record_sqlite_write_latency(float(i))
        # p95 of 1..20 is approximately 19.05 (index 18 of 20 samples)
        assert collector.sqlite_write_p95_ms == pytest.approx(19.0, rel=0.1)

    def test_p95_with_single_sample(self) -> None:
        """p95 equals the single sample when only one exists."""
        collector = MetricsCollector()
        collector.record_sqlite_write_latency(42.5)
        assert collector.sqlite_write_p95_ms == 42.5

    def test_p95_with_identical_samples(self) -> None:
        """p95 equals the value when all samples are identical."""
        collector = MetricsCollector()
        for _ in range(10):
            collector.record_sqlite_write_latency(5.0)
        assert collector.sqlite_write_p95_ms == 5.0

    def test_latency_bounded_memory(self) -> None:
        """Latency samples are bounded to last 1000."""
        collector = MetricsCollector()
        # Add 1500 samples
        for i in range(1500):
            collector.record_sqlite_write_latency(float(i))
        # Should only keep last 1000, so p95 should be around 1495 (index 995 of 1000)
        assert collector.sqlite_write_p95_ms > 990.0


class TestMetricsCollectorSnapshot:
    """Tests for MetricsCollector snapshot functionality."""

    def test_snapshot_contains_current_values(self) -> None:
        """snapshot() returns MetricsSnapshot with current values."""
        collector = MetricsCollector()
        collector.increment_receipt_write_total(5)
        collector.increment_receipt_write_failures(1)
        collector.record_sqlite_write_latency(10.0)

        snap = collector.snapshot()
        assert isinstance(snap, MetricsSnapshot)
        assert snap.receipt_write_total == 5
        assert snap.receipt_write_failures == 1
        assert snap.receipt_write_failure_rate == pytest.approx(0.2)
        assert snap.sqlite_write_p95_ms == 10.0

    def test_snapshot_immutable_after_creation(self) -> None:
        """Snapshot values don't change when collector is updated."""
        collector = MetricsCollector()
        collector.increment_receipt_write_total(10)
        snap1 = collector.snapshot()

        collector.increment_receipt_write_total(5)
        snap2 = collector.snapshot()

        assert snap1.receipt_write_total == 10
        assert snap2.receipt_write_total == 15

    def test_snapshot_to_dict(self) -> None:
        """snapshot().to_dict() returns serializable dict."""
        collector = MetricsCollector()
        snap = collector.snapshot()
        d = snap.to_dict()
        assert isinstance(d, dict)
        assert "receipt_write_total" in d
        assert "receipt_write_failure_rate" in d
        assert "sqlite_write_p95_ms" in d
        assert "collected_at" in d


class TestMetricsCollectorReset:
    """Tests for MetricsCollector reset functionality."""

    def test_reset_clears_all_counters(self) -> None:
        """reset() clears all metrics to initial state."""
        collector = MetricsCollector()
        collector.increment_receipt_write_total(10)
        collector.increment_receipt_write_failures(3)
        collector.record_sqlite_write_latency(25.0)

        collector.reset()

        assert collector.receipt_write_total == 0
        assert collector.receipt_write_failures == 0
        assert collector.receipt_write_failure_rate == 0.0
        assert collector.sqlite_write_p95_ms == 0.0


class TestMetricsCollectorConcurrency:
    """Tests for MetricsCollector thread safety."""

    def test_concurrent_increments(self) -> None:
        """Concurrent increments are handled correctly (no crash)."""
        import threading

        collector = MetricsCollector()
        num_threads = 10
        increments_per_thread = 100

        def worker() -> None:
            for _ in range(increments_per_thread):
                collector.increment_receipt_write_total()
                collector.increment_receipt_write_failures()

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        expected_total = num_threads * increments_per_thread
        assert collector.receipt_write_total == expected_total
        assert collector.receipt_write_failures == expected_total

    def test_concurrent_latency_recording(self) -> None:
        """Concurrent latency recording is handled correctly (no crash)."""
        import threading

        collector = MetricsCollector()
        num_threads = 5
        latencies_per_thread = 50

        def worker() -> None:
            for i in range(latencies_per_thread):
                collector.record_sqlite_write_latency(float(i))

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All latencies should have been recorded
        assert collector.sqlite_write_p95_ms > 0
