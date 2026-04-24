"""Tests for polaris.delivery.cli.pm.utils.

Covers pure utility functions with normal, boundary, and edge cases.
"""

from __future__ import annotations

from typing import Any

from polaris.delivery.cli.pm.utils import (
    _normalize_audit_result,
    _slug_token,
    _use_context_engine_v2,
    auto_plan_enabled,
    compact_text,
    format_json_for_prompt,
    is_qa_enabled,
    normalize_path,
    normalize_path_list,
    normalize_str_list,
    read_json_file,
    read_tail_lines,
    requires_manual_intervention_for_error,
    should_pause_for_manual_intervention,
    truncate_text_block,
)


class TestTruncateTextBlock:
    """Tests for truncate_text_block."""

    def test_empty_string(self) -> None:
        assert truncate_text_block("") == ""

    def test_none_input(self) -> None:
        assert truncate_text_block(None) == ""  # type: ignore[arg-type]

    def test_short_text_no_truncation(self) -> None:
        assert truncate_text_block("hello") == "hello"

    def test_long_text_truncation(self) -> None:
        text = "a" * 5000
        result = truncate_text_block(text, max_chars=100)
        assert result.endswith("\n...[truncated]")
        assert len(result) <= 100 + len("\n...[truncated]")

    def test_zero_max_chars(self) -> None:
        text = "hello"
        result = truncate_text_block(text, max_chars=0)
        assert result == text

    def test_negative_max_chars(self) -> None:
        text = "hello"
        result = truncate_text_block(text, max_chars=-1)
        assert result == text

    def test_strips_newlines(self) -> None:
        assert truncate_text_block("\n\nhello\n\n") == "hello"


class TestCompactText:
    """Tests for compact_text."""

    def test_empty_string(self) -> None:
        assert compact_text("") == ""

    def test_none_input(self) -> None:
        assert compact_text(None) == ""  # type: ignore[arg-type]

    def test_whitespace_normalization(self) -> None:
        text = "  hello   world  \n\n  foo  "
        assert compact_text(text) == "hello world foo"

    def test_truncation(self) -> None:
        text = "a" * 500
        result = compact_text(text, max_len=100)
        assert result.endswith("...")
        assert len(result) == 100

    def test_no_truncation_needed(self) -> None:
        assert compact_text("short text") == "short text"

    def test_zero_max_len(self) -> None:
        text = "hello"
        result = compact_text(text, max_len=0)
        assert result == text


class TestSlugToken:
    """Tests for _slug_token."""

    def test_basic_slug(self) -> None:
        assert _slug_token("hello world") == "hello-world"

    def test_backslash_replacement(self) -> None:
        assert _slug_token("path\\to\\file") == "path-to-file"

    def test_forward_slash_replacement(self) -> None:
        assert _slug_token("path/to/file") == "path-to-file"

    def test_removes_special_chars(self) -> None:
        assert _slug_token("hello@world#!") == "helloworld"

    def test_strips_punctuation(self) -> None:
        assert _slug_token("...test...") == "test"
        assert _slug_token("___test___") == "test"

    def test_none_input(self) -> None:
        assert _slug_token(None) == "task"

    def test_empty_string(self) -> None:
        assert _slug_token("") == "task"

    def test_custom_fallback(self) -> None:
        assert _slug_token("", fallback="default") == "default"

    def test_allows_dots_and_underscores(self) -> None:
        assert _slug_token("file_name.txt") == "file_name.txt"


class TestNormalizeAuditResult:
    """Tests for _normalize_audit_result."""

    def test_bool_true(self) -> None:
        assert _normalize_audit_result(True) == "pass"

    def test_bool_false(self) -> None:
        assert _normalize_audit_result(False) == "fail"

    def test_pass_variants(self) -> None:
        for value in ("pass", "passed", "ok", "success"):
            assert _normalize_audit_result(value) == "pass"

    def test_fail_variants(self) -> None:
        for value in ("fail", "failed", "reject", "rejected"):
            assert _normalize_audit_result(value) == "fail"

    def test_empty_returns_empty(self) -> None:
        assert _normalize_audit_result("") == ""
        assert _normalize_audit_result(None) == ""

    def test_unknown_value(self) -> None:
        assert _normalize_audit_result("unknown") == ""


class TestShouldPauseForManualIntervention:
    """Tests for should_pause_for_manual_intervention."""

    def test_director_no_result(self) -> None:
        assert should_pause_for_manual_intervention("DIRECTOR_NO_RESULT") is True

    def test_director_exit_codes(self) -> None:
        assert should_pause_for_manual_intervention("DIRECTOR_EXIT_1") is True
        assert should_pause_for_manual_intervention("DIRECTOR_EXIT_255") is True

    def test_director_entry_missing(self) -> None:
        assert should_pause_for_manual_intervention("DIRECTOR_ENTRY_MISSING") is True

    def test_director_start_failed(self) -> None:
        assert should_pause_for_manual_intervention("DIRECTOR_START_FAILED") is True

    def test_non_intervention_code(self) -> None:
        assert should_pause_for_manual_intervention("SOME_OTHER_ERROR") is False

    def test_empty_code(self) -> None:
        assert should_pause_for_manual_intervention("") is False

    def test_none_code(self) -> None:
        assert should_pause_for_manual_intervention(None) is False  # type: ignore[arg-type]

    def test_case_insensitive(self) -> None:
        assert should_pause_for_manual_intervention("director_no_result") is True
        assert should_pause_for_manual_intervention("Director_Exit_1") is True


class TestRequiresManualInterventionForError:
    """Tests for requires_manual_intervention_for_error."""

    def test_execution_started_true(self) -> None:
        assert requires_manual_intervention_for_error("DIRECTOR_NO_RESULT", False, True) is False

    def test_execution_started_false_with_intervention(self) -> None:
        assert requires_manual_intervention_for_error("DIRECTOR_NO_RESULT", False, False) is True

    def test_execution_started_false_without_intervention(self) -> None:
        assert requires_manual_intervention_for_error("SOME_ERROR", False, False) is False

    def test_director_started_true(self) -> None:
        assert requires_manual_intervention_for_error("DIRECTOR_NO_RESULT", True, None) is False

    def test_director_started_false_with_intervention(self) -> None:
        assert requires_manual_intervention_for_error("DIRECTOR_NO_RESULT", False, None) is True

    def test_none_execution_started_director_started(self) -> None:
        assert requires_manual_intervention_for_error("SOME_ERROR", True, None) is False

    def test_none_execution_started_director_not_started_no_intervention(self) -> None:
        assert requires_manual_intervention_for_error("SOME_ERROR", False, None) is False


class TestUseContextEngineV2:
    """Tests for _use_context_engine_v2."""

    def test_enabled_values(self, monkeypatch: Any) -> None:
        for value in ("v2", "context_v2", "engine_v2", "context-engine-v2"):
            monkeypatch.setenv("KERNELONE_CONTEXT_ENGINE", value)
            assert _use_context_engine_v2() is True

    def test_disabled_values(self, monkeypatch: Any) -> None:
        for value in ("v1", "", "default"):
            monkeypatch.setenv("KERNELONE_CONTEXT_ENGINE", value)
            assert _use_context_engine_v2() is False

    def test_unset(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("KERNELONE_CONTEXT_ENGINE", raising=False)
        assert _use_context_engine_v2() is False


class TestAutoPlanEnabled:
    """Tests for auto_plan_enabled."""

    def test_enabled_by_default(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("KERNELONE_AUTO_PLAN", raising=False)
        assert auto_plan_enabled() is True

    def test_explicitly_enabled(self, monkeypatch: Any) -> None:
        for value in ("1", "true", "yes", "on"):
            monkeypatch.setenv("KERNELONE_AUTO_PLAN", value)
            assert auto_plan_enabled() is True

    def test_explicitly_disabled(self, monkeypatch: Any) -> None:
        for value in ("0", "false", "no", "off"):
            monkeypatch.setenv("KERNELONE_AUTO_PLAN", value)
            assert auto_plan_enabled() is False


class TestIsQaEnabled:
    """Tests for is_qa_enabled."""

    def test_enabled_by_default(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("KERNELONE_QA_ENABLED", raising=False)
        assert is_qa_enabled() is True

    def test_explicitly_disabled(self, monkeypatch: Any) -> None:
        for value in ("0", "false", "no", "off"):
            monkeypatch.setenv("KERNELONE_QA_ENABLED", value)
            assert is_qa_enabled() is False


class TestNormalizeStrList:
    """Tests for normalize_str_list."""

    def test_list_input(self) -> None:
        result = normalize_str_list(["a", "b", "c"])
        assert result == ["a", "b", "c"]

    def test_string_input(self) -> None:
        result = normalize_str_list("hello")
        assert result == ["hello"]

    def test_none_input(self) -> None:
        result = normalize_str_list(None)
        assert result == []


class TestNormalizePathList:
    """Tests for normalize_path_list."""

    def test_list_input(self) -> None:
        result = normalize_path_list(["a/b", "c/d"])
        assert isinstance(result, list)

    def test_none_input(self) -> None:
        result = normalize_path_list(None)
        assert result == []


class TestNormalizePath:
    """Tests for normalize_path."""

    def test_basic_path(self) -> None:
        result = normalize_path("src/main.py")
        assert isinstance(result, str)

    def test_none_input(self) -> None:
        result = normalize_path(None)
        assert result == ""


class TestFormatJsonForPrompt:
    """Tests for format_json_for_prompt."""

    def test_none_input(self) -> None:
        assert format_json_for_prompt(None) == "none"

    def test_dict_input(self) -> None:
        result = format_json_for_prompt({"key": "value"})
        assert "key" in result
        assert "value" in result

    def test_truncation(self) -> None:
        data = {"key": "x" * 3000}
        result = format_json_for_prompt(data, max_chars=100)
        assert result.endswith("...")

    def test_zero_max_chars(self) -> None:
        data = {"key": "value"}
        result = format_json_for_prompt(data, max_chars=0)
        assert "key" in result


class TestReadJsonFile:
    """Tests for read_json_file."""

    def test_nonexistent_file(self, tmp_path: Any) -> None:
        path = str(tmp_path / "nonexistent.json")
        assert read_json_file(path) is None

    def test_empty_path(self) -> None:
        assert read_json_file("") is None

    def test_none_path(self) -> None:
        assert read_json_file(None) is None  # type: ignore[arg-type]

    def test_valid_json_file(self, tmp_path: Any) -> None:
        path = tmp_path / "test.json"
        path.write_text('{"key": "value"}', encoding="utf-8")
        result = read_json_file(str(path))
        assert result == {"key": "value"}

    def test_invalid_json_file(self, tmp_path: Any) -> None:
        path = tmp_path / "bad.json"
        path.write_text("not json", encoding="utf-8")
        assert read_json_file(str(path)) is None


class TestReadTailLines:
    """Tests for read_tail_lines."""

    def test_nonexistent_file(self, tmp_path: Any) -> None:
        path = str(tmp_path / "nonexistent.txt")
        assert read_tail_lines(path) == []

    def test_empty_path(self) -> None:
        assert read_tail_lines("") == []

    def test_read_last_lines(self, tmp_path: Any) -> None:
        path = tmp_path / "test.txt"
        lines = [f"line {i}" for i in range(100)]
        path.write_text("\n".join(lines), encoding="utf-8")
        result = read_tail_lines(str(path), max_lines=10)
        assert len(result) == 10
        assert result[0] == "line 90"
        assert result[-1] == "line 99"

    def test_read_more_than_available(self, tmp_path: Any) -> None:
        path = tmp_path / "test.txt"
        path.write_text("line1\nline2\nline3", encoding="utf-8")
        result = read_tail_lines(str(path), max_lines=10)
        assert len(result) == 3

    def test_zero_max_lines(self, tmp_path: Any) -> None:
        path = tmp_path / "test.txt"
        path.write_text("line1\nline2", encoding="utf-8")
        result = read_tail_lines(str(path), max_lines=0)
        assert result == []
