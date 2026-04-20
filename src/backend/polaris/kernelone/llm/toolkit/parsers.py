"""Backward compatibility alias for parsers module.

This file has been split into a directory structure.
Please update imports to use the new module path:

    from polaris.kernelone.llm.toolkit.parsers import parse_tool_calls

Or import directly from specific submodules:

    from polaris.kernelone.llm.toolkit.parsers.core import parse_tool_calls
    from polaris.kernelone.llm.toolkit.parsers.canonical import CanonicalToolCallParser
    from polaris.kernelone.llm.toolkit.parsers.xml_based import XMLToolParser

DEPRECATED (DELETED 2026-03-28):
    PromptBasedToolParser - had pre-existing regex bug
    ToolChainParser - unused, superseded by canonical parser
"""

from __future__ import annotations

# Re-export everything from the new module structure
from polaris.kernelone.llm.toolkit.parsers import (
    CANONICAL_ARGUMENT_KEYS,
    # New unified parser
    CanonicalToolCall,
    CanonicalToolCallParser,
    # Parser classes
    NativeFunctionCallingParser,
    # Utilities
    ParsedToolCall,
    XMLToolParser,
    deduplicate_tool_calls,
    extract_arguments,
    extract_tool_calls_and_remainder,
    format_tool_result,
    has_tool_calls,
    # Core functions
    parse_tool_calls,
    parse_value,
)

__all__ = [
    "CANONICAL_ARGUMENT_KEYS",
    # New unified parser
    "CanonicalToolCall",
    "CanonicalToolCallParser",
    # Parser classes
    "NativeFunctionCallingParser",
    # Utilities
    "ParsedToolCall",
    "XMLToolParser",
    "deduplicate_tool_calls",
    "extract_arguments",
    "extract_tool_calls_and_remainder",
    "format_tool_result",
    "has_tool_calls",
    # Core functions
    "parse_tool_calls",
    "parse_value",
]
