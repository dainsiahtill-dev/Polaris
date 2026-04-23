"""Tool Executor Adapters - Bridge Cells layer to KernelOne canonical interface.

This module provides adapters that bridge between:
- CellToolExecutorPort: Cells-layer interface (execute(tool_name, args))
- ToolExecutorPort: KernelOne canonical interface (execute_call(workspace, tool_call))

Architecture per AGENTS.md:
    Cell -> effect port -> kernelone contract -> infrastructure adapter

These adapters enable Cells to use their natural interface while
routing through KernelOne's canonical contracts.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from polaris.kernelone.llm.contracts.tool import (
    CellToolExecutorPort,
    ToolCall,
    ToolExecutionResult,
    ToolExecutorPort,
)

if TYPE_CHECKING:
    pass


class CellToKernelExecutorAdapter(ToolExecutorPort):
    """Adapter: CellToolExecutorPort (Cells) -> ToolExecutorPort (KernelOne).

    Allows Cells-layer executors to be used with KernelOne runtime.

    Example:
        >>> from polaris.cells.roles.kernel.internal.testing import FakeToolExecutor
        >>> fake = FakeToolExecutor()
        >>> adapter = CellToKernelExecutorAdapter(fake, workspace=".")
        >>> # Now adapter can be used with KernelToolCallingRuntime
        >>> result = adapter.execute_call(workspace=".", tool_call=...)
    """

    def __init__(
        self,
        cell_executor: CellToolExecutorPort,
        workspace: str = ".",
    ) -> None:
        """Initialize adapter with Cells-layer executor.

        Args:
            cell_executor: Cells-layer tool executor
            workspace: Default workspace path
        """
        self._cell_executor = cell_executor
        self._workspace = workspace

    def execute_call(
        self,
        *,
        workspace: str,
        tool_call: ToolCall,
    ) -> ToolExecutionResult:
        """Execute via CellToolExecutorPort interface.

        Args:
            workspace: Workspace path
            tool_call: Tool call to execute

        Returns:
            ToolExecutionResult
        """
        import asyncio

        # Create context with workspace
        context: dict[str, Any] = {"workspace": workspace}

        # Cell executor uses async interface, we need to handle it
        coro = self._cell_executor.execute(
            tool_name=tool_call.name,
            args=dict(tool_call.arguments or {}),
            context=context,
        )

        # Check if we're in an async context
        try:
            asyncio.get_running_loop()
            # We're in async context, need to run the coroutine
            # This is a synchronous method, so we need to block
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                future = pool.submit(asyncio.run, coro)
                result_dict = future.result()
        except RuntimeError:
            # No running loop, we can use asyncio.run directly
            result_dict = asyncio.run(coro)

        # Convert dict result to ToolExecutionResult
        success = bool(result_dict.get("success", result_dict.get("ok", True)))
        result_data: dict[str, Any] = {}
        error = ""
        blocked = False

        if isinstance(result_dict, dict):
            result_value = result_dict.get("result", result_dict.get("data"))
            if isinstance(result_value, dict):
                result_data = dict(result_value)
            elif result_value is not None:
                result_data = {"value": result_value}
            error = str(result_dict.get("error") or "")
            blocked = "blocked" in error.lower() or bool(result_dict.get("authorized") is False)

        return ToolExecutionResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            success=success,
            result=result_data,
            error=error,
            blocked=blocked,
        )


class KernelToCellExecutorAdapter(CellToolExecutorPort):
    """Adapter: ToolExecutorPort (KernelOne) -> CellToolExecutorPort (Cells).

    Allows KernelOne executors to be used with Cells-layer code.

    Example:
        >>> from polaris.infrastructure.llm.tools import LLMToolkitExecutorAdapter
        >>> kernel_adapter = LLMToolkitExecutorAdapter()
        >>> cell_adapter = KernelToCellExecutorAdapter(kernel_adapter, workspace=".")
        >>> # Now cell_adapter.execute(tool_name, args) works
    """

    def __init__(
        self,
        kernel_executor: ToolExecutorPort,
        workspace: str = ".",
    ) -> None:
        """Initialize adapter with KernelOne executor.

        Args:
            kernel_executor: KernelOne tool executor
            workspace: Default workspace path
        """
        self._kernel_executor = kernel_executor
        self._workspace = workspace

    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute via ToolExecutorPort interface.

        Args:
            tool_name: Tool name
            args: Tool arguments
            context: Optional context (used for workspace override)

        Returns:
            Result dictionary
        """
        # Determine workspace from context or use default
        workspace = str(context.get("workspace", self._workspace) if context else self._workspace)

        # Create ToolCall
        tool_call = ToolCall(
            id=str(uuid.uuid4()),
            name=str(tool_name or "").strip().lower(),
            arguments=dict(args or {}),
            source="cell_adapter",
        )

        # Execute via KernelOne interface
        result = self._kernel_executor.execute_call(
            workspace=workspace,
            tool_call=tool_call,
        )

        # Convert to dict format
        return {
            "success": result.success,
            "tool": result.name,
            "result": dict(result.result) if isinstance(result.result, dict) else {"value": result.result},
            "error": result.error,
            "blocked": result.blocked,
            "call_id": result.tool_call_id,
        }


__all__ = [
    "CellToKernelExecutorAdapter",
    "KernelToCellExecutorAdapter",
]
