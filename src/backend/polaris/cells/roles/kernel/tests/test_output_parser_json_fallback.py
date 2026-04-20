"""Integration tests for OutputParser JSON fallback parsing.

These tests verify the JSON tool call fallback parsing integrated into
OutputParser.parse_execution_tool_calls(), which handles cases where LLM
returns tool calls as JSON text instead of using native tool calling protocols.

Fallback chain:
1. Native tool calling protocol (OpenAI/Anthropic format)
2. JSON tool call text parsing (fallback)
"""

from __future__ import annotations

import json

from polaris.cells.roles.kernel.internal.output_parser import OutputParser


class TestOutputParserJSONFallbackNormal:
    """Normal scenario tests: valid JSON tool calls in text."""

    def test_native_calls_take_precedence_over_json(self) -> None:
        """Native tool calls should be returned before JSON fallback."""
        parser = OutputParser()
        content = '{"name": "read_file", "arguments": {"path": "fallback.txt"}}'

        # Simulate native tool calls present
        native_calls = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "native_tool", "arguments": json.dumps({"key": "value"})},
            }
        ]

        result = parser.parse_execution_tool_calls(
            content=content,
            native_tool_calls=native_calls,
        )

        # Native call should be returned, not the JSON fallback
        assert len(result) == 1
        assert result[0].tool == "native_tool"
        assert result[0].args == {"key": "value"}

    def test_json_fallback_when_no_native_calls(self) -> None:
        """JSON fallback should be used when no native calls are present."""
        parser = OutputParser()
        content = '{"name": "read_file", "arguments": {"path": "test.py"}}'

        result = parser.parse_execution_tool_calls(content=content)

        assert len(result) == 1
        assert result[0].tool == "read_file"
        assert result[0].args == {"path": "test.py"}

    def test_json_with_arguments_key(self) -> None:
        """JSON tool call using 'arguments' key should be parsed."""
        parser = OutputParser()
        content = '{"name": "read_file", "arguments": {"path": "test.py", "offset": 0}}'

        result = parser.parse_execution_tool_calls(content=content)

        assert len(result) == 1
        assert result[0].tool == "read_file"
        assert result[0].args == {"path": "test.py", "offset": 0}

    def test_json_with_args_key(self) -> None:
        """JSON tool call using 'args' key should be parsed."""
        parser = OutputParser()
        content = '{"name": "write", "args": {"path": "out.txt", "content": "hello"}}'

        result = parser.parse_execution_tool_calls(content=content)

        assert len(result) == 1
        assert result[0].tool == "write"
        assert result[0].args == {"path": "out.txt", "content": "hello"}

    def test_json_with_tool_key(self) -> None:
        """JSON tool call using 'tool' key should be parsed."""
        parser = OutputParser()
        content = '{"tool": "delete", "arguments": {"path": "old.txt"}}'

        result = parser.parse_execution_tool_calls(content=content)

        assert len(result) == 1
        assert result[0].tool == "delete"
        assert result[0].args == {"path": "old.txt"}

    def test_multiple_json_calls_in_text(self) -> None:
        """Multiple JSON tool calls in text should all be parsed."""
        parser = OutputParser()
        content = (
            '{"name": "read", "arguments": {"path": "a.txt"}}'
            ' {"name": "write", "arguments": {"path": "b.txt", "content": "data"}}'
            ' {"tool": "delete", "args": {"path": "c.txt"}}'
        )

        result = parser.parse_execution_tool_calls(content=content)

        assert len(result) == 3
        tools = {call.tool for call in result}
        assert tools == {"read", "write", "delete"}

        # Verify arguments
        write_call = next(c for c in result if c.tool == "write")
        assert write_call.args == {"path": "b.txt", "content": "data"}


class TestOutputParserJSONFallbackBoundary:
    """Boundary scenario tests: edge cases and special formats."""

    def test_json_embedded_in_text(self) -> None:
        """JSON embedded in regular text should be parsed."""
        parser = OutputParser()
        content = 'Let me read the file: {"name": "read", "arguments": {"path": "f.txt"}}'

        result = parser.parse_execution_tool_calls(content=content)

        assert len(result) == 1
        assert result[0].tool == "read"
        assert result[0].args == {"path": "f.txt"}

    def test_json_embedded_with_leading_text(self) -> None:
        """JSON with leading text before it should be parsed."""
        parser = OutputParser()
        content = 'I\'ll execute: {"name": "search", "arguments": {"query": "test"}}'

        result = parser.parse_execution_tool_calls(content=content)

        assert len(result) == 1
        assert result[0].tool == "search"
        assert result[0].args == {"query": "test"}

    def test_json_embedded_with_trailing_text(self) -> None:
        """JSON with trailing text after it should be parsed."""
        parser = OutputParser()
        content = '{"name": "list", "arguments": {}} then continue here'

        result = parser.parse_execution_tool_calls(content=content)

        assert len(result) == 1
        assert result[0].tool == "list"

    def test_json_with_whitespace(self) -> None:
        """JSON with extra whitespace should be parsed."""
        parser = OutputParser()
        content = '  \n  {"name": "read", "arguments": {"path": "f.txt"}}  \n  '

        result = parser.parse_execution_tool_calls(content=content)

        assert len(result) == 1
        assert result[0].tool == "read"

    def test_json_with_nested_arguments(self) -> None:
        """JSON with nested arguments should preserve structure."""
        parser = OutputParser()
        nested_args = {"query": {"term": "test", "filters": {"type": "py", "limit": 10}}}
        content = json.dumps({"name": "search", "arguments": nested_args})

        result = parser.parse_execution_tool_calls(content=content)

        assert len(result) == 1
        assert result[0].tool == "search"
        assert result[0].args == {"query": {"term": "test", "filters": {"type": "py", "limit": 10}}}

    def test_json_with_various_value_types(self) -> None:
        """JSON with various argument value types should be preserved."""
        parser = OutputParser()
        complex_args = {
            "string": "hello",
            "number": 42,
            "float": 3.14,
            "bool_true": True,
            "bool_false": False,
            "null": None,
            "array": [1, 2, 3],
        }
        content = json.dumps({"name": "complex", "arguments": complex_args})

        result = parser.parse_execution_tool_calls(content=content)

        assert len(result) == 1
        assert result[0].args == complex_args

    def test_json_with_capitalized_keys(self) -> None:
        """JSON with capitalized key names should be parsed."""
        parser = OutputParser()
        content = '{"Name": "read_file", "Arguments": {"Path": "test.py"}}'

        result = parser.parse_execution_tool_calls(content=content)

        assert len(result) == 1
        assert result[0].tool == "read_file"
        # Parameter keys preserve original casing per JSONToolParser design
        # Downstream executors handle key normalization
        assert "Path" in result[0].args or "path" in result[0].args
        assert result[0].args.get("Path") == "test.py" or result[0].args.get("path") == "test.py"

    def test_json_inside_code_block(self) -> None:
        """JSON inside markdown code block should be parsed."""
        parser = OutputParser()
        content = '```json\n{"name": "read", "arguments": {"path": "f.txt"}}\n```'

        result = parser.parse_execution_tool_calls(content=content)

        assert len(result) == 1
        assert result[0].tool == "read"

    def test_tool_name_normalized_to_lowercase(self) -> None:
        """Tool names should be normalized to lowercase."""
        parser = OutputParser()
        content = '{"name": "ReadFile", "arguments": {}}'

        result = parser.parse_execution_tool_calls(content=content)

        assert len(result) == 1
        assert result[0].tool == "readfile"


class TestOutputParserJSONFallbackException:
    """Exception scenario tests: invalid inputs and error handling."""

    def test_invalid_json_returns_empty(self) -> None:
        """Invalid JSON should return empty list."""
        parser = OutputParser()
        content = '{"name": "read", "arguments":}'  # Missing value

        result = parser.parse_execution_tool_calls(content=content)

        assert result == []

    def test_malformed_json_returns_empty(self) -> None:
        """Malformed JSON should return empty list."""
        parser = OutputParser()
        content = "{ name: 'read', arguments: {} }"  # Not valid JSON

        result = parser.parse_execution_tool_calls(content=content)

        assert result == []

    def test_no_json_in_text(self) -> None:
        """Plain text without JSON should return empty list."""
        parser = OutputParser()
        content = "Hello, this is a plain text response with no JSON."

        result = parser.parse_execution_tool_calls(content=content)

        assert result == []

    def test_json_without_name_field(self) -> None:
        """JSON without name field should return empty list."""
        parser = OutputParser()
        content = '{"arguments": {"path": "f.txt"}}'

        result = parser.parse_execution_tool_calls(content=content)

        assert result == []

    def test_json_without_arguments_field(self) -> None:
        """JSON without arguments field should return call with empty args."""
        parser = OutputParser()
        content = '{"name": "ping"}'

        result = parser.parse_execution_tool_calls(content=content)

        assert len(result) == 1
        assert result[0].tool == "ping"
        assert result[0].args == {}

    def test_empty_content(self) -> None:
        """Empty content should return empty list."""
        parser = OutputParser()

        result = parser.parse_execution_tool_calls(content="")
        assert result == []

        result = parser.parse_execution_tool_calls(content="   ")
        assert result == []

    def test_none_content(self) -> None:
        """None content should return empty list."""
        parser = OutputParser()

        result = parser.parse_execution_tool_calls(content=None)  # type: ignore
        assert result == []

    def test_only_punctuation(self) -> None:
        """Content with only punctuation should return empty list."""
        parser = OutputParser()

        result = parser.parse_execution_tool_calls(content="..., ---, !!!")
        assert result == []


class TestOutputParserJSONFallbackAllowedTools:
    """Tests for allowed_tool_names filtering."""

    def test_allowed_tool_names_whitelist(self) -> None:
        """Only tools in whitelist should be returned."""
        parser = OutputParser()
        content = (
            '{"name": "read", "arguments": {}} {"name": "write", "arguments": {}} {"name": "delete", "arguments": {}}'
        )

        result = parser.parse_execution_tool_calls(
            content=content,
            allowed_tool_names=["read", "write"],
        )

        assert len(result) == 2
        tools = {call.tool for call in result}
        assert tools == {"read", "write"}
        assert "delete" not in tools

    def test_single_allowed_tool(self) -> None:
        """Only the single allowed tool should be returned."""
        parser = OutputParser()
        content = '{"name": "search", "arguments": {}}'

        result = parser.parse_execution_tool_calls(
            content=content,
            allowed_tool_names=["search"],
        )

        assert len(result) == 1
        assert result[0].tool == "search"

    def test_empty_allowed_tools_allows_all(self) -> None:
        """When allowed_tool_names is empty list, all tools are allowed (no restriction)."""
        parser = OutputParser()
        content = '{"name": "read", "arguments": {}}'

        result = parser.parse_execution_tool_calls(
            content=content,
            allowed_tool_names=[],
        )

        # Empty list is treated as "no restriction" - all tools allowed
        assert len(result) == 1
        assert result[0].tool == "read"

    def test_case_insensitive_allowed_tools(self) -> None:
        """Allowed tool names should match case-insensitively."""
        parser = OutputParser()
        content = '{"name": "ReadFile", "arguments": {}}'

        result = parser.parse_execution_tool_calls(
            content=content,
            allowed_tool_names=["readfile"],
        )

        assert len(result) == 1


class TestOutputParserJSONFallbackDeduplication:
    """Tests for tool call deduplication."""

    def test_duplicate_calls_filtered(self) -> None:
        """Duplicate tool calls with same args should be deduplicated."""
        parser = OutputParser()
        content = '{"name": "read", "arguments": {"path": "a.txt"}} {"name": "read", "arguments": {"path": "a.txt"}}'

        result = parser.parse_execution_tool_calls(content=content)

        assert len(result) == 1

    def test_same_tool_different_args_kept(self) -> None:
        """Same tool with different args should be kept."""
        parser = OutputParser()
        content = '{"name": "read", "arguments": {"path": "a.txt"}} {"name": "read", "arguments": {"path": "b.txt"}}'

        result = parser.parse_execution_tool_calls(content=content)

        assert len(result) == 2
        paths = {call.args.get("path") for call in result}
        assert paths == {"a.txt", "b.txt"}

    def test_native_and_json_dedup(self) -> None:
        """Native call matching JSON fallback should be deduplicated."""
        parser = OutputParser()
        content = '{"name": "read", "arguments": {"path": "test.py"}}'

        # Same call in native format
        native_calls = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "read", "arguments": json.dumps({"path": "test.py"})},
            }
        ]

        result = parser.parse_execution_tool_calls(
            content=content,
            native_tool_calls=native_calls,
        )

        # Native should be kept, JSON should not duplicate
        assert len(result) == 1


class TestOutputParserJSONFallbackFormat:
    """Tests for output format compatibility."""

    def test_result_is_tool_call_result(self) -> None:
        """Result items should be ToolCallResult instances."""
        parser = OutputParser()
        content = '{"name": "read", "arguments": {"path": "f.txt"}}'

        result = parser.parse_execution_tool_calls(content=content)

        assert len(result) == 1
        assert isinstance(result[0], parser.__class__.__bases__[0])  # ToolCallResult
        assert hasattr(result[0], "tool")
        assert hasattr(result[0], "args")

    def test_arguments_are_dict(self) -> None:
        """Arguments should be returned as dictionaries."""
        parser = OutputParser()
        content = '{"name": "read", "arguments": {"path": "f.txt", "limit": 100}}'

        result = parser.parse_execution_tool_calls(content=content)

        assert isinstance(result[0].args, dict)
        assert result[0].args["path"] == "f.txt"
        assert result[0].args["limit"] == 100


class TestOutputParserJSONFallbackIntegration:
    """Integration tests combining multiple scenarios."""

    def test_mixed_content_with_multiple_calls(self) -> None:
        """Complex content with multiple JSON calls and text."""
        parser = OutputParser()
        content = """
        I need to do several things:

        First, read the config:
        {"name": "read_file", "arguments": {"path": "config.json"}}

        Then search for patterns:
        {"tool": "ripgrep", "args": {"pattern": "TODO"}}

        Finally write the results:
        {"name": "write", "arguments": {"path": "output.txt", "content": "done"}}
        """

        result = parser.parse_execution_tool_calls(content=content)

        assert len(result) == 3
        tools = {call.tool for call in result}
        assert tools == {"read_file", "ripgrep", "write"}

    def test_json_with_params_and_parameters_keys(self) -> None:
        """JSON with 'params' and 'parameters' keys should also work."""
        parser = OutputParser()

        # Test 'params' key
        content1 = '{"name": "read", "params": {"path": "a.txt"}}'
        result1 = parser.parse_execution_tool_calls(content=content1)
        assert len(result1) == 1
        assert result1[0].args == {"path": "a.txt"}

        # Test 'parameters' key
        content2 = '{"name": "read", "parameters": {"path": "b.txt"}}'
        result2 = parser.parse_execution_tool_calls(content=content2)
        assert len(result2) == 1
        assert result2[0].args == {"path": "b.txt"}

    def test_json_with_function_key(self) -> None:
        """JSON with 'function' key should also work for tool name."""
        parser = OutputParser()
        content = '{"function": "list_dir", "arguments": {}}'

        result = parser.parse_execution_tool_calls(content=content)

        assert len(result) == 1
        assert result[0].tool == "list_dir"

    def test_json_with_action_key(self) -> None:
        """JSON with 'action' key should also work for tool name."""
        parser = OutputParser()
        content = '{"action": "execute", "arguments": {"cmd": "ls"}}'

        result = parser.parse_execution_tool_calls(content=content)

        assert len(result) == 1
        assert result[0].tool == "execute"

    def test_unicode_content(self) -> None:
        """JSON with unicode characters should be parsed correctly."""
        parser = OutputParser()
        content = '{"name": "write", "arguments": {"path": "中文文件.txt", "content": "Hello 世界"}}'

        result = parser.parse_execution_tool_calls(content=content)

        assert len(result) == 1
        assert result[0].args["path"] == "中文文件.txt"
        assert result[0].args["content"] == "Hello 世界"
