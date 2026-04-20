"""Fake Tool Executor for testing.

Provides a programmable fake tool executor that satisfies the
CellToolExecutorPort interface from KernelOne.

# -*- coding: utf-8 -*-
UTF-8 encoding verified: All text uses UTF-8

UNIFIED INTERFACE (P0-010):
    This module now uses polaris.kernelone.llm.contracts.CellToolExecutorPort
    as the canonical interface for Cells-layer tool execution.

    Previously defined a duplicate ToolExecutorProtocol here.
    Now imports from KernelOne for consistency.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.internal.testing.exceptions import (
    FakeToolExecutionError,
    FakeToolNotFoundError,
)

# Import unified interface from KernelOne (P0-010 fix)

if TYPE_CHECKING:
    from collections.abc import Callable

# CellToolExecutorPort is now the canonical interface imported from KernelOne.
# FakeToolExecutor implements it directly (see class definition below).


@dataclass
class ToolCallRecord:
    """Record of a single tool call for verification.

    Attributes:
        call_index: Sequential call number (0-indexed).
        tool_name: Name of the tool that was called.
        args: Arguments passed to the tool.
        result: Result returned by the tool.
        execution_time_ms: Simulated execution time in milliseconds.
    """

    call_index: int
    tool_name: str
    args: dict[str, Any]
    result: dict[str, Any]
    execution_time_ms: float = 0.0


@dataclass
class ToolRegistration:
    """Internal registration for a fake tool."""

    handler: Callable[[dict[str, Any]], dict[str, Any]]
    requires_approval: bool = False
    delay_ms: float = 0.0


class FakeToolExecutor:
    """Programmable fake tool executor for testing.

    This class implements CellToolExecutorPort (async interface) from KernelOne
    and allows tests to register fake tool implementations that return
    pre-programmed results. It records all calls for later verification.

    P0-010 Unified Interface:
        Implements async execute() method per CellToolExecutorPort contract.
        Also provides synchronous execute_sync() for backward compatibility.

    Features:
        - Register tools with static results or dynamic handlers
        - Approval requirement simulation
        - Call recording and verification
        - Exception injection for specific tools
        - Execution delay simulation

    Example:
        >>> executor = FakeToolExecutor()
        >>> executor.register_tool(
        ...     "read_file",
        ...     lambda args: {"success": True, "content": "file content"}
        ... )
        >>> executor.register_tool_with_result(
        ...     "write_file",
        ...     {"success": True, "bytes_written": 100},
        ...     requires_approval=True
        ... )
        >>>
        >>> # Use in test (async)
        >>> result = await executor.execute("read_file", {"path": "test.py"})
        >>> assert result["success"] is True
        >>> assert executor.call_count == 1
        >>>
        >>> # Use in test (sync, backward compatible)
        >>> result = executor.execute_sync("read_file", {"path": "test.py"})
    """

    def __init__(self) -> None:
        """Initialize the fake tool executor."""
        self._tools: dict[str, ToolRegistration] = {}
        self._call_records: list[ToolCallRecord] = []
        self._call_count: int = 0
        self._global_approval_policy: Callable[[str, dict[str, Any]], bool] | None = None
        self._default_result: dict[str, Any] | None = None

    @property
    def call_count(self) -> int:
        """Number of calls made to this executor."""
        return self._call_count

    @property
    def call_records(self) -> list[ToolCallRecord]:
        """Get a copy of all call records."""
        return copy.deepcopy(self._call_records)

    @property
    def registered_tools(self) -> list[str]:
        """List of registered tool names."""
        return list(self._tools.keys())

    def register_tool(
        self,
        tool_name: str,
        handler: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        requires_approval: bool = False,
        delay_ms: float = 0.0,
    ) -> FakeToolExecutor:
        """Register a tool with a custom handler function.

        Args:
            tool_name: Name of the tool.
            handler: Function that takes args dict and returns result dict.
            requires_approval: Whether this tool requires user approval.
            delay_ms: Simulated execution delay in milliseconds.

        Returns:
            Self for method chaining.
        """
        self._tools[tool_name] = ToolRegistration(
            handler=handler,
            requires_approval=requires_approval,
            delay_ms=delay_ms,
        )
        return self

    def register_tool_with_result(
        self,
        tool_name: str,
        result: dict[str, Any],
        *,
        requires_approval: bool = False,
        delay_ms: float = 0.0,
    ) -> FakeToolExecutor:
        """Register a tool that returns a static result.

        Args:
            tool_name: Name of the tool.
            result: Static result dictionary to return.
            requires_approval: Whether this tool requires user approval.
            delay_ms: Simulated execution delay in milliseconds.

        Returns:
            Self for method chaining.
        """
        return self.register_tool(
            tool_name,
            lambda args: dict(result),
            requires_approval=requires_approval,
            delay_ms=delay_ms,
        )

    def register_tools_from_dict(
        self,
        tools: dict[str, dict[str, Any]],
        *,
        default_requires_approval: bool = False,
    ) -> FakeToolExecutor:
        """Register multiple tools from a dictionary.

        Args:
            tools: Dictionary mapping tool names to result dictionaries.
            default_requires_approval: Default approval requirement for all tools.

        Returns:
            Self for method chaining.
        """
        for tool_name, result in tools.items():
            self.register_tool_with_result(
                tool_name,
                result,
                requires_approval=default_requires_approval,
            )
        return self

    def set_global_approval_policy(
        self,
        policy: Callable[[str, dict[str, Any]], bool],
    ) -> FakeToolExecutor:
        """Set a global policy function for approval decisions.

        The policy function receives (tool_name, args) and returns True if approval
        is required. This overrides individual tool settings.

        Args:
            policy: Function that determines if approval is required.

        Returns:
            Self for method chaining.
        """
        self._global_approval_policy = policy
        return self

    def set_default_result(self, result: dict[str, Any]) -> FakeToolExecutor:
        """Set a default result for unregistered tools.

        When set, calling an unregistered tool will return this result instead
        of raising FakeToolNotFoundError.

        Args:
            result: Default result dictionary.

        Returns:
            Self for method chaining.
        """
        self._default_result = dict(result)
        return self

    def reset(self) -> FakeToolExecutor:
        """Reset the executor state, clearing all registrations and records.

        Returns:
            Self for method chaining.
        """
        self._tools.clear()
        self._call_records.clear()
        self._call_count = 0
        self._global_approval_policy = None
        self._default_result = None
        return self

    def execute_sync(self, tool_name: str, args: dict) -> dict[str, Any]:
        """Execute a tool by name with given arguments (synchronous version).

        Args:
            tool_name: Name of the tool to execute.
            args: Dictionary of arguments to pass to the tool.

        Returns:
            Result dictionary from the registered handler.

        Raises:
            FakeToolNotFoundError: If tool is not registered and no default result is set.
            FakeToolExecutionError: If the tool handler raises an exception.
        """
        registration = self._tools.get(tool_name)

        if registration is None:
            if self._default_result is not None:
                result = dict(self._default_result)
                result["tool"] = tool_name
                self._record_call(tool_name, args, result)
                return result
            raise FakeToolNotFoundError(tool_name)

        try:
            result = registration.handler(dict(args))
            # Ensure result is a dict
            if not isinstance(result, dict):
                result = {"success": True, "result": result}
        except (RuntimeError, ValueError) as e:
            raise FakeToolExecutionError(tool_name, e) from e

        self._record_call(tool_name, args, result, delay_ms=registration.delay_ms)
        return result

    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a tool by name with given arguments (async, per CellToolExecutorPort).

        This is the unified interface method that satisfies CellToolExecutorPort
        from KernelOne.

        Args:
            tool_name: Name of the tool to execute.
            args: Dictionary of arguments to pass to the tool.
            context: Optional execution context (ignored in fake, for interface compliance).

        Returns:
            Result dictionary from the registered handler.

        Raises:
            FakeToolNotFoundError: If tool is not registered and no default result is set.
            FakeToolExecutionError: If the tool handler raises an exception.
        """
        # Delegate to sync implementation (fake tools don't need real async)
        return self.execute_sync(tool_name, args)

    def execute_tool(self, tool_name: str, args: dict) -> dict[str, Any]:
        """Alias for execute_sync() for compatibility with RoleToolGateway interface."""
        return self.execute_sync(tool_name, args)

    def requires_approval(
        self,
        tool_name: str,
        args: dict | None = None,
        state: Any | None = None,
    ) -> bool:
        """Check if a tool call requires user approval.

        Args:
            tool_name: Name of the tool to check.
            args: Optional tool arguments for context-aware checks.
            state: Optional execution state for policy evaluation.

        Returns:
            True if approval is required, False otherwise.
        """
        # Global policy takes precedence
        if self._global_approval_policy is not None:
            return self._global_approval_policy(tool_name, args or {})

        registration = self._tools.get(tool_name)
        if registration is None:
            return False

        return registration.requires_approval

    def check_tool_permission(self, tool_name: str) -> tuple[bool, str]:
        """Check tool permission and return reason.

        Compatible with RoleToolGateway.check_tool_permission interface.

        Returns:
            Tuple of (allowed, reason).
        """
        if tool_name not in self._tools:
            return False, f"Tool '{tool_name}' not found"

        if self.requires_approval(tool_name):
            return False, f"Tool '{tool_name}' requires approval"

        return True, ""

    def reset_execution_count(self) -> None:
        """Reset execution count (compatible with RoleToolGateway interface)."""
        self._call_count = 0

    def close(self) -> None:
        """Close the executor (no-op for fake)."""
        pass

    def _record_call(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
        delay_ms: float = 0.0,
    ) -> None:
        """Record a tool call."""
        record = ToolCallRecord(
            call_index=self._call_count,
            tool_name=tool_name,
            args=dict(args),
            result=dict(result),
            execution_time_ms=delay_ms,
        )
        self._call_records.append(record)
        self._call_count += 1

    def assert_call_count(self, expected: int) -> None:
        """Assert that the call count matches expected.

        Args:
            expected: Expected number of calls.

        Raises:
            AssertionError: If call count doesn't match.
        """
        if self._call_count != expected:
            raise AssertionError(f"Expected {expected} tool calls, but got {self._call_count}")

    def assert_called(self, tool_name: str, times: int | None = None) -> None:
        """Assert that a specific tool was called.

        Args:
            tool_name: Name of the tool to check.
            times: Optional expected number of calls. If None, just checks at least once.

        Raises:
            AssertionError: If tool wasn't called as expected.
        """
        calls_for_tool = [r for r in self._call_records if r.tool_name == tool_name]

        if not calls_for_tool:
            raise AssertionError(f"Tool '{tool_name}' was not called")

        if times is not None and len(calls_for_tool) != times:
            raise AssertionError(f"Tool '{tool_name}' was called {len(calls_for_tool)} times, expected {times}")

    def assert_called_with(
        self,
        tool_name: str,
        call_index: int | None = None,
        **expected_args: Any,
    ) -> None:
        """Assert that a tool was called with specific arguments.

        Args:
            tool_name: Name of the tool to check.
            call_index: Optional specific call index to check. If None, checks all calls.
            **expected_args: Expected argument values.

        Raises:
            AssertionError: If no matching call found.
        """
        calls_for_tool = [r for r in self._call_records if r.tool_name == tool_name]

        if not calls_for_tool:
            raise AssertionError(f"Tool '{tool_name}' was not called")

        if call_index is not None:
            if call_index >= len(calls_for_tool):
                raise AssertionError(
                    f"Tool '{tool_name}' was only called {len(calls_for_tool)} times, "
                    f"but checked for call index {call_index}"
                )
            calls_to_check = [calls_for_tool[call_index]]
        else:
            calls_to_check = calls_for_tool

        for record in calls_to_check:
            for key, expected_value in expected_args.items():
                actual_value = record.args.get(key)
                if actual_value != expected_value:
                    raise AssertionError(
                        f"Tool '{tool_name}' call {record.call_index}: "
                        f"Expected args[{key!r}]={expected_value!r}, got {actual_value!r}"
                    )

    def get_calls_for_tool(self, tool_name: str) -> list[ToolCallRecord]:
        """Get all call records for a specific tool.

        Args:
            tool_name: Name of the tool.

        Returns:
            List of call records for that tool.
        """
        return [r for r in self._call_records if r.tool_name == tool_name]


__all__ = [
    "FakeToolExecutionError",
    "FakeToolExecutor",
    "FakeToolNotFoundError",
    "ToolCallRecord",
    # P0-010: ToolExecutorProtocol removed, use CellToolExecutorPort from KernelOne
]
