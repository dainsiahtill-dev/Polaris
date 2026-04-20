"""Unit tests for JSONToolParser.

Tests cover:
- Normal scenarios: valid JSON tool calls
- Boundary scenarios: nested JSON, mixed content
- Exception scenarios: invalid JSON, missing fields
- Regression: no breaking changes to existing behavior
"""

from __future__ import annotations

import json

from polaris.kernelone.llm.toolkit.parsers.json_based import (
    JSONToolParser,
    is_json_tool_call,
    parse_json_tool_calls,
)
from polaris.kernelone.llm.toolkit.parsers.utils import ParsedToolCall


class TestJSONToolParserNormal:
    """Normal scenario tests: valid JSON tool calls."""

    def test_parse_simple_json_call_with_arguments(self) -> None:
        """Normal: Simple JSON tool call with arguments."""
        text = '{"name": "read_file", "arguments": {"path": "test.py"}}'
        result = JSONToolParser.parse(text)

        assert len(result) == 1
        assert result[0].name == "read_file"
        assert result[0].arguments == {"path": "test.py"}

    def test_parse_simple_json_call_with_args(self) -> None:
        """Normal: JSON tool call using 'args' instead of 'arguments'."""
        text = '{"name": "read_file", "args": {"file": "test.py"}}'
        result = JSONToolParser.parse(text)

        assert len(result) == 1
        assert result[0].name == "read_file"
        assert result[0].arguments == {"file": "test.py"}

    def test_parse_with_tool_key(self) -> None:
        """Normal: JSON tool call using 'tool' instead of 'name'."""
        text = '{"tool": "write_file", "arguments": {"path": "out.txt", "content": "hello"}}'
        result = JSONToolParser.parse(text)

        assert len(result) == 1
        assert result[0].name == "write_file"
        assert result[0].arguments == {"path": "out.txt", "content": "hello"}

    def test_parse_with_params_key(self) -> None:
        """Normal: JSON tool call using 'params' for arguments."""
        text = '{"name": "execute", "params": {"cmd": "ls"}}'
        result = JSONToolParser.parse(text)

        assert len(result) == 1
        assert result[0].name == "execute"
        assert result[0].arguments == {"cmd": "ls"}

    def test_parse_multiple_calls(self) -> None:
        """Normal: Multiple JSON tool calls in text."""
        text = '{"name": "read", "args": {}} {"name": "write", "args": {}}'
        result = JSONToolParser.parse(text)

        assert len(result) == 2
        assert result[0].name == "read"
        assert result[1].name == "write"

    def test_parse_empty_arguments(self) -> None:
        """Normal: Tool call with empty arguments object."""
        text = '{"name": "ping", "arguments": {}}'
        result = JSONToolParser.parse(text)

        assert len(result) == 1
        assert result[0].name == "ping"
        assert result[0].arguments == {}


class TestJSONToolParserBoundary:
    """Boundary scenario tests: edge cases and special formats."""

    def test_parse_nested_arguments(self) -> None:
        """Boundary: Nested arguments in JSON."""
        text = '{"name": "search", "arguments": {"query": {"term": "test", "filters": {"type": "py"}}}}'
        result = JSONToolParser.parse(text)

        assert len(result) == 1
        assert result[0].name == "search"
        assert result[0].arguments == {"query": {"term": "test", "filters": {"type": "py"}}}

    def test_parse_json_with_extra_fields(self) -> None:
        """Boundary: JSON with extra fields besides name and arguments."""
        text = '{"name": "read", "arguments": {"path": "f.txt"}, "extra": "ignored"}'
        result = JSONToolParser.parse(text)

        assert len(result) == 1
        assert result[0].name == "read"
        assert result[0].arguments == {"path": "f.txt"}

    def test_parse_json_with_whitespace(self) -> None:
        """Boundary: JSON with extra whitespace."""
        text = '  \n  {"name": "read", "arguments": {"path": "f.txt"}}  \n  '
        result = JSONToolParser.parse(text)

        assert len(result) == 1
        assert result[0].name == "read"

    def test_parse_json_in_text_context(self) -> None:
        """Boundary: JSON embedded in regular text."""
        text = 'Let me read the file: {"name": "read", "arguments": {"path": "f.txt"}}'
        result = JSONToolParser.parse(text)

        assert len(result) == 1
        assert result[0].name == "read"

    def test_parse_preserves_argument_types(self) -> None:
        """Boundary: Various argument value types."""
        text = json.dumps(
            {
                "name": "complex",
                "arguments": {
                    "string": "hello",
                    "number": 42,
                    "float": 3.14,
                    "bool_true": True,
                    "bool_false": False,
                    "null": None,
                    "array": [1, 2, 3],
                },
            }
        )
        result = JSONToolParser.parse(text)

        assert len(result) == 1
        args = result[0].arguments
        assert args["string"] == "hello"
        assert args["number"] == 42
        assert args["float"] == 3.14
        assert args["bool_true"] is True
        assert args["bool_false"] is False
        assert args["null"] is None
        assert args["array"] == [1, 2, 3]

    def test_parse_with_double_quoted_args_string(self) -> None:
        """Boundary: Arguments as JSON string within string field."""
        text = '{"name": "read", "arguments": "{\\"path\\": \\"f.txt\\"}"}'
        result = JSONToolParser.parse(text)

        # The arguments field is a string, not a dict
        # Current implementation returns empty arguments in this case
        assert len(result) == 1
        assert result[0].name == "read"

    def test_parse_with_numbers_in_args(self) -> None:
        """Boundary: Arguments containing numbers."""
        text = '{"name": "slice", "arguments": {"offset": 100, "limit": 50}}'
        result = JSONToolParser.parse(text)

        assert len(result) == 1
        assert result[0].arguments == {"offset": 100, "limit": 50}


class TestJSONToolParserException:
    """Exception scenario tests: invalid inputs and error handling."""

    def test_parse_invalid_json_returns_empty(self) -> None:
        """Exception: Invalid JSON returns empty list."""
        text = '{"name": "read", invalid}'
        result = JSONToolParser.parse(text)

        assert result == []

    def test_parse_missing_name_returns_empty(self) -> None:
        """Exception: JSON without name field returns empty."""
        text = '{"arguments": {"path": "f.txt"}}'
        result = JSONToolParser.parse(text)

        assert result == []

    def test_parse_missing_arguments_is_valid(self) -> None:
        """Exception: JSON without arguments field returns call with empty args."""
        text = '{"name": "ping"}'
        result = JSONToolParser.parse(text)

        assert len(result) == 1
        assert result[0].name == "ping"
        assert result[0].arguments == {}

    def test_parse_empty_string_returns_empty(self) -> None:
        """Exception: Empty string returns empty list."""
        assert JSONToolParser.parse("") == []
        assert JSONToolParser.parse("   ") == []
        assert JSONToolParser.parse(None) == []  # type: ignore

    def test_parse_non_string_returns_empty(self) -> None:
        """Exception: Non-string input returns empty list."""
        assert JSONToolParser.parse(123) == []  # type: ignore
        assert JSONToolParser.parse(["list"]) == []  # type: ignore
        assert JSONToolParser.parse({"dict": "value"}) == []  # type: ignore

    def test_parse_invalid_name_type_returns_empty(self) -> None:
        """Exception: Name field is not a string."""
        text = '{"name": 123, "arguments": {}}'
        result = JSONToolParser.parse(text)

        assert result == []

    def test_parse_only_open_brace(self) -> None:
        """Exception: Incomplete JSON object."""
        text = '{"name": "read"'
        result = JSONToolParser.parse(text)

        assert result == []


class TestJSONToolParserAllowedNames:
    """Tests for allowed tool names filtering."""

    def test_with_allowed_names(self) -> None:
        """Normal: Filtering by allowed tool names."""
        text = '{"name": "read", "args": {}} {"name": "write", "args": {}}'
        result = JSONToolParser.parse(text, allowed_tool_names=["read"])

        assert len(result) == 1
        assert result[0].name == "read"

    def test_with_allowed_names_case_sensitive(self) -> None:
        """Boundary: Allowed names are case-insensitive internally."""
        text = '{"name": "Read", "args": {}}'
        result = JSONToolParser.parse(text, allowed_tool_names=["read"])

        assert len(result) == 1
        assert result[0].name == "Read"

    def test_with_empty_allowed_names(self) -> None:
        """Normal: Empty allowed names list means no filtering."""
        text = '{"name": "read", "args": {}}'
        result = JSONToolParser.parse(text, allowed_tool_names=[])

        assert len(result) == 1

    def test_no_allowed_names(self) -> None:
        """Normal: No allowed names specified means no filtering."""
        text = '{"name": "any_tool", "args": {}}'
        result = JSONToolParser.parse(text)

        assert len(result) == 1


class TestJSONToolParserDeduplication:
    """Tests for deduplication logic."""

    def test_duplicate_calls_removed(self) -> None:
        """Normal: Duplicate tool calls are removed."""
        text = '{"name": "read", "args": {"path": "f.txt"}} {"name": "read", "args": {"path": "f.txt"}}'
        result = JSONToolParser.parse(text)

        assert len(result) == 1

    def test_different_arguments_not_deduplicated(self) -> None:
        """Normal: Same tool with different arguments is kept."""
        text = '{"name": "read", "args": {"path": "a.txt"}} {"name": "read", "args": {"path": "b.txt"}}'
        result = JSONToolParser.parse(text)

        assert len(result) == 2


class TestJSONToolParserRegression:
    """Regression tests: ensure no breaking changes."""

    def test_existing_xml_format_still_works(self) -> None:
        """Regression: Pure XML without JSON is not parsed."""
        # XML tool tags without JSON inside should return empty
        text = "<tool_call><invoke>read</invoke></tool_call>"
        result = JSONToolParser.parse(text)
        # Should not find JSON tool call format
        assert result == []

    def test_json_inside_xml_is_parsed(self) -> None:
        """Regression: JSON inside XML should be parsed."""
        text = '<tool_call>{"name": "read", "arguments": {}}</tool_call>'
        result = JSONToolParser.parse(text)
        # JSON inside XML should be parsed
        assert len(result) == 1
        assert result[0].name == "read"

    def test_non_json_text_returns_empty(self) -> None:
        """Regression: Plain text without JSON returns empty."""
        text = "Hello, this is a plain text response with no JSON."
        result = JSONToolParser.parse(text)

        assert result == []

    def test_whitespace_only_returns_empty(self) -> None:
        """Regression: Whitespace-only input returns empty."""
        assert JSONToolParser.parse("   \n\t  ") == []


class TestIsJsonToolCall:
    """Tests for the is_json_tool_call helper function."""

    def test_valid_json_tool_call(self) -> None:
        """Normal: Valid JSON tool call format."""
        assert is_json_tool_call('{"name": "read", "arguments": {}}') is True

    def test_non_json(self) -> None:
        """Normal: Non-JSON text."""
        assert is_json_tool_call("Hello, world!") is False

    def test_json_without_required_fields(self) -> None:
        """Boundary: JSON without name or arguments."""
        assert is_json_tool_call('{"path": "f.txt"}') is False

    def test_empty_string(self) -> None:
        """Exception: Empty string."""
        assert is_json_tool_call("") is False
        assert is_json_tool_call("   ") is False

    def test_non_string_input(self) -> None:
        """Exception: Non-string input."""
        assert is_json_tool_call(123) is False  # type: ignore
        assert is_json_tool_call(None) is False  # type: ignore


class TestParseJsonToolCallsConvenience:
    """Tests for the parse_json_tool_calls convenience function."""

    def test_basic_usage(self) -> None:
        """Normal: Basic usage of convenience function."""
        result = parse_json_tool_calls('{"name": "read", "args": {"path": "f.txt"}}')

        assert len(result) == 1
        assert isinstance(result[0], ParsedToolCall)

    def test_with_allowed_names(self) -> None:
        """Normal: Convenience function with allowed names."""
        result = parse_json_tool_calls(
            '{"name": "read", "args": {}}',
            allowed_tool_names=["write"],
        )

        assert len(result) == 0


class TestJSONToolParserEdgeCases:
    """Additional edge case tests."""

    def test_unicode_in_arguments(self) -> None:
        """Boundary: Unicode characters in arguments."""
        text = '{"name": "write", "arguments": {"content": "你好世界"}}'
        result = JSONToolParser.parse(text)

        assert len(result) == 1
        assert result[0].arguments["content"] == "你好世界"

    def test_multiline_json(self) -> None:
        """Boundary: JSON spanning multiple lines."""
        text = """
        {
            "name": "read",
            "arguments": {
                "path": "file.txt"
            }
        }
        """
        result = JSONToolParser.parse(text)

        assert len(result) == 1
        assert result[0].name == "read"

    def test_special_characters_in_path(self) -> None:
        """Boundary: Special characters in file paths."""
        text = '{"name": "read", "arguments": {"path": "path/with spaces/and$chars"}}'
        result = JSONToolParser.parse(text)

        assert len(result) == 1
        assert result[0].arguments["path"] == "path/with spaces/and$chars"
