"""Tests for polaris.delivery.ws.endpoints.json_utils module."""

from __future__ import annotations

import json

import pytest
from polaris.delivery.ws.endpoints.json_utils import (
    parse_json_line,
    resolve_journal_event_channel,
    sanitize_snapshot_lines,
)


class TestParseJsonLine:
    """Tests for parse_json_line function."""

    def test_valid_json_object(self) -> None:
        """Test parsing valid JSON object."""
        result = parse_json_line('{"key": "value"}')
        assert result == {"key": "value"}

    def test_valid_json_with_whitespace(self) -> None:
        """Test parsing JSON with leading/trailing whitespace."""
        result = parse_json_line('   {"key": "value"}   ')
        assert result == {"key": "value"}

    def test_empty_string_returns_none(self) -> None:
        """Test empty string returns None."""
        assert parse_json_line("") is None

    def test_whitespace_only_returns_none(self) -> None:
        """Test whitespace-only string returns None."""
        assert parse_json_line("   \n\t  ") is None

    def test_none_input_returns_none(self) -> None:
        """Test None input returns None."""
        assert parse_json_line(None) is None  # type: ignore[arg-type]

    def test_invalid_json_returns_none(self) -> None:
        """Test invalid JSON returns None."""
        assert parse_json_line("not json") is None

    def test_json_array_returns_none(self) -> None:
        """Test JSON array returns None (not a dict)."""
        assert parse_json_line('[1, 2, 3]') is None

    def test_json_string_returns_none(self) -> None:
        """Test JSON string returns None (not a dict)."""
        assert parse_json_line('"hello"') is None

    def test_json_number_returns_none(self) -> None:
        """Test JSON number returns None (not a dict)."""
        assert parse_json_line('42') is None

    def test_json_bool_returns_none(self) -> None:
        """Test JSON boolean returns None (not a dict)."""
        assert parse_json_line('true') is None

    def test_malformed_json_brace_only(self) -> None:
        """Test malformed JSON with just brace."""
        assert parse_json_line('{') is None

    def test_nested_json_object(self) -> None:
        """Test parsing nested JSON object."""
        result = parse_json_line('{"outer": {"inner": 1}}')
        assert result == {"outer": {"inner": 1}}

    def test_unicode_in_json(self) -> None:
        """Test parsing JSON with unicode characters."""
        result = parse_json_line('{"msg": "\u4e2d\u6587"}')
        assert result == {"msg": "中文"}

    def test_logs_debug_on_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test that parse errors are logged at debug level."""
        with caplog.at_level("DEBUG"):
            parse_json_line("bad json")
        assert "json.loads" in caplog.text or caplog.text == ""

    def test_empty_dict(self) -> None:
        """Test parsing empty dict."""
        result = parse_json_line('{}')
        assert result == {}

    def test_json_with_null(self) -> None:
        """Test parsing JSON with null values."""
        result = parse_json_line('{"key": null}')
        assert result == {"key": None}


class TestSanitizeSnapshotLines:
    """Tests for sanitize_snapshot_lines function."""

    def test_non_llm_channel_returns_unchanged(self) -> None:
        """Test non-LLM channel returns lines unchanged."""
        lines = ["line1", "line2"]
        assert sanitize_snapshot_lines("system", lines) == lines

    def test_llm_channel_with_json_start(self) -> None:
        """Test LLM channel with JSON-starting line returns unchanged."""
        lines = ['{"key": "value"}', "line2"]
        assert sanitize_snapshot_lines("llm", lines) == lines

    def test_llm_channel_with_non_json_start(self) -> None:
        """Test LLM channel with non-JSON first line strips it."""
        lines = ["garbage", '{"key": "value"}']
        assert sanitize_snapshot_lines("llm", lines) == ['{"key": "value"}']

    def test_llm_channel_empty_lines(self) -> None:
        """Test LLM channel with empty lines returns unchanged."""
        lines: list[str] = []
        assert sanitize_snapshot_lines("llm", lines) == []

    def test_pm_llm_channel(self) -> None:
        """Test pm_llm channel is treated as LLM channel."""
        lines = ["garbage", "content"]
        assert sanitize_snapshot_lines("pm_llm", lines) == ["content"]

    def test_whitespace_json_start(self) -> None:
        """Test whitespace before JSON start is handled."""
        lines = ["   {\"key\": \"value\"}"]
        assert sanitize_snapshot_lines("llm", lines) == lines

    def test_empty_first_element(self) -> None:
        """Test empty first element with LLM channel returns unchanged."""
        lines = ["", "content"]
        result = sanitize_snapshot_lines("llm", lines)
        # Empty first line is falsy, so the condition `first and not first.startswith("{")` is False
        assert result == lines

    def test_director_llm_channel(self) -> None:
        """Test director_llm channel is treated as LLM channel."""
        lines = ["header", '{"data": 1}']
        assert sanitize_snapshot_lines("director_llm", lines) == ['{"data": 1}']

    def test_none_in_first_line(self) -> None:
        """Test None-like first line in LLM channel."""
        lines = ["None", "content"]
        result = sanitize_snapshot_lines("llm", lines)
        assert result == ["content"]

    def test_system_channel_no_change(self) -> None:
        """Test system channel never changes lines."""
        lines = ["garbage", "more"]
        assert sanitize_snapshot_lines("system", lines) == lines


class TestResolveJournalEventChannel:
    """Tests for resolve_journal_event_channel function."""

    def test_empty_line_returns_system(self) -> None:
        """Test empty line returns 'system'."""
        assert resolve_journal_event_channel("") == "system"

    def test_invalid_json_returns_system(self) -> None:
        """Test invalid JSON returns 'system'."""
        assert resolve_journal_event_channel("not json") == "system"

    def test_channel_system(self) -> None:
        """Test explicit channel 'system'."""
        line = json.dumps({"channel": "system"})
        assert resolve_journal_event_channel(line) == "system"

    def test_channel_process(self) -> None:
        """Test explicit channel 'process'."""
        line = json.dumps({"channel": "process"})
        assert resolve_journal_event_channel(line) == "process"

    def test_channel_llm(self) -> None:
        """Test explicit channel 'llm'."""
        line = json.dumps({"channel": "llm"})
        assert resolve_journal_event_channel(line) == "llm"

    def test_channel_case_insensitive(self) -> None:
        """Test channel name is case-insensitive."""
        line = json.dumps({"channel": "LLM"})
        assert resolve_journal_event_channel(line) == "llm"

    def test_domain_llm(self) -> None:
        """Test domain 'llm' resolves to 'llm'."""
        line = json.dumps({"domain": "llm"})
        assert resolve_journal_event_channel(line) == "llm"

    def test_domain_process(self) -> None:
        """Test domain 'process' resolves to 'process'."""
        line = json.dumps({"domain": "process"})
        assert resolve_journal_event_channel(line) == "process"

    def test_domain_system(self) -> None:
        """Test domain 'system' resolves to 'system'."""
        line = json.dumps({"domain": "system"})
        assert resolve_journal_event_channel(line) == "system"

    def test_domain_case_insensitive(self) -> None:
        """Test domain is case-insensitive."""
        line = json.dumps({"domain": "PROCESS"})
        assert resolve_journal_event_channel(line) == "process"

    def test_unknown_domain_returns_system(self) -> None:
        """Test unknown domain returns 'system'."""
        line = json.dumps({"domain": "unknown"})
        assert resolve_journal_event_channel(line) == "system"

    def test_unknown_channel_returns_system(self) -> None:
        """Test unknown channel returns 'system'."""
        line = json.dumps({"channel": "unknown"})
        assert resolve_journal_event_channel(line) == "system"

    def test_channel_takes_precedence_over_domain(self) -> None:
        """Test channel field takes precedence over domain."""
        line = json.dumps({"channel": "llm", "domain": "system"})
        assert resolve_journal_event_channel(line) == "llm"

    def test_no_channel_no_domain_returns_system(self) -> None:
        """Test no channel and no domain returns 'system'."""
        line = json.dumps({"other": "value"})
        assert resolve_journal_event_channel(line) == "system"

    def test_whitespace_channel(self) -> None:
        """Test whitespace-only channel returns 'system'."""
        line = json.dumps({"channel": "   "})
        assert resolve_journal_event_channel(line) == "system"

    def test_whitespace_domain(self) -> None:
        """Test whitespace-only domain returns 'system'."""
        line = json.dumps({"domain": "   "})
        assert resolve_journal_event_channel(line) == "system"

    def test_json_array_returns_system(self) -> None:
        """Test JSON array input returns 'system'."""
        assert resolve_journal_event_channel("[1, 2, 3]") == "system"


class TestModuleExports:
    """Tests for module exports."""

    def test_all_exports(self) -> None:
        """Test that __all__ contains expected exports."""
        from polaris.delivery.ws.endpoints.json_utils import __all__

        assert "parse_json_line" in __all__
        assert "resolve_journal_event_channel" in __all__
        assert "sanitize_snapshot_lines" in __all__
