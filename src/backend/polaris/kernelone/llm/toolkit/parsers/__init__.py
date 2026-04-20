"""Parsers module - Split from parsers.py (1359 lines).

This module provides tool call parsing functionality.
The original parsers.py has been split into the following structure:

parsers/
    __init__.py           # Re-exports for backward compatibility
    canonical.py          # Unified CanonicalToolCallParser entry point (returns list[ToolCall])
    core.py               # Legacy unified parsing entry point
    utils.py              # Shared utilities (ParsedToolCall = ToolCall alias, helpers)
    native_function.py    # OpenAI/Anthropic/Gemini/Ollama/DeepSeek parser
    xml_based.py         # XML format parsers (MiniMax/Claude/Llama)

P0-002: All parse methods now return list[ToolCall] (canonical type).
ParsedToolCall is now an alias to ToolCall from contracts.tool.

Deprecated (DELETED):
    prompt_based.py       # [DELETED 2026-03-28] TOOL_NAME format parser
    tool_chain.py        # [DELETED 2026-03-28] tool_chain format parser
"""

from __future__ import annotations

# Import canonical ToolCall from contracts (P0-001 unified)
from polaris.kernelone.llm.contracts.tool import ToolCall

# New unified parser
from polaris.kernelone.llm.toolkit.parsers.canonical import (
    CANONICAL_ARGUMENT_KEYS,
    CanonicalToolCallParser,
    extract_arguments,
)

# Re-export from core module
from polaris.kernelone.llm.toolkit.parsers.core import (
    extract_tool_calls_and_remainder,
    format_tool_result,
    has_tool_calls,
    parse_tool_calls,
)

# Re-export parser classes
from polaris.kernelone.llm.toolkit.parsers.native_function import (
    NativeFunctionCallingParser,
)

# Re-export from utils module (ParsedToolCall = ToolCall alias)
from polaris.kernelone.llm.toolkit.parsers.utils import (
    deduplicate_tool_calls,
    parse_value,
)

# Backward compatibility: ParsedToolCall is now an alias to ToolCall
ParsedToolCall = ToolCall

# Backward compatibility: CanonicalToolCall is deprecated, use ToolCall directly
from polaris.kernelone.llm.toolkit.parsers.xml_based import (  # noqa: E402
    XMLToolParser,
)

__all__ = [
    "CANONICAL_ARGUMENT_KEYS",
    # New unified parser (returns list[ToolCall])
    "CanonicalToolCallParser",
    # Parser classes
    "NativeFunctionCallingParser",
    # Unified types (P0-001 + P0-002)
    "ToolCall",
    # Backward compatibility aliases
    "ParsedToolCall",  # Alias to ToolCall
    # Utilities
    "deduplicate_tool_calls",
    "extract_arguments",
    "extract_tool_calls_and_remainder",
    "format_tool_result",
    "has_tool_calls",
    # Core functions
    "parse_tool_calls",
    "parse_value",
    "XMLToolParser",
]
