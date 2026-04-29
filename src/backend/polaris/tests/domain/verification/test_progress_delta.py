"""Tests for progress_delta module."""

from __future__ import annotations

import pytest
from polaris.domain.verification.progress_delta import (
    ProgressDelta,
    ProgressTracker,
    compute_progress_delta,
    detect_stall,
)


# =============================================================================
# ProgressDelta
# =============================================================================
def test_progress_delta_is_frozen():
    delta = ProgressDelta(
        files_created=1,
        missing_targets_reduced=0,
        errors_reduced=0,
        unresolved_imports_reduced=0,
        trend="improving",
        is_stalled=False,
        stall_rounds=0,
    )
    with pytest.raises(AttributeError):
        delta.trend = "stable"


def test_progress_delta_summary_improving():
    delta = ProgressDelta(
        files_created=2,
        missing_targets_reduced=1,
        errors_reduced=3,
        unresolved_imports_reduced=0,
        trend="improving",
        is_stalled=False,
        stall_rounds=0,
    )
    assert "Trend: improving" in delta.get_summary()
    assert "+2 files" in delta.get_summary()
    assert "-1 missing" in delta.get_summary()
    assert "-3 errors" in delta.get_summary()


def test_progress_delta_summary_stalled():
    delta = ProgressDelta(
        files_created=0,
        missing_targets_reduced=0,
        errors_reduced=0,
        unresolved_imports_reduced=0,
        trend="stable",
        is_stalled=True,
        stall_rounds=3,
    )
    assert delta.get_summary() == "STALLED (3 rounds)"


# =============================================================================
# ProgressTracker
# =============================================================================
def test_tracker_first_update():
    tracker = ProgressTracker(stall_threshold=2)
    delta = tracker.update(
        files_created=1,
        missing_targets=["a.py"],
        errors=["e1"],
        unresolved_imports=["i1"],
    )
    assert delta.trend == "initial"
    assert delta.is_stalled is False
    assert delta.stall_rounds == 0
    assert tracker._round == 1


def test_tracker_improving():
    tracker = ProgressTracker(stall_threshold=2)
    tracker.update(files_created=0, missing_targets=["a.py"], errors=[], unresolved_imports=[])
    delta = tracker.update(
        files_created=1,
        missing_targets=["a.py"],
        errors=[],
        unresolved_imports=[],
    )
    assert delta.trend == "improving"
    assert delta.is_stalled is False
    assert delta.stall_rounds == 0
    assert delta.files_created == 1


def test_tracker_stable_becomes_stalled():
    tracker = ProgressTracker(stall_threshold=2)
    tracker.update(files_created=0, missing_targets=["a.py"], errors=[], unresolved_imports=[])
    tracker.update(files_created=0, missing_targets=["a.py"], errors=[], unresolved_imports=[])
    delta = tracker.update(
        files_created=0,
        missing_targets=["a.py"],
        errors=[],
        unresolved_imports=[],
    )
    assert delta.trend == "stable"
    assert delta.is_stalled is True
    assert delta.stall_rounds == 2


def test_tracker_degrading():
    tracker = ProgressTracker(stall_threshold=3)
    tracker.update(files_created=0, missing_targets=["a.py"], errors=[], unresolved_imports=[])
    delta = tracker.update(
        files_created=-1,
        missing_targets=["a.py", "b.py"],
        errors=[],
        unresolved_imports=[],
    )
    assert delta.trend == "degrading"
    assert delta.stall_rounds == 1


def test_tracker_should_escalate():
    tracker = ProgressTracker(stall_threshold=2)
    # Round 1: initial, stall_count=0
    tracker.update(files_created=0, missing_targets=["a.py"], errors=[], unresolved_imports=[])
    assert tracker.should_escalate() is False
    # Round 2: stable, stall_count=1
    tracker.update(files_created=0, missing_targets=["a.py"], errors=[], unresolved_imports=[])
    assert tracker.should_escalate() is False
    # Round 3: stable, stall_count=2, is_stalled=True
    tracker.update(files_created=0, missing_targets=["a.py"], errors=[], unresolved_imports=[])
    assert tracker.should_escalate() is False  # 2 < 4
    # Round 4: stable, stall_count=3
    tracker.update(files_created=0, missing_targets=["a.py"], errors=[], unresolved_imports=[])
    assert tracker.should_escalate() is False  # 3 < 4
    # Round 5: stable, stall_count=4
    tracker.update(files_created=0, missing_targets=["a.py"], errors=[], unresolved_imports=[])
    assert tracker.should_escalate() is True  # 4 >= 4


def test_tracker_missing_reduced():
    tracker = ProgressTracker(stall_threshold=2)
    tracker.update(files_created=0, missing_targets=["a.py", "b.py"], errors=[], unresolved_imports=[])
    delta = tracker.update(
        files_created=0,
        missing_targets=["b.py"],
        errors=[],
        unresolved_imports=[],
    )
    assert delta.missing_targets_reduced == 1
    assert delta.trend == "improving"


def test_tracker_errors_reduced():
    tracker = ProgressTracker(stall_threshold=2)
    tracker.update(files_created=0, missing_targets=[], errors=["e1", "e2"], unresolved_imports=[])
    delta = tracker.update(
        files_created=0,
        missing_targets=[],
        errors=["e1"],
        unresolved_imports=[],
    )
    assert delta.errors_reduced == 1
    assert delta.trend == "improving"


def test_tracker_unresolved_imports_reduced():
    tracker = ProgressTracker(stall_threshold=2)
    tracker.update(files_created=0, missing_targets=[], errors=[], unresolved_imports=["i1"])
    delta = tracker.update(
        files_created=0,
        missing_targets=[],
        errors=[],
        unresolved_imports=[],
    )
    assert delta.unresolved_imports_reduced == 1
    assert delta.trend == "improving"


# =============================================================================
# compute_progress_delta
# =============================================================================
def test_compute_progress_delta_improving():
    delta = compute_progress_delta(
        previous_missing=["a.py"],
        current_missing=[],
        previous_errors=[],
        current_errors=[],
        files_created=1,
    )
    assert delta.trend == "improving"
    assert delta.is_stalled is False
    assert delta.files_created == 1
    assert delta.missing_targets_reduced == 1


def test_compute_progress_delta_stable():
    delta = compute_progress_delta(
        previous_missing=["a.py"],
        current_missing=["a.py"],
        previous_errors=[],
        current_errors=[],
        files_created=0,
    )
    assert delta.trend == "stable"
    assert delta.is_stalled is True
    assert delta.missing_targets_reduced == 0


def test_compute_progress_delta_no_change_stable():
    delta = compute_progress_delta(
        previous_missing=["a.py"],
        current_missing=["a.py"],
        previous_errors=[],
        current_errors=[],
        files_created=0,
    )
    assert delta.trend == "stable"
    assert delta.is_stalled is True


def test_compute_progress_delta_degrading_with_negative_files():
    delta = compute_progress_delta(
        previous_missing=["a.py"],
        current_missing=["a.py"],
        previous_errors=[],
        current_errors=[],
        files_created=-1,
    )
    assert delta.trend == "degrading"
    assert delta.is_stalled is True


# =============================================================================
# detect_stall
# =============================================================================
def test_detect_stall_insufficient_history():
    history = [
        ProgressDelta(0, 0, 0, 0, "stable", False, 0),
    ]
    assert detect_stall(history, threshold=2) is False


def test_detect_stall_all_stable():
    history = [
        ProgressDelta(0, 0, 0, 0, "improving", False, 0),
        ProgressDelta(0, 0, 0, 0, "stable", False, 0),
        ProgressDelta(0, 0, 0, 0, "stable", True, 1),
    ]
    assert detect_stall(history, threshold=2) is True


def test_detect_stall_one_improving_in_recent():
    history = [
        ProgressDelta(0, 0, 0, 0, "stable", False, 0),
        ProgressDelta(0, 0, 0, 0, "improving", False, 0),
        ProgressDelta(0, 0, 0, 0, "stable", False, 0),
    ]
    assert detect_stall(history, threshold=2) is False


def test_detect_stall_degrading_counts():
    history = [
        ProgressDelta(0, 0, 0, 0, "improving", False, 0),
        ProgressDelta(0, 0, 0, 0, "degrading", False, 0),
        ProgressDelta(0, 0, 0, 0, "degrading", False, 0),
    ]
    assert detect_stall(history, threshold=2) is True


def test_detect_stall_threshold_boundary():
    history = [
        ProgressDelta(0, 0, 0, 0, "stable", False, 0),
    ]
    assert detect_stall(history, threshold=1) is True
