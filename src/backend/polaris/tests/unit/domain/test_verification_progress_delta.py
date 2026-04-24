"""Tests for polaris.domain.verification.progress_delta."""

from __future__ import annotations

from polaris.domain.verification.progress_delta import (
    ProgressDelta,
    ProgressTracker,
    compute_progress_delta,
    detect_stall,
)


class TestProgressDelta:
    def test_get_summary_stalled(self) -> None:
        delta = ProgressDelta(
            files_created=0,
            missing_targets_reduced=0,
            errors_reduced=0,
            unresolved_imports_reduced=0,
            trend="stable",
            is_stalled=True,
            stall_rounds=3,
        )
        assert "STALLED" in delta.get_summary()
        assert "3 rounds" in delta.get_summary()

    def test_get_summary_improving(self) -> None:
        delta = ProgressDelta(
            files_created=2,
            missing_targets_reduced=1,
            errors_reduced=0,
            unresolved_imports_reduced=0,
            trend="improving",
            is_stalled=False,
            stall_rounds=0,
        )
        summary = delta.get_summary()
        assert "improving" in summary
        assert "+2 files" in summary


class TestProgressTracker:
    def test_first_update(self) -> None:
        tracker = ProgressTracker()
        delta = tracker.update(0, [], [], [])
        assert delta.trend == "initial"
        assert delta.is_stalled is False

    def test_improving(self) -> None:
        tracker = ProgressTracker()
        tracker.update(0, ["a.py"], ["err"], ["imp"])
        delta = tracker.update(0, [], [], ["imp"])
        assert delta.trend == "improving"
        assert delta.is_stalled is False
        assert delta.missing_targets_reduced == 1
        assert delta.errors_reduced == 1

    def test_stable_becomes_stalled(self) -> None:
        tracker = ProgressTracker(stall_threshold=2)
        tracker.update(0, ["a.py"], [], [])
        tracker.update(0, ["a.py"], [], [])
        delta = tracker.update(0, ["a.py"], [], [])
        assert delta.trend == "stable"
        assert delta.is_stalled is True
        assert delta.stall_rounds == 2

    def test_should_escalate(self) -> None:
        tracker = ProgressTracker(stall_threshold=1)
        tracker.update(0, ["a.py"], [], [])
        tracker.update(0, ["a.py"], [], [])
        tracker.update(0, ["a.py"], [], [])
        tracker.update(0, ["a.py"], [], [])
        assert tracker.should_escalate() is True

    def test_should_not_escalate(self) -> None:
        tracker = ProgressTracker(stall_threshold=10)
        tracker.update(0, ["a.py"], [], [])
        tracker.update(0, ["a.py"], [], [])
        assert tracker.should_escalate() is False


class TestComputeProgressDelta:
    def test_improving(self) -> None:
        delta = compute_progress_delta(
            previous_missing=["a.py"],
            current_missing=[],
            previous_errors=["err"],
            current_errors=[],
            files_created=1,
        )
        assert delta.trend == "improving"
        assert delta.is_stalled is False
        assert delta.missing_targets_reduced == 1
        assert delta.errors_reduced == 1

    def test_stable(self) -> None:
        delta = compute_progress_delta(
            previous_missing=["a.py"],
            current_missing=["a.py"],
            previous_errors=[],
            current_errors=[],
            files_created=0,
        )
        assert delta.trend == "stable"
        assert delta.is_stalled is True

    def test_degrading(self) -> None:
        delta = compute_progress_delta(
            previous_missing=[],
            current_missing=["a.py"],
            previous_errors=[],
            current_errors=["err"],
            files_created=-1,
        )
        assert delta.trend == "degrading"
        assert delta.is_stalled is True


class TestDetectStall:
    def test_not_enough_history(self) -> None:
        deltas = [
            ProgressDelta(0, 0, 0, 0, "stable", False, 0),
        ]
        assert detect_stall(deltas, threshold=2) is False

    def test_stalled(self) -> None:
        deltas = [
            ProgressDelta(0, 0, 0, 0, "improving", False, 0),
            ProgressDelta(0, 0, 0, 0, "stable", False, 1),
            ProgressDelta(0, 0, 0, 0, "stable", True, 2),
        ]
        assert detect_stall(deltas, threshold=2) is True

    def test_not_stalled(self) -> None:
        deltas = [
            ProgressDelta(0, 0, 0, 0, "stable", False, 1),
            ProgressDelta(0, 0, 0, 0, "improving", False, 0),
        ]
        assert detect_stall(deltas, threshold=2) is False
