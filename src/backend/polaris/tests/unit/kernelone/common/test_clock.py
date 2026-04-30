"""Tests for polaris.kernelone.common.clock."""

from __future__ import annotations

import pytest
from polaris.kernelone.common.clock import MockClock, RealClock


class TestRealClock:
    def test_time_returns_float(self) -> None:
        clock = RealClock()
        result = clock.time()
        assert isinstance(result, float)
        assert result > 0


class TestMockClock:
    def test_initial_time(self) -> None:
        clock = MockClock()
        assert clock.time() == 0.0

    def test_custom_initial_time(self) -> None:
        clock = MockClock(initial_time=100.0)
        assert clock.time() == 100.0

    def test_advance(self) -> None:
        clock = MockClock()
        clock.advance(5.0)
        assert clock.time() == 5.0

    def test_advance_negative_raises(self) -> None:
        clock = MockClock()
        with pytest.raises(ValueError, match="negative"):
            clock.advance(-1.0)

    def test_sleep_records_call(self) -> None:
        clock = MockClock()
        clock.sleep(2.0)
        assert clock.sleep_calls == [2.0]

    def test_sleep_auto_advance(self) -> None:
        clock = MockClock(auto_sleep=True)
        clock.sleep(3.0)
        assert clock.now == 3.0
        assert clock.sleep_calls == [3.0]

    def test_set_time(self) -> None:
        clock = MockClock()
        clock.set_time(50.0)
        assert clock.time() == 50.0

    def test_reset(self) -> None:
        clock = MockClock(initial_time=10.0)
        clock.sleep(1.0)
        clock.time()
        clock.reset()
        assert clock.now == 0.0
        assert clock.sleep_calls == []
        assert clock.time_calls == []

    def test_time_calls_recorded(self) -> None:
        clock = MockClock(initial_time=5.0)
        clock.time()
        clock.time()
        assert clock.time_calls == [5.0, 5.0]

    def test_now_property(self) -> None:
        clock = MockClock(initial_time=7.0)
        assert clock.now == 7.0
