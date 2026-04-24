"""Tests for polaris.kernelone.resilience.backoff."""

from __future__ import annotations

from polaris.kernelone.common.clock import MockClock
from polaris.kernelone.resilience.backoff import BackoffController, build_backoff_seconds


class TestBuildBackoffSeconds:
    def test_first_attempt(self) -> None:
        result = build_backoff_seconds(attempt=1, base_delay_seconds=1.0, max_delay_seconds=60.0)
        assert 1.0 <= result <= 1.2

    def test_second_attempt(self) -> None:
        result = build_backoff_seconds(attempt=2, base_delay_seconds=1.0, max_delay_seconds=60.0)
        assert 2.0 <= result <= 2.4

    def test_max_delay_capped(self) -> None:
        result = build_backoff_seconds(attempt=10, base_delay_seconds=1.0, max_delay_seconds=5.0)
        assert result <= 6.0  # 5.0 + 20% jitter


class TestBackoffController:
    def test_initial_state(self) -> None:
        clock = MockClock()
        bc = BackoffController(clock)
        assert bc.failure_count == 0
        assert bc.current_delay == 1.0
        assert bc.is_exhausted is False

    def test_on_failure_increments(self) -> None:
        clock = MockClock()
        bc = BackoffController(clock)
        delay = bc.on_failure()
        assert bc.failure_count == 1
        assert delay == 1.0
        assert bc.current_delay == 2.0

    def test_on_failure_doubles(self) -> None:
        clock = MockClock()
        bc = BackoffController(clock, base_delay_seconds=1.0, max_delay_seconds=10.0)
        bc.on_failure()
        bc.on_failure()
        assert bc.current_delay == 4.0

    def test_on_success_resets(self) -> None:
        clock = MockClock()
        bc = BackoffController(clock)
        bc.on_failure()
        bc.on_failure()
        bc.on_success()
        assert bc.failure_count == 0
        assert bc.current_delay == 1.0

    def test_is_exhausted(self) -> None:
        clock = MockClock()
        bc = BackoffController(clock, max_attempts=3)
        bc.on_failure()
        bc.on_failure()
        assert bc.is_exhausted is False
        bc.on_failure()
        assert bc.is_exhausted is True

    def test_sleep_until_ready(self) -> None:
        clock = MockClock()
        bc = BackoffController(clock)
        bc.on_failure()
        bc.sleep_until_ready()
        assert clock.sleep_calls == [2.0]

    def test_delay_capped_at_max(self) -> None:
        clock = MockClock()
        bc = BackoffController(clock, base_delay_seconds=1.0, max_delay_seconds=4.0)
        bc.on_failure()  # 1.0 -> 2.0
        bc.on_failure()  # 2.0 -> 4.0
        bc.on_failure()  # 4.0 -> 4.0 (capped)
        assert bc.current_delay == 4.0
