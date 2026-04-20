"""Canonical Tool Call Parser - Unified entry point for tool call parsing.

This module provides CanonicalToolCallParser as the single unified entry point
for parsing tool calls from various LLM providers.

Architecture:
    CanonicalToolCallParser.parse() -> List[ToolCall] (from contracts.tool)
        |
        +-- OpenAIAdapter (OpenAI native function calling)
        +-- AzureOpenAIAdapter (Azure OpenAI with envelope)
        +-- AnthropicAdapter (Anthropic native tool use)
        +-- GeminiAdapter (Gemini function calling)
        +-- OllamaAdapter (Ollama function calling)
        +-- DeepSeekAdapter (DeepSeek function calling)
        +-- MistralAdapter (Mistral AI function calling)
        +-- GroqAdapter (Groq API function calling)
        +-- CohereAdapter (Cohere tool_calls format)
        +-- VertexAIAdapter (Google Vertex AI function calling)
        +-- BedrockAdapter (AWS Bedrock Claude via Converse API)
        +-- JSONTextAdapter (JSON text format fallback)
        +-- XMLTextAdapter (XML text format fallback)

Unified ToolCall (from polaris.kernelone.llm.contracts.tool):
- id: Unique identifier for the tool call
- name: Tool name (normalized to lowercase)
- arguments: Tool arguments (dict)
- source: Source of the tool call (e.g., "openai", "anthropic", "json_text")
- raw: Raw original text if parsed from text
- parse_error: Error message if parsing failed

P0-002: Unified return type to list[ToolCall] (canonical).
"""

from __future__ import annotations

import copy
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

# Import canonical ToolCall from contracts (P0-001 unified)
from polaris.kernelone.llm.contracts.tool import ToolCall

logger = logging.getLogger(__name__)


# Canonical argument keys - all adapters use these
CANONICAL_ARGUMENT_KEYS = ["arguments", "args", "params", "parameters", "input"]


def extract_arguments(data: dict[str, Any]) -> dict[str, Any]:
    """Extract arguments from a tool call dict using canonical keys.

    Args:
        data: Tool call data dict

    Returns:
        Arguments dict, or empty dict if no canonical key found
    """
    for key in CANONICAL_ARGUMENT_KEYS:
        if key in data:
            value = data[key]
            if isinstance(value, dict):
                return value
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except json.JSONDecodeError:
                    pass
    # Fallback: return the whole dict minus known non-argument keys
    result = {k: v for k, v in data.items() if k not in ["type", "name", "id", "function"]}
    return result if result else data


# ------------------------------------------------------------------
# Provider field mapping — drives the generic _parse_with_mapping path
# ------------------------------------------------------------------


@dataclass(frozen=True)
class _ProviderFieldMapping:
    """Describes how to extract tool name and arguments from one provider's format.

    Internal helper - not exported.
    """

    #: Top-level key that holds the function call block (None = use item itself)
    block_key: str | None
    #: Key for the tool name inside the block
    name_key: str
    #: Key for the arguments inside the block
    args_key: str


_PROVIDER_MAPPINGS: dict[str, _ProviderFieldMapping] = {
    # OpenAI: {"id": "...", "type": "function", "function": {"name": "...", "arguments": "..."}}
    "openai": _ProviderFieldMapping(block_key="function", name_key="name", args_key="arguments"),
    # Azure OpenAI: Same as OpenAI but with Azure envelope
    "azure_openai": _ProviderFieldMapping(block_key="function", name_key="name", args_key="arguments"),
    # Anthropic: {"type": "tool_use", "name": "...", "input": "..."}
    "anthropic": _ProviderFieldMapping(block_key=None, name_key="name", args_key="input"),
    # Gemini: {"functionCall": {"name": "...", "args": "..."}}  (or function_call)
    # Note: block_key=None means we use the extracted fc dict directly,
    # so name_key/args_key point to the TOP-LEVEL keys inside fc.
    "gemini": _ProviderFieldMapping(block_key=None, name_key="name", args_key="args"),
    # Ollama: {"function": {"name": "...", "arguments": "..."}} nested under message.tool_calls
    "ollama": _ProviderFieldMapping(block_key="function", name_key="name", args_key="arguments"),
    # DeepSeek: {"function": {"name": "...", "arguments": "..."}}
    "deepseek": _ProviderFieldMapping(block_key="function", name_key="name", args_key="arguments"),
    # Mistral: {"function": {"name": "...", "arguments": "..."}}
    "mistral": _ProviderFieldMapping(block_key="function", name_key="name", args_key="arguments"),
    # Groq: OpenAI-compatible format
    "groq": _ProviderFieldMapping(block_key="function", name_key="name", args_key="arguments"),
    # Cohere: {"name": "...", "parameters": {...}} at root level
    "cohere": _ProviderFieldMapping(block_key=None, name_key="name", args_key="parameters"),
    # Vertex AI: Gemini-compatible format wrapped in Vertex envelope
    "vertex": _ProviderFieldMapping(block_key=None, name_key="name", args_key="args"),
    # AWS Bedrock Claude: {"toolUse": {"name": "...", "input": "..."}}
    "bedrock": _ProviderFieldMapping(block_key="toolUse", name_key="name", args_key="input"),
}


def _generate_tool_call_id(provider: str, name: str, item: dict[str, Any] | None = None) -> str:
    """Generate a unique ID for a tool call.

    Args:
        provider: Provider name
        name: Tool name
        item: Optional original item dict (may contain 'id')

    Returns:
        Unique tool call ID
    """
    # Try to extract id from item if available
    if item and isinstance(item, dict):
        existing_id = item.get("id")
        if existing_id and isinstance(existing_id, str) and existing_id.strip():
            return existing_id.strip()

    # Generate a new UUID-based id
    return f"{provider}_{name}_{uuid.uuid4().hex[:8]}"


def _build_tool_call(
    name: str,
    arguments: dict[str, Any],
    provider: str,
    item: dict[str, Any],
    call_id: str | None = None,
    parse_error: str | None = None,
) -> ToolCall:
    """Build a canonical ToolCall instance.

    Args:
        name: Tool name (normalized lowercase)
        arguments: Tool arguments dict
        provider: Provider/source format
        item: Raw original item dict
        call_id: Optional explicit ID
        parse_error: Optional parse error message

    Returns:
        ToolCall instance
    """
    # Normalize name to lowercase
    name = str(name or "").strip().lower()
    if not name:
        raise ValueError("Tool call name cannot be empty")

    # Generate ID if not provided
    if not call_id:
        call_id = _generate_tool_call_id(provider, name, item)

    # Build raw JSON string
    raw = json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else str(item)

    return ToolCall(
        id=call_id,
        name=name,
        arguments=copy.deepcopy(arguments),
        source=provider,
        raw=raw,
        parse_error=parse_error,
    )


# ------------------------------------------------------------------
# Backward compatibility alias (DEPRECATED)
# ------------------------------------------------------------------

# CanonicalToolCall is deprecated - use ToolCall directly
# This alias is kept for backward compatibility with tests that import CanonicalToolCall
CanonicalToolCall = ToolCall


class CanonicalToolCallParser:
    """Unified parser for tool calls from all providers.

    This parser provides a single entry point for parsing tool calls,
    with format_hint for provider-specific parsing and auto-detect fallback.

    All parse methods return list[ToolCall] (from contracts.tool).
    """

    def __init__(self, tool_spec_registry: Any = None) -> None:
        """Initialize parser with optional registry.

        Args:
            tool_spec_registry: Optional ToolSpecRegistry instance for validation
        """
        self._registry = tool_spec_registry

    def parse(
        self,
        raw: Any,
        format_hint: str | None = None,
        allowed_tools: list[str] | None = None,
    ) -> list[ToolCall]:
        """Parse tool calls from raw input.

        Args:
            raw: Raw tool call data (list, dict, or response object)
            format_hint: Provider format hint ("openai", "anthropic", "gemini", etc.)
            allowed_tools: Optional whitelist of allowed tool names

        Returns:
            List of ToolCall instances (unified canonical type)
        """
        if format_hint:
            return self._parse_with_hint(raw, format_hint, allowed_tools)
        return self._auto_parse(raw, allowed_tools)

    def _parse_with_hint(
        self,
        raw: Any,
        hint: str,
        allowed_tools: list[str] | None,
    ) -> list[ToolCall]:
        """Parse with explicit format hint.

        Args:
            raw: Raw tool call data
            hint: Format hint (openai, anthropic, gemini, ollama, deepseek, json_text)
            allowed_tools: Optional whitelist

        Returns:
            List of ToolCall instances
        """
        hint = hint.strip().lower()

        if hint == "json_text":
            return self._parse_json_text(raw, allowed_tools)

        mapping = _PROVIDER_MAPPINGS.get(hint)
        if mapping is None:
            logger.warning(f"Unknown format hint: {hint}, falling back to auto-parse")
            return self._auto_parse(raw, allowed_tools)

        # Gemini auto-wraps raw in parts
        if hint == "gemini" and isinstance(raw, dict):
            parts = raw.get("parts", [])
            results_gemini: list[ToolCall] = []
            for part in parts:
                if isinstance(part, dict):
                    fc = part.get("functionCall") or part.get("function_call")
                    if fc:
                        r = self._parse_single_with_mapping(fc, hint, allowed_tools)
                        if r:
                            results_gemini.extend(r)
            return results_gemini

        # Ollama wraps in message.tool_calls
        if hint == "ollama" and isinstance(raw, dict):
            message = raw.get("message", {})
            tool_calls = message.get("tool_calls", []) if isinstance(message, dict) else []
            results_ollama: list[ToolCall] = []
            for call in tool_calls:
                r = self._parse_single_with_mapping(call, hint, allowed_tools)
                if r:
                    results_ollama.extend(r)
            return results_ollama

        # Standard list-of-dicts format (openai, anthropic, deepseek)
        items = raw if isinstance(raw, list) else [raw]
        results: list[ToolCall] = []
        for item in items:
            r = self._parse_single_with_mapping(item, hint, allowed_tools)
            if r:
                results.extend(r)
        return results

    def _parse_single_with_mapping(
        self,
        item: dict[str, Any],
        provider: str,
        allowed_tools: list[str] | None,
    ) -> list[ToolCall]:
        """Parse a single item using a provider's field mapping.

        Args:
            item: Single tool call dict
            provider: Provider name
            allowed_tools: Optional whitelist

        Returns:
            List of one ToolCall (empty if skipped)
        """
        mapping = _PROVIDER_MAPPINGS.get(provider)
        if mapping is None:
            return []

        block = item.get(mapping.block_key) if mapping.block_key else item
        if not isinstance(block, dict):
            return []

        name = str(block.get(mapping.name_key) or "").strip().lower()
        if not name:
            return []

        allowed_set = {t.strip().lower() for t in (allowed_tools or [])} or None
        if allowed_set and name not in allowed_set:
            return []

        args_str = block.get(mapping.args_key, "{}")
        arguments, parse_error = self._parse_json_arguments(args_str)

        # Extract id from item if available (OpenAI format has 'id' at top level)
        call_id = item.get("id") if isinstance(item, dict) else None

        try:
            return [
                _build_tool_call(
                    name=name,
                    arguments=arguments,
                    provider=provider,
                    item=item,
                    call_id=call_id,
                    parse_error=parse_error,
                )
            ]
        except ValueError:
            return []

    def _auto_parse(
        self,
        raw: Any,
        allowed_tools: list[str] | None,
    ) -> list[ToolCall]:
        """Auto-detect format and parse.

        Tries providers in order of prevalence:
        1. Structured dict formats (openai, azure_openai, anthropic, deepseek, mistral, groq)
        2. Wrapped dict formats (gemini, ollama, vertex, cohere, bedrock)
        3. Text formats (json_text via _parse_json_text)

        Args:
            raw: Raw tool call data
            allowed_tools: Optional whitelist

        Returns:
            List of ToolCall instances
        """
        # Try list formats first (openai, azure_openai, anthropic, deepseek, mistral, groq)
        if isinstance(raw, list):
            for provider in ("openai", "azure_openai", "anthropic", "deepseek", "mistral", "groq"):
                mapping = _PROVIDER_MAPPINGS.get(provider)
                if mapping is None:
                    continue
                results: list[ToolCall] = []
                for item in raw:
                    if not isinstance(item, dict):
                        continue
                    r = self._parse_single_with_mapping(item, provider, allowed_tools)
                    if r:
                        results.extend(r)
                if results:
                    return results

        # Try dict formats (gemini, ollama, vertex, cohere, bedrock)
        if isinstance(raw, dict):
            # Try standalone formats first
            for provider in ("gemini", "ollama", "vertex", "cohere", "bedrock"):
                mapping = _PROVIDER_MAPPINGS.get(provider)
                if mapping is None:
                    continue
                # Gemini/Vertex: parts[]; Ollama: message.tool_calls; Cohere: root tool_calls; Bedrock: output.message.content
                if provider in ("gemini", "vertex"):
                    parts = raw.get("parts", [])
                    results = []
                    for part in parts:
                        if not isinstance(part, dict):
                            continue
                        fc = part.get("functionCall") or part.get("function_call")
                        if not isinstance(fc, dict):
                            continue
                        r = self._parse_single_with_mapping(fc, provider, allowed_tools)
                        if r:
                            results.extend(r)
                    if results:
                        return results
                elif provider == "ollama":
                    message = raw.get("message", {})
                    tool_calls = message.get("tool_calls", []) if isinstance(message, dict) else []
                    results = []
                    for call in tool_calls:
                        if not isinstance(call, dict):
                            continue
                        r = self._parse_single_with_mapping(call, provider, allowed_tools)
                        if r:
                            results.extend(r)
                    if results:
                        return results
                elif provider == "cohere":
                    tool_calls = raw.get("tool_calls", [])
                    results = []
                    for call in tool_calls:
                        if not isinstance(call, dict):
                            continue
                        r = self._parse_single_with_mapping(call, provider, allowed_tools)
                        if r:
                            results.extend(r)
                    if results:
                        return results
                elif provider == "bedrock":
                    results = self._parse_bedrock(raw, allowed_tools)
                    if results:
                        return results

        # Fallback: try JSON text parsing on string input
        if isinstance(raw, str) and raw.strip():
            json_results = self._parse_json_text(raw, allowed_tools)
            if json_results:
                return json_results

        return []

    def parse_multi_format(
        self,
        raw: Any,
        allowed_tools: list[str] | None = None,
    ) -> list[ToolCall]:
        """Parse tool calls from potentially mixed formats.

        This method tries ALL known formats and returns combined results.
        Use this when the input might contain multiple format types.

        Args:
            raw: Raw tool call data (str, list, or dict)
            allowed_tools: Optional whitelist

        Returns:
            List of all ToolCall instances found across all formats
        """
        all_results: list[ToolCall] = []
        seen_ids: set[str] = set()

        # If it's a string, try text parsing first
        if isinstance(raw, str):
            text = raw.strip()
            if text:
                # Try JSON text
                json_results = self._parse_json_text(text, allowed_tools)
                for tc in json_results:
                    if tc.id not in seen_ids:
                        seen_ids.add(tc.id)
                        all_results.append(tc)

                # Also try XML parsing by importing XMLToolParser
                from polaris.kernelone.llm.toolkit.parsers.xml_based import XMLToolParser

                xml_results = XMLToolParser.parse(text, allowed_tool_names=allowed_tools)
                for pr in xml_results:
                    if pr.name and pr.name not in seen_ids:
                        seen_ids.add(pr.name)
                        all_results.append(
                            ToolCall(
                                id=pr.id,
                                name=pr.name,
                                arguments=pr.arguments,
                                source="xml_text",
                                raw=pr.raw,
                                parse_error=None,
                            )
                        )

        # Try all structured formats
        for provider in _PROVIDER_MAPPINGS:
            try:
                results = self._parse_with_hint(raw, provider, allowed_tools)
                for tc in results:
                    if tc.id not in seen_ids:
                        seen_ids.add(tc.id)
                        all_results.append(tc)
            except (ValueError, KeyError, TypeError, AttributeError):
                # Skip formats that don't match
                continue

        return all_results

    # ------------------------------------------------------------------
    # Provider-specific entry points — delegate to _parse_with_mapping
    # ------------------------------------------------------------------

    def _parse_openai(
        self,
        tool_calls: list[dict[str, Any]],
        allowed_tools: list[str] | None,
    ) -> list[ToolCall]:
        return self._parse_with_hint(tool_calls, "openai", allowed_tools)

    def _parse_anthropic(
        self,
        blocks: list[dict[str, Any]],
        allowed_tools: list[str] | None,
    ) -> list[ToolCall]:
        return self._parse_with_hint(blocks, "anthropic", allowed_tools)

    def _parse_gemini(
        self,
        response: dict[str, Any],
        allowed_tools: list[str] | None,
    ) -> list[ToolCall]:
        return self._parse_with_hint(response, "gemini", allowed_tools)

    def _parse_ollama(
        self,
        response: dict[str, Any],
        allowed_tools: list[str] | None,
    ) -> list[ToolCall]:
        return self._parse_with_hint(response, "ollama", allowed_tools)

    def _parse_deepseek(
        self,
        tool_calls: list[dict[str, Any]],
        allowed_tools: list[str] | None,
    ) -> list[ToolCall]:
        return self._parse_with_hint(tool_calls, "deepseek", allowed_tools)

    def _parse_azure_openai(
        self,
        response: dict[str, Any],
        allowed_tools: list[str] | None,
    ) -> list[ToolCall]:
        return self._parse_with_hint(response, "azure_openai", allowed_tools)

    def _parse_mistral(
        self,
        response: dict[str, Any],
        allowed_tools: list[str] | None,
    ) -> list[ToolCall]:
        return self._parse_with_hint(response, "mistral", allowed_tools)

    def _parse_groq(
        self,
        response: dict[str, Any],
        allowed_tools: list[str] | None,
    ) -> list[ToolCall]:
        return self._parse_with_hint(response, "groq", allowed_tools)

    def _parse_cohere(
        self,
        response: dict[str, Any],
        allowed_tools: list[str] | None,
    ) -> list[ToolCall]:
        return self._parse_with_hint(response, "cohere", allowed_tools)

    def _parse_vertex(
        self,
        response: dict[str, Any],
        allowed_tools: list[str] | None,
    ) -> list[ToolCall]:
        return self._parse_with_hint(response, "vertex", allowed_tools)

    def _parse_bedrock(
        self,
        response: dict[str, Any],
        allowed_tools: list[str] | None,
    ) -> list[ToolCall]:
        return self._parse_with_hint(response, "bedrock", allowed_tools)

    def _parse_json_text(
        self,
        text: str,
        allowed_tools: list[str] | None,
    ) -> list[ToolCall]:
        """Parse JSON text format tool calls.

        This is a fallback for JSON-encoded tool calls in text format.

        Args:
            text: Text containing JSON tool call data
            allowed_tools: Optional whitelist

        Returns:
            List of ToolCall instances
        """
        import re

        results: list[ToolCall] = []
        allowed_set = {t.strip().lower() for t in (allowed_tools or [])} or None

        # Match JSON object patterns
        json_pattern = r'\{[^{}]*"tool"[^{}]*\}'
        matches = re.findall(json_pattern, text, re.DOTALL)

        for match in matches:
            try:
                data = json.loads(match)
                name = str(data.get("tool") or data.get("name") or "").strip().lower()
                if not name:
                    continue
                if allowed_set and name not in allowed_set:
                    continue

                arguments = extract_arguments(data)

                try:
                    results.append(
                        _build_tool_call(
                            name=name,
                            arguments=arguments,
                            provider="json_text",
                            item=data,
                        )
                    )
                except ValueError:
                    continue
            except json.JSONDecodeError:
                continue

        return results

    @staticmethod
    def _parse_json_arguments(args_str: str) -> tuple[dict[str, Any], str | None]:
        """Parse JSON arguments string.

        Args:
            args_str: JSON string of arguments

        Returns:
            Tuple of (arguments_dict, error_message_or_None)
        """
        if not args_str:
            return {}, None

        if isinstance(args_str, dict):
            return args_str, None

        try:
            return json.loads(args_str), None
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse JSON arguments: {e}")
            return {}, str(e)
