"""KernelOne LLM contracts.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

This package contains the canonical KernelOne LLM core and tool contracts.

For core request/response contracts, import from this package or from the
domain facade closest to your layer:
    from polaris.kernelone.llm.contracts import AIRequest, AIResponse
    from polaris.kernelone.llm.engine.contracts import AIRequest
    from polaris.kernelone.llm.toolkit.contracts import ProviderPort

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

from polaris.kernelone.llm.contracts.core import (
    AIRequest,
    AIResponse,
    CompressionResult,
    ModelSpec,
    ProviderFormatter,
    StreamEventType,
    TaskType,
    TokenBudgetDecision,
    Usage,
)
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
    "AIRequest",
    "AIResponse",
    "CellToolExecutorPort",
    "CompressionResult",
    "ModelSpec",
    "ProviderFormatter",
    "StreamEventType",
    "TaskType",
    "TokenBudgetDecision",
    "ToolCall",
    "ToolCallParserPort",
    "ToolExecutionResult",
    "ToolExecutorPort",
    "ToolPolicy",
    "ToolRoundOutcome",
    "ToolRoundRequest",
    "Usage",
]
