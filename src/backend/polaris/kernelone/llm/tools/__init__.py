"""KernelOne LLM tool-calling runtime exports.

DEPRECATED: This module is deprecated. Please migrate to the following:

    # Tool contracts (unified ToolCall)
    from polaris.kernelone.llm.contracts import (
        ToolCall,
        ToolCallParserPort,
        ToolExecutionResult,
        ToolExecutorPort,
        ToolPolicy,
        ToolRoundOutcome,
        ToolRoundRequest,
    )

    # Executor
    from polaris.kernelone.llm.toolkit import (
        AgentAccelToolExecutor,
        KernelToolCallingRuntime,
        build_tool_feedback,
    )

    # Message normalization (if needed)
    from polaris.kernelone.llm.tools.message_normalizer import (
        MessageNormalizer,
        MessageNormalizerConfig,
        NormalizationResult,
        normalize_messages,
        validate_conversation_structure,
        validate_message_structure,
        auto_fix_conversation,
    )

This module is kept for backward compatibility only.
"""

from __future__ import annotations

import warnings

# Emit deprecation warning for direct imports
warnings.warn(
    "polaris.kernelone.llm.tools is deprecated, use polaris.kernelone.llm.contracts and polaris.kernelone.llm.toolkit instead",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export from new canonical locations for backward compatibility
from polaris.kernelone.llm.contracts import (  # noqa: E402
    ToolCall,
    ToolCallParserPort,
    ToolExecutionResult,
    ToolExecutorPort,
    ToolPolicy,
    ToolRoundOutcome,
    ToolRoundRequest,
)
from polaris.kernelone.llm.toolkit import (  # noqa: E402
    KernelToolCallingRuntime,
    build_tool_feedback,
)
from polaris.kernelone.llm.tools.message_normalizer import (  # noqa: E402
    MessageNormalizer,
    MessageNormalizerConfig,
    NormalizationResult,
    auto_fix_conversation,
    normalize_messages,
    validate_conversation_structure,
    validate_message_structure,
)
from polaris.kernelone.llm.tools.normalizer import (  # noqa: E402
    normalize_tool_arguments,
    normalize_tool_calls,
    normalize_tool_name,
)
from polaris.kernelone.llm.tools.schema_validator import (  # noqa: E402
    SchemaValidationResult,
    SchemaValidator,
    ToolSchema,
    export_all_tools_to_json_schema,
    export_tool_to_json_schema,
    validate_all_tool_schemas,
    validate_tool_schema,
)

# Note: Message normalizer is kept in tools/ as it's domain-specific
# (conversation messages, not tool calls)

__all__ = [
    # Runtime (from toolkit)
    "KernelToolCallingRuntime",
    # Message normalizer (kept in tools/)
    "MessageNormalizer",
    "MessageNormalizerConfig",
    "NormalizationResult",
    "SchemaValidationResult",
    # Schema validator
    "SchemaValidator",
    # Contracts (from contracts)
    "ToolCall",
    "ToolCallParserPort",
    "ToolExecutionResult",
    "ToolExecutorPort",
    "ToolPolicy",
    "ToolRoundOutcome",
    "ToolRoundRequest",
    "ToolSchema",
    "auto_fix_conversation",
    # Normalizer
    "build_tool_feedback",
    "export_all_tools_to_json_schema",
    "export_tool_to_json_schema",
    "normalize_messages",
    "normalize_tool_arguments",
    "normalize_tool_calls",
    "normalize_tool_name",
    "validate_all_tool_schemas",
    "validate_conversation_structure",
    "validate_message_structure",
    "validate_tool_schema",
]
