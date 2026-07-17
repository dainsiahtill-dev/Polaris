"""Package entrypoint for KernelOne LLM toolkit executor components.

This module provides the tool execution infrastructure for the LLM toolkit.
The public package root exports the stable executor APIs from these modules:

executor/
    __init__.py          # Package-root public executor exports
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

import subprocess

# Public executor boundary error surfaced by AgentAccelToolExecutor.
from polaris.kernelone.llm.exceptions import BudgetExceededError
from polaris.kernelone.llm.toolkit.executor.command_capability import (
    CommandCapabilityDenialReasonV1,
    CommandCapabilityValidationInputV1,
    CommandCapabilityValidationResultV1,
    CommandCapabilityValidationStatusV1,
    validate_command_capability,
)

# Public executor APIs exposed at the package root.
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
    "CODE_INTELLIGENCE_AVAILABLE",
    "AgentAccelToolExecutor",
    "BudgetExceededError",
    "CommandCapabilityDenialReasonV1",
    "CommandCapabilityValidationInputV1",
    "CommandCapabilityValidationResultV1",
    "CommandCapabilityValidationStatusV1",
    "KernelToolCallingRuntime",
    "build_tool_feedback",
    "execute_tool_call",
    "execute_tool_calls",
    "validate_command_capability",
]
