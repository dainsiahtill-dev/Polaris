"""Tests for polaris.cells.director.execution.public.tools.

Verifies that the public tools facade correctly re-exports KernelOne symbols.
"""

from __future__ import annotations

from polaris.cells.director.execution.public import tools as public_tools


class TestPublicToolsReExports:
    """Tests that public tools module re-exports expected symbols."""

    def test_has_allowed_execution_commands(self) -> None:
        assert hasattr(public_tools, "ALLOWED_EXECUTION_COMMANDS")
        assert isinstance(public_tools.ALLOWED_EXECUTION_COMMANDS, frozenset)

    def test_has_build_tool_cli_args(self) -> None:
        assert hasattr(public_tools, "build_tool_cli_args")
        assert callable(public_tools.build_tool_cli_args)

    def test_has_is_command_allowed(self) -> None:
        assert hasattr(public_tools, "is_command_allowed")
        assert callable(public_tools.is_command_allowed)

    def test_has_is_command_blocked(self) -> None:
        assert hasattr(public_tools, "is_command_blocked")
        assert callable(public_tools.is_command_blocked)

    def test_is_command_allowed_blocks_dangerous(self) -> None:
        assert public_tools.is_command_blocked("rm -rf /") is True

    def test_is_command_allowed_allows_safe(self) -> None:
        assert public_tools.is_command_allowed("pytest") is True

    def test_build_tool_cli_args_returns_list(self) -> None:
        result = public_tools.build_tool_cli_args("pytest", {"-q": None, "file": "test.py"})
        assert isinstance(result, list)
