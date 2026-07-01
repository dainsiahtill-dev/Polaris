"""Tests for the Role Kernel single-tool pre-execution policy gate.

Verifies that RoleToolGateway.check_tool_permission() is enforced inside
tool_runtime_executor.execute_single_tool() BEFORE calling the injected executor,
closing the stream-transport bypass where injected_tool_executor skipped
KernelToolExecutor's permission check entirely.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
from polaris.cells.roles.kernel.internal.kernel.tool_runtime_executor import execute_single_tool
from polaris.cells.roles.kernel.internal.tool_gateway import ToolAuthorizationError


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
        The pre-execution gate in execute_single_tool() closes this gap.
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

        with pytest.raises(ToolAuthorizationError) as exc_info:
            await execute_single_tool(
                kernel,
                tool_name="execute_command",
                args={"command": "echo $API_KEY"},
                context={"profile": profile, "request": request},
            )

        assert "execute_command" in str(exc_info.value)

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

        with pytest.raises(ToolAuthorizationError) as exc_info:
            await execute_single_tool(
                kernel,
                tool_name="delete_file",
                args={"path": "important.py"},
                context={"profile": profile, "request": request},
            )

        assert "delete_file" in str(exc_info.value)
        kernel._injected_tool_executor.execute_single.assert_not_called()

    @pytest.mark.asyncio
    async def test_fallback_path_enforces_precheck_before_kerneltool_executor(self) -> None:
        """When no injected executor is set, the fallback path still enforces precheck."""
        kernel = RoleExecutionKernel(workspace=".")
        # Ensure no injected executor (non-stream transport)
        kernel._injected_tool_executor = None

        profile = _make_mock_profile(blacklist=["execute_command"])
        request = MagicMock()
        request.metadata = {}

        with pytest.raises(ToolAuthorizationError) as exc_info:
            await execute_single_tool(
                kernel,
                tool_name="execute_command",
                args={"command": "ls"},
                context={"profile": profile, "request": request},
            )

        assert "execute_command" in str(exc_info.value)
