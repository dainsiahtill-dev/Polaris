"""Tests for polaris.cells.orchestration.pm_dispatch.internal.pm_task_utils.

Covers pure functions, dataclass-like protocol behavior, and I/O helpers.
All filesystem tests use temporary directories.
"""

from __future__ import annotations

from typing import Any

import pytest

from polaris.cells.orchestration.pm_dispatch.internal.pm_task_utils import (
    PM_SPIN_GUARD_STATUS,
    NoopShangshulingPort,
    ShangshulingPort,
    append_pm_report,
    get_director_task_status_summary,
    get_task_signature,
    normalize_task_status,
    to_bool,
)


class TestNormalizeTaskStatus:
    """Tests for normalize_task_status."""

    @pytest.mark.parametrize(
        ("input_value", "expected"),
        [
            ("todo", "todo"),
            ("to_do", "todo"),
            ("pending", "todo"),
            ("in_progress", "in_progress"),
            ("in-progress", "in_progress"),
            ("doing", "in_progress"),
            ("active", "in_progress"),
            ("review", "review"),
            ("in_review", "review"),
            ("needs_continue", "needs_continue"),
            ("need_continue", "needs_continue"),
            ("continue", "needs_continue"),
            ("retry_same_task", "needs_continue"),
            ("done", "done"),
            ("success", "done"),
            ("completed", "done"),
            ("failed", "failed"),
            ("fail", "failed"),
            ("error", "failed"),
            ("blocked", "blocked"),
            ("block", "blocked"),
        ],
    )
    def test_canonical_values(self, input_value: str, expected: str) -> None:
        assert normalize_task_status(input_value) == expected

    def test_unknown_defaults_to_todo(self) -> None:
        assert normalize_task_status("random_status") == "todo"

    def test_none_defaults_to_todo(self) -> None:
        assert normalize_task_status(None) == "todo"

    def test_empty_string_defaults_to_todo(self) -> None:
        assert normalize_task_status("") == "todo"

    def test_whitespace_stripped(self) -> None:
        assert normalize_task_status("  DONE  ") == "done"

    def test_case_insensitive(self) -> None:
        assert normalize_task_status("In_Progress") == "in_progress"

    def test_non_string_coerced(self) -> None:
        assert normalize_task_status(123) == "todo"


class TestGetTaskSignature:
    """Tests for get_task_signature."""

    def test_empty_list_returns_empty(self) -> None:
        assert get_task_signature([]) == ""

    def test_non_list_returns_empty(self) -> None:
        assert get_task_signature("not a list") == ""

    def test_none_returns_empty(self) -> None:
        assert get_task_signature(None) == ""

    def test_uses_fingerprint_field(self) -> None:
        tasks = [{"fingerprint": "abc123", "id": "task-1"}]
        assert get_task_signature(tasks) == "abc123"

    def test_falls_back_to_id(self) -> None:
        tasks = [{"id": "task-1"}]
        assert get_task_signature(tasks) == "task-1"

    def test_fallback_to_hash_when_no_id(self) -> None:
        tasks = [{"name": "task-a"}]
        sig = get_task_signature(tasks)
        assert len(sig) == 16
        assert all(c in "0123456789abcdef" for c in sig)

    def test_deterministic_hash(self) -> None:
        tasks = [{"name": "task-a"}]
        assert get_task_signature(tasks) == get_task_signature(tasks)

    def test_first_task_priority(self) -> None:
        tasks = [{"fingerprint": "first"}, {"fingerprint": "second"}]
        assert get_task_signature(tasks) == "first"


class TestGetDirectorTaskStatusSummary:
    """Tests for get_director_task_status_summary."""

    def test_empty_list(self) -> None:
        result = get_director_task_status_summary([])
        assert result["total"] == 0

    def test_non_list(self) -> None:
        result = get_director_task_status_summary("bad")
        assert result["total"] == 0

    def test_counts_director_tasks_only(self) -> None:
        tasks = [
            {"assigned_to": "director", "status": "done"},
            {"assigned_to": "pm", "status": "done"},
            {"assigned_to": "director", "status": "failed"},
        ]
        result = get_director_task_status_summary(tasks)
        assert result["total"] == 2
        assert result["done"] == 1
        assert result["failed"] == 1

    def test_normalizes_status(self) -> None:
        tasks = [
            {"assigned_to": "director", "status": "in-progress"},
            {"assigned_to": "director", "status": "active"},
        ]
        result = get_director_task_status_summary(tasks)
        assert result["in_progress"] == 2

    def test_skips_non_dict_items(self) -> None:
        tasks = [
            {"assigned_to": "director", "status": "done"},
            "not a dict",
            {"assigned_to": "director", "status": "todo"},
        ]
        result = get_director_task_status_summary(tasks)
        assert result["total"] == 2

    def test_missing_assigned_to_skipped(self) -> None:
        tasks = [{"status": "done"}]
        result = get_director_task_status_summary(tasks)
        assert result["total"] == 0

    def test_case_insensitive_assignee(self) -> None:
        tasks = [{"assigned_to": "DIRECTOR", "status": "done"}]
        result = get_director_task_status_summary(tasks)
        assert result["total"] == 1
        assert result["done"] == 1


class TestToBool:
    """Tests for to_bool."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (True, True),
            (False, False),
            ("1", True),
            ("true", True),
            ("yes", True),
            ("on", True),
            ("0", False),
            ("false", False),
            ("no", False),
            ("off", False),
        ],
    )
    def test_explicit_values(self, value: Any, expected: bool) -> None:
        assert to_bool(value) == expected

    def test_default_true(self) -> None:
        assert to_bool("maybe") is True

    def test_default_false(self) -> None:
        assert to_bool("maybe", default=False) is False

    def test_none_returns_default(self) -> None:
        assert to_bool(None) is True

    def test_empty_string_returns_default(self) -> None:
        assert to_bool("") is True


class TestAppendPmReport:
    """Tests for append_pm_report."""

    def test_empty_path_noop(self, tmp_path: Any) -> None:
        append_pm_report("", "content")

    def test_creates_parent_dirs(self, tmp_path: Any) -> None:
        path = str(tmp_path / "deep" / "path" / "report.txt")
        append_pm_report(path, "hello")
        assert (tmp_path / "deep" / "path" / "report.txt").read_text(encoding="utf-8") == "hello\n"

    def test_appends_content(self, tmp_path: Any) -> None:
        path = str(tmp_path / "report.txt")
        append_pm_report(path, "line1")
        append_pm_report(path, "line2")
        content = (tmp_path / "report.txt").read_text(encoding="utf-8")
        assert content == "line1\nline2\n"

    def test_adds_trailing_newline_if_missing(self, tmp_path: Any) -> None:
        path = str(tmp_path / "report.txt")
        append_pm_report(path, "no newline")
        content = (tmp_path / "report.txt").read_text(encoding="utf-8")
        assert content == "no newline\n"

    def test_preserves_existing_trailing_newline(self, tmp_path: Any) -> None:
        path = str(tmp_path / "report.txt")
        append_pm_report(path, "has newline\n")
        content = (tmp_path / "report.txt").read_text(encoding="utf-8")
        assert content == "has newline\n"


class TestNoopShangshulingPort:
    """Tests for NoopShangshulingPort."""

    def test_isinstance_shangshuling_port(self) -> None:
        port = NoopShangshulingPort()
        assert isinstance(port, ShangshulingPort)

    def test_sync_tasks_returns_zero(self) -> None:
        port = NoopShangshulingPort()
        assert port.sync_tasks_to_shangshuling("/workspace", []) == 0

    def test_get_ready_tasks_returns_empty(self) -> None:
        port = NoopShangshulingPort()
        assert port.get_shangshuling_ready_tasks("/workspace") == []

    def test_record_completion_returns_false(self) -> None:
        port = NoopShangshulingPort()
        assert port.record_shangshuling_task_completion("/workspace", "t1", True, {}) is False

    def test_archive_history_returns_none(self) -> None:
        port = NoopShangshulingPort()
        assert port.archive_task_history("/workspace", "/cache", "r1", 1, {}, None, "ts") is None


class TestConstants:
    """Tests for module constants."""

    def test_pm_spin_guard_status(self) -> None:
        assert PM_SPIN_GUARD_STATUS == "PM_SPIN_GUARD_ACTIVE"


class TestShangshulingPortProtocol:
    """Tests for ShangshulingPort Protocol."""

    def test_custom_implementation(self) -> None:
        class CustomPort:
            def sync_tasks_to_shangshuling(self, workspace_full: str, tasks: list[dict[str, Any]]) -> int:
                return 42

            def get_shangshuling_ready_tasks(self, workspace_full: str, limit: int = 6) -> list[dict[str, Any]]:
                return [{"id": "t1"}]

            def record_shangshuling_task_completion(
                self, workspace_full: str, task_id: str, success: bool, metadata: dict[str, Any]
            ) -> bool:
                return True

            def archive_task_history(
                self,
                workspace_full: str,
                cache_root_full: str,
                run_id: str,
                iteration: int,
                normalized: dict[str, Any],
                director_result: Any,
                timestamp: str,
            ) -> None:
                return

        port = CustomPort()
        assert isinstance(port, ShangshulingPort)
        assert port.sync_tasks_to_shangshuling("ws", []) == 42
