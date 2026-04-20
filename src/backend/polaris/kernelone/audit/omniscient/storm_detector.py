"""AuditStormDetector — sliding window event counter with multi-level thresholds.

Design:
- Uses collections.deque with timestamps for sliding window
- Thread-safe with threading.Lock for sync callers
- Multi-level thresholds: NORMAL, ELEVATED, WARNING, CRITICAL, EMERGENCY
- Provides should_drop() and should_skip_body() for back-pressure
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# =============================================================================
# Storm Level Enum
# =============================================================================


class StormLevel(str, Enum):
    """Storm detection levels for audit event back-pressure.

    Attributes:
        NORMAL: Normal event rate, no action needed.
        ELEVATED: Slightly elevated rate, monitor closely.
        WARNING: High rate, consider dropping DEBUG events.
        CRITICAL: Very high rate, drop non-ERROR events, skip bodies.
        EMERGENCY: Extreme rate, drop everything except errors.
    """

    NORMAL = "normal"
    ELEVATED = "elevated"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


# =============================================================================
# Storm Detector
# =============================================================================


@dataclass
class _EventCount:
    """Count entry for a specific event type."""

    count: int = 0
    timestamps: deque[float] = field(default_factory=deque)


class AuditStormDetector:
    """Sliding window event counter with multi-level storm detection.

    Maintains a sliding window of event timestamps and computes a storm
    level based on configurable thresholds. Used for back-pressure
    control in the audit bus.

    Attributes:
        window_seconds: Sliding window duration in seconds.
        elevated_threshold: Events per window for ELEVATED level.
        warning_threshold: Events per window for WARNING level.
        critical_threshold: Events per window for CRITICAL level.
        emergency_threshold: Events per window for EMERGENCY level.

    Usage:
        detector = AuditStormDetector(
            window_seconds=1,
            elevated_threshold=500,
            warning_threshold=2000,
            critical_threshold=5000,
        )

        detector.record_event("llm_call")
        level = detector.get_level()
        if detector.should_drop():
            return  # Drop event
        if detector.should_skip_body():
            redacted = True  # Skip body content
    """

    def __init__(
        self,
        window_seconds: float = 1.0,
        elevated_threshold: int = 500,
        warning_threshold: int = 2000,
        critical_threshold: int = 5000,
        emergency_threshold: int | None = None,
    ) -> None:
        """Initialize the storm detector with thresholds.

        Args:
            window_seconds: Sliding window duration in seconds.
            elevated_threshold: Count threshold for ELEVATED level.
            warning_threshold: Count threshold for WARNING level.
            critical_threshold: Count threshold for CRITICAL level.
            emergency_threshold: Count threshold for EMERGENCY level.
                              Defaults to critical_threshold * 2.
        """
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        if elevated_threshold <= 0:
            raise ValueError("elevated_threshold must be positive")
        if warning_threshold <= elevated_threshold:
            raise ValueError("warning_threshold must be greater than elevated_threshold")
        if critical_threshold <= warning_threshold:
            raise ValueError("critical_threshold must be greater than warning_threshold")

        self._window_seconds = window_seconds
        self._elevated_threshold = elevated_threshold
        self._warning_threshold = warning_threshold
        self._critical_threshold = critical_threshold
        self._emergency_threshold = emergency_threshold or (critical_threshold * 2)

        # Per-event-type counters with timestamps
        self._event_counts: dict[str, _EventCount] = {}
        self._total_count = _EventCount()

        # Lock for thread-safe access
        self._lock = threading.Lock()

        # Track current level to avoid recomputation
        self._cached_level: StormLevel = StormLevel.NORMAL
        self._last_cleanup_time: float = time.monotonic()

    def record_event(self, event_type: str) -> None:
        """Record an event occurrence for storm detection.

        Args:
            event_type: Type of event (e.g., "llm_call", "tool_execution").
        """
        now = time.monotonic()

        with self._lock:
            # Get or create counter for event type
            if event_type not in self._event_counts:
                self._event_counts[event_type] = _EventCount()
            counter = self._event_counts[event_type]

            # Add timestamp
            counter.timestamps.append(now)
            counter.count += 1

            # Also track total
            self._total_count.timestamps.append(now)
            self._total_count.count += 1

            # Cleanup old timestamps periodically
            self._cleanup_locked(now)

    def get_level(self) -> StormLevel:
        """Compute current storm level based on sliding window counts.

        Returns:
            Current StormLevel based on total event count.
        """
        now = time.monotonic()

        with self._lock:
            self._cleanup_locked(now)

            total = self._total_count.count

            if total >= self._emergency_threshold:
                level = StormLevel.EMERGENCY
            elif total >= self._critical_threshold:
                level = StormLevel.CRITICAL
            elif total >= self._warning_threshold:
                level = StormLevel.WARNING
            elif total >= self._elevated_threshold:
                level = StormLevel.ELEVATED
            else:
                level = StormLevel.NORMAL

            self._cached_level = level
            return level

    def should_drop(self) -> bool:
        """Determine if events should be dropped based on storm level.

        At EMERGENCY level, all non-ERROR events should be dropped.

        Returns:
            True if events should be dropped, False otherwise.
        """
        return self.get_level() == StormLevel.EMERGENCY

    def should_skip_body(self) -> bool:
        """Determine if event bodies (prompts/responses) should be skipped.

        At CRITICAL or higher level, body content should be redacted
        to reduce memory and processing overhead.

        Returns:
            True if body content should be skipped, False otherwise.
        """
        level = self.get_level()
        return level in (StormLevel.CRITICAL, StormLevel.EMERGENCY)

    def should_drop_event_type(self, event_type: str) -> bool:
        """Determine if a specific event type should be dropped.

        Non-ERROR/WARNING events are dropped at CRITICAL+ levels.

        Args:
            event_type: The event type to check.

        Returns:
            True if the event type should be dropped.
        """
        level = self.get_level()
        if level != StormLevel.EMERGENCY:
            return False

        # At EMERGENCY, only ERROR events are allowed
        normalized = event_type.lower()
        return "error" not in normalized and "failure" not in normalized

    def should_redact_body(self, event_type: str) -> bool:
        """Determine if event body should be redacted.

        Args:
            event_type: The event type to check.

        Returns:
            True if body content should be redacted.
        """
        level = self.get_level()
        if level in (StormLevel.NORMAL, StormLevel.ELEVATED):
            return False
        if level == StormLevel.WARNING:
            # Only redact DEBUG events at WARNING level
            normalized = event_type.lower()
            return "debug" in normalized

        # At CRITICAL+, redact all body content
        return True

    def get_stats(self) -> dict[str, Any]:
        """Get current storm detection statistics.

        Returns:
            Dictionary with current counts, level, and thresholds.
        """
        with self._lock:
            # Cleanup before stats
            self._cleanup_locked(time.monotonic())

            per_type: dict[str, int] = {event_type: counter.count for event_type, counter in self._event_counts.items()}

            return {
                "level": self._cached_level.value,
                "total_count": self._total_count.count,
                "per_type_count": per_type,
                "window_seconds": self._window_seconds,
                "thresholds": {
                    "elevated": self._elevated_threshold,
                    "warning": self._warning_threshold,
                    "critical": self._critical_threshold,
                    "emergency": self._emergency_threshold,
                },
            }

    def reset(self) -> None:
        """Reset all counters and state.

        This is primarily for testing or when switching contexts.
        """
        with self._lock:
            self._event_counts.clear()
            self._total_count = _EventCount()
            self._cached_level = StormLevel.NORMAL

    def _cleanup_locked(self, now: float) -> None:
        """Remove timestamps outside the sliding window.

        Must be called while holding the lock.

        Args:
            now: Current monotonic time.
        """
        cutoff = now - self._window_seconds

        # Cleanup total count
        while self._total_count.timestamps and self._total_count.timestamps[0] < cutoff:
            self._total_count.timestamps.popleft()
            self._total_count.count -= 1

        # Cleanup per-type counts
        for counter in self._event_counts.values():
            while counter.timestamps and counter.timestamps[0] < cutoff:
                counter.timestamps.popleft()
                counter.count -= 1

        # Remove empty counters
        self._event_counts = {et: c for et, c in self._event_counts.items() if c.count > 0}

    @property
    def current_count(self) -> int:
        """Get current event count in the window (thread-safe)."""
        with self._lock:
            self._cleanup_locked(time.monotonic())
            return self._total_count.count
