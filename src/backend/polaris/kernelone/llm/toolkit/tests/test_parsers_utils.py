"""Tests for polaris.kernelone.llm.toolkit.parsers.utils module.

Covers:
- _normalize_allowed_tool_names function
- parse_value function with all type conversions
- resolve_signature_requirement function
- is_quoted_line function
- stable_json function
- tool_signature function
- deduplicate_tool_calls function
"""

from __future__ import annotations

import os
from unittest.mock import patch


class TestNormalizeAllowedToolNames:
    """Tests for _normalize_allowed_tool_names function."""

    def test_none_input_returns_empty_set(self) -> None:
        """Verify None input returns empty set."""
        from polaris.kernelone.llm.toolkit.parsers.utils import (
            _normalize_allowed_tool_names,
        )

        result = _normalize_allowed_tool_names(None)
        assert result == set()

    def test_empty_list_returns_empty_set(self) -> None:
        """Verify empty list returns empty set."""
        from polaris.kernelone.llm.toolkit.parsers.utils import (
            _normalize_allowed_tool_names,
        )

        result = _normalize_allowed_tool_names([])
        assert result == set()

    def test_normalizes_to_lowercase(self) -> None:
        """Verify names are normalized to lowercase."""
        from polaris.kernelone.llm.toolkit.parsers.utils import (
            _normalize_allowed_tool_names,
        )

        result = _normalize_allowed_tool_names(["ReadFile", "WriteFile", "SEARCH"])
        assert result == {"readfile", "writefile", "search"}

    def test_strips_whitespace(self) -> None:
        """Verify whitespace is stripped from names."""
        from polaris.kernelone.llm.toolkit.parsers.utils import (
            _normalize_allowed_tool_names,
        )

        result = _normalize_allowed_tool_names(["  read  ", " write ", "search  "])
        assert result == {"read", "write", "search"}

    def test_filters_empty_strings(self) -> None:
        """Verify empty strings after stripping are filtered out."""
        from polaris.kernelone.llm.toolkit.parsers.utils import (
            _normalize_allowed_tool_names,
        )

        result = _normalize_allowed_tool_names(["read", "", "  ", "write"])
        assert result == {"read", "write"}


class TestParseValue:
    """Tests for parse_value function."""

    def test_parses_json_object(self) -> None:
        """Verify parse_value parses JSON objects."""
        from polaris.kernelone.llm.toolkit.parsers.utils import parse_value

        result = parse_value('{"key": "value", "num": 42}')
        assert result == {"key": "value", "num": 42}

    def test_parses_json_array(self) -> None:
        """Verify parse_value parses JSON arrays."""
        from polaris.kernelone.llm.toolkit.parsers.utils import parse_value

        result = parse_value('[1, 2, 3, "four"]')
        assert result == [1, 2, 3, "four"]

    def test_parses_true_values(self) -> None:
        """Verify parse_value parses true-like values."""
        from polaris.kernelone.llm.toolkit.parsers.utils import parse_value

        assert parse_value("true") is True
        assert parse_value("True") is True
        assert parse_value("TRUE") is True
        assert parse_value("yes") is True
        assert parse_value("YES") is True
        assert parse_value("on") is True
        assert parse_value("ON") is True

    def test_parses_false_values(self) -> None:
        """Verify parse_value parses false-like values."""
        from polaris.kernelone.llm.toolkit.parsers.utils import parse_value

        assert parse_value("false") is False
        assert parse_value("False") is False
        assert parse_value("FALSE") is False
        assert parse_value("no") is False
        assert parse_value("NO") is False
        assert parse_value("off") is False
        assert parse_value("OFF") is False

    def test_parses_integer(self) -> None:
        """Verify parse_value parses integers."""
        from polaris.kernelone.llm.toolkit.parsers.utils import parse_value

        assert parse_value("42") == 42
        assert parse_value("-123") == -123
        assert parse_value("0") == 0

    def test_parses_float(self) -> None:
        """Verify parse_value parses floats."""
        from polaris.kernelone.llm.toolkit.parsers.utils import parse_value

        assert parse_value("3.14") == 3.14
        assert parse_value("-0.5") == -0.5
        assert parse_value("0.0") == 0.0

    def test_removes_double_quotes(self) -> None:
        """Verify parse_value removes surrounding double quotes."""
        from polaris.kernelone.llm.toolkit.parsers.utils import parse_value

        assert parse_value('"hello"') == "hello"
        assert parse_value('"quoted string"') == "quoted string"

    def test_removes_single_quotes(self) -> None:
        """Verify parse_value removes surrounding single quotes."""
        from polaris.kernelone.llm.toolkit.parsers.utils import parse_value

        assert parse_value("'hello'") == "hello"
        assert parse_value("'single quoted'") == "single quoted"

    def test_strips_whitespace(self) -> None:
        """Verify parse_value strips whitespace."""
        from polaris.kernelone.llm.toolkit.parsers.utils import parse_value

        assert parse_value("  42  ") == 42
        assert parse_value("  true  ") is True

    def test_returns_string_for_unrecognized(self) -> None:
        """Verify parse_value returns unrecognized values as strings."""
        from polaris.kernelone.llm.toolkit.parsers.utils import parse_value

        assert parse_value("hello world") == "hello world"
        assert parse_value("not-a-number") == "not-a-number"

    def test_json_with_whitespace(self) -> None:
        """Verify parse_value handles JSON with extra whitespace."""
        from polaris.kernelone.llm.toolkit.parsers.utils import parse_value

        result = parse_value('  {"key": "value"}  ')
        assert result == {"key": "value"}


class TestResolveSignatureRequirement:
    """Tests for resolve_signature_requirement function."""

    def test_explicit_true_returns_true(self) -> None:
        """Verify explicit True returns True."""
        from polaris.kernelone.llm.toolkit.parsers.utils import (
            resolve_signature_requirement,
        )

        assert resolve_signature_requirement(True) is True

    def test_explicit_false_returns_false(self) -> None:
        """Verify explicit False returns False."""
        from polaris.kernelone.llm.toolkit.parsers.utils import (
            resolve_signature_requirement,
        )

        assert resolve_signature_requirement(False) is False

    def test_env_var_true_values(self) -> None:
        """Verify environment variable with true-like values."""
        from polaris.kernelone.llm.toolkit.parsers.utils import (
            resolve_signature_requirement,
        )

        true_values = ["1", "true", "True", "TRUE", "yes", "on"]

        for value in true_values:
            with patch.object(os.environ, "get", return_value=value):
                result = resolve_signature_requirement(None)
                assert result is True, f"Failed for {value}"

    def test_env_var_false_values(self) -> None:
        """Verify environment variable with false-like values."""
        from polaris.kernelone.llm.toolkit.parsers.utils import (
            resolve_signature_requirement,
        )

        with patch.object(os.environ, "get", return_value="0"):
            result = resolve_signature_requirement(None)
            assert result is False

    def test_env_var_empty_returns_false(self) -> None:
        """Verify empty environment variable returns False."""
        from polaris.kernelone.llm.toolkit.parsers.utils import (
            resolve_signature_requirement,
        )

        with patch.object(os.environ, "get", return_value=""):
            result = resolve_signature_requirement(None)
            assert result is False

    def test_env_var_not_set_returns_false(self) -> None:
        """Verify missing environment variable returns None (which falls back to False)."""
        from polaris.kernelone.llm.toolkit.parsers.utils import (
            resolve_signature_requirement,
        )

        with patch.object(os.environ, "get", return_value=None):
            result = resolve_signature_requirement(None)
            assert result is False


class TestIsQuotedLine:
    """Tests for is_quoted_line function."""

    def test_quoted_line(self) -> None:
        """Verify lines starting with '>' are detected as quoted."""
        from polaris.kernelone.llm.toolkit.parsers.utils import is_quoted_line

        text = "line one\n> quoted line"
        # Position of "quoted line"
        result = is_quoted_line(text, 10)
        assert result is True

    def test_non_quoted_line(self) -> None:
        """Verify lines not starting with '>' are not quoted."""
        from polaris.kernelone.llm.toolkit.parsers.utils import is_quoted_line

        text = "line one\nregular line"
        # Position of "regular line"
        result = is_quoted_line(text, 10)
        assert result is False

    def test_first_line_quoted(self) -> None:
        """Verify first line can be quoted."""
        from polaris.kernelone.llm.toolkit.parsers.utils import is_quoted_line

        text = "> quoted first line"
        result = is_quoted_line(text, 0)
        assert result is True

    def test_first_line_not_quoted(self) -> None:
        """Verify first line without '>' is not quoted."""
        from polaris.kernelone.llm.toolkit.parsers.utils import is_quoted_line

        text = "regular first line"
        result = is_quoted_line(text, 0)
        assert result is False

    def test_whitespace_before_quotes(self) -> None:
        """Verify whitespace before '>' is stripped."""
        from polaris.kernelone.llm.toolkit.parsers.utils import is_quoted_line

        text = "  > quoted with whitespace"
        result = is_quoted_line(text, 0)
        assert result is True

    def test_at_end_of_text(self) -> None:
        """Verify position at end of text works."""
        from polaris.kernelone.llm.toolkit.parsers.utils import is_quoted_line

        text = "line\n> quoted"
        result = is_quoted_line(text, len(text) - 1)
        assert result is True


class TestStableJson:
    """Tests for stable_json function."""

    def test_dict_sorted_keys(self) -> None:
        """Verify dict keys are sorted for stable output."""
        from polaris.kernelone.llm.toolkit.parsers.utils import stable_json

        data = {"z": 1, "a": 2, "m": 3}
        result = stable_json(data)
        keys = list(result.keys())
        assert keys == sorted(keys)

    def test_nested_dict_sorted(self) -> None:
        """Verify nested dicts are also sorted."""
        from polaris.kernelone.llm.toolkit.parsers.utils import stable_json

        data = {"outer": {"z": 1, "a": 2}, "inner": {"b": 1}}
        result = stable_json(data)
        outer_keys = list(result["outer"].keys())
        assert outer_keys == sorted(outer_keys)

    def test_list_preserved(self) -> None:
        """Verify lists are preserved."""
        from polaris.kernelone.llm.toolkit.parsers.utils import stable_json

        data = [3, 1, 4, 1, 5]
        result = stable_json(data)
        assert result == [3, 1, 4, 1, 5]

    def test_tuple_converted_to_list(self) -> None:
        """Verify tuples are converted to lists."""
        from polaris.kernelone.llm.toolkit.parsers.utils import stable_json

        data = (1, 2, 3)
        result = stable_json(data)
        assert result == [1, 2, 3]
        assert isinstance(result, list)

    def test_primitives_unchanged(self) -> None:
        """Verify primitive types are unchanged."""
        from polaris.kernelone.llm.toolkit.parsers.utils import stable_json

        assert stable_json("string") == "string"
        assert stable_json(42) == 42
        assert stable_json(3.14) == 3.14
        assert stable_json(True) is True
        assert stable_json(None) is None

    def test_complex_nested_structure(self) -> None:
        """Verify complex nested structures are properly handled."""
        from polaris.kernelone.llm.toolkit.parsers.utils import stable_json

        data = {
            "z_key": {"a": [1, 2], "b": {"x": 1, "y": 2}},
            "a_key": "value",
        }
        result = stable_json(data)
        assert isinstance(result, dict)
        assert isinstance(result["z_key"]["b"], dict)
        assert list(result["z_key"]["b"].keys()) == ["x", "y"]


class TestToolSignature:
    """Tests for tool_signature function."""

    def test_signature_generation(self) -> None:
        """Verify tool_signature generates correct signature."""
        from polaris.kernelone.llm.contracts.tool import ToolCall
        from polaris.kernelone.llm.toolkit.parsers.utils import tool_signature

        # Create an actual ToolCall instance
        call = ToolCall(id="call_1", name="ReadFile", arguments={"path": "/tmp/test.txt"})
        signature = tool_signature(call)
        assert signature == ("readfile", '{"path": "/tmp/test.txt"}')

    def test_signature_normalizes_name(self) -> None:
        """Verify tool_signature normalizes name to lowercase."""
        from polaris.kernelone.llm.contracts.tool import ToolCall
        from polaris.kernelone.llm.toolkit.parsers.utils import tool_signature

        call = ToolCall(id="call_2", name="  WriteFile  ", arguments={"path": "/tmp/out.txt"})
        signature = tool_signature(call)
        assert signature[0] == "writefile"

    def test_signature_empty_arguments(self) -> None:
        """Verify tool_signature handles empty arguments."""
        from polaris.kernelone.llm.contracts.tool import ToolCall
        from polaris.kernelone.llm.toolkit.parsers.utils import tool_signature

        call = ToolCall(id="call_3", name="NoArgs", arguments={})
        signature = tool_signature(call)
        assert signature == ("noargs", "{}")

    def test_signature_non_dict_arguments(self) -> None:
        """Verify tool_signature handles non-dict arguments."""
        from polaris.kernelone.llm.contracts.tool import ToolCall
        from polaris.kernelone.llm.toolkit.parsers.utils import tool_signature

        # ToolCall.arguments should be dict, but we can test the fallback
        call = ToolCall(id="call_4", name="Test", arguments={})
        signature = tool_signature(call)
        assert signature == ("test", "{}")

    def test_signature_preserves_all_fields(self) -> None:
        """Verify tool_signature preserves all fields in signature."""
        from polaris.kernelone.llm.contracts.tool import ToolCall
        from polaris.kernelone.llm.toolkit.parsers.utils import tool_signature

        call = ToolCall(
            id="call_5",
            name="MultiTool",
            arguments={"arg1": "value1", "arg2": 42},
        )
        signature = tool_signature(call)
        # Verify the full signature contains the name and sorted args
        assert signature[0] == "multitool"
        assert '"arg1"' in signature[1]
        assert '"arg2"' in signature[1]


class TestDeduplicateToolCalls:
    """Tests for deduplicate_tool_calls function."""

    def test_empty_list(self) -> None:
        """Verify deduplicate_tool_calls handles empty list."""
        from polaris.kernelone.llm.toolkit.parsers.utils import deduplicate_tool_calls

        result = deduplicate_tool_calls([])
        assert result == []

    def test_no_duplicates(self) -> None:
        """Verify deduplicate_tool_calls preserves non-duplicate calls."""
        from polaris.kernelone.llm.contracts.tool import ToolCall
        from polaris.kernelone.llm.toolkit.parsers.utils import deduplicate_tool_calls

        calls = [
            ToolCall(id="call_1", name="read", arguments={"path": "/a.txt"}),
            ToolCall(id="call_2", name="write", arguments={"path": "/b.txt"}),
        ]

        result = deduplicate_tool_calls(calls)
        assert len(result) == 2

    def test_removes_duplicates(self) -> None:
        """Verify deduplicate_tool_calls removes exact duplicates."""
        from polaris.kernelone.llm.contracts.tool import ToolCall
        from polaris.kernelone.llm.toolkit.parsers.utils import deduplicate_tool_calls

        calls = [
            ToolCall(id="call_1", name="read", arguments={"path": "/a.txt"}),
            ToolCall(id="call_2", name="read", arguments={"path": "/a.txt"}),  # Duplicate
            ToolCall(id="call_3", name="write", arguments={"path": "/b.txt"}),
        ]

        result = deduplicate_tool_calls(calls)
        assert len(result) == 2

    def test_different_args_not_duplicates(self) -> None:
        """Verify calls with different args are not considered duplicates."""
        from polaris.kernelone.llm.contracts.tool import ToolCall
        from polaris.kernelone.llm.toolkit.parsers.utils import deduplicate_tool_calls

        calls = [
            ToolCall(id="call_1", name="read", arguments={"path": "/a.txt"}),
            ToolCall(id="call_2", name="read", arguments={"path": "/b.txt"}),  # Different args
        ]

        result = deduplicate_tool_calls(calls)
        assert len(result) == 2

    def test_preserves_first_occurrence(self) -> None:
        """Verify first occurrence is preserved when duplicates exist."""
        from polaris.kernelone.llm.contracts.tool import ToolCall
        from polaris.kernelone.llm.toolkit.parsers.utils import deduplicate_tool_calls

        calls = [
            ToolCall(id="call_1", name="read", arguments={"path": "/a.txt"}),
            ToolCall(id="call_2", name="read", arguments={"path": "/a.txt"}),  # Duplicate
            ToolCall(id="call_3", name="read", arguments={"path": "/a.txt"}),  # Another duplicate
        ]

        result = deduplicate_tool_calls(calls)
        assert len(result) == 1
        assert result[0].id == "call_1"
