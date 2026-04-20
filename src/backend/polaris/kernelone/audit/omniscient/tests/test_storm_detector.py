"""Tests for AuditStormDetector sliding window thresholds."""

from __future__ import annotations

import time

import pytest
from polaris.kernelone.audit.omniscient.storm_detector import (
    AuditStormDetector,
    StormLevel,
)


class TestAuditStormDetector:
    """Tests for AuditStormDetector."""

    def test_create_detector(self) -> None:
        """Test creating a detector with default thresholds."""
        detector = AuditStormDetector()
        assert detector.get_level() == StormLevel.NORMAL

    def test_create_detector_with_custom_thresholds(self) -> None:
        """Test creating a detector with custom thresholds."""
        detector = AuditStormDetector(
            window_seconds=0.5,
            elevated_threshold=100,
            warning_threshold=500,
            critical_threshold=1000,
            emergency_threshold=2000,
        )
        assert detector.get_level() == StormLevel.NORMAL

    def test_invalid_window_seconds(self) -> None:
        """Test that invalid window_seconds raises ValueError."""
        with pytest.raises(ValueError, match="window_seconds must be positive"):
            AuditStormDetector(window_seconds=0)

    def test_invalid_elevated_threshold(self) -> None:
        """Test that invalid elevated_threshold raises ValueError."""
        with pytest.raises(ValueError, match="elevated_threshold must be positive"):
            AuditStormDetector(elevated_threshold=0)

    def test_invalid_threshold_order(self) -> None:
        """Test that threshold ordering is validated."""
        with pytest.raises(ValueError, match="warning_threshold must be greater than elevated"):
            AuditStormDetector(warning_threshold=100, elevated_threshold=200)

        with pytest.raises(ValueError, match="critical_threshold must be greater than warning"):
            AuditStormDetector(elevated_threshold=10, critical_threshold=100, warning_threshold=200)


class TestStormDetectorLevels:
    """Tests for storm detection levels."""

    def test_normal_level(self) -> None:
        """Test that normal level is returned with low event count."""
        detector = AuditStormDetector(
            elevated_threshold=10,
            warning_threshold=20,
            critical_threshold=30,
        )

        for _ in range(5):
            detector.record_event("test")
            time.sleep(0.01)

        assert detector.get_level() == StormLevel.NORMAL

    def test_elevated_level(self) -> None:
        """Test elevated level is reached."""
        detector = AuditStormDetector(
            elevated_threshold=10,
            warning_threshold=20,
            critical_threshold=30,
        )

        for _ in range(15):
            detector.record_event("test")

        # Get level outside the recording lock
        level = detector.get_level()
        assert level in (StormLevel.ELEVATED, StormLevel.WARNING, StormLevel.CRITICAL)

    def test_warning_level(self) -> None:
        """Test warning level is reached."""
        detector = AuditStormDetector(
            elevated_threshold=10,
            warning_threshold=20,
            critical_threshold=30,
        )

        for _ in range(25):
            detector.record_event("test")

        level = detector.get_level()
        assert level in (StormLevel.WARNING, StormLevel.CRITICAL)

    def test_critical_level(self) -> None:
        """Test critical level is reached."""
        detector = AuditStormDetector(
            elevated_threshold=10,
            warning_threshold=20,
            critical_threshold=30,
        )

        for _ in range(35):
            detector.record_event("test")

        level = detector.get_level()
        assert level in (StormLevel.CRITICAL, StormLevel.EMERGENCY)

    def test_emergency_level(self) -> None:
        """Test emergency level is reached."""
        detector = AuditStormDetector(
            elevated_threshold=10,
            warning_threshold=20,
            critical_threshold=30,
            emergency_threshold=40,
        )

        for _ in range(50):
            detector.record_event("test")

        level = detector.get_level()
        assert level == StormLevel.EMERGENCY


class TestStormDetectorSlidingWindow:
    """Tests for sliding window behavior."""

    def test_sliding_window_expiry(self) -> None:
        """Test that events expire after window duration."""
        detector = AuditStormDetector(
            window_seconds=0.1,  # 100ms window
            elevated_threshold=10,
        )

        # Record events
        for _ in range(5):
            detector.record_event("test")

        # Should still be in window
        assert detector.current_count == 5

        # Wait for window to expire
        time.sleep(0.15)

        # Count should be reduced
        assert detector.current_count == 0

    def test_partial_window_expiry(self) -> None:
        """Test that only old events expire."""
        detector = AuditStormDetector(
            window_seconds=0.1,  # 100ms window
            elevated_threshold=10,
        )

        # Record initial events
        for _ in range(5):
            detector.record_event("test")

        # Wait half the window
        time.sleep(0.06)

        # Record more events
        for _ in range(3):
            detector.record_event("test")

        # Should have some events remaining (5 that haven't expired yet)
        count = detector.current_count
        assert count >= 3  # At least the newer events


class TestStormDetectorShouldDrop:
    """Tests for should_drop() behavior."""

    def test_should_drop_at_emergency(self) -> None:
        """Test that should_drop returns True at emergency level."""
        detector = AuditStormDetector(
            elevated_threshold=10,
            warning_threshold=20,
            critical_threshold=30,
            emergency_threshold=40,
        )

        # Record enough events to reach emergency
        for _ in range(50):
            detector.record_event("test")

        assert detector.should_drop() is True

    def test_should_not_drop_below_emergency(self) -> None:
        """Test that should_drop returns False below emergency level."""
        detector = AuditStormDetector(
            elevated_threshold=10,
            warning_threshold=20,
            critical_threshold=30,
        )

        # Record events but not enough for emergency
        for _ in range(35):
            detector.record_event("test")

        assert detector.should_drop() is False


class TestStormDetectorShouldSkipBody:
    """Tests for should_skip_body() behavior."""

    def test_should_skip_body_at_critical(self) -> None:
        """Test that should_skip_body returns True at critical level."""
        detector = AuditStormDetector(
            elevated_threshold=10,
            warning_threshold=20,
            critical_threshold=30,
        )

        # Record enough events for critical
        for _ in range(35):
            detector.record_event("test")

        assert detector.should_skip_body() is True

    def test_should_not_skip_body_below_critical(self) -> None:
        """Test that should_skip_body returns False below critical level."""
        detector = AuditStormDetector(
            elevated_threshold=10,
            warning_threshold=20,
            critical_threshold=30,
        )

        # Record events but not enough for critical
        for _ in range(25):
            detector.record_event("test")

        assert detector.should_skip_body() is False


class TestStormDetectorPerTypeCounting:
    """Tests for per-type event counting."""

    def test_per_type_stats(self) -> None:
        """Test that per-type stats are tracked."""
        detector = AuditStormDetector(
            window_seconds=1.0,
            elevated_threshold=10,
        )

        detector.record_event("llm_call")
        detector.record_event("llm_call")
        detector.record_event("tool_execution")
        detector.record_event("dialogue")

        stats = detector.get_stats()
        assert stats["per_type_count"]["llm_call"] == 2
        assert stats["per_type_count"]["tool_execution"] == 1
        assert stats["per_type_count"]["dialogue"] == 1

    def test_should_drop_event_type(self) -> None:
        """Test that specific event types can be dropped."""
        detector = AuditStormDetector(
            elevated_threshold=10,
            warning_threshold=20,
            critical_threshold=30,
            emergency_threshold=40,
        )

        # Not at emergency, nothing should be dropped
        assert detector.should_drop_event_type("debug") is False
        assert detector.should_drop_event_type("error") is False

        # At emergency, non-error events should be dropped
        for _ in range(50):
            detector.record_event("test")

        assert detector.should_drop_event_type("debug") is True
        assert detector.should_drop_event_type("error") is False
        assert detector.should_drop_event_type("llm_call") is True


class TestStormDetectorStats:
    """Tests for storm detector statistics."""

    def test_get_stats_structure(self) -> None:
        """Test that get_stats returns correct structure."""
        detector = AuditStormDetector(
            window_seconds=1.0,
            elevated_threshold=500,
            warning_threshold=2000,
            critical_threshold=5000,
            emergency_threshold=10000,
        )

        detector.record_event("test")

        stats = detector.get_stats()

        assert "level" in stats
        assert "total_count" in stats
        assert "per_type_count" in stats
        assert "window_seconds" in stats
        assert "thresholds" in stats
        assert stats["thresholds"]["elevated"] == 500
        assert stats["thresholds"]["warning"] == 2000
        assert stats["thresholds"]["critical"] == 5000
        assert stats["thresholds"]["emergency"] == 10000

    def test_reset(self) -> None:
        """Test that reset clears all state."""
        detector = AuditStormDetector(
            elevated_threshold=10,
            warning_threshold=20,
            critical_threshold=30,
        )

        # Record events
        for _ in range(15):
            detector.record_event("test")

        # Reset
        detector.reset()

        # Check state is cleared
        stats = detector.get_stats()
        assert stats["total_count"] == 0
        assert stats["level"] == StormLevel.NORMAL.value
        assert stats["per_type_count"] == {}


class TestStormDetectorConcurrency:
    """Tests for thread safety."""

    def test_concurrent_record_events(self) -> None:
        """Test that concurrent event recording is safe."""
        import threading

        detector = AuditStormDetector(
            window_seconds=1.0,
            elevated_threshold=1000,
        )

        def record_events(count: int) -> None:
            for _ in range(count):
                detector.record_event("test")
                time.sleep(0.001)

        threads = [threading.Thread(target=record_events, args=(50,)) for _ in range(4)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All events should be recorded
        stats = detector.get_stats()
        assert stats["total_count"] == 200
