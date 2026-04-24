"""Tests for polaris.cells.orchestration.pm_dispatch.internal.pm_task_utils."""

from __future__ import annotations

import pytest
from polaris.cells.orchestration.pm_dispatch.internal.pm_task_utils import (
    PM_SPIN_GUARD_STATUS,
    NoopShangshulingPort,
    ShangshulingPort,
    get_director_task_status_summary,
    get_task_signature,
    normalize_task_status,
    to_bool,
)


class TestNormalizeTaskStatus:
    @pytest.mark.parametrize(
        ("input_val", "expected"),
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
    def test_normalize_task_status_valid(self, input_val: str, expected: str) -> None:
        assert normalize_task_status(input_val) == expected

    def test_normalize_task_status_none(self) -> None:
        assert normalize_task_status(None) == "todo"

    def test_normalize_task_status_empty_string(self) -> None:
        assert normalize_task_status("") == "todo"

    def test_normalize_task_status_whitespace(self) -> None:
        assert normalize_task_status("  todo ") == "todo"

    def test_normalize_task_status_unknown(self) -> None:
        assert normalize_task_status("unknown_status") == "todo"


class TestGetTaskSignature:
    def test_empty_list(self) -> None:
        assert get_task_signature([]) == ""

    def test_none_input(self) -> None:
        assert get_task_signature(None) == ""

    def test_non_list_input(self) -> None:
        assert get_task_signature("not a list") == ""

    def test_first_task_with_id(self) -> None:
        tasks = [{"id": "TASK-001", "title": "Test"}]
        assert get_task_signature(tasks) == "TASK-001"

    def test_first_task_with_fingerprint(self) -> None:
        tasks = [{"fingerprint": "abc123", "title": "Test"}]
        assert get_task_signature(tasks) == "abc123"

    def test_first_task_fingerprint_over_id(self) -> None:
        tasks = [{"fingerprint": "abc123", "id": "TASK-001", "title": "Test"}]
        assert get_task_signature(tasks) == "abc123"

    def test_signature_is_sha256_prefix(self) -> None:
        tasks = [{"title": "Test", "description": "No id or fingerprint"}]
        sig = get_task_signature(tasks)
        assert len(sig) == 16
        assert sig.isalnum()

    def test_non_dict_first_element(self) -> None:
        tasks = ["not a dict", {"id": "TASK-001"}]
        sig = get_task_signature(tasks)
        # When first element is not a dict, primary={}, so sig="",
        # then fallback computes SHA256 hash of the whole list
        assert len(sig) == 16
        assert sig.isalnum()


class TestGetDirectorTaskStatusSummary:
    def test_empty_list(self) -> None:
        summary = get_director_task_status_summary([])
        assert summary["total"] == 0
        assert summary["todo"] == 0

    def test_none_input(self) -> None:
        summary = get_director_task_status_summary(None)
        assert summary["total"] == 0

    def test_non_list_input(self) -> None:
        summary = get_director_task_status_summary("not a list")
        assert summary["total"] == 0

    def test_filters_non_director_tasks(self) -> None:
        tasks = [
            {"assigned_to": "director", "status": "done"},
            {"assigned_to": "other", "status": "done"},
        ]
        summary = get_director_task_status_summary(tasks)
        assert summary["total"] == 1

    def test_counts_by_status(self) -> None:
        tasks = [
            {"assigned_to": "director", "status": "todo"},
            {"assigned_to": "director", "status": "in_progress"},
            {"assigned_to": "director", "status": "done"},
            {"assigned_to": "director", "status": "failed"},
        ]
        summary = get_director_task_status_summary(tasks)
        assert summary["total"] == 4
        assert summary["todo"] == 1
        assert summary["in_progress"] == 1
        assert summary["done"] == 1
        assert summary["failed"] == 1

    def test_skips_non_dict_items(self) -> None:
        tasks = [
            {"assigned_to": "director", "status": "done"},
            "not a dict",
            None,
            {"assigned_to": "director", "status": "review"},
        ]
        summary = get_director_task_status_summary(tasks)
        assert summary["total"] == 2


class TestToBool:
    @pytest.mark.parametrize(
        ("input_val", "expected"),
        [
            (True, True),
            (False, False),
            ("true", True),
            ("True", True),
            ("1", True),
            ("yes", True),
            ("on", True),
            ("false", False),
            ("False", False),
            ("0", False),
            ("no", False),
            ("off", False),
        ],
    )
    def test_to_bool_converts_correctly(self, input_val: str, expected: bool) -> None:
        assert to_bool(input_val) is expected

    def test_to_bool_default(self) -> None:
        assert to_bool("unknown", default=False) is False
        assert to_bool("unknown", default=True) is True

    def test_to_bool_none(self) -> None:
        assert to_bool(None) is True
        assert to_bool(None, default=False) is False

    def test_to_bool_empty_string(self) -> None:
        assert to_bool("") is True


class TestPmSpinGuardStatus:
    def test_pm_spin_guard_status_value(self) -> None:
        assert PM_SPIN_GUARD_STATUS == "PM_SPIN_GUARD_ACTIVE"


class TestNoopShangshulingPort:
    def test_sync_tasks_returns_zero(self) -> None:
        port = NoopShangshulingPort()
        assert port.sync_tasks_to_shangshuling("/workspace", [{"id": "TASK-001"}]) == 0

    def test_get_ready_tasks_returns_empty(self) -> None:
        port = NoopShangshulingPort()
        assert port.get_shangshuling_ready_tasks("/workspace") == []

    def test_record_completion_returns_false(self) -> None:
        port = NoopShangshulingPort()
        assert port.record_shangshuling_task_completion("/workspace", "TASK-001", True, {}) is False

    def test_archive_history_returns_none(self) -> None:
        port = NoopShangshulingPort()
        assert port.archive_task_history("/workspace", "/cache", "run-1", 1, {}, {}, "2024-01-01") is None


class TestShangshulingPortProtocol:
    def test_is_runtime_checkable(self) -> None:
        """ShangshulingPort is a runtime-checkable protocol."""
        assert hasattr(ShangshulingPort, "__protocol_attrs__")

    def test_noop_implements_protocol(self) -> None:
        """NoopShangshulingPort should be accepted as implementing ShangshulingPort."""
        port = NoopShangshulingPort()
        assert isinstance(port, ShangshulingPort)
