"""KernelOne LLM contracts.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

This subdirectory contains the provider-level and tool contracts.

For tool contracts, import from:
    from polaris.kernelone.llm.contracts.tool import (
        CellToolExecutorPort,  # Cells-layer unified interface
        ToolCall,
        ToolCallParserPort,
        ToolExecutionResult,
        ToolExecutorPort,      # KernelOne canonical interface
        ToolPolicy,
        ToolRoundOutcome,
        ToolRoundRequest,
    )
"""

from __future__ import annotations

# Re-export tool contracts from tool.py
from polaris.kernelone.llm.contracts.tool import (
    CellToolExecutorPort,
    ToolCall,
    ToolCallParserPort,
    ToolExecutionResult,
    ToolExecutorPort,
    ToolPolicy,
    ToolRoundOutcome,
    ToolRoundRequest,
)

__all__ = [
    "CellToolExecutorPort",
    "ToolCall",
    "ToolCallParserPort",
    "ToolExecutionResult",
    "ToolExecutorPort",
    "ToolPolicy",
    "ToolRoundOutcome",
    "ToolRoundRequest",
]
