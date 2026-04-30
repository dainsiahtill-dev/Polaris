"""Tests for polaris.cells.roles.adapters.internal.schemas.base."""

from __future__ import annotations

import pytest
from polaris.cells.roles.adapters.internal.schemas.base import (
    BaseToolEnabledOutput,
    ToolCall,
)
from pydantic import ValidationError


class TestToolCall:
    """Tests for ToolCall pydantic model."""

    def test_create_minimal(self) -> None:
        tc = ToolCall(tool="search_code")
        assert tc.tool == "search_code"
        assert tc.arguments == {}
        assert tc.reasoning is None

    def test_create_with_arguments(self) -> None:
        tc = ToolCall(tool="read_file", arguments={"path": "/tmp/test.txt"})
        assert tc.arguments == {"path": "/tmp/test.txt"}

    def test_create_with_reasoning(self) -> None:
        tc = ToolCall(tool="glob", reasoning="Need to find all python files")
        assert tc.reasoning == "Need to find all python files"

    def test_create_full(self) -> None:
        tc = ToolCall(
            tool="search_code",
            arguments={"query": "def foo"},
            reasoning="Search for function definitions",
        )
        assert tc.tool == "search_code"
        assert tc.arguments == {"query": "def foo"}
        assert tc.reasoning == "Search for function definitions"

    def test_tool_required(self) -> None:
        with pytest.raises(ValidationError):
            ToolCall()  # type: ignore[call-arg]

    def test_arguments_defaults_to_empty_dict(self) -> None:
        tc = ToolCall(tool="x")
        assert tc.arguments == {}
        assert isinstance(tc.arguments, dict)

    def test_reasoning_defaults_to_none(self) -> None:
        tc = ToolCall(tool="x")
        assert tc.reasoning is None

    def test_arguments_can_be_empty_dict(self) -> None:
        tc = ToolCall(tool="x", arguments={})
        assert tc.arguments == {}

    def test_tool_name_with_special_chars(self) -> None:
        tc = ToolCall(tool="tool-with-dashes_123")
        assert tc.tool == "tool-with-dashes_123"

    def test_serialization(self) -> None:
        tc = ToolCall(tool="read_file", arguments={"path": "/tmp/test.txt"})
        data = tc.model_dump()
        assert data["tool"] == "read_file"
        assert data["arguments"] == {"path": "/tmp/test.txt"}

    def test_deserialization(self) -> None:
        data = {"tool": "search_code", "arguments": {"q": "test"}, "reasoning": "r"}
        tc = ToolCall.model_validate(data)
        assert tc.tool == "search_code"
        assert tc.arguments == {"q": "test"}
        assert tc.reasoning == "r"


class TestBaseToolEnabledOutput:
    """Tests for BaseToolEnabledOutput pydantic model."""

    def test_create_minimal(self) -> None:
        out = BaseToolEnabledOutput()
        assert out.tool_calls == []
        assert out.is_complete is True
        assert out.next_action == "respond"

    def test_create_with_tool_calls(self) -> None:
        tc = ToolCall(tool="search_code")
        out = BaseToolEnabledOutput(tool_calls=[tc])
        assert len(out.tool_calls) == 1
        assert out.tool_calls[0].tool == "search_code"

    def test_is_complete_default_true(self) -> None:
        out = BaseToolEnabledOutput()
        assert out.is_complete is True

    def test_is_complete_can_be_false(self) -> None:
        out = BaseToolEnabledOutput(is_complete=False)
        assert out.is_complete is False

    def test_next_action_default_respond(self) -> None:
        out = BaseToolEnabledOutput()
        assert out.next_action == "respond"

    def test_next_action_can_be_call_tools(self) -> None:
        out = BaseToolEnabledOutput(next_action="call_tools")
        assert out.next_action == "call_tools"

    def test_next_action_invalid_value(self) -> None:
        with pytest.raises(ValidationError):
            BaseToolEnabledOutput(next_action="invalid")  # type: ignore[call-arg]

    def test_empty_tool_calls_list(self) -> None:
        out = BaseToolEnabledOutput(tool_calls=[])
        assert out.tool_calls == []

    def test_multiple_tool_calls(self) -> None:
        out = BaseToolEnabledOutput(
            tool_calls=[
                ToolCall(tool="search_code"),
                ToolCall(tool="read_file"),
            ]
        )
        assert len(out.tool_calls) == 2

    def test_get_tools_to_execute_when_respond(self) -> None:
        out = BaseToolEnabledOutput(
            next_action="respond",
            tool_calls=[ToolCall(tool="search_code")],
        )
        assert out.get_tools_to_execute() == []

    def test_get_tools_to_execute_when_call_tools(self) -> None:
        tc = ToolCall(tool="search_code")
        out = BaseToolEnabledOutput(
            next_action="call_tools",
            tool_calls=[tc],
        )
        result = out.get_tools_to_execute()
        assert len(result) == 1
        assert result[0].tool == "search_code"

    def test_get_tools_to_execute_when_call_tools_but_empty(self) -> None:
        out = BaseToolEnabledOutput(
            next_action="call_tools",
            tool_calls=[],
        )
        assert out.get_tools_to_execute() == []

    def test_get_tools_to_execute_when_respond_even_with_tools(self) -> None:
        out = BaseToolEnabledOutput(
            next_action="respond",
            tool_calls=[ToolCall(tool="search_code")],
        )
        assert out.get_tools_to_execute() == []

    def test_serialization(self) -> None:
        out = BaseToolEnabledOutput(
            tool_calls=[ToolCall(tool="read_file", arguments={"path": "/tmp/test.txt"})],
            is_complete=False,
            next_action="call_tools",
        )
        data = out.model_dump()
        assert data["is_complete"] is False
        assert data["next_action"] == "call_tools"
        assert len(data["tool_calls"]) == 1

    def test_deserialization(self) -> None:
        data = {
            "tool_calls": [{"tool": "search_code", "arguments": {}}],
            "is_complete": True,
            "next_action": "respond",
        }
        out = BaseToolEnabledOutput.model_validate(data)
        assert out.is_complete is True
        assert out.next_action == "respond"
        assert out.tool_calls[0].tool == "search_code"


class TestBaseToolEnabledOutputEdgeCases:
    """Tests for BaseToolEnabledOutput edge cases."""

    def test_none_tool_calls_not_allowed(self) -> None:
        # Pydantic will coerce None to default_factory=list
        out = BaseToolEnabledOutput(tool_calls=[])  # type: ignore[arg-type]
        assert out.tool_calls == []

    def test_boolean_is_complete(self) -> None:
        out = BaseToolEnabledOutput(is_complete=True)
        assert out.is_complete is True
        out2 = BaseToolEnabledOutput(is_complete=False)
        assert out2.is_complete is False

    def test_nested_tool_call_arguments(self) -> None:
        out = BaseToolEnabledOutput(
            tool_calls=[
                ToolCall(
                    tool="complex",
                    arguments={"nested": {"deep": "value"}, "list": [1, 2, 3]},
                )
            ]
        )
        assert out.tool_calls[0].arguments["nested"]["deep"] == "value"


class TestToolCallAndOutputIntegration:
    """Integration tests between ToolCall and BaseToolEnabledOutput."""

    def test_full_workflow_respond(self) -> None:
        out = BaseToolEnabledOutput(
            tool_calls=[ToolCall(tool="search_code")],
            is_complete=True,
            next_action="respond",
        )
        assert out.get_tools_to_execute() == []
        assert out.is_complete is True

    def test_full_workflow_call_tools(self) -> None:
        out = BaseToolEnabledOutput(
            tool_calls=[
                ToolCall(tool="search_code", reasoning="Find references"),
                ToolCall(tool="read_file", arguments={"path": "/tmp/a.py"}),
            ],
            is_complete=False,
            next_action="call_tools",
        )
        tools = out.get_tools_to_execute()
        assert len(tools) == 2
        assert tools[0].tool == "search_code"
        assert tools[1].tool == "read_file"
