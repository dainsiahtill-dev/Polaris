"""Native Function Calling parser.

Parses tool calls from OpenAI, Anthropic, Gemini, Ollama, and DeepSeek native formats.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from polaris.kernelone.llm.toolkit.parsers.utils import (
    ParsedToolCall,
    _normalize_allowed_tool_names,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)


class NativeFunctionCallingParser:
    """Native Function Calling parser.

    Parses tool calls from various provider native formats:
    - OpenAI: tool_calls array
    - Azure OpenAI: tool_calls array (Azure-specific response envelope)
    - Anthropic: content blocks with tool_use
    - Gemini: function_call in parts
    - Ollama: message content
    - DeepSeek: tool_calls array
    - Mistral: tool_calls array
    - Groq: tool_calls array
    - Cohere: tool_calls array
    - AWS Bedrock (Claude): streaming diff format
    - Vertex AI: function_call in parts (similar to Gemini)
    """

    @classmethod
    def parse_openai(
        cls,
        tool_calls: list[dict[str, Any]],
        *,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> list[ParsedToolCall]:
        """Parse OpenAI format tool calls.

        Args:
            tool_calls: List of tool call dicts from OpenAI response
            allowed_tool_names: Optional whitelist

        Returns:
            List of parsed tool calls
        """
        results: list[ParsedToolCall] = []
        allowed = _normalize_allowed_tool_names(allowed_tool_names)

        for call in tool_calls:
            if call.get("type") == "function":
                function = call.get("function", {})
                name = str(function.get("name") or "").strip().lower()
                if not name:
                    continue
                if allowed and name not in allowed:
                    continue

                args_str = function.get("arguments", "{}")
                arguments, _parse_error = cls._parse_json_arguments(args_str)

                results.append(
                    ParsedToolCall(
                        id=str(call.get("id") or f"openai_{len(results)}"),
                        name=name,
                        arguments=arguments,
                        raw=json.dumps(call, ensure_ascii=False),
                    )
                )

        return results

    @classmethod
    def parse_anthropic(
        cls,
        tool_calls: list[dict[str, Any]],
        *,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> list[ParsedToolCall]:
        """Parse Anthropic format tool calls.

        Args:
            tool_calls: List of content blocks from Anthropic response
            allowed_tool_names: Optional whitelist

        Returns:
            List of parsed tool calls
        """
        results: list[ParsedToolCall] = []
        allowed = _normalize_allowed_tool_names(allowed_tool_names)

        for block in tool_calls:
            if block.get("type") == "tool_use":
                name = str(block.get("name") or "").strip().lower()
                if not name:
                    continue
                if allowed and name not in allowed:
                    continue

                arguments = block.get("input", {})

                results.append(
                    ParsedToolCall(
                        id=str(block.get("id") or f"anthropic_{len(results)}"),
                        name=name,
                        arguments=arguments if isinstance(arguments, dict) else {},
                        raw=json.dumps(block, ensure_ascii=False),
                    )
                )

        return results

    @classmethod
    def parse_gemini(
        cls,
        response: dict[str, Any],
        *,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> list[ParsedToolCall]:
        """Parse Gemini format tool calls.

        Args:
            response: Gemini API response dict
            allowed_tool_names: Optional whitelist

        Returns:
            List of parsed tool calls
        """
        results: list[ParsedToolCall] = []
        allowed = _normalize_allowed_tool_names(allowed_tool_names)

        # Gemini 1.5 format: function_call in candidates
        candidates = response.get("candidates", [])
        for candidate in candidates:
            content = candidate.get("content", {})
            parts = content.get("parts", []) if isinstance(content, dict) else []

            for part in parts:
                if not isinstance(part, dict):
                    continue
                fc = part.get("functionCall") or part.get("function_call")
                if not isinstance(fc, dict):
                    continue
                name = str(fc.get("name") or "").strip().lower()
                if not name:
                    continue
                if allowed and name not in allowed:
                    continue

                    args_str = fc.get("args", "{}")
                    if isinstance(args_str, str):
                        arguments, _ = cls._parse_json_arguments(args_str)
                    else:
                        arguments = args_str if isinstance(args_str, dict) else {}

                    results.append(
                        ParsedToolCall(
                            id=f"gemini_{len(results)}",
                            name=name,
                            arguments=arguments,
                            raw=json.dumps(fc, ensure_ascii=False),
                        )
                    )

        return results

    @classmethod
    def parse_ollama(
        cls,
        response: dict[str, Any],
        *,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> list[ParsedToolCall]:
        """Parse Ollama format tool calls.

        Args:
            response: Ollama API response dict
            allowed_tool_names: Optional whitelist

        Returns:
            List of parsed tool calls
        """
        results: list[ParsedToolCall] = []
        allowed = _normalize_allowed_tool_names(allowed_tool_names)

        # Ollama format: tool_calls array
        tool_calls = response.get("tool_calls", [])
        for call in tool_calls:
            name = str(call.get("function", {}).get("name") or call.get("name") or "").strip().lower()
            if not name:
                continue
            if allowed and name not in allowed:
                continue

            args_str = call.get("function", {}).get("arguments", "{}")
            arguments, _ = (
                cls._parse_json_arguments(args_str)
                if isinstance(args_str, str)
                else (args_str if isinstance(args_str, dict) else {})
            )

            results.append(
                ParsedToolCall(
                    id=str(call.get("id") or f"ollama_{len(results)}"),
                    name=name,
                    arguments=arguments,
                    raw=json.dumps(call, ensure_ascii=False),
                )
            )

        return results

    @classmethod
    def parse_deepseek(
        cls,
        response: dict[str, Any],
        *,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> list[ParsedToolCall]:
        """Parse DeepSeek format tool calls.

        Args:
            response: DeepSeek API response dict
            allowed_tool_names: Optional whitelist

        Returns:
            List of parsed tool calls
        """
        results: list[ParsedToolCall] = []
        allowed = _normalize_allowed_tool_names(allowed_tool_names)

        # DeepSeek format: choices with tool_calls
        choices = response.get("choices", [])
        for choice in choices:
            message = choice.get("message", {}) if isinstance(choice, dict) else {}
            tool_calls = message.get("tool_calls", []) if isinstance(message, dict) else []

            for call in tool_calls:
                function = call.get("function", {}) if isinstance(call, dict) else {}
                name = str(function.get("name") or "").strip().lower()
                if not name:
                    continue
                if allowed and name not in allowed:
                    continue

                args_str = function.get("arguments", "{}")
                arguments, _ = cls._parse_json_arguments(args_str)

                results.append(
                    ParsedToolCall(
                        id=str(call.get("id") or f"deepseek_{len(results)}"),
                        name=name,
                        arguments=arguments,
                        raw=json.dumps(call, ensure_ascii=False),
                    )
                )

        return results

    @classmethod
    def parse_azure_openai(
        cls,
        response: dict[str, Any],
        *,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> list[ParsedToolCall]:
        """Parse Azure OpenAI format tool calls.

        Azure OpenAI uses the same tool_calls format as OpenAI but wraps
        responses in an Azure-specific envelope with sessionId and claim dictionaries.

        Args:
            response: Azure OpenAI API response dict
            allowed_tool_names: Optional whitelist

        Returns:
            List of parsed tool calls
        """
        results: list[ParsedToolCall] = []
        allowed = _normalize_allowed_tool_names(allowed_tool_names)

        # Azure wraps in .choices[].message.tool_calls or .choices[].delta.tool_calls
        choices = response.get("choices", [])
        for choice in choices:
            if not isinstance(choice, dict):
                continue

            # Handle both complete (message) and streaming (delta) responses
            message = choice.get("message", {}) or choice.get("delta", {})
            if not isinstance(message, dict):
                continue

            tool_calls = message.get("tool_calls", [])
            for call in tool_calls:
                function = call.get("function", {}) if isinstance(call, dict) else {}
                name = str(function.get("name") or "").strip().lower()
                if not name:
                    continue
                if allowed and name not in allowed:
                    continue

                args_str = function.get("arguments", "{}")
                arguments, _ = (
                    cls._parse_json_arguments(args_str)
                    if isinstance(args_str, str)
                    else (args_str if isinstance(args_str, dict) else {})
                )

                results.append(
                    ParsedToolCall(
                        id=str(call.get("id") or f"azure_{len(results)}"),
                        name=name,
                        arguments=arguments,
                        raw=json.dumps(call, ensure_ascii=False),
                    )
                )

        return results

    @classmethod
    def parse_mistral(
        cls,
        response: dict[str, Any],
        *,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> list[ParsedToolCall]:
        """Parse Mistral AI format tool calls.

        Mistral uses tool_calls array in choices[].message.tool_calls.

        Args:
            response: Mistral API response dict
            allowed_tool_names: Optional whitelist

        Returns:
            List of parsed tool calls
        """
        results: list[ParsedToolCall] = []
        allowed = _normalize_allowed_tool_names(allowed_tool_names)

        choices = response.get("choices", [])
        for choice in choices:
            message = choice.get("message", {}) if isinstance(choice, dict) else {}
            tool_calls = message.get("tool_calls", []) if isinstance(message, dict) else []

            for call in tool_calls:
                function = call.get("function", {}) if isinstance(call, dict) else {}
                name = str(function.get("name") or "").strip().lower()
                if not name:
                    continue
                if allowed and name not in allowed:
                    continue

                args_str = function.get("arguments", "{}")
                arguments, _ = (
                    cls._parse_json_arguments(args_str)
                    if isinstance(args_str, str)
                    else (args_str if isinstance(args_str, dict) else {})
                )

                results.append(
                    ParsedToolCall(
                        id=str(call.get("id") or f"mistral_{len(results)}"),
                        name=name,
                        arguments=arguments,
                        raw=json.dumps(call, ensure_ascii=False),
                    )
                )

        return results

    @classmethod
    def parse_groq(
        cls,
        response: dict[str, Any],
        *,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> list[ParsedToolCall]:
        """Parse Groq API format tool calls.

        Groq uses OpenAI-compatible tool_calls format.

        Args:
            response: Groq API response dict
            allowed_tool_names: Optional whitelist

        Returns:
            List of parsed tool calls
        """
        results: list[ParsedToolCall] = []
        allowed = _normalize_allowed_tool_names(allowed_tool_names)

        choices = response.get("choices", [])
        for choice in choices:
            message = choice.get("message", {}) if isinstance(choice, dict) else {}
            tool_calls = message.get("tool_calls", []) if isinstance(message, dict) else []

            for call in tool_calls:
                function = call.get("function", {}) if isinstance(call, dict) else {}
                name = str(function.get("name") or "").strip().lower()
                if not name:
                    continue
                if allowed and name not in allowed:
                    continue

                args_str = function.get("arguments", "{}")
                arguments, _ = (
                    cls._parse_json_arguments(args_str)
                    if isinstance(args_str, str)
                    else (args_str if isinstance(args_str, dict) else {})
                )

                results.append(
                    ParsedToolCall(
                        id=str(call.get("id") or f"groq_{len(results)}"),
                        name=name,
                        arguments=arguments,
                        raw=json.dumps(call, ensure_ascii=False),
                    )
                )

        return results

    @classmethod
    def parse_cohere(
        cls,
        response: dict[str, Any],
        *,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> list[ParsedToolCall]:
        """Parse Cohere API format tool calls.

        Cohere uses a distinct format with tool_calls at response root level.

        Args:
            response: Cohere API response dict
            allowed_tool_names: Optional whitelist

        Returns:
            List of parsed tool calls
        """
        results: list[ParsedToolCall] = []
        allowed = _normalize_allowed_tool_names(allowed_tool_names)

        # Cohere format: response.tool_calls = [{name: "...", parameters: {...}}]
        tool_calls = response.get("tool_calls", [])
        for i, call in enumerate(tool_calls):
            if not isinstance(call, dict):
                continue

            name = str(call.get("name") or "").strip().lower()
            if not name:
                continue
            if allowed and name not in allowed:
                continue

            # Cohere uses 'parameters' key for arguments
            params = call.get("parameters", {})
            arguments, _ = (
                cls._parse_json_arguments(params)
                if isinstance(params, str)
                else (params if isinstance(params, dict) else {})
            )

            results.append(
                ParsedToolCall(
                    id=str(call.get("id") or f"cohere_{i}"),
                    name=name,
                    arguments=arguments,
                    raw=json.dumps(call, ensure_ascii=False),
                )
            )

        return results

    @classmethod
    def parse_vertex_ai(
        cls,
        response: dict[str, Any],
        *,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> list[ParsedToolCall]:
        """Parse Vertex AI (Google Cloud) format tool calls.

        Vertex AI uses Gemini format with additional wrapper. Content may be
        in candidates[].content.parts[] or in groundedGeneration.

        Args:
            response: Vertex AI API response dict
            allowed_tool_names: Optional whitelist

        Returns:
            List of parsed tool calls
        """
        results: list[ParsedToolCall] = []
        allowed = _normalize_allowed_tool_names(allowed_tool_names)

        # Vertex AI wraps Gemini-style responses
        candidates = response.get("candidates", [])
        for candidate in candidates:
            content = candidate.get("content", {})
            parts = content.get("parts", []) if isinstance(content, dict) else []

            for part in parts:
                if not isinstance(part, dict):
                    continue

                # Vertex function call format
                fc = part.get("functionCall") or part.get("function_call")
                if not isinstance(fc, dict):
                    continue
                name = str(fc.get("name") or "").strip().lower()
                if not name:
                    continue
                if allowed and name not in allowed:
                    continue

                    args_str = fc.get("args", "{}")
                    arguments, _ = (
                        cls._parse_json_arguments(args_str)
                        if isinstance(args_str, str)
                        else (args_str if isinstance(args_str, dict) else {})
                    )

                    results.append(
                        ParsedToolCall(
                            id=f"vertex_{len(results)}",
                            name=name,
                            arguments=arguments,
                            raw=json.dumps(fc, ensure_ascii=False),
                        )
                    )

        return results

    @classmethod
    def parse_bedrock_claude(
        cls,
        response: dict[str, Any],
        *,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> list[ParsedToolCall]:
        """Parse AWS Bedrock Claude (via Converse API) format tool calls.

        Bedrock Claude uses stop_reason="tool_use" with content blocks.

        Args:
            response: Bedrock Converse API response dict
            allowed_tool_names: Optional whitelist

        Returns:
            List of parsed tool calls
        """
        results: list[ParsedToolCall] = []
        allowed = _normalize_allowed_tool_names(allowed_tool_names)

        # Bedrock Converse API structure
        output = response.get("output", {})
        message = output.get("message", {}) if isinstance(output, dict) else {}
        content = message.get("content", []) if isinstance(message, dict) else []

        for i, block in enumerate(content):
            if not isinstance(block, dict):
                continue

            # Bedrock tool use format: type is "tool_use" (underscore, not camelCase)
            block_type = block.get("type", "")
            if block_type not in ("tool_use", "toolUse"):
                continue

            # Extract tool_use block - prefer explicit key, fallback to empty dict
            tool_use: dict[str, Any] = {}
            if "toolUse" in block:
                tool_use = block.get("toolUse", {}) if isinstance(block.get("toolUse"), dict) else {}
            elif "tool_use" in block:
                tool_use = block.get("tool_use", {}) if isinstance(block.get("tool_use"), dict) else {}

            if not tool_use:
                continue

            name = str(tool_use.get("name") or "").strip().lower()
            if not name:
                continue
            if allowed and name not in allowed:
                continue

            input_data = tool_use.get("input", {})
            arguments, _ = (
                cls._parse_json_arguments(input_data)
                if isinstance(input_data, str)
                else (input_data if isinstance(input_data, dict) else {})
            )

            # toolUseId may be None or empty string
            tool_id = tool_use.get("toolUseId") or tool_use.get("tool_use_id")
            results.append(
                ParsedToolCall(
                    id=str(tool_id) if tool_id else f"bedrock_{i}",
                    name=name,
                    arguments=arguments,
                    raw=json.dumps(block, ensure_ascii=False),
                )
            )

        return results

    @staticmethod
    def _parse_json_arguments(args_str: str) -> tuple[dict[str, Any], str | None]:
        """Parse JSON arguments string.

        Args:
            args_str: JSON string of arguments

        Returns:
            Tuple of (parsed_dict, error_message or None)
        """
        raw = str(args_str or "").strip() or "{}"
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed, None
            return {}, "arguments must be a JSON object"
        except json.JSONDecodeError as exc:
            return {}, f"invalid JSON arguments: {exc}"
