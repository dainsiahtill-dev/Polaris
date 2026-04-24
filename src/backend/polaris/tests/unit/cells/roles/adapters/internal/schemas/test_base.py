"""Tests for polaris.cells.roles.adapters.internal.schemas.base."""

from __future__ import annotations

from polaris.cells.roles.adapters.internal.schemas.base import BaseToolEnabledOutput, ToolCall


class TestToolCall:
    def test_defaults(self) -> None:
        tc = ToolCall(tool="read_file", arguments={"path": "/tmp/f.py"})
        assert tc.tool == "read_file"
        assert tc.arguments == {"path": "/tmp/f.py"}
        assert tc.reasoning is None


class TestBaseToolEnabledOutput:
    def test_defaults(self) -> None:
        out = BaseToolEnabledOutput()
        assert out.tool_calls == []
        assert out.is_complete is True
        assert out.next_action == "respond"

    def test_get_tools_to_execute_when_respond(self) -> None:
        out = BaseToolEnabledOutput(next_action="respond", tool_calls=[ToolCall(tool="t", arguments={})])
        assert out.get_tools_to_execute() == []

    def test_get_tools_to_execute_when_call_tools(self) -> None:
        tc = ToolCall(tool="t", arguments={})
        out = BaseToolEnabledOutput(next_action="call_tools", tool_calls=[tc])
        assert out.get_tools_to_execute() == [tc]

    def test_get_tools_to_execute_when_call_tools_but_empty(self) -> None:
        out = BaseToolEnabledOutput(next_action="call_tools", tool_calls=[])
        assert out.get_tools_to_execute() == []
