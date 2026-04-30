"""Tests for polaris.delivery.cli.audit.audit.reporters module."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from polaris.delivery.cli.audit.audit.reporters import (
    format_factory_events_compact,
    format_failure_compact,
    format_health_compact,
    format_journal_events_compact,
    format_json_output,
    format_search_errors_compact,
    format_triage_compact,
    print_diagnosis,
)


class TestPrintDiagnosis:
    """Tests for print_diagnosis with mocked logger."""

    @pytest.fixture
    def mock_logger(self) -> MagicMock:
        return MagicMock(spec=logging.Logger)

    def test_basic_diagnosis(self, mock_logger: MagicMock) -> None:
        result = {
            "runtime_root": "/test/path",
            "runtime_exists": True,
            "audit_dir": {"exists": True},
            "events_dir": {"exists": True},
            "all_event_files": [],
            "log_files": [],
            "canonical_files": [],
            "index_files": [],
            "corruption_file": {"exists": False},
            "recommendations": [],
        }
        with patch(
            "polaris.delivery.cli.audit.audit.reporters.logger", mock_logger
        ):
            print_diagnosis(result)
        assert mock_logger.info.call_count >= 3

    def test_runtime_not_exists(self, mock_logger: MagicMock) -> None:
        result = {
            "runtime_root": "/test/path",
            "runtime_exists": False,
            "audit_dir": None,
            "events_dir": None,
            "all_event_files": [],
            "log_files": [],
            "canonical_files": [],
            "index_files": [],
            "corruption_file": {"exists": False},
            "recommendations": [],
        }
        with patch(
            "polaris.delivery.cli.audit.audit.reporters.logger", mock_logger
        ):
            print_diagnosis(result)
        assert mock_logger.info.call_count >= 3

    def test_with_event_files(self, mock_logger: MagicMock) -> None:
        result = {
            "runtime_root": "/test/path",
            "runtime_exists": True,
            "audit_dir": {"exists": True},
            "events_dir": {"exists": True},
            "all_event_files": ["file1.json", "file2.json"],
            "log_files": ["log1.json"],
            "canonical_files": [],
            "index_files": [],
            "corruption_file": {"exists": False},
            "recommendations": [],
            "total_events": 5,
        }
        with patch(
            "polaris.delivery.cli.audit.audit.reporters.logger", mock_logger
        ):
            print_diagnosis(result)
        assert mock_logger.info.call_count >= 5

    def test_many_event_files_truncated(self, mock_logger: MagicMock) -> None:
        files = [f"file{i}.json" for i in range(15)]
        result = {
            "runtime_root": "/test/path",
            "runtime_exists": True,
            "audit_dir": {"exists": True},
            "events_dir": {"exists": True},
            "all_event_files": files,
            "log_files": [],
            "canonical_files": [],
            "index_files": [],
            "corruption_file": {"exists": False},
            "recommendations": [],
        }
        with patch(
            "polaris.delivery.cli.audit.audit.reporters.logger", mock_logger
        ):
            print_diagnosis(result)
        assert mock_logger.info.call_count >= 5

    def test_with_log_files(self, mock_logger: MagicMock) -> None:
        result = {
            "runtime_root": "/test/path",
            "runtime_exists": True,
            "audit_dir": {"exists": True},
            "events_dir": {"exists": True},
            "all_event_files": [],
            "log_files": ["log1.json", "log2.json"],
            "canonical_files": [],
            "index_files": [],
            "corruption_file": {"exists": False},
            "recommendations": [],
            "total_events": 3,
        }
        with patch(
            "polaris.delivery.cli.audit.audit.reporters.logger", mock_logger
        ):
            print_diagnosis(result)
        assert mock_logger.info.call_count >= 5

    def test_with_recommendations(self, mock_logger: MagicMock) -> None:
        result = {
            "runtime_root": "/test/path",
            "runtime_exists": True,
            "audit_dir": {"exists": True},
            "events_dir": {"exists": True},
            "all_event_files": [],
            "log_files": [],
            "canonical_files": [],
            "index_files": [],
            "corruption_file": {"exists": False},
            "recommendations": ["rec1", "rec2"],
        }
        with patch(
            "polaris.delivery.cli.audit.audit.reporters.logger", mock_logger
        ):
            print_diagnosis(result)
        assert mock_logger.info.call_count >= 5

    def test_with_corruption(self, mock_logger: MagicMock) -> None:
        result = {
            "runtime_root": "/test/path",
            "runtime_exists": True,
            "audit_dir": {"exists": True},
            "events_dir": {"exists": True},
            "all_event_files": [],
            "log_files": [],
            "canonical_files": [],
            "index_files": [],
            "corruption_file": {"exists": True},
            "recommendations": [],
        }
        with patch(
            "polaris.delivery.cli.audit.audit.reporters.logger", mock_logger
        ):
            print_diagnosis(result)
        assert mock_logger.info.call_count >= 4

    def test_with_factory_events(self, mock_logger: MagicMock) -> None:
        result = {
            "runtime_root": "/test/path",
            "runtime_exists": True,
            "audit_dir": {"exists": True},
            "events_dir": {"exists": True},
            "all_event_files": [],
            "log_files": [],
            "canonical_files": [],
            "index_files": [],
            "corruption_file": {"exists": False},
            "recommendations": [],
            "factory_events_found": True,
            "latest_run_id": "run-123",
            "factory_event_count": 5,
            "factory_events_path": "/test/factory",
        }
        with patch(
            "polaris.delivery.cli.audit.audit.reporters.logger", mock_logger
        ):
            print_diagnosis(result)
        assert mock_logger.info.call_count >= 5

    def test_with_alternative_runtimes(self, mock_logger: MagicMock) -> None:
        result = {
            "runtime_root": "/test/path",
            "runtime_exists": True,
            "audit_dir": {"exists": True},
            "events_dir": {"exists": True},
            "all_event_files": [],
            "log_files": [],
            "canonical_files": [],
            "index_files": [],
            "corruption_file": {"exists": False},
            "recommendations": [],
            "alternative_runtimes": ["/alt1", "/alt2"],
        }
        with patch(
            "polaris.delivery.cli.audit.audit.reporters.logger", mock_logger
        ):
            print_diagnosis(result)
        assert mock_logger.info.call_count >= 5

    def test_with_inventory_stats(self, mock_logger: MagicMock) -> None:
        result = {
            "runtime_root": "/test/path",
            "runtime_exists": True,
            "audit_dir": {"exists": True},
            "events_dir": {"exists": True},
            "all_event_files": [],
            "log_files": [],
            "canonical_files": [],
            "index_files": [],
            "corruption_file": {"exists": False},
            "recommendations": [],
            "all_events_total": 100,
            "event_inventory": {
                "audit": {"files": 5, "events": 50},
                "runtime": {"files": 3, "events": 30},
            },
        }
        with patch(
            "polaris.delivery.cli.audit.audit.reporters.logger", mock_logger
        ):
            print_diagnosis(result)
        assert mock_logger.info.call_count >= 5

    def test_with_invalid_lines_and_errors(self, mock_logger: MagicMock) -> None:
        result = {
            "runtime_root": "/test/path",
            "runtime_exists": True,
            "audit_dir": {"exists": True},
            "events_dir": {"exists": True},
            "all_event_files": [],
            "log_files": [],
            "canonical_files": [],
            "index_files": [],
            "corruption_file": {"exists": False},
            "recommendations": [],
            "all_events_total": 100,
            "event_inventory": {},
            "invalid_event_lines": 5,
            "read_errors": 2,
        }
        with patch(
            "polaris.delivery.cli.audit.audit.reporters.logger", mock_logger
        ):
            print_diagnosis(result)
        assert mock_logger.info.call_count >= 5


class TestFormatFailureCompact:
    """Tests for format_failure_compact."""

    def test_basic_failure(self) -> None:
        event = {
            "timestamp": "2024-01-15T10:30:00+00:00",
            "event_type": "tool_call",
            "source": {"role": "director"},
            "action": {"name": "test_tool", "error": "something failed"},
        }
        result = format_failure_compact(event)
        assert "tool_call" in result
        assert "director" in result
        assert "test_tool" in result
        assert "something failed" in result

    def test_no_relative_time(self) -> None:
        event = {
            "timestamp": "2024-01-15T10:30:00+00:00",
            "event_type": "tool_call",
            "source": {"role": "director"},
            "action": {"name": "test_tool", "error": "fail"},
        }
        result = format_failure_compact(event, use_relative_time=False)
        assert "2024-01-15" in result

    def test_no_error(self) -> None:
        event = {
            "timestamp": "2024-01-15T10:30:00+00:00",
            "event_type": "tool_call",
            "source": {"role": "director"},
            "action": {"name": "test_tool"},
        }
        result = format_failure_compact(event)
        assert "错误" not in result

    def test_non_dict_source(self) -> None:
        event = {
            "timestamp": "2024-01-15T10:30:00+00:00",
            "event_type": "tool_call",
            "source": "not_dict",
            "action": {"name": "test_tool", "error": "fail"},
        }
        result = format_failure_compact(event)
        assert "unknown" in result

    def test_none_action(self) -> None:
        event = {
            "timestamp": "2024-01-15T10:30:00+00:00",
            "event_type": "tool_call",
            "source": {"role": "director"},
            "action": None,
        }
        result = format_failure_compact(event)
        assert "操作:" in result

    def test_empty_event(self) -> None:
        event: dict[str, object] = {}
        result = format_failure_compact(event)
        assert "unknown" in result


class TestFormatHealthCompact:
    """Tests for format_health_compact."""

    def test_healthy(self) -> None:
        result = {
            "overall": "healthy",
            "checks": {
                "disk": {"status": "ok", "message": "ok"},
                "memory": {"status": "ok", "message": "ok"},
            },
        }
        output = format_health_compact(result, Path("/test"))
        assert "✓ Health: healthy" in output
        assert "test" in output
        assert "disk" in output
        assert "memory" in output

    def test_degraded(self) -> None:
        result = {
            "overall": "degraded",
            "checks": {
                "disk": {"status": "warning", "message": "low"},
            },
        }
        output = format_health_compact(result, Path("/test"))
        assert "⚠ Health: degraded" in output

    def test_unhealthy(self) -> None:
        result = {
            "overall": "unhealthy",
            "checks": {
                "disk": {"status": "error", "message": "fail"},
            },
        }
        output = format_health_compact(result, Path("/test"))
        assert "✗ Health: unhealthy" in output

    def test_unknown_status(self) -> None:
        result = {
            "overall": "unknown",
            "checks": {
                "disk": {"status": "info", "message": "info"},
            },
        }
        output = format_health_compact(result, Path("/test"))
        assert "? Health: unknown" in output

    def test_empty_checks(self) -> None:
        result = {"overall": "healthy", "checks": {}}
        output = format_health_compact(result, Path("/test"))
        assert "✓ Health: healthy" in output


class TestFormatTriageCompact:
    """Tests for format_triage_compact."""

    def test_success_status(self) -> None:
        result = {
            "status": "success",
            "mode": "test",
            "run_id": "run-1",
            "task_id": "task-1",
            "generated_at": "2024-01-15T10:30:00+00:00",
            "pm_quality_history": [],
            "director_tool_audit": {"total": 10, "failed": 2},
        }
        output = format_triage_compact(result)
        assert "Triage report" in output
        assert "run-1" in output
        assert "task-1" in output
        assert "Tool calls: 10" in output
        assert "Tool failures: 2" in output

    def test_partial_status(self) -> None:
        result = {
            "status": "partial",
            "mode": "test",
            "run_id": "run-1",
            "task_id": "task-1",
            "generated_at": "2024-01-15T10:30:00+00:00",
            "pm_quality_history": [],
            "director_tool_audit": {},
        }
        output = format_triage_compact(result)
        assert "Triage report" in output

    def test_failure_hops(self) -> None:
        result = {
            "status": "success",
            "mode": "test",
            "run_id": "run-1",
            "task_id": "task-1",
            "generated_at": "2024-01-15T10:30:00+00:00",
            "pm_quality_history": [],
            "director_tool_audit": {},
            "failure_hops": {
                "has_failure": True,
                "failure_code": "E001",
                "hop1_phase": {"phase": "pm", "actor": "test"},
            },
        }
        output = format_triage_compact(result)
        assert "Failure detected!" in output
        assert "E001" in output
        assert "pm" in output

    def test_not_found(self) -> None:
        result = {
            "status": "not_found",
            "mode": "test",
            "help_message": "No events found",
            "suggestions": ["Check path", "Try again"],
        }
        output = format_triage_compact(result)
        assert "No events found" in output
        assert "Check path" in output
        assert "Try again" in output

    def test_error_status(self) -> None:
        result = {"status": "error", "mode": "test", "error": "something wrong"}
        output = format_triage_compact(result)
        assert "Error: something wrong" in output

    def test_no_failure_hops(self) -> None:
        result = {
            "status": "success",
            "mode": "test",
            "run_id": "run-1",
            "task_id": "task-1",
            "generated_at": "2024-01-15T10:30:00+00:00",
            "pm_quality_history": [],
            "director_tool_audit": {},
            "failure_hops": {"has_failure": False},
        }
        output = format_triage_compact(result)
        assert "Triage report" in output
        assert "Failure detected!" not in output


class TestFormatSearchErrorsCompact:
    """Tests for format_search_errors_compact."""

    def test_empty_chains(self) -> None:
        output = format_search_errors_compact(
            [], pattern="test", time_window="1h"
        )
        assert "找到 0 个错误链条" in output
        assert "1h" in output

    def test_single_chain_basic(self) -> None:
        chain = MagicMock()
        chain.chain_id = "chain-123"
        chain.failure_event = MagicMock()
        chain.failure_event.ts = "2024-01-15T10:30:00+00:00"
        chain.failure_event.refs = {"phase": "pm", "run_id": "run-1", "task_id": "task-1"}
        chain.failure_event.output = {}
        chain.tool_name = "test_tool"
        chain.tool_args = []
        chain.failure_reason = "error msg"
        chain.timeline = []
        chain.context_events = []

        output = format_search_errors_compact(
            [chain], pattern="test", time_window="1h"
        )
        assert "chain-123" in output
        assert "test_tool" in output
        assert "error msg" in output
        assert "pm" in output
        assert "run-1" in output
        assert "task-1" in output

    def test_show_args(self) -> None:
        chain = MagicMock()
        chain.chain_id = "chain-1"
        chain.failure_event = MagicMock()
        chain.failure_event.ts = ""
        chain.failure_event.refs = {}
        chain.failure_event.output = {}
        chain.tool_name = "tool"
        chain.tool_args = ["arg1", "arg2"]
        chain.failure_reason = "err"
        chain.timeline = []
        chain.context_events = []

        output = format_search_errors_compact(
            [chain], pattern="test", time_window="1h", show_args=True
        )
        assert "arg1" in output
        assert "arg2" in output

    def test_show_output_with_stderr(self) -> None:
        chain = MagicMock()
        chain.chain_id = "chain-1"
        chain.failure_event = MagicMock()
        chain.failure_event.ts = ""
        chain.failure_event.refs = {}
        chain.failure_event.output = {
            "exit_code": 1,
            "stderr": "line1\nline2\nline3\nline4\nline5\nline6",
        }
        chain.tool_name = "tool"
        chain.tool_args = []
        chain.failure_reason = "err"
        chain.timeline = []
        chain.context_events = []

        output = format_search_errors_compact(
            [chain], pattern="test", time_window="1h", show_output=True
        )
        assert "Exit code: 1" in output
        assert "line1" in output
        assert "truncated" in output

    def test_link_chains(self) -> None:
        link = MagicMock()
        link.ts = "2024-01-15T10:30:00+00:00"
        link.kind = "action"
        link.actor = "director"
        link.name = "test"
        link.ok = True

        chain = MagicMock()
        chain.chain_id = "chain-1"
        chain.failure_event = MagicMock()
        chain.failure_event.ts = ""
        chain.failure_event.refs = {}
        chain.failure_event.output = {}
        chain.tool_name = "tool"
        chain.tool_args = []
        chain.failure_reason = "err"
        chain.timeline = [link]
        chain.context_events = []

        output = format_search_errors_compact(
            [chain], pattern="test", time_window="1h", link_chains=True
        )
        assert "director" in output
        assert "test" in output

    def test_context_events(self) -> None:
        ctx = MagicMock()
        ctx.ts = "2024-01-15T10:30:00+00:00"
        ctx.kind = "action"
        ctx.actor = "director"
        ctx.name = "ctx_event"
        ctx.ts_epoch = 1705315800

        chain = MagicMock()
        chain.chain_id = "chain-1"
        chain.failure_event = MagicMock()
        chain.failure_event.ts = ""
        chain.failure_event.refs = {}
        chain.failure_event.output = {}
        chain.tool_name = "tool"
        chain.tool_args = []
        chain.failure_reason = "err"
        chain.timeline = []
        chain.context_events = [ctx]

        output = format_search_errors_compact(
            [chain], pattern="test", time_window="1h", context=1
        )
        assert "ctx_event" in output

    def test_multiple_chains(self) -> None:
        chain1 = MagicMock()
        chain1.chain_id = "chain-1"
        chain1.failure_event = MagicMock()
        chain1.failure_event.ts = ""
        chain1.failure_event.refs = {}
        chain1.failure_event.output = {}
        chain1.tool_name = "tool1"
        chain1.tool_args = []
        chain1.failure_reason = "err1"
        chain1.timeline = []
        chain1.context_events = []

        chain2 = MagicMock()
        chain2.chain_id = "chain-2"
        chain2.failure_event = MagicMock()
        chain2.failure_event.ts = ""
        chain2.failure_event.refs = {}
        chain2.failure_event.output = {}
        chain2.tool_name = "tool2"
        chain2.tool_args = []
        chain2.failure_reason = "err2"
        chain2.timeline = []
        chain2.context_events = []

        output = format_search_errors_compact(
            [chain1, chain2], pattern="test", time_window="1h"
        )
        assert "chain-1" in output
        assert "chain-2" in output


class TestFormatFactoryEventsCompact:
    """Tests for format_factory_events_compact."""

    def test_empty_collection(self) -> None:
        collection: dict[str, object] = {"runs": []}
        output = format_factory_events_compact(collection)
        assert "总计: 0 个事件" in output

    def test_single_run(self) -> None:
        collection = {
            "runs": [
                {
                    "run_id": "run-1",
                    "total_events": 5,
                    "events_file": "events.json",
                    "events": [
                        {
                            "timestamp": "2024-01-15T10:30:00+00:00",
                            "type": "test",
                            "stage": "init",
                            "message": "started",
                        }
                    ],
                }
            ]
        }
        output = format_factory_events_compact(collection)
        assert "run-1" in output
        assert "events.json" in output
        assert "started" in output
        assert "总计: 5 个事件" in output

    def test_no_relative_time(self) -> None:
        collection = {
            "runs": [
                {
                    "run_id": "run-1",
                    "total_events": 1,
                    "events_file": "events.json",
                    "events": [
                        {
                            "timestamp": "2024-01-15T10:30:00+00:00",
                            "type": "test",
                            "stage": "init",
                            "message": "msg",
                        }
                    ],
                }
            ]
        }
        output = format_factory_events_compact(
            collection, use_relative_time=False
        )
        assert "10:30:00" in output

    def test_truncated_events(self) -> None:
        collection = {
            "runs": [
                {
                    "run_id": "run-1",
                    "total_events": 10,
                    "events_file": "events.json",
                    "events": [{"timestamp": "", "type": "test", "stage": "", "message": ""}],
                    "invalid_lines": 2,
                    "read_errors": 1,
                }
            ]
        }
        output = format_factory_events_compact(collection)
        assert "还有 9 个事件" in output
        assert "忽略损坏行: 2" in output
        assert "读取错误: 1" in output

    def test_long_message_truncated(self) -> None:
        collection = {
            "runs": [
                {
                    "run_id": "run-1",
                    "total_events": 1,
                    "events_file": "events.json",
                    "events": [
                        {
                            "timestamp": "2024-01-15T10:30:00+00:00",
                            "type": "test",
                            "stage": "init",
                            "message": "x" * 100,
                        }
                    ],
                }
            ]
        }
        output = format_factory_events_compact(collection)
        assert "x" * 80 in output
        assert "x" * 81 not in output

    def test_multiple_runs(self) -> None:
        collection = {
            "runs": [
                {
                    "run_id": "run-1",
                    "total_events": 3,
                    "events_file": "e1.json",
                    "events": [],
                },
                {
                    "run_id": "run-2",
                    "total_events": 2,
                    "events_file": "e2.json",
                    "events": [],
                },
            ]
        }
        output = format_factory_events_compact(collection)
        assert "run-1" in output
        assert "run-2" in output
        assert "总计: 5 个事件" in output


class TestFormatJournalEventsCompact:
    """Tests for format_journal_events_compact."""

    def test_empty_events(self) -> None:
        output = format_journal_events_compact([])
        assert "Journal 事件" in output

    def test_single_event(self) -> None:
        events = [
            {
                "ts": "2024-01-15T10:30:00+00:00",
                "kind": "test_kind",
            }
        ]
        output = format_journal_events_compact(events)
        assert "test_kind" in output
        assert "journal" in output

    def test_uses_timestamp_fallback(self) -> None:
        events = [
            {
                "timestamp": "2024-01-15T10:30:00+00:00",
                "kind": "test_kind",
            }
        ]
        output = format_journal_events_compact(events)
        assert "test_kind" in output

    def test_no_relative_time(self) -> None:
        events = [
            {
                "ts": "2024-01-15T10:30:00+00:00",
                "kind": "test_kind",
            }
        ]
        output = format_journal_events_compact(events, use_relative_time=False)
        assert "10:30:00" in output

    def test_multiple_events(self) -> None:
        events = [
            {"ts": "2024-01-15T10:30:00+00:00", "kind": "kind1"},
            {"ts": "2024-01-15T10:31:00+00:00", "kind": "kind2"},
        ]
        output = format_journal_events_compact(events)
        assert "kind1" in output
        assert "kind2" in output

    def test_missing_ts(self) -> None:
        events = [{"kind": "test_kind"}]
        output = format_journal_events_compact(events)
        assert "test_kind" in output


class TestFormatJsonOutput:
    """Tests for format_json_output."""

    def test_dict_output(self) -> None:
        data = {"key": "value", "num": 42}
        output = format_json_output(data)
        parsed = json.loads(output)
        assert parsed["key"] == "value"
        assert parsed["num"] == 42

    def test_list_output(self) -> None:
        data = [1, 2, 3]
        output = format_json_output(data)
        parsed = json.loads(output)
        assert parsed == [1, 2, 3]

    def test_unicode_preservation(self) -> None:
        data = {"message": "你好世界"}
        output = format_json_output(data)
        assert "你好世界" in output

    def test_datetime_serialization(self) -> None:
        dt = datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc)
        data = {"time": dt}
        output = format_json_output(data)
        assert "2024-01-15" in output

    def test_nested_structure(self) -> None:
        data = {"outer": {"inner": [1, 2, {"deep": "value"}]}}
        output = format_json_output(data)
        parsed = json.loads(output)
        assert parsed["outer"]["inner"][2]["deep"] == "value"

    def test_indent_formatting(self) -> None:
        data = {"a": 1}
        output = format_json_output(data)
        assert "  \"a\": 1" in output


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
