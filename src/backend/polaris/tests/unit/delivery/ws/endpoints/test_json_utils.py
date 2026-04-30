"""Tests for polaris.delivery.ws.endpoints.json_utils."""

from __future__ import annotations

from polaris.delivery.ws.endpoints.json_utils import (
    parse_json_line,
    resolve_journal_event_channel,
    sanitize_snapshot_lines,
)


class TestParseJsonLine:
    def test_valid_json(self) -> None:
        result = parse_json_line('{"key": "value"}')
        assert result == {"key": "value"}

    def test_invalid_json(self) -> None:
        assert parse_json_line("not json") is None

    def test_empty_string(self) -> None:
        assert parse_json_line("") is None

    def test_whitespace_only(self) -> None:
        assert parse_json_line("   ") is None

    def test_json_list(self) -> None:
        assert parse_json_line("[1, 2, 3]") is None

    def test_none_input(self) -> None:
        assert parse_json_line(None) is None


class TestSanitizeSnapshotLines:
    def test_non_llm_channel(self) -> None:
        lines = ["line1", "line2"]
        result = sanitize_snapshot_lines("system", lines)
        assert result == lines

    def test_llm_channel_no_brace(self) -> None:
        lines = ["header", '{"key": "value"}']
        result = sanitize_snapshot_lines("llm", lines)
        assert result == ['{"key": "value"}']

    def test_llm_channel_starts_with_brace(self) -> None:
        lines = ['{"key": "value"}', "line2"]
        result = sanitize_snapshot_lines("llm", lines)
        assert result == lines

    def test_empty_lines(self) -> None:
        result = sanitize_snapshot_lines("llm", [])
        assert result == []

    def test_llm_channel_with_whitespace(self) -> None:
        lines = ["  header", '{"key": "value"}']
        result = sanitize_snapshot_lines("llm", lines)
        assert result == ['{"key": "value"}']


class TestResolveJournalEventChannel:
    def test_valid_channel_field(self) -> None:
        result = resolve_journal_event_channel('{"channel": "llm"}')
        assert result == "llm"

    def test_valid_domain_llm(self) -> None:
        result = resolve_journal_event_channel('{"domain": "llm"}')
        assert result == "llm"

    def test_valid_domain_process(self) -> None:
        result = resolve_journal_event_channel('{"domain": "process"}')
        assert result == "process"

    def test_valid_domain_system(self) -> None:
        result = resolve_journal_event_channel('{"domain": "system"}')
        assert result == "system"

    def test_invalid_json(self) -> None:
        result = resolve_journal_event_channel("not json")
        assert result == "system"

    def test_empty_json(self) -> None:
        result = resolve_journal_event_channel("{}")
        assert result == "system"

    def test_unknown_domain(self) -> None:
        result = resolve_journal_event_channel('{"domain": "unknown"}')
        assert result == "system"

    def test_channel_takes_precedence(self) -> None:
        result = resolve_journal_event_channel('{"channel": "process", "domain": "llm"}')
        assert result == "process"
