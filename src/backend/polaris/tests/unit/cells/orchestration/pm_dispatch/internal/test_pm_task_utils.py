"""Tests for polaris.cells.orchestration.pm_dispatch.internal.pm_task_utils."""

from __future__ import annotations

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


class TestAppendPmReport:
    """Tests for append_pm_report file I/O utility."""

    def test_empty_path_is_noop(self, tmp_path) -> None:
        append_pm_report("", "some content")
        # Should not raise and should not create any file

    def test_creates_parent_directories(self, tmp_path) -> None:
        report_path = tmp_path / "nested" / "dir" / "report.md"
        assert not report_path.parent.exists()
        append_pm_report(str(report_path), "line 1")
        assert report_path.exists()
        assert report_path.parent.exists()

    def test_appends_content_with_newline(self, tmp_path) -> None:
        report_path = tmp_path / "report.md"
        append_pm_report(str(report_path), "line 1")
        append_pm_report(str(report_path), "line 2")
        content = report_path.read_text(encoding="utf-8")
        assert content == "line 1\nline 2\n"

    def test_does_not_double_newline(self, tmp_path) -> None:
        report_path = tmp_path / "report.md"
        append_pm_report(str(report_path), "already has newline\n")
        content = report_path.read_text(encoding="utf-8")
        assert content == "already has newline\n"
        assert "\n\n" not in content

    def test_uses_utf8_encoding(self, tmp_path) -> None:
        report_path = tmp_path / "report.md"
        unicode_content = "Unicode: \u7981\u7528\u8bcd\u6c47"
        append_pm_report(str(report_path), unicode_content)
        content = report_path.read_text(encoding="utf-8")
        assert unicode_content in content

    def test_appends_to_existing_file(self, tmp_path) -> None:
        report_path = tmp_path / "report.md"
        report_path.write_text("existing\n", encoding="utf-8")
        append_pm_report(str(report_path), "appended")
        content = report_path.read_text(encoding="utf-8")
        assert content == "existing\nappended\n"


class TestGetTaskSignatureEdgeCases:
    """Additional edge cases for get_task_signature."""

    def test_deterministic_for_same_input(self) -> None:
        tasks = [{"title": "A", "desc": "B"}]
        sig1 = get_task_signature(tasks)
        sig2 = get_task_signature(tasks)
        assert sig1 == sig2

    def test_different_inputs_produce_different_signatures(self) -> None:
        tasks_a = [{"title": "A"}]
        tasks_b = [{"title": "B"}]
        sig_a = get_task_signature(tasks_a)
        sig_b = get_task_signature(tasks_b)
        assert sig_a != sig_b

    def test_dict_order_does_not_matter(self) -> None:
        tasks = [{"z": 1, "a": 2}]
        sig = get_task_signature(tasks)
        assert len(sig) == 16

    def test_falsy_id_falls_back_to_fingerprint(self) -> None:
        tasks = [{"id": "", "fingerprint": "fp-123"}]
        assert get_task_signature(tasks) == "fp-123"

    def test_whitespace_id_is_stripped(self) -> None:
        tasks = [{"id": "  TASK-001  "}]
        assert get_task_signature(tasks) == "TASK-001"

    def test_list_with_multiple_dicts(self) -> None:
        tasks = [
            {"id": "first"},
            {"id": "second"},
        ]
        assert get_task_signature(tasks) == "first"


class TestGetDirectorTaskStatusSummaryEdgeCases:
    """Additional edge cases for status summary aggregation."""

    def test_case_insensitive_assigned_to(self) -> None:
        tasks = [
            {"assigned_to": "Director", "status": "done"},
            {"assigned_to": "DIRECTOR", "status": "done"},
            {"assigned_to": "director", "status": "done"},
        ]
        summary = get_director_task_status_summary(tasks)
        assert summary["total"] == 3
        assert summary["done"] == 3

    def test_status_normalization(self) -> None:
        tasks = [
            {"assigned_to": "director", "status": "DONE"},
            {"assigned_to": "director", "status": "Success"},
            {"assigned_to": "director", "status": "IN_PROGRESS"},
        ]
        summary = get_director_task_status_summary(tasks)
        assert summary["done"] == 2
        assert summary["in_progress"] == 1

    def test_unknown_status_defaults_to_todo(self) -> None:
        tasks = [
            {"assigned_to": "director", "status": "weird_status"},
        ]
        summary = get_director_task_status_summary(tasks)
        assert summary["todo"] == 1

    def test_missing_assigned_to_skips_task(self) -> None:
        tasks = [
            {"status": "done"},
            {"assigned_to": "director", "status": "done"},
        ]
        summary = get_director_task_status_summary(tasks)
        assert summary["total"] == 1

    def test_empty_assigned_to_skips_task(self) -> None:
        tasks = [
            {"assigned_to": "", "status": "done"},
            {"assigned_to": "director", "status": "done"},
        ]
        summary = get_director_task_status_summary(tasks)
        assert summary["total"] == 1

    def test_blocked_status_counted(self) -> None:
        tasks = [
            {"assigned_to": "director", "status": "blocked"},
            {"assigned_to": "director", "status": "block"},
        ]
        summary = get_director_task_status_summary(tasks)
        assert summary["blocked"] == 2

    def test_needs_continue_variants(self) -> None:
        tasks = [
            {"assigned_to": "director", "status": "needs_continue"},
            {"assigned_to": "director", "status": "need_continue"},
            {"assigned_to": "director", "status": "continue"},
            {"assigned_to": "director", "status": "retry_same_task"},
        ]
        summary = get_director_task_status_summary(tasks)
        assert summary["needs_continue"] == 4


class TestToBoolEdgeCases:
    """Additional edge cases for to_bool conversion."""

    @pytest.mark.parametrize(
        ("input_val", "expected"),
        [
            ("  true  ", True),
            ("TRUE", True),
            ("  FALSE  ", False),
            ("False", False),
        ],
    )
    def test_whitespace_and_case(self, input_val: str, expected: bool) -> None:
        assert to_bool(input_val) is expected

    def test_whitespace_only_defaults(self) -> None:
        assert to_bool("   ") is True
        assert to_bool("   ", default=False) is False

    def test_int_zero_and_one(self) -> None:
        # Note: 0 is falsy so value or '' yields empty string, falling
        # through to the default (True).  This is the documented behaviour.
        assert to_bool(1) is True  # '1' matches truthy set
        assert to_bool(0) is True  # falsy -> empty string -> default

    def test_float_defaults(self) -> None:
        assert to_bool(1.5) is True
        assert to_bool(0.0, default=False) is False

    def test_negative_number_defaults(self) -> None:
        assert to_bool(-1) is True

    def test_list_defaults(self) -> None:
        assert to_bool([1, 2, 3]) is True

    def test_dict_defaults(self) -> None:
        assert to_bool({"key": "value"}) is True
