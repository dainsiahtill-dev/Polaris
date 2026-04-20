"""Tests for polaris.cells.workspace.integrity.internal.diff_tracker

Focus: exception-observability contract — failures must be logged, never
silently swallowed.  File I/O and subprocess calls are mocked so these tests
run entirely in-process with no real filesystem or git dependency.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from polaris.cells.workspace.integrity.internal.diff_tracker import (
    FileChangeSnapshot,
    FileChangeTracker,
    TaskFileChangeTracker,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tracker(tmp_path: Path) -> FileChangeTracker:
    tracker = FileChangeTracker(str(tmp_path))
    # Treat as git repo so we exercise the git code-paths
    tracker._is_git_repo = True
    return tracker


# ---------------------------------------------------------------------------
# _capture_git_baseline failure → empty dict + warning logged
# ---------------------------------------------------------------------------

class TestCaptureGitBaselineFailure:
    def test_returns_empty_dict_on_exception(self, tmp_path: Path, caplog):
        tracker = _make_tracker(tmp_path)

        with patch.object(tracker._cmd_svc, "run", side_effect=RuntimeError("git broken")):
            with caplog.at_level(logging.WARNING):
                result = tracker._capture_git_baseline()

        assert result == {}, "Should return empty dict on failure"
        assert any("Failed to capture git baseline" in r.message for r in caplog.records), (
            "Warning must be logged when git baseline capture fails"
        )

    def test_logs_with_exc_info(self, tmp_path: Path, caplog):
        tracker = _make_tracker(tmp_path)

        with patch.object(tracker._cmd_svc, "run", side_effect=OSError("permission denied")):
            with caplog.at_level(logging.WARNING):
                tracker._capture_git_baseline()

        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warning_records, "At least one warning must be emitted"
        assert warning_records[0].exc_info is not None, (
            "exc_info must be attached to the log record so the traceback is preserved"
        )


# ---------------------------------------------------------------------------
# _capture_filesystem_baseline — per-file stat failure → warning + continue
# ---------------------------------------------------------------------------

class TestFilesystemBaselineStatFailure:
    def test_partial_failure_yields_warning_not_empty(self, tmp_path: Path, caplog):
        """A single-file stat failure should not abort the whole walk."""
        # Create two files
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("hello", encoding="utf-8")
        f2.write_text("world", encoding="utf-8")

        tracker = FileChangeTracker(str(tmp_path))
        tracker._is_git_repo = False

        original_stat = Path.stat

        call_count = {"n": 0}

        def flaky_stat(self_, *args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise OSError("simulated permission error")
            return original_stat(self_, *args, **kwargs)

        with patch.object(Path, "stat", flaky_stat), caplog.at_level(logging.WARNING):
            baseline = tracker._capture_filesystem_baseline()

        # At least one file should still be captured
        assert len(baseline) >= 1, "Partial failure should not wipe all results"
        assert any("Could not stat file" in r.message for r in caplog.records), (
            "Per-file OSError must produce a warning"
        )

    def test_walk_failure_yields_warning_and_empty(self, tmp_path: Path, caplog):
        tracker = FileChangeTracker(str(tmp_path))
        tracker._is_git_repo = False

        with patch("polaris.cells.workspace.integrity.internal.diff_tracker.os.walk",
                   side_effect=OSError("disk exploded")), caplog.at_level(logging.WARNING):
            baseline = tracker._capture_filesystem_baseline()

        assert baseline == {}, "Walk failure should yield empty baseline"
        assert any("Failed to walk workspace" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _get_git_changes failure → empty snapshot + warning logged
# ---------------------------------------------------------------------------

class TestGetGitChangesFailure:
    def test_returns_empty_snapshot_on_exception(self, tmp_path: Path, caplog):
        tracker = _make_tracker(tmp_path)
        tracker._baseline = {}  # ensure baseline is set so we reach git path

        with patch.object(tracker._cmd_svc, "run", side_effect=RuntimeError("network gone")):
            with caplog.at_level(logging.WARNING):
                snapshot = tracker._get_git_changes()

        assert isinstance(snapshot, FileChangeSnapshot)
        assert snapshot.created == 0
        assert snapshot.modified == 0
        assert snapshot.deleted == 0
        assert any("Failed to compute git changes" in r.message for r in caplog.records)

    def test_logs_with_exc_info(self, tmp_path: Path, caplog):
        tracker = _make_tracker(tmp_path)
        tracker._baseline = {}

        with patch.object(tracker._cmd_svc, "run", side_effect=ValueError("bad returncode")):
            with caplog.at_level(logging.WARNING):
                tracker._get_git_changes()

        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warning_records
        assert warning_records[0].exc_info is not None


# ---------------------------------------------------------------------------
# _get_deleted_files / _get_new_files / _get_tracked_files failures
# → empty set + warning
# ---------------------------------------------------------------------------

class TestHelperSetFetchFailures:
    @pytest.mark.parametrize("method_name,log_fragment", [
        ("_get_deleted_files", "Failed to fetch deleted files"),
        ("_get_new_files", "Failed to fetch new (untracked) files"),
        ("_get_tracked_files", "Failed to fetch tracked files"),
    ])
    def test_returns_empty_set_and_warns(
        self, tmp_path: Path, caplog, method_name: str, log_fragment: str
    ):
        tracker = _make_tracker(tmp_path)

        with patch.object(tracker._cmd_svc, "run", side_effect=RuntimeError("oops")):
            with caplog.at_level(logging.WARNING):
                result = getattr(tracker, method_name)()

        assert result == set(), f"{method_name} must return empty set on failure"
        assert any(log_fragment in r.message for r in caplog.records), (
            f"Warning containing '{log_fragment}' expected"
        )


# ---------------------------------------------------------------------------
# _get_filesystem_changes failure → partial/empty snapshot + warning
# ---------------------------------------------------------------------------

class TestGetFilesystemChangesFailure:
    def test_returns_empty_snapshot_on_exception(self, tmp_path: Path, caplog):
        tracker = FileChangeTracker(str(tmp_path))
        tracker._is_git_repo = False
        tracker._baseline = {"a.txt": "123_456"}

        with patch.object(
            tracker,
            "_capture_filesystem_baseline",
            side_effect=RuntimeError("fs error"),
        ), caplog.at_level(logging.WARNING):
            snapshot = tracker._get_filesystem_changes()

        assert isinstance(snapshot, FileChangeSnapshot)
        assert any("Failed to compute filesystem changes" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# get_changes with no baseline returns empty snapshot (no crash)
# ---------------------------------------------------------------------------

class TestGetChangesNoBaseline:
    def test_returns_empty_snapshot_without_crash(self, tmp_path: Path):
        tracker = FileChangeTracker(str(tmp_path))
        # Force non-git path
        tracker._is_git_repo = False

        # First call captures baseline and returns empty (no changes yet)
        snapshot = tracker.get_changes()
        assert isinstance(snapshot, FileChangeSnapshot)


# ---------------------------------------------------------------------------
# TaskFileChangeTracker integration
# ---------------------------------------------------------------------------

class TestTaskFileChangeTracker:
    def test_finish_returns_snapshot(self, tmp_path: Path):
        tft = TaskFileChangeTracker(str(tmp_path), task_id="t1")
        tft.tracker._is_git_repo = False

        with patch.object(tft.tracker, "_capture_filesystem_baseline", return_value={}):
            tft.start()
            snapshot = tft.finish()

        assert isinstance(snapshot, FileChangeSnapshot)
        assert not tft._started
