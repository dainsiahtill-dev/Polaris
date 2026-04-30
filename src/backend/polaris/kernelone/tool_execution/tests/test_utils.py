"""Tests for tool_execution/utils module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from polaris.kernelone.tool_execution.utils import (
    append_log,
    as_list,
    safe_int,
    sanitize_tool_name,
    split_list_value,
    split_tool_step,
)


class TestSafeInt:
    """Tests for safe_int function."""

    def test_valid_int(self) -> None:
        """Convert valid integer."""
        assert safe_int(42) == 42

    def test_valid_int_string(self) -> None:
        """Convert valid integer string."""
        assert safe_int("42") == 42

    def test_valid_negative_int(self) -> None:
        """Convert negative integer."""
        assert safe_int(-10) == -10

    def test_invalid_string_returns_default(self) -> None:
        """Invalid string returns default."""
        assert safe_int("abc") == -1

    def test_none_returns_default(self) -> None:
        """None returns default."""
        assert safe_int(None) == -1

    def test_custom_default(self) -> None:
        """Custom default is used."""
        assert safe_int("abc", default=0) == 0

    def test_float_returns_default(self) -> None:
        """Float returns default (implementation does not accept float)."""
        assert safe_int(3.14) == -1

    def test_empty_string_returns_default(self) -> None:
        """Empty string returns default."""
        assert safe_int("") == -1

    def test_list_returns_default(self) -> None:
        """List returns default."""
        assert safe_int([1, 2]) == -1

    def test_dict_returns_default(self) -> None:
        """Dict returns default."""
        assert safe_int({"a": 1}) == -1


class TestAppendLog:
    """Tests for append_log function."""

    @patch("polaris.kernelone.tool_execution.utils._append_log_impl")
    def test_delegates_to_impl(self, mock_impl: MagicMock) -> None:
        """Function delegates to internal implementation."""
        append_log("/tmp/test.log", "hello")
        mock_impl.assert_called_once_with("/tmp/test.log", "hello")

    @patch("polaris.kernelone.tool_execution.utils._append_log_impl")
    def test_utf8_content(self, mock_impl: MagicMock) -> None:
        """UTF-8 content is passed through."""
        append_log("/tmp/test.log", "你好世界")
        mock_impl.assert_called_once_with("/tmp/test.log", "你好世界")

    @patch("polaris.kernelone.tool_execution.utils._append_log_impl")
    def test_empty_text(self, mock_impl: MagicMock) -> None:
        """Empty text is passed through."""
        append_log("/tmp/test.log", "")
        mock_impl.assert_called_once_with("/tmp/test.log", "")


class TestSanitizeToolName:
    """Tests for sanitize_tool_name function."""

    def test_valid_name_unchanged(self) -> None:
        """Valid name is unchanged."""
        assert sanitize_tool_name("read_file") == "read_file"

    def test_spaces_replaced(self) -> None:
        """Spaces are replaced with underscores."""
        assert sanitize_tool_name("read file") == "read_file"

    def test_special_chars_replaced(self) -> None:
        """Special characters are replaced."""
        assert sanitize_tool_name("read@file!") == "read_file_"

    def test_empty_string(self) -> None:
        """Empty string returns 'tool'."""
        assert sanitize_tool_name("") == "tool"

    def test_none_returns_tool(self) -> None:
        """None returns 'tool'."""
        assert sanitize_tool_name(None) == "tool"  # type: ignore[arg-type]

    def test_dots_preserved(self) -> None:
        """Dots are preserved."""
        assert sanitize_tool_name("file.txt") == "file.txt"

    def test_hyphens_preserved(self) -> None:
        """Hyphens are preserved."""
        assert sanitize_tool_name("my-tool") == "my-tool"

    def test_whitespace_trimmed(self) -> None:
        """Leading/trailing whitespace is trimmed."""
        assert sanitize_tool_name("  hello  ") == "hello"

    def test_unicode_replaced(self) -> None:
        """Unicode characters are replaced."""
        assert sanitize_tool_name("工具") == "_"


class TestAsList:
    """Tests for as_list function."""

    def test_none_returns_empty(self) -> None:
        """None returns empty list."""
        assert as_list(None) == []

    def test_list_returns_unchanged(self) -> None:
        """List returns unchanged."""
        assert as_list([1, 2, 3]) == [1, 2, 3]

    def test_tuple_converted(self) -> None:
        """Tuple is converted to list."""
        assert as_list((1, 2, 3)) == [1, 2, 3]

    def test_string_wrapped(self) -> None:
        """String is wrapped in list."""
        assert as_list("hello") == ["hello"]

    def test_int_returns_empty(self) -> None:
        """Int returns empty list."""
        assert as_list(42) == []

    def test_dict_returns_empty(self) -> None:
        """Dict returns empty list."""
        assert as_list({"a": 1}) == []

    def test_empty_list(self) -> None:
        """Empty list returns empty list."""
        assert as_list([]) == []

    def test_empty_tuple(self) -> None:
        """Empty tuple returns empty list."""
        assert as_list(()) == []

    def test_empty_string(self) -> None:
        """Empty string returns list with empty string."""
        assert as_list("") == [""]


class TestSplitToolStep:
    """Tests for split_tool_step function."""

    def test_empty_string(self) -> None:
        """Empty string returns empty list."""
        assert split_tool_step("") == []

    def test_simple_tokens(self) -> None:
        """Simple space-separated tokens."""
        assert split_tool_step("a b c") == ["a", "b", "c"]

    def test_quoted_tokens(self) -> None:
        """Quoted tokens are preserved."""
        assert split_tool_step('arg1 "hello world"') == ["arg1", "hello world"]

    def test_shlex_failure_fallback(self) -> None:
        """When shlex fails, falls back to str.split."""
        assert split_tool_step("a b c") == ["a", "b", "c"]

    def test_single_token(self) -> None:
        """Single token returns single-element list."""
        assert split_tool_step("hello") == ["hello"]

    def test_none_input(self) -> None:
        """None input returns empty list."""
        assert split_tool_step(None) == []  # type: ignore[arg-type]


class TestSplitListValue:
    """Tests for split_list_value function."""

    def test_empty_string(self) -> None:
        """Empty string returns empty list."""
        assert split_list_value("") == []

    def test_simple_comma_list(self) -> None:
        """Simple comma-separated values."""
        assert split_list_value("a,b,c") == ["a", "b", "c"]

    def test_bracket_list(self) -> None:
        """List with brackets."""
        assert split_list_value("[a, b, c]") == ["a", "b", "c"]

    def test_quoted_items(self) -> None:
        """Quoted items are stripped of quotes."""
        assert split_list_value("'a', \"b\", c") == ["a", "b", "c"]

    def test_whitespace_trimmed(self) -> None:
        """Whitespace around items is trimmed."""
        assert split_list_value("  a  ,  b  ") == ["a", "b"]

    def test_empty_items_skipped(self) -> None:
        """Empty items are skipped."""
        assert split_list_value("a,,b") == ["a", "b"]

    def test_single_item(self) -> None:
        """Single item returns single-element list."""
        assert split_list_value("hello") == ["hello"]

    def test_none_input(self) -> None:
        """None input returns empty list."""
        assert split_list_value(None) == []  # type: ignore[arg-type]

    def test_unicode_items(self) -> None:
        """Unicode items are preserved."""
        assert split_list_value("你好, 世界") == ["你好", "世界"]


class TestModuleExports:
    """Tests for module public API."""

    def test_all_functions_importable(self) -> None:
        """All expected functions are importable."""
        from polaris.kernelone.tool_execution import utils

        assert hasattr(utils, "safe_int")
        assert hasattr(utils, "append_log")
        assert hasattr(utils, "sanitize_tool_name")
        assert hasattr(utils, "as_list")
        assert hasattr(utils, "split_tool_step")
        assert hasattr(utils, "split_list_value")
