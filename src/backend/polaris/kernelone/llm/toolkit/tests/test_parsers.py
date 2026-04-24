"""Tests for tool call parsers.

P0-002: All parse methods now return list[ToolCall] (canonical type).
ParsedToolCall is now an alias to ToolCall from contracts.tool.
"""

from typing import Any

from polaris.kernelone.llm.contracts.tool import ToolCall
from polaris.kernelone.llm.toolkit.parsers import (
    CANONICAL_ARGUMENT_KEYS,
    CanonicalToolCallParser,
    NativeFunctionCallingParser,
    ParsedToolCall,  # Alias to ToolCall
    extract_arguments,
    extract_tool_calls_and_remainder,
    has_tool_calls,
    parse_tool_calls,
)


class TestCanonicalToolCallParser:
    """Test CanonicalToolCallParser - the unified parser entry point.

    P0-002: parse() now returns list[ToolCall].
    """

    def test_parse_openai_format(self) -> None:
        """Test parsing OpenAI format tool calls."""
        parser = CanonicalToolCallParser()
        tool_calls = [
            {
                "id": "call_123",
                "type": "function",
                "function": {"name": "repo_rg", "arguments": '{"pattern": "test", "path": "src"}'},
            }
        ]
        result = parser.parse(tool_calls, format_hint="openai")

        assert len(result) == 1
        # P0-002: ToolCall uses 'name' field (not 'tool_name')
        assert result[0].name == "repo_rg"
        assert result[0].source == "openai"
        assert result[0].arguments == {"pattern": "test", "path": "src"}
        assert result[0].id == "call_123"

    def test_parse_anthropic_format(self) -> None:
        """Test parsing Anthropic format tool calls."""
        parser = CanonicalToolCallParser()
        blocks = [{"type": "tool_use", "name": "repo_read_head", "input": '{"file": "test.py", "n": 10}'}]
        result = parser.parse(blocks, format_hint="anthropic")

        assert len(result) == 1
        # P0-002: ToolCall uses 'name' field
        assert result[0].name == "repo_read_head"
        assert result[0].source == "anthropic"
        assert result[0].arguments == {"file": "test.py", "n": 10}

    def test_parse_with_allowed_tools_filter(self) -> None:
        """Test that allowed_tools filter works."""
        parser = CanonicalToolCallParser()
        tool_calls = [
            {"id": "1", "type": "function", "function": {"name": "repo_rg", "arguments": "{}"}},
            {"id": "2", "type": "function", "function": {"name": "repo_read_head", "arguments": "{}"}},
        ]
        result = parser.parse(tool_calls, format_hint="openai", allowed_tools=["repo_rg"])

        assert len(result) == 1
        assert result[0].name == "repo_rg"

    def test_canonical_argument_keys(self) -> None:
        """Test that CANONICAL_ARGUMENT_KEYS is defined."""
        assert "arguments" in CANONICAL_ARGUMENT_KEYS
        assert "args" in CANONICAL_ARGUMENT_KEYS
        assert "params" in CANONICAL_ARGUMENT_KEYS
        assert "parameters" in CANONICAL_ARGUMENT_KEYS
        assert "input" in CANONICAL_ARGUMENT_KEYS

    def test_extract_arguments(self) -> None:
        """Test extract_arguments helper."""
        # Test arguments key
        data = {"arguments": {"a": 1}}
        assert extract_arguments(data) == {"a": 1}

        # Test args key
        data = {"args": {"b": 2}}
        assert extract_arguments(data) == {"b": 2}

        # Test fallback: when no canonical key found, filter known non-argument keys
        # If result would be empty, return original data unchanged
        fallback_data: dict[str, Any] = {"foo": "bar", "tool": "test"}  # no canonical keys, no filter keys
        result = extract_arguments(fallback_data)
        assert result == {"foo": "bar", "tool": "test"}

    def test_returns_tool_call_type(self) -> None:
        """Test that parse() returns ToolCall instances."""
        parser = CanonicalToolCallParser()
        tool_calls = [{"id": "call_1", "type": "function", "function": {"name": "repo_rg", "arguments": "{}"}}]
        result = parser.parse(tool_calls, format_hint="openai")

        # P0-002: Result should be list[ToolCall]
        assert len(result) == 1
        assert isinstance(result[0], ToolCall)


class TestToolCallUnified:
    """Test unified ToolCall type from contracts.tool."""

    def test_parsed_tool_call_is_tool_call(self) -> None:
        """Test that ParsedToolCall is now an alias to ToolCall."""
        # P0-002: ParsedToolCall = ToolCall
        assert ParsedToolCall is ToolCall

    def test_tool_call_fields(self) -> None:
        """Test ToolCall has canonical fields."""
        call = ToolCall(
            id="call_123",
            name="repo_rg",
            arguments={"pattern": "test"},
            source="openai",
            raw="{}",
            parse_error=None,
        )

        # Unified fields (P0-001 + P0-002)
        assert call.id == "call_123"
        assert call.name == "repo_rg"
        assert call.arguments == {"pattern": "test"}
        assert call.source == "openai"


class TestNativeFunctionCallingParser:
    """Test NativeFunctionCallingParser - existing parser."""

    def test_parse_openai(self) -> None:
        """Test parsing OpenAI format."""
        tool_calls = [
            {"id": "call_1", "type": "function", "function": {"name": "repo_rg", "arguments": '{"pattern": "test"}'}}
        ]
        result = NativeFunctionCallingParser.parse_openai(tool_calls)

        assert len(result) == 1
        assert result[0].name == "repo_rg"


class TestCoreParsingFunctions:
    """Test core parsing functions from parsers module."""

    def test_extract_tool_calls_and_remainder_returns_empty(self) -> None:
        """Test that extract_tool_calls_and_remainder returns empty list."""
        result, remainder = extract_tool_calls_and_remainder("some text")
        assert result == []
        assert remainder == "some text"

    def test_has_tool_calls_returns_false(self) -> None:
        """Test that has_tool_calls always returns False (text protocols deprecated)."""
        assert has_tool_calls("[TOOL]test[/TOOL]") is False
        assert has_tool_calls("any text") is False

    def test_parse_tool_calls_with_native_format(self) -> None:
        """Test parse_tool_calls with native format."""
        tool_calls = [
            {"id": "call_1", "type": "function", "function": {"name": "repo_rg", "arguments": '{"pattern": "test"}'}}
        ]
        result = parse_tool_calls(tool_calls=tool_calls, provider="openai")

        assert len(result) >= 1
        # P0-002: result is list[ToolCall]
        assert isinstance(result[0], ToolCall)
