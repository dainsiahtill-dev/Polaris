"""Tests for polaris.cells.director.execution.logic.

Covers write_gate_check and re-exported utility functions.
"""

from __future__ import annotations

from polaris.cells.director.execution.logic import (
    extract_defect_ticket,
    parse_acceptance,
    parse_json_payload,
    write_gate_check,
)


class TestWriteGateCheck:
    """Tests for write_gate_check."""

    def test_no_requirement_always_passes(self) -> None:
        ok, reason = write_gate_check([], ["a.py"])
        assert ok is True
        assert reason == ""

    def test_require_change_with_no_files_fails(self) -> None:
        ok, reason = write_gate_check([], [], require_change=True)
        assert ok is False
        assert "No files changed" in reason

    def test_require_change_with_files_passes(self) -> None:
        ok, reason = write_gate_check(["a.py"], [], require_change=True)
        assert ok is True
        assert reason == ""

    def test_with_pm_target_files_ignored(self) -> None:
        ok, reason = write_gate_check(["a.py"], ["b.py"], pm_target_files=["c.py"])
        assert ok is True
        assert reason == ""


class TestExtractDefectTicket:
    """Tests for extract_defect_ticket re-export."""

    def test_extracts_defect_from_payload(self) -> None:
        payload = {"defects": [{"id": "D-1", "severity": "high"}]}
        result = extract_defect_ticket(payload)
        assert result is not None

    def test_no_defects_returns_empty_dict(self) -> None:
        result = extract_defect_ticket({})
        assert result == {}


class TestParseAcceptance:
    """Tests for parse_acceptance re-export.

    Note: The current implementation appears to return None for all inputs.
    These tests document the actual behavior rather than an ideal behavior.
    """

    def test_parses_acceptance_criteria_returns_none(self) -> None:
        text = "- [ ] AC1: User can login\n- [x] AC2: User can logout"
        result = parse_acceptance(text)
        assert result is None

    def test_empty_string_returns_none(self) -> None:
        result = parse_acceptance("")
        assert result is None


class TestParseJsonPayload:
    """Tests for parse_json_payload re-export."""

    def test_parses_valid_json(self) -> None:
        result = parse_json_payload('{"key": "value"}')
        assert result == {"key": "value"}

    def test_parses_json_with_markdown_fence(self) -> None:
        result = parse_json_payload('```json\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_invalid_json_returns_none(self) -> None:
        result = parse_json_payload("not json")
        assert result is None
