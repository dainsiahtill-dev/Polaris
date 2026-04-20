"""Tests for TurnEngine pre-execution policy gate.

Verifies that RoleToolGateway.check_tool_permission() is enforced inside
TurnEngine._execute_single_tool() BEFORE calling the kernel executor,
closing the stream-transport bypass where injected_tool_executor skipped
KernelToolExecutor's permission check entirely.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
from polaris.cells.roles.kernel.internal.turn_engine.engine import TurnEngine


def _make_mock_profile(
    blacklist: list[str] | None = None,
    whitelist: list[str] | None = None,
    role_id: str = "director",
) -> MagicMock:
    """Factory: minimal mock RoleProfile with policy."""
    profile = MagicMock()
    profile.role_id = role_id
    profile.policy = MagicMock()
    profile.policy.blacklist = blacklist or []
    profile.policy.whitelist = whitelist or []
    profile.policy.allow_code_write = True
    profile.policy.allow_command_execution = True
    profile.policy.allow_file_delete = True
    profile.policy.max_tool_calls_per_turn = 10
    profile.policy._is_code_write_tool = MagicMock(return_value=False)
    profile.policy._is_command_execution_tool = MagicMock(return_value=False)
    profile.policy._is_file_delete_tool = MagicMock(return_value=False)
    profile.policy._validate_scope = MagicMock(return_value=True)
    return profile


class TestPolicyInterceptor:
    """Pre-execution policy gate tests."""

    @pytest.mark.asyncio
    async def test_blocks_forbidden_tool_when_injected_executor_set(self) -> None:
        """FORBIDDEN TOOL MUST be blocked at pre-check even when injected executor is configured.

        This is the primary regression test for the stream-transport bypass:
        kernel._injected_tool_executor was skipping KernelToolExecutor's permission check.
        The pre-execution gate in TurnEngine._execute_single_tool() closes this gap.
        """
        kernel = RoleExecutionKernel(workspace=".")
        # Simulate stream transport: injected executor bypasses KernelToolExecutor
        kernel._injected_tool_executor = MagicMock()
        kernel._injected_tool_executor.execute_single = AsyncMock(
            return_value={"success": True, "result": {"output": "should not reach here"}}
        )

        profile = _make_mock_profile(blacklist=["execute_command", "run_shell"])
        request = MagicMock()
        request.metadata = {}

        engine = TurnEngine(kernel=kernel)

        call = {"tool": "execute_command", "args": {"command": "echo $API_KEY"}}
        result = await engine._execute_single_tool(profile=profile, request=request, call=call)

        # Pre-check must block the tool
        assert result["success"] is False, "Forbidden tool should be blocked"
        assert result["authorized"] is False, "Tool should be marked unauthorized"
        assert result["policy"] == "ToolPolicy", "Policy layer should be ToolPolicy"
        assert "TOOL_BLOCKED" in result["error"], f"Error should contain TOOL_BLOCKED marker: {result['error']}"
        assert "execute_command" in result["error"], f"Error should mention tool name: {result['error']}"

        # Injected executor must NEVER be called
        kernel._injected_tool_executor.execute_single.assert_not_called()

    @pytest.mark.asyncio
    async def test_blocked_result_includes_policy_name(self) -> None:
        """Blocked result must include policy layer name for traceability."""
        kernel = RoleExecutionKernel(workspace=".")
        kernel._injected_tool_executor = MagicMock()

        profile = _make_mock_profile(blacklist=["delete_file"])
        request = MagicMock()
        request.metadata = {}

        engine = TurnEngine(kernel=kernel)

        call = {"tool": "delete_file", "args": {"path": "important.py"}}
        result = await engine._execute_single_tool(profile=profile, request=request, call=call)

        assert result["policy"] == "ToolPolicy"
        assert "TOOL_BLOCKED" in result["error"]
        kernel._injected_tool_executor.execute_single.assert_not_called()

    @pytest.mark.asyncio
    async def test_fallback_kerneltool_executor_still_works(self) -> None:
        """When no injected executor is set, KernelToolExecutor path still works."""
        kernel = RoleExecutionKernel(workspace=".")
        # Ensure no injected executor (non-stream transport)
        kernel._injected_tool_executor = None

        profile = _make_mock_profile(blacklist=["execute_command"])
        request = MagicMock()
        request.metadata = {}

        engine = TurnEngine(kernel=kernel)

        call = {"tool": "execute_command", "args": {"command": "ls"}}
        result = await engine._execute_single_tool(profile=profile, request=request, call=call)

        # Should be blocked at pre-check (redundant with KernelToolExecutor but not harmful)
        assert result["success"] is False
        assert "TOOL_BLOCKED" in result["error"]
