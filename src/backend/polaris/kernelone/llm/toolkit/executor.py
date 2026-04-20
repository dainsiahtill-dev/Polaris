"""Backward compatibility alias for executor module.

This file has been split into a directory structure.
KernelFileSystem is the canonical file I/O abstraction used by the executor.
Please update imports to use the new module path:

    from polaris.kernelone.llm.toolkit.executor import AgentAccelToolExecutor

Or import directly from the core module:

    from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor
"""

from __future__ import annotations

# Re-export everything from the new module structure
from polaris.kernelone.llm.toolkit.executor import (
    AgentAccelToolExecutor,
    BudgetExceededError,
    execute_tool_call,
    execute_tool_calls,
)

__all__ = [
    "AgentAccelToolExecutor",
    "BudgetExceededError",
    "execute_tool_call",
    "execute_tool_calls",
]
