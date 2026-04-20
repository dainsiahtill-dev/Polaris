"""Testing infrastructure for `roles.kernel` cell.

This module provides fake implementations and test harnesses for unit testing
kernel components without requiring monkeypatching or external dependencies.

# -*- coding: utf-8 -*-
UTF-8 encoding verified: All text uses UTF-8

P0-010 Unified Interface:
    ToolExecutorProtocol is now CellToolExecutorPort from KernelOne.
    Import from: polaris.kernelone.llm.contracts import CellToolExecutorPort

Example:
    >>> from polaris.cells.roles.kernel.internal.testing import KernelTestHarness
    >>> kernel = (
    ...     KernelTestHarness()
    ...     .with_fake_llm(responses=[{"content": "Hello"}])
    ...     .with_fake_tools({"read_file": {"success": True, "content": "file content"}})
    ...     .build()
    ... )
"""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.testing.exceptions import (
    FakeLLMExhaustedError,
    FakeToolExecutionError,
    FakeToolNotFoundError,
    HarnessConfigurationError,
)
from polaris.cells.roles.kernel.internal.testing.fake_context import (
    ContextAssemblerProtocol,
    FakeContextAssembler,
)
from polaris.cells.roles.kernel.internal.testing.fake_llm import (
    FakeLLMInvoker,
    LLMInvokerProtocol,
    LLMResponseBuilder,
)
from polaris.cells.roles.kernel.internal.testing.fake_tools import (
    FakeToolExecutor,
    ToolCallRecord,
)
from polaris.cells.roles.kernel.internal.testing.harness import (
    HarnessConfig,
    KernelTestHarness,
)

# Import unified interface from KernelOne (P0-010 fix)
from polaris.kernelone.llm.contracts.tool import CellToolExecutorPort

# Backward compatibility alias: ToolExecutorProtocol -> CellToolExecutorPort
# (P0-010: Deprecated, will be removed in future. Use CellToolExecutorPort.)
ToolExecutorProtocol = CellToolExecutorPort

__all__ = [
    "CellToolExecutorPort",
    "ContextAssemblerProtocol",
    # Fake Context
    "FakeContextAssembler",
    # Exceptions
    "FakeLLMExhaustedError",
    # Fake LLM
    "FakeLLMInvoker",
    "FakeToolExecutionError",
    # Fake Tools
    "FakeToolExecutor",
    "FakeToolNotFoundError",
    "HarnessConfig",
    "HarnessConfigurationError",
    # Harness
    "KernelTestHarness",
    "LLMInvokerProtocol",
    "LLMResponseBuilder",
    "ToolCallRecord",
    # Backward compatibility (deprecated)
    "ToolExecutorProtocol",
]
