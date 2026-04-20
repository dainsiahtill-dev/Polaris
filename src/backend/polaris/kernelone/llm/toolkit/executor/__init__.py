"""Executor module - Split from executor.py (1838 lines).

This module provides the tool execution infrastructure for the LLM toolkit.
The original executor.py has been split into the following structure:

executor/
    __init__.py          # Backward compatibility - re-exports from core
    core.py              # AgentAccelToolExecutor main class
    runtime.py           # KernelToolCallingRuntime and build_tool_feedback
    handlers/
        __init__.py      # Handler registry
        filesystem.py     # read_file, write_file, edit_file handlers
        search.py        # search_code, grep, ripgrep handlers
        command.py       # execute_command handler
        session_memory.py # search_memory, read_artifact, read_episode, get_state
        navigation.py    # glob, list_directory, file_exists handlers
    utils.py             # Shared utilities (path helpers)
"""

from __future__ import annotations

import subprocess  # noqa: F401 - backward compatibility for test monkeypatching

# Re-export BudgetExceededError from unified exceptions for backward compatibility
from polaris.kernelone.llm.exceptions import BudgetExceededError

# Re-export all public symbols from the new module structure
# for backward compatibility
from polaris.kernelone.llm.toolkit.executor.core import (
    CODE_INTELLIGENCE_AVAILABLE,
    AgentAccelToolExecutor,
    execute_tool_call,
    execute_tool_calls,
)
from polaris.kernelone.llm.toolkit.executor.runtime import (
    KernelToolCallingRuntime,
    build_tool_feedback,
)

__all__ = [
    "AgentAccelToolExecutor",
    "BudgetExceededError",
    "CODE_INTELLIGENCE_AVAILABLE",
    "KernelToolCallingRuntime",
    "build_tool_feedback",
    "execute_tool_call",
    "execute_tool_calls",
]
