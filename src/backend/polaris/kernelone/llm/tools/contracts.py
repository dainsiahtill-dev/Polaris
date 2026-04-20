"""Tool contracts - KernelOne tool-calling runtime types.

DEPRECATED: This module is deprecated. Please import from:
    from polaris.kernelone.llm.contracts.tool import (
        ToolCall,
        ToolCallParserPort,
        ToolExecutionResult,
        ToolExecutorPort,
        ToolPolicy,
        ToolRoundOutcome,
        ToolRoundRequest,
    )

This module is kept for backward compatibility only.
"""

from __future__ import annotations

# Re-export from canonical location for backward compatibility
from polaris.kernelone.llm.contracts.tool import (
    ToolCall,
    ToolCallParserPort,
    ToolExecutionResult,
    ToolExecutorPort,
    ToolPolicy,
    ToolRoundOutcome,
    ToolRoundRequest,
)

__all__ = [
    "ToolCall",
    "ToolCallParserPort",
    "ToolExecutionResult",
    "ToolExecutorPort",
    "ToolPolicy",
    "ToolRoundOutcome",
    "ToolRoundRequest",
]
