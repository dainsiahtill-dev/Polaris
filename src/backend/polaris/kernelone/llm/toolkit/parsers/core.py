"""Core parsing module.

This module provides the unified parsing entry point and utilities.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from polaris.kernelone.llm.toolkit.parsers.utils import (
    ParsedToolCall,
    deduplicate_tool_calls,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

_TOOL_CALL_WRAPPER_RE = re.compile(
    r"\[(?P<bracket_tag>tool_calls?|TOOL_CALLS?)\](?P<bracket_payload>.*?)\[/\1\]"
    r"|<(?P<angle_tag>tool_calls?)>(?P<angle_payload>.*?)</\3>",
    re.IGNORECASE | re.DOTALL,
)


def _normalize_allowed_tool_names(
    allowed_tool_names: Iterable[str] | None,
) -> set[str]:
    from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_name

    return {
        normalize_tool_name(str(item or ""))
        for item in (allowed_tool_names or [])
        if normalize_tool_name(str(item or ""))
    }


def _normalize_textual_calls(
    calls: list[ParsedToolCall],
    *,
    allowed_tool_names: Iterable[str] | None,
) -> list[ParsedToolCall]:
    from polaris.kernelone.llm.contracts.tool import ToolCall
    from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_arguments, normalize_tool_name

    allowed = _normalize_allowed_tool_names(allowed_tool_names)
    normalized: list[ParsedToolCall] = []
    for index, call in enumerate(calls):
        name = normalize_tool_name(str(call.name or ""))
        if not name:
            continue
        if allowed and name not in allowed:
            continue
        arguments = normalize_tool_arguments(name, call.arguments if isinstance(call.arguments, dict) else {})
        normalized.append(
            ToolCall(
                id=str(call.id or f"text_{index}"),
                name=name,
                arguments=arguments,
                source=call.source or "text_fallback",
                raw=call.raw,
                parse_error=call.parse_error,
            )
        )
    return deduplicate_tool_calls(normalized)


def _parse_text_fallback_calls(
    text: str | None,
    *,
    allowed_tool_names: Iterable[str] | None,
) -> list[ParsedToolCall]:
    token = str(text or "")
    if not token.strip():
        return []
    from polaris.kernelone.llm.toolkit.parsers.json_based import JSONToolParser

    return _normalize_textual_calls(
        JSONToolParser.parse(token, allowed_tool_names=None),
        allowed_tool_names=allowed_tool_names,
    )


def _strip_recovered_tool_call_wrappers(
    text: str,
    *,
    allowed_tool_names: Iterable[str] | None,
) -> str:
    source = str(text or "")
    if not source:
        return ""
    pieces: list[str] = []
    cursor = 0
    removed_any = False
    for match in _TOOL_CALL_WRAPPER_RE.finditer(source):
        wrapped = str(match.group(0) or "")
        if not _parse_text_fallback_calls(wrapped, allowed_tool_names=allowed_tool_names):
            continue
        pieces.append(source[cursor : match.start()])
        cursor = match.end()
        removed_any = True
    if not removed_any:
        return source
    pieces.append(source[cursor:])
    return "".join(pieces).strip()


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

    if not results and text:
        results.extend(
            _parse_text_fallback_calls(
                text,
                allowed_tool_names=allowed_tool_names,
            )
        )

    return deduplicate_tool_calls(results)


def extract_tool_calls_and_remainder(
    text: str,
    *,
    allowed_tool_names: Iterable[str] | None = None,
) -> tuple[list[ParsedToolCall], str]:
    """Extract tool calls and return remaining text.

    Args:
        text: Input text
        allowed_tool_names: Optional whitelist of allowed tool names

    Returns:
        Parsed text fallback calls plus text with accepted explicit wrappers removed.
    """
    source = str(text or "")
    calls = _parse_text_fallback_calls(source, allowed_tool_names=allowed_tool_names)
    if not calls:
        return [], source
    return calls, _strip_recovered_tool_call_wrappers(source, allowed_tool_names=allowed_tool_names)


def has_tool_calls(text: str) -> bool:
    """Check if text contains tool calls.

    Args:
        text: Input text

    Returns:
        True when text contains a recoverable JSON/text fallback call.
    """
    return bool(_parse_text_fallback_calls(text, allowed_tool_names=None))


def format_tool_result(tool_name: str, result: dict[str, Any]) -> str:
    """Format tool result for LLM consumption.

    Args:
        tool_name: Name of the tool
        result: Tool execution result

    Returns:
        Formatted result string
    """
    return f"Tool result: {tool_name}\n```json\n{json.dumps(result, ensure_ascii=False, indent=2)}\n```"
