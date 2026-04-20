"""Tool runtime - dual execution lanes.

UTF-8: all text literals in this file use UTF-8 encoding.
Blueprint: §9 ToolRuntime

Package structure:
- tools/contracts.py: ExecutionLane, ToolStatus, ToolExecutionResult
- tools/registry.py: ToolRegistry
- tools/materializer.py: ToolMaterializer
- runtime/execution_lane_selector.py: ExecutionLaneSelector
- runtime/direct_executor.py: DirectExecutor
- runtime/programmatic_executor.py: ProgrammaticExecutor
- runtime/tool_runtime.py: ToolRuntime (统一入口)
"""

from polaris.kernelone.single_agent.runtime.tool_runtime import ToolRuntime

__all__ = ["ToolRuntime"]
