"""Tests for polaris.kernelone.fs.memory_snapshot (pure utility functions)."""

from __future__ import annotations

from polaris.kernelone.fs.memory_snapshot import get_memory_summary


class TestGetMemorySummary:
    """Tests for get_memory_summary pure utility."""

    def test_none_snapshot_returns_none(self) -> None:
        result = get_memory_summary(None, max_chars=100)
        assert result == "none"

    def test_empty_snapshot_returns_none(self) -> None:
        result = get_memory_summary({}, max_chars=100)
        assert result == "none"

    def test_last_run_at_field(self) -> None:
        snapshot = {"last_run_at": "2026-04-24T10:00:00Z"}
        result = get_memory_summary(snapshot, max_chars=200)
        assert "last_run_at: 2026-04-24T10:00:00Z" in result

    def test_last_summary_field(self) -> None:
        snapshot = {"last_summary": "Completed successfully"}
        result = get_memory_summary(snapshot, max_chars=200)
        assert "last_summary: Completed successfully" in result

    def test_last_next_step_field(self) -> None:
        snapshot = {"last_next_step": "Review pull request"}
        result = get_memory_summary(snapshot, max_chars=200)
        assert "last_next_step: Review pull request" in result

    def test_last_log_path_field(self) -> None:
        snapshot = {"last_log_path": "/tmp/run.log"}
        result = get_memory_summary(snapshot, max_chars=200)
        assert "last_log_path: /tmp/run.log" in result

    def test_multiple_fields(self) -> None:
        snapshot = {
            "last_run_at": "2026-04-24T10:00:00Z",
            "last_summary": "Done",
            "last_next_step": "Next",
        }
        result = get_memory_summary(snapshot, max_chars=200)
        assert "last_run_at" in result
        assert "last_summary" in result
        assert "last_next_step" in result

    def test_truncation(self) -> None:
        snapshot = {"last_summary": "A" * 300}
        result = get_memory_summary(snapshot, max_chars=50)
        assert result.endswith("...")
        assert len(result) == 53  # 50 + "..."

    def test_truncation_zero_max_chars(self) -> None:
        snapshot = {"last_summary": "Should not show"}
        result = get_memory_summary(snapshot, max_chars=0)
        assert "last_summary" in result

    def test_unknown_fields_ignored(self) -> None:
        snapshot = {"unknown_field": "value", "another": "field"}
        result = get_memory_summary(snapshot, max_chars=200)
        assert "unknown_field" not in result
        assert "another" not in result

    def test_returns_string(self) -> None:
        result = get_memory_summary({"last_run_at": "2026"}, max_chars=100)
        assert isinstance(result, str)
