"""Core parsing module.

This module provides the unified parsing entry point and utilities.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from polaris.kernelone.llm.toolkit.parsers.utils import (
    ParsedToolCall,
    deduplicate_tool_calls,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)


def parse_tool_calls(
    text: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    response: dict[str, Any] | None = None,
    provider: str = "auto",
    allowed_tool_names: Iterable[str] | None = None,
) -> list[ParsedToolCall]:
    """Unified tool call parsing entry point.

    Note: Runtime canonical protocol is provider-native tool_calls / function_call.
    This function handles parsing from various sources but the canonical execution
    path uses native tool calling.

    Args:
        text: Text input (deprecated for execution, kept for compatibility)
        tool_calls: Native tool_calls list (OpenAI/Anthropic format)
        response: Complete LLM response object (Gemini/Ollama/DeepSeek)
        provider: Provider type hint (openai, anthropic, gemini, ollama, deepseek, xml, auto)
        allowed_tool_names: Optional whitelist of allowed tool names

    Returns:
        List of parsed tool calls (deduplicated)
    """
    results: list[ParsedToolCall] = []

    # Import parsers lazily
    from polaris.kernelone.llm.toolkit.parsers.native_function import (
        NativeFunctionCallingParser,
    )

    provider_hint = str(provider or "auto").strip().lower() or "auto"

    # 1. Try native Function Calling (OpenAI/Anthropic).
    # The payload shape is authoritative; `provider` is only a hint because
    # some internal execution paths normalize stream-native events into a
    # canonical OpenAI-like shape even when the transport provider is
    # anthropic-compatible.
    if tool_calls:
        if provider_hint == "anthropic":
            tool_call_provider_attempts = ("anthropic", "openai")
        elif provider_hint == "openai":
            tool_call_provider_attempts = ("openai", "anthropic")
        else:
            tool_call_provider_attempts = ("openai", "anthropic")

        for provider_attempt in tool_call_provider_attempts:
            if provider_attempt == "openai":
                results.extend(
                    NativeFunctionCallingParser.parse_openai(
                        tool_calls,
                        allowed_tool_names=allowed_tool_names,
                    )
                )
            elif provider_attempt == "anthropic":
                results.extend(
                    NativeFunctionCallingParser.parse_anthropic(
                        tool_calls,
                        allowed_tool_names=allowed_tool_names,
                    )
                )

    # 2. Parse from response object (Gemini/Ollama/DeepSeek)
    if response and isinstance(response, dict):
        if provider_hint == "gemini":
            results.extend(
                NativeFunctionCallingParser.parse_gemini(
                    response,
                    allowed_tool_names=allowed_tool_names,
                )
            )
        elif provider_hint == "ollama":
            results.extend(
                NativeFunctionCallingParser.parse_ollama(
                    response,
                    allowed_tool_names=allowed_tool_names,
                )
            )
        elif provider_hint == "deepseek":
            results.extend(
                NativeFunctionCallingParser.parse_deepseek(
                    response,
                    allowed_tool_names=allowed_tool_names,
                )
            )
        elif provider_hint == "auto":
            # Auto-detect format
            gemini_tools = NativeFunctionCallingParser.parse_gemini(
                response,
                allowed_tool_names=allowed_tool_names,
            )
            if gemini_tools:
                results.extend(gemini_tools)
            else:
                ollama_tools = NativeFunctionCallingParser.parse_ollama(
                    response,
                    allowed_tool_names=allowed_tool_names,
                )
                if ollama_tools:
                    results.extend(ollama_tools)
                else:
                    deepseek_tools = NativeFunctionCallingParser.parse_deepseek(
                        response,
                        allowed_tool_names=allowed_tool_names,
                    )
                    if deepseek_tools:
                        results.extend(deepseek_tools)

    # Text protocol is deprecated for execution - kept for compatibility
    del text

    return deduplicate_tool_calls(results)


def extract_tool_calls_and_remainder(text: str) -> tuple[list[ParsedToolCall], str]:
    """Extract tool calls and return remaining text.

    Note: This function is deprecated for execution as text-based tool
    protocols are no longer executed.

    Args:
        text: Input text

    Returns:
        (empty list, original text) - text protocols not executed
    """
    return [], str(text or "")


def has_tool_calls(text: str) -> bool:
    """Check if text contains tool calls.

    Note: Always returns False as text-based protocols are deprecated.

    Args:
        text: Input text

    Returns:
        False (text protocols not executed)
    """
    del text
    return False


def format_tool_result(tool_name: str, result: dict[str, Any]) -> str:
    """Format tool result for LLM consumption.

    Args:
        tool_name: Name of the tool
        result: Tool execution result

    Returns:
        Formatted result string
    """
    return f"Tool result: {tool_name}\n```json\n{json.dumps(result, ensure_ascii=False, indent=2)}\n```"
