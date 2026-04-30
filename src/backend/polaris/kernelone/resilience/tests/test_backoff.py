"""Tests for resilience/backoff module."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from polaris.kernelone.resilience.backoff import BackoffController, build_backoff_seconds


class TestBuildBackoffSeconds:
    """Tests for build_backoff_seconds function."""

    def test_first_attempt(self) -> None:
        """First attempt uses base delay."""
        result = build_backoff_seconds(
            attempt=1,
            base_delay_seconds=1.0,
            max_delay_seconds=60.0,
        )
        assert 1.0 <= result <= 1.2

    def test_exponential_growth(self) -> None:
        """Delay doubles with each attempt."""
        base = 1.0
        max_d = 60.0
        d1 = build_backoff_seconds(attempt=1, base_delay_seconds=base, max_delay_seconds=max_d)
        d2 = build_backoff_seconds(attempt=2, base_delay_seconds=base, max_delay_seconds=max_d)
        d3 = build_backoff_seconds(attempt=3, base_delay_seconds=base, max_delay_seconds=max_d)
        assert d1 < d2 < d3

    def test_max_delay_cap(self) -> None:
        """Delay is capped at max_delay_seconds."""
        result = build_backoff_seconds(
            attempt=10,
            base_delay_seconds=1.0,
            max_delay_seconds=5.0,
        )
        assert result <= 6.0  # 5.0 + 20% jitter

    def test_zero_base_delay(self) -> None:
        """Zero base delay returns small value."""
        result = build_backoff_seconds(
            attempt=1,
            base_delay_seconds=0.0,
            max_delay_seconds=60.0,
        )
        assert 0.0 <= result <= 0.1

    def test_jitter_range(self) -> None:
        """Jitter is within 20% of bounded delay."""
        base = 2.0
        max_d = 100.0
        results = [
            build_backoff_seconds(attempt=1, base_delay_seconds=base, max_delay_seconds=max_d)
            for _ in range(20)
        ]
        for r in results:
            assert base <= r <= base * 1.2

    def test_attempt_zero_treated_as_one(self) -> None:
        """Attempt 0 is treated same as attempt 1."""
        base = 1.0
        max_d = 60.0
        r0 = build_backoff_seconds(attempt=0, base_delay_seconds=base, max_delay_seconds=max_d)
        r1 = build_backoff_seconds(attempt=1, base_delay_seconds=base, max_delay_seconds=max_d)
        # Both should be in same range
        assert base <= r0 <= base * 1.2
        assert base <= r1 <= base * 1.2


class TestBackoffController:
    """Tests for BackoffController class."""

    @pytest.fixture
    def mock_clock(self) -> MagicMock:
        """Create a mock clock."""
        clock = MagicMock()
        clock.sleep = MagicMock()
        return clock

    def test_initial_state(self, mock_clock: MagicMock) -> None:
        """Initial state is correct."""
        bc = BackoffController(clock=mock_clock)
        assert bc.failure_count == 0
        assert bc.current_delay == 1.0
        assert bc.is_exhausted is False

    def test_on_failure_increments_count(self, mock_clock: MagicMock) -> None:
        """on_failure increments failure count."""
        bc = BackoffController(clock=mock_clock)
        bc.on_failure()
        assert bc.failure_count == 1

    def test_on_failure_doubles_delay(self, mock_clock: MagicMock) -> None:
        """on_failure doubles the delay."""
        bc = BackoffController(clock=mock_clock, base_delay_seconds=1.0)
        bc.on_failure()
        assert bc.current_delay == 2.0
        bc.on_failure()
        assert bc.current_delay == 4.0

    def test_on_failure_caps_delay(self, mock_clock: MagicMock) -> None:
        """Delay is capped at max_delay_seconds."""
        bc = BackoffController(
            clock=mock_clock,
            base_delay_seconds=10.0,
            max_delay_seconds=15.0,
        )
        delay = bc.on_failure()
        # Returns current delay before doubling: 10.0
        assert delay == 10.0
        # current_delay is doubled then capped: min(20.0, 15.0) = 15.0
        assert bc.current_delay == 15.0

    def test_on_success_resets(self, mock_clock: MagicMock) -> None:
        """on_success resets state."""
        bc = BackoffController(clock=mock_clock)
        bc.on_failure()
        bc.on_failure()
        bc.on_success()
        assert bc.failure_count == 0
        assert bc.current_delay == 1.0

    def test_is_exhausted(self, mock_clock: MagicMock) -> None:
        """is_exhausted becomes True at max_attempts."""
        bc = BackoffController(clock=mock_clock, max_attempts=3)
        bc.on_failure()
        assert bc.is_exhausted is False
        bc.on_failure()
        assert bc.is_exhausted is False
        bc.on_failure()
        assert bc.is_exhausted is True

    def test_sleep_until_ready(self, mock_clock: MagicMock) -> None:
        """sleep_until_ready calls clock.sleep."""
        bc = BackoffController(clock=mock_clock)
        bc.on_failure()
        bc.sleep_until_ready()
        mock_clock.sleep.assert_called_once()

    def test_custom_base_delay(self, mock_clock: MagicMock) -> None:
        """Custom base delay is respected."""
        bc = BackoffController(clock=mock_clock, base_delay_seconds=0.5)
        assert bc.current_delay == 0.5

    def test_custom_max_attempts(self, mock_clock: MagicMock) -> None:
        """Custom max attempts is respected."""
        bc = BackoffController(clock=mock_clock, max_attempts=5)
        for _ in range(5):
            bc.on_failure()
        assert bc.is_exhausted is True

    def test_on_failure_return_value(self, mock_clock: MagicMock) -> None:
        """on_failure returns the delay before doubling."""
        bc = BackoffController(clock=mock_clock, base_delay_seconds=2.0)
        delay = bc.on_failure()
        assert delay == 2.0
        assert bc.current_delay == 4.0

    def test_exhausted_after_many_failures(self, mock_clock: MagicMock) -> None:
        """Controller becomes exhausted after many failures."""
        bc = BackoffController(clock=mock_clock, max_attempts=10)
        for _ in range(15):
            bc.on_failure()
        assert bc.is_exhausted is True
        assert bc.failure_count == 15


class TestBackoffControllerIntegration:
    """Integration tests for BackoffController."""

    def test_typical_retry_flow(self) -> None:
        """Typical retry flow with mock clock."""
        clock = MagicMock()
        bc = BackoffController(
            clock=clock,
            base_delay_seconds=1.0,
            max_delay_seconds=8.0,
            max_attempts=4,
        )

        # First failure
        d1 = bc.on_failure()
        assert d1 == 1.0
        bc.sleep_until_ready()
        clock.sleep.assert_called_with(2.0)

        # Second failure
        d2 = bc.on_failure()
        assert d2 == 2.0
        bc.sleep_until_ready()
        clock.sleep.assert_called_with(4.0)

        # Third failure
        d3 = bc.on_failure()
        assert d3 == 4.0
        bc.sleep_until_ready()
        clock.sleep.assert_called_with(8.0)

        # Success resets
        bc.on_success()
        assert bc.failure_count == 0
        assert bc.current_delay == 1.0

    def test_max_delay_prevents_infinite_growth(self) -> None:
        """Max delay prevents infinite growth."""
        clock = MagicMock()
        bc = BackoffController(
            clock=clock,
            base_delay_seconds=1.0,
            max_delay_seconds=4.0,
        )

        bc.on_failure()  # delay = 2.0
        bc.on_failure()  # delay = 4.0
        bc.on_failure()  # delay = 4.0 (capped)
        bc.on_failure()  # delay = 4.0 (capped)

        assert bc.current_delay == 4.0


class TestModuleExports:
    """Tests for module public API."""

    def test_all_exports_present(self) -> None:
        """All expected names are importable."""
        from polaris.kernelone.resilience import backoff

        assert hasattr(backoff, "BackoffController")
        assert hasattr(backoff, "build_backoff_seconds")

    def test_backoff_controller_slots(self) -> None:
        """BackoffController uses __slots__."""
        assert hasattr(BackoffController, "__slots__")
