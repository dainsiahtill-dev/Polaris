"""Native Function Calling parser.

Parses tool calls from OpenAI, Anthropic, Gemini, Ollama, and DeepSeek native formats.
"""

from __future__ import annotations

import ast
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

_FUNCTION_ARGUMENT_KEYS = (
    "arguments",
    "parameters",
    "params",
    "input",
    "args",
    "kwargs",
    "tool_input",
    "tool_arguments",
    "tool_args",
    "function_arguments",
    "function_args",
)

_EMPTY_ARGUMENT_STRINGS = frozenset({"", "{}", "[]", "null"})


def _argument_payload_is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, dict | list):
        return not value
    if isinstance(value, str):
        return value.strip().lower() in _EMPTY_ARGUMENT_STRINGS
    return False


def _function_arguments_payload(function: dict[str, Any]) -> Any:
    fallback: Any = {}
    for key in _FUNCTION_ARGUMENT_KEYS:
        if key not in function:
            continue
        value = function[key]
        if not _argument_payload_is_empty(value):
            return value
        if key == "arguments":
            fallback = value
    return fallback


def _parsed_tool_name(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_name

    return normalize_tool_name(raw)


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
                name = _parsed_tool_name(function.get("name"))
                if not name:
                    continue
                if allowed and name not in allowed:
                    continue

                args_str = _function_arguments_payload(function)
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
                name = _parsed_tool_name(block.get("name"))
                if not name:
                    continue
                if allowed and name not in allowed:
                    continue

                arguments, _ = cls._parse_json_arguments(block.get("input", {}))

                results.append(
                    ParsedToolCall(
                        id=str(block.get("id") or f"anthropic_{len(results)}"),
                        name=name,
                        arguments=arguments,
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
                name = _parsed_tool_name(fc.get("name"))
                if not name:
                    continue
                if allowed and name not in allowed:
                    continue

                # ADR-0090 W1.7: this parse body was mis-indented under the
                # whitelist `continue` and therefore unreachable — parse_gemini
                # silently returned [] for every response.
                arguments, _ = cls._parse_json_arguments(_function_arguments_payload(fc))

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
            name = _parsed_tool_name(call.get("function", {}).get("name") or call.get("name"))
            if not name:
                continue
            if allowed and name not in allowed:
                continue

            function_payload = _function_arguments_payload(call.get("function", {}))
            if _argument_payload_is_empty(function_payload):
                function_payload = _function_arguments_payload(call)
            arguments, _ = cls._parse_json_arguments(function_payload)

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
                name = _parsed_tool_name(function.get("name"))
                if not name:
                    continue
                if allowed and name not in allowed:
                    continue

                args_str = _function_arguments_payload(function)
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
                name = _parsed_tool_name(function.get("name"))
                if not name:
                    continue
                if allowed and name not in allowed:
                    continue

                arguments, _ = cls._parse_json_arguments(_function_arguments_payload(function))

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
                name = _parsed_tool_name(function.get("name"))
                if not name:
                    continue
                if allowed and name not in allowed:
                    continue

                arguments, _ = cls._parse_json_arguments(_function_arguments_payload(function))

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
                name = _parsed_tool_name(function.get("name"))
                if not name:
                    continue
                if allowed and name not in allowed:
                    continue

                arguments, _ = cls._parse_json_arguments(_function_arguments_payload(function))

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

            name = _parsed_tool_name(call.get("name"))
            if not name:
                continue
            if allowed and name not in allowed:
                continue

            arguments, _ = cls._parse_json_arguments(_function_arguments_payload(call))

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
                name = _parsed_tool_name(fc.get("name"))
                if not name:
                    continue
                if allowed and name not in allowed:
                    continue

                # ADR-0090 W1.7: this parse body was mis-indented under the
                # whitelist `continue` (unreachable), and the dict branch tried
                # to tuple-unpack a plain dict — parse_vertex_ai returned [].
                arguments, _ = cls._parse_json_arguments(_function_arguments_payload(fc))

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

            name = _parsed_tool_name(tool_use.get("name"))
            if not name:
                continue
            if allowed and name not in allowed:
                continue

            arguments, _ = cls._parse_json_arguments(_function_arguments_payload(tool_use))

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
    def _parse_json_arguments(args_str: Any) -> tuple[dict[str, Any], str | None]:
        """Parse JSON arguments string or decoded object.

        Args:
            args_str: JSON string or already-decoded arguments object

        Returns:
            Tuple of (parsed_dict, error_message or None)
        """
        if isinstance(args_str, dict):
            return dict(args_str), None
        if isinstance(args_str, list) and len(args_str) == 1 and isinstance(args_str[0], dict):
            return dict(args_str[0]), None

        raw = str(args_str or "").strip() or "{}"
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            try:
                parsed = ast.literal_eval(raw)
            except (ValueError, SyntaxError):
                return {}, f"invalid JSON arguments: {exc}"
        if isinstance(parsed, dict):
            return parsed, None
        if isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], dict):
            return dict(parsed[0]), None
        return {}, "arguments must be a JSON object"
