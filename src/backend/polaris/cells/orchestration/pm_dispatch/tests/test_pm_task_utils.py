"""Unit tests for orchestration.pm_dispatch internal pm_task_utils.

Tests pure functions: normalize_task_status, get_task_signature,
get_director_task_status_summary, to_bool, append_pm_report;
and ShangshulingPort Protocol + NoopShangshulingPort.
"""

from __future__ import annotations

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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_pm_spin_guard_status_value() -> None:
    assert PM_SPIN_GUARD_STATUS == "PM_SPIN_GUARD_ACTIVE"


# ---------------------------------------------------------------------------
# normalize_task_status
# ---------------------------------------------------------------------------


class TestNormalizeTaskStatus:
    def test_todo_aliases(self) -> None:
        assert normalize_task_status("todo") == "todo"
        assert normalize_task_status("to_do") == "todo"
        assert normalize_task_status("pending") == "todo"
        assert normalize_task_status("PENDING") == "todo"

    def test_in_progress_aliases(self) -> None:
        assert normalize_task_status("in_progress") == "in_progress"
        assert normalize_task_status("in-progress") == "in_progress"
        assert normalize_task_status("doing") == "in_progress"
        assert normalize_task_status("active") == "in_progress"

    def test_review_aliases(self) -> None:
        assert normalize_task_status("review") == "review"
        assert normalize_task_status("in_review") == "review"

    def test_needs_continue_aliases(self) -> None:
        assert normalize_task_status("needs_continue") == "needs_continue"
        assert normalize_task_status("need_continue") == "needs_continue"
        assert normalize_task_status("continue") == "needs_continue"
        assert normalize_task_status("retry_same_task") == "needs_continue"

    def test_done_aliases(self) -> None:
        assert normalize_task_status("done") == "done"
        assert normalize_task_status("success") == "done"
        assert normalize_task_status("completed") == "done"

    def test_failed_aliases(self) -> None:
        assert normalize_task_status("failed") == "failed"
        assert normalize_task_status("fail") == "failed"
        assert normalize_task_status("error") == "failed"

    def test_blocked_aliases(self) -> None:
        assert normalize_task_status("blocked") == "blocked"
        assert normalize_task_status("block") == "blocked"

    def test_unknown_returns_todo(self) -> None:
        assert normalize_task_status("unknown_status") == "todo"
        assert normalize_task_status("") == "todo"
        assert normalize_task_status(None) == "todo"

    def test_whitespace_stripped(self) -> None:
        assert normalize_task_status("  in_progress  ") == "in_progress"


# ---------------------------------------------------------------------------
# get_task_signature
# ---------------------------------------------------------------------------


class TestGetTaskSignature:
    def test_empty_input(self) -> None:
        assert get_task_signature([]) == ""
        assert get_task_signature(None) == ""
        assert get_task_signature("not a list") == ""

    def test_uses_first_task_fingerprint(self) -> None:
        tasks = [{"fingerprint": "fp123", "id": "id456"}]
        assert get_task_signature(tasks) == "fp123"

    def test_falls_back_to_id(self) -> None:
        tasks = [{"id": "task-id-abc"}]
        assert get_task_signature(tasks) == "task-id-abc"

    def test_falls_back_to_sha256(self) -> None:
        tasks = [{"title": "Build login", "goal": "Create form"}]
        sig = get_task_signature(tasks)
        assert len(sig) == 16
        assert sig.isalnum()

    def test_non_dict_first_item(self) -> None:
        tasks = ["not a dict", {"id": "t1"}]
        # Non-dict first item triggers SHA fallback
        sig = get_task_signature(tasks)
        assert len(sig) == 16

    def test_signature_is_deterministic(self) -> None:
        tasks = [{"title": "Same", "goal": "Same goal"}]
        sig1 = get_task_signature(tasks)
        sig2 = get_task_signature(tasks)
        assert sig1 == sig2


# ---------------------------------------------------------------------------
# get_director_task_status_summary
# ---------------------------------------------------------------------------


class TestGetDirectorTaskStatusSummary:
    def test_empty_input(self) -> None:
        result = get_director_task_status_summary([])
        assert result["total"] == 0

    def test_non_list_input(self) -> None:
        result = get_director_task_status_summary("not a list")
        assert result["total"] == 0

    def test_filters_non_director_tasks(self) -> None:
        tasks = [
            {"assigned_to": "pm", "status": "done"},
            {"assigned_to": "director", "status": "done"},
        ]
        result = get_director_task_status_summary(tasks)
        assert result["total"] == 1
        assert result["done"] == 1

    def test_counts_by_status(self) -> None:
        tasks = [
            {"assigned_to": "director", "status": "todo"},
            {"assigned_to": "director", "status": "todo"},
            {"assigned_to": "director", "status": "in_progress"},
        ]
        result = get_director_task_status_summary(tasks)
        assert result["total"] == 3
        assert result["todo"] == 2
        assert result["in_progress"] == 1

    def test_normalizes_status_aliases(self) -> None:
        tasks = [
            {"assigned_to": "director", "status": "completed"},  # -> done
            {"assigned_to": "director", "status": "failed"},
        ]
        result = get_director_task_status_summary(tasks)
        assert result["done"] == 1
        assert result["failed"] == 1

    def test_skips_non_dict_items(self) -> None:
        tasks = [
            "not a dict",
            123,
            {"assigned_to": "director", "status": "done"},
        ]
        result = get_director_task_status_summary(tasks)
        assert result["total"] == 1

    def test_all_status_keys_present(self) -> None:
        result = get_director_task_status_summary([])
        expected_keys = {"total", "todo", "in_progress", "review", "needs_continue", "done", "failed", "blocked"}
        assert set(result.keys()) == expected_keys


# ---------------------------------------------------------------------------
# to_bool
# ---------------------------------------------------------------------------


class TestToBool:
    def test_true_aliases(self) -> None:
        assert to_bool(True) is True
        assert to_bool("true") is True
        assert to_bool("True") is True
        assert to_bool("1") is True
        assert to_bool("yes") is True
        assert to_bool("on") is True

    def test_false_aliases(self) -> None:
        assert to_bool(False) is False
        assert to_bool("false") is False
        assert to_bool("False") is False
        assert to_bool("0") is False
        assert to_bool("no") is False
        assert to_bool("off") is False

    def test_unknown_returns_default(self) -> None:
        assert to_bool("maybe", default=True) is True
        assert to_bool("maybe", default=False) is False
        assert to_bool(None, default=False) is False
        assert to_bool("", default=True) is True  # empty string not in false aliases

    def test_whitespace_stripped(self) -> None:
        assert to_bool("  true  ") is True
        assert to_bool("  0  ") is False


# ---------------------------------------------------------------------------
# append_pm_report
# ---------------------------------------------------------------------------


class TestAppendPmReport:
    def test_empty_path_is_noop(self) -> None:
        append_pm_report("", "some content")
        # No exception means success

    def test_creates_parent_dirs(self, tmp_path) -> None:
        report_path = tmp_path / "a" / "b" / "report.md"
        append_pm_report(str(report_path), "Hello")
        assert report_path.exists()
        content = report_path.read_text(encoding="utf-8")
        assert "Hello" in content

    def test_adds_trailing_newline(self, tmp_path) -> None:
        report_path = tmp_path / "report.md"
        append_pm_report(str(report_path), "Line1")
        content = report_path.read_text(encoding="utf-8")
        assert content == "Line1\n"

    def test_no_double_newline_if_already_present(self, tmp_path) -> None:
        report_path = tmp_path / "report.md"
        append_pm_report(str(report_path), "Line1\n")
        content = report_path.read_text(encoding="utf-8")
        assert content == "Line1\n"

    def test_appends_to_existing_file(self, tmp_path) -> None:
        report_path = tmp_path / "report.md"
        report_path.write_text("Existing\n", encoding="utf-8")
        append_pm_report(str(report_path), "New content")
        content = report_path.read_text(encoding="utf-8")
        assert "Existing" in content
        assert "New content" in content


# ---------------------------------------------------------------------------
# ShangshulingPort Protocol
# ---------------------------------------------------------------------------


class TestShangshulingPortIsProtocol:
    def test_noop_is_runtime_checkable(self) -> None:
        port = NoopShangshulingPort()
        # runtime_checkable allows isinstance at runtime
        assert isinstance(port, ShangshulingPort)


# ---------------------------------------------------------------------------
# NoopShangshulingPort
# ---------------------------------------------------------------------------


class TestNoopShangshulingPort:
    def test_sync_tasks_returns_zero(self) -> None:
        port = NoopShangshulingPort()
        assert port.sync_tasks_to_shangshuling("/ws", [{"id": "t1"}]) == 0

    def test_get_ready_tasks_returns_empty(self) -> None:
        port = NoopShangshulingPort()
        assert port.get_shangshuling_ready_tasks("/ws") == []
        assert port.get_shangshuling_ready_tasks("/ws", limit=3) == []

    def test_record_completion_returns_false(self) -> None:
        port = NoopShangshulingPort()
        result = port.record_shangshuling_task_completion("/ws", "t1", success=True, metadata={})
        assert result is False

    def test_archive_history_is_silent(self) -> None:
        port = NoopShangshulingPort()
        # Must not raise
        port.archive_task_history(
            workspace_full="/ws",
            cache_root_full="/cache",
            run_id="run-1",
            iteration=1,
            normalized={},
            director_result={},
            timestamp="2026-03-23T00:00:00Z",
        )
