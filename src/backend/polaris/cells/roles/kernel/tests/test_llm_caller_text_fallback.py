"""Tests for text fallback parsing utilities.

These tests verify the text-based tool call fallback parsing added to handle
cases where LLM returns tool calls as JSON text instead of using native
tool calling protocols.

The actual implementations are in tool_helpers.py, not LLMCaller.
"""

from __future__ import annotations

import json
from typing import Any

# Import from correct module (tool_helpers.py, not LLMCaller)
from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import (
    _convert_json_to_tool_call,
    _extract_tool_calls_from_text,
    extract_native_tool_calls,
)


class TestExtractToolCallsFromTextNormal:
    """Normal scenario tests: valid JSON tool calls in text."""

    def test_simple_json_tool_call_with_arguments(self) -> None:
        """Normal: Simple JSON tool call with arguments."""
        text = '{"name": "read_file", "arguments": {"path": "test.py"}}'
        result = _extract_tool_calls_from_text(text)

        assert len(result) == 1
        assert result[0]["function"]["name"] == "read_file"
        assert result[0]["type"] == "function"
        assert "id" in result[0]

    def test_json_with_capital_name(self) -> None:
        """Normal: JSON with capitalized Name field."""
        text = '{"Name": "read_file", "arguments": {"path": "test.py"}}'
        result = _extract_tool_calls_from_text(text)

        assert len(result) == 1
        assert result[0]["function"]["name"] == "read_file"

    def test_json_with_args_key(self) -> None:
        """Normal: JSON using 'args' instead of 'arguments'."""
        text = '{"name": "write", "args": {"path": "out.txt", "content": "hello"}}'
        result = _extract_tool_calls_from_text(text)

        assert len(result) == 1
        assert result[0]["function"]["name"] == "write"

    def test_json_with_tool_key(self) -> None:
        """Normal: JSON using 'tool' instead of 'name'."""
        text = '{"tool": "delete", "arguments": {"path": "old.txt"}}'
        result = _extract_tool_calls_from_text(text)

        assert len(result) == 1
        assert result[0]["function"]["name"] == "delete"

    def test_multiple_tool_calls_in_text(self) -> None:
        """Normal: Multiple tool calls in text."""
        text = '{"name": "read", "arguments": {}} {"name": "write", "arguments": {}}'
        result = _extract_tool_calls_from_text(text)

        assert len(result) == 2
        names = {r["function"]["name"] for r in result}
        assert names == {"read", "write"}

    def test_json_in_context(self) -> None:
        """Normal: JSON tool call embedded in regular text."""
        text = 'Let me read the file: {"name": "read", "arguments": {"path": "f.txt"}}'
        result = _extract_tool_calls_from_text(text)

        assert len(result) == 1
        assert result[0]["function"]["name"] == "read"

    def test_json_with_nested_arguments(self) -> None:
        """Normal: Nested arguments in JSON."""
        text = json.dumps({"name": "search", "arguments": {"query": {"term": "test", "filters": {"type": "py"}}}})
        result = _extract_tool_calls_from_text(text)

        assert len(result) == 1
        args = json.loads(result[0]["function"]["arguments"])
        assert args == {"query": {"term": "test", "filters": {"type": "py"}}}


class TestExtractToolCallsFromTextBoundary:
    """Boundary scenario tests: edge cases."""

    def test_empty_arguments(self) -> None:
        """Boundary: Tool call with empty arguments."""
        text = '{"name": "ping", "arguments": {}}'
        result = _extract_tool_calls_from_text(text)

        assert len(result) == 1
        assert json.loads(result[0]["function"]["arguments"]) == {}

    def test_whitespace_around_json(self) -> None:
        """Boundary: JSON with whitespace."""
        text = '  \n  {"name": "read", "arguments": {}}  \n  '
        result = _extract_tool_calls_from_text(text)

        assert len(result) == 1

    def test_extra_fields_in_json(self) -> None:
        """Boundary: JSON with extra fields besides name and arguments."""
        text = '{"name": "read", "arguments": {"path": "f.txt"}, "extra": "ignored"}'
        result = _extract_tool_calls_from_text(text)

        assert len(result) == 1
        args = json.loads(result[0]["function"]["arguments"])
        assert args == {"path": "f.txt"}

    def test_various_argument_types(self) -> None:
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
        result = _extract_tool_calls_from_text(text)

        assert len(result) == 1
        args = json.loads(result[0]["function"]["arguments"])
        assert args["string"] == "hello"
        assert args["number"] == 42
        assert args["float"] == 3.14
        assert args["bool_true"] is True
        assert args["bool_false"] is False
        assert args["null"] is None
        assert args["array"] == [1, 2, 3]

    def test_special_characters_in_path(self) -> None:
        """Boundary: Special characters in file paths."""
        text = '{"name": "read", "arguments": {"path": "path/with spaces/and$chars"}}'
        result = _extract_tool_calls_from_text(text)

        assert len(result) == 1
        args = json.loads(result[0]["function"]["arguments"])
        assert args["path"] == "path/with spaces/and$chars"


class TestExtractToolCallsFromTextException:
    """Exception scenario tests: invalid inputs."""

    def test_empty_string(self) -> None:
        """Exception: Empty string returns empty list."""
        assert _extract_tool_calls_from_text("") == []
        assert _extract_tool_calls_from_text("   ") == []

    def test_none_input(self) -> None:
        """Exception: None input returns empty list."""
        assert _extract_tool_calls_from_text(None) == []  # type: ignore

    def test_non_string_input(self) -> None:
        """Exception: Non-string input returns empty list."""
        assert _extract_tool_calls_from_text(123) == []  # type: ignore
        assert _extract_tool_calls_from_text(["list"]) == []  # type: ignore
        assert _extract_tool_calls_from_text({"dict": "value"}) == []  # type: ignore

    def test_missing_name_returns_empty(self) -> None:
        """Exception: JSON without name field returns empty."""
        text = '{"arguments": {"path": "f.txt"}}'
        result = _extract_tool_calls_from_text(text)

        assert result == []

    def test_missing_arguments_is_valid(self) -> None:
        """Exception: JSON without arguments field returns call with empty args."""
        text = '{"name": "ping"}'
        result = _extract_tool_calls_from_text(text)

        assert len(result) == 1
        assert result[0]["function"]["name"] == "ping"
        assert json.loads(result[0]["function"]["arguments"]) == {}

    def test_invalid_name_type(self) -> None:
        """Exception: Name field is not a string."""
        text = '{"name": 123, "arguments": {}}'
        result = _extract_tool_calls_from_text(text)

        assert result == []

    def test_plain_text_no_json(self) -> None:
        """Exception: Plain text without JSON returns empty."""
        text = "Hello, this is a plain text response with no JSON."
        result = _extract_tool_calls_from_text(text)

        assert result == []


class TestExtractToolCallsFromTextFormat:
    """Tests for output format compatibility."""

    def test_output_is_openai_format(self) -> None:
        """Verify output matches OpenAI function calling format."""
        text = '{"name": "read_file", "arguments": {"path": "test.py"}}'
        result = _extract_tool_calls_from_text(text)

        assert len(result) == 1
        call = result[0]
        # Verify OpenAI format structure
        assert "id" in call
        assert call["type"] == "function"
        assert "function" in call
        assert "name" in call["function"]
        assert "arguments" in call["function"]
        # Arguments should be a JSON string
        args = json.loads(call["function"]["arguments"])
        assert args == {"path": "test.py"}

    def test_arguments_are_json_string(self) -> None:
        """Verify arguments are returned as JSON string, not dict."""
        text = '{"name": "read", "arguments": {"path": "f.txt"}}'
        result = _extract_tool_calls_from_text(text)

        args_str = result[0]["function"]["arguments"]
        # Should be a string that can be parsed as JSON
        assert isinstance(args_str, str)
        args = json.loads(args_str)
        assert args == {"path": "f.txt"}

    def test_id_is_unique(self) -> None:
        """Verify each call gets a unique ID."""
        text = '{"name": "read", "arguments": {}} {"name": "write", "arguments": {}}'
        result = _extract_tool_calls_from_text(text)

        ids = {r["id"] for r in result}
        assert len(ids) == len(result)  # All unique


class TestExtractNativeToolCallsWithFallback:
    """Tests for _extract_native_tool_calls with text fallback."""

    def test_native_calls_take_precedence(self) -> None:
        """Native tool calls should be returned before text fallback."""
        raw = {"tool_calls": [{"id": "1", "type": "function", "function": {"name": "native", "arguments": "{}"}}]}
        calls, provider = extract_native_tool_calls(
            raw,
            provider_id="openai",
            model="gpt-4",
            response_text='{"name": "text_fallback", "arguments": {}}',
        )

        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "native"
        assert provider == "openai"

    def test_text_fallback_when_no_native(self) -> None:
        """Text fallback should be used when no native calls."""
        raw: dict[str, Any] = {}
        calls, provider = extract_native_tool_calls(
            raw,
            provider_id="openai",
            model="gpt-4",
            response_text='{"name": "read_file", "arguments": {"path": "test.py"}}',
        )

        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "read_file"
        assert provider == "text_fallback"

    def test_empty_response_text_no_fallback(self) -> None:
        """Empty response text should not trigger fallback."""
        raw: dict[str, Any] = {}
        calls, provider = extract_native_tool_calls(
            raw,
            provider_id="openai",
            model="gpt-4",
            response_text="",
        )

        assert calls == []
        assert provider == "openai"

    def test_none_response_text_no_fallback(self) -> None:
        """None response text should not trigger fallback."""
        raw: dict[str, Any] = {}
        calls, provider = extract_native_tool_calls(
            raw,
            provider_id="openai",
            model="gpt-4",
            response_text=None,
        )

        assert calls == []
        assert provider == "openai"


class TestMixedNativeAndText:
    """Tests for mixed native and text tool call scenarios."""

    def test_mixed_native_and_text_prefers_native(self) -> None:
        """Native tool calls should take precedence over text tool calls in same response.

        This tests the scenario where LLM returns both native tool_calls in the
        response payload AND JSON text tool calls. The native calls should be
        returned and text fallback should be skipped.
        """
        # Simulate response with native tool_calls AND text containing tool calls
        raw = {
            "tool_calls": [
                {
                    "id": "native_call_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "native.txt"}'},
                }
            ]
        }
        # Text fallback contains different tool call
        response_text = '{"name": "write_file", "arguments": {"path": "text_fallback.txt"}}'

        calls, provider = extract_native_tool_calls(
            raw,
            provider_id="openai",
            model="gpt-4",
            response_text=response_text,
        )

        # Native call should be returned, text fallback ignored
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "read_file"
        assert calls[0]["id"] == "native_call_1"
        assert provider == "openai"

    def test_text_fallback_used_when_native_empty(self) -> None:
        """Text fallback should be used when native tool_calls is empty list.

        This tests the scenario where response has empty tool_calls array
        but text contains valid JSON tool calls.
        """
        raw: dict[str, Any] = {"tool_calls": []}
        response_text = '{"name": "search", "arguments": {"query": "test"}}'

        calls, provider = extract_native_tool_calls(
            raw,
            provider_id="openai",
            model="gpt-4",
            response_text=response_text,
        )

        # Text fallback should be used
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "search"
        assert provider == "text_fallback"

    def test_multiple_native_then_text_fallback(self) -> None:
        """Multiple native calls returned, text fallback ignored.

        Even if text contains additional tool calls, native calls take precedence.
        """
        raw = {
            "tool_calls": [
                {"id": "1", "type": "function", "function": {"name": "read", "arguments": "{}"}},
                {"id": "2", "type": "function", "function": {"name": "write", "arguments": "{}"}},
            ]
        }
        # Additional text tool call should be ignored
        response_text = '{"name": "delete", "arguments": {"path": "ignored.txt"}}'

        calls, provider = extract_native_tool_calls(
            raw,
            provider_id="openai",
            model="gpt-4",
            response_text=response_text,
        )

        assert len(calls) == 2
        names = {c["function"]["name"] for c in calls}
        assert names == {"read", "write"}
        assert provider == "openai"

    def test_native_empty_dict_vs_text_fallback(self) -> None:
        """Empty dict in tool_calls should trigger text fallback.

        Some providers may return tool_calls as empty dict {} instead of empty list.
        """
        raw: dict[str, Any] = {"tool_calls": {}}
        response_text = '{"name": "ping", "arguments": {}}'

        calls, provider = extract_native_tool_calls(
            raw,
            provider_id="openai",
            model="gpt-4",
            response_text=response_text,
        )

        # Text fallback should be used since tool_calls is empty dict
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "ping"
        assert provider == "text_fallback"


class TestConvertJsonToToolCall:
    """Tests for _convert_json_to_tool_call helper."""

    def test_valid_conversion(self) -> None:
        """Valid JSON should convert correctly."""
        data = {"name": "read", "arguments": {"path": "f.txt"}}
        result = _convert_json_to_tool_call(data)

        assert result is not None
        assert result["function"]["name"] == "read"
        assert json.loads(result["function"]["arguments"]) == {"path": "f.txt"}

    def test_case_insensitive_keys(self) -> None:
        """Key matching should be case-insensitive."""
        data = {"Name": "read", "Arguments": {"path": "f.txt"}}
        result = _convert_json_to_tool_call(data)

        assert result is not None
        assert result["function"]["name"] == "read"

    def test_tool_key_instead_of_name(self) -> None:
        """'tool' key should work as well as 'name'."""
        data = {"tool": "write", "args": {"content": "hello"}}
        result = _convert_json_to_tool_call(data)

        assert result is not None
        assert result["function"]["name"] == "write"

    def test_invalid_tool_name(self) -> None:
        """Invalid tool names should return None."""
        # Name starting with number
        data = {"name": "123invalid", "arguments": {}}
        result = _convert_json_to_tool_call(data)
        assert result is None

        # Empty name
        data = {"name": "", "arguments": {}}
        result = _convert_json_to_tool_call(data)
        assert result is None

    def test_missing_name(self) -> None:
        """Missing name field should return None."""
        data = {"arguments": {"path": "f.txt"}}
        result = _convert_json_to_tool_call(data)
        assert result is None

    def test_non_dict_input(self) -> None:
        """Non-dict input should return None."""
        assert _convert_json_to_tool_call("string") is None  # type: ignore[arg-type]
        assert _convert_json_to_tool_call([1, 2]) is None  # type: ignore[arg-type]
        assert _convert_json_to_tool_call(None) is None  # type: ignore[arg-type]
