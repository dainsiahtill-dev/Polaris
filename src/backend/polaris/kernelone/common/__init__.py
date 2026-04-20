"""KernelOne common utilities."""

from __future__ import annotations

from polaris.kernelone.common import clock

ClockPort = clock.ClockPort
RealClock = clock.RealClock
MockClock = clock.MockClock

__all__ = ["ClockPort", "MockClock", "RealClock", "clock"]
