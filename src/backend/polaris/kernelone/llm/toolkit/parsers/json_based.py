"""JSON-based tool call parser.

This module provides parsing of tool calls from JSON text format.
LLM may output tool calls as raw JSON text instead of using native tool calling
protocols or XML tags. This parser handles those cases.

Supported formats:
    - {"name": "tool_name", "arguments": {...}}
    - {"name": "tool_name", "args": {...}}
    - {"tool": "tool_name", "arguments": {...}}

Example:
    >>> parser = JSONToolParser()
    >>> text = '{"name": "read_file", "arguments": {"path": "test.py"}}'
    >>> result = parser.parse(text)
    >>> len(result)
    1
    >>> result[0].name
    'read_file'
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from polaris.kernelone.llm.toolkit.parsers.utils import (
    ParsedToolCall,
    _normalize_allowed_tool_names,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)


class JSONToolParser:
    """JSON format tool call parser.

    Parses tool calls from JSON text format. This serves as a fallback
    when native tool calling protocols and XML tags are not available.

    Attributes:
        ARGUMENT_KEYS: Accepted keys for arguments field.
        TOOL_NAME_KEYS: Accepted keys for tool name field.
    """

    # Keys that indicate the arguments field
    ARGUMENT_KEYS: frozenset[str] = frozenset({"arguments", "args", "params", "parameters"})

    # Keys that indicate the tool name field
    TOOL_NAME_KEYS: frozenset[str] = frozenset({"name", "tool", "function", "action", "tool_name"})

    # Regex patterns for JSON tool call detection
    # Matches JSON objects containing name and arguments fields
    _JSON_TOOL_CALL_RE = re.compile(
        r"""
        \{                           # Opening brace
        (?P<content>
            (?:[^{}]|              # Non-brace characters
             \{[^{}]*\})*          # Or single-level nested objects
        )
        \}                           # Closing brace
        """,
        re.VERBOSE | re.DOTALL,
    )

    # Pattern to validate JSON structure has required fields
    _HAS_NAME_RE = re.compile(
        r'"(' + '"|'.join(TOOL_NAME_KEYS) + r'")\s*:',
        re.IGNORECASE,
    )

    # Pattern to validate JSON structure has arguments field
    _HAS_ARGUMENTS_RE = re.compile(
        r'"(' + '"|'.join(ARGUMENT_KEYS) + r'")\s*:',
        re.IGNORECASE,
    )

    def __init__(
        self,
        *,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> None:
        """Initialize the JSON tool parser.

        Args:
            allowed_tool_names: Optional whitelist of allowed tool names.
                               If provided, only these tools will be parsed.
        """
        self._allowed_names = _normalize_allowed_tool_names(allowed_tool_names)

    @classmethod
    def parse(
        cls,
        text: str,
        *,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> list[ParsedToolCall]:
        """Parse JSON tool calls from text.

        Args:
            text: Text containing JSON tool calls.
            allowed_tool_names: Optional whitelist of allowed tool names.

        Returns:
            List of parsed tool calls. Empty list if no valid calls found.

        Raises:
            No exceptions are raised; invalid JSON returns empty list.
        """
        if not text or not isinstance(text, str):
            return []

        parser = cls(allowed_tool_names=allowed_tool_names)
        return parser._parse_text(text)

    def _parse_text(self, text: str) -> list[ParsedToolCall]:
        """Internal text parsing method.

        Args:
            text: Raw text to parse.

        Returns:
            List of parsed tool calls.
        """
        results: list[ParsedToolCall] = []

        # Strategy 1: Try to parse the entire text as JSON
        try:
            parsed = json.loads(text.strip())
            if isinstance(parsed, dict):
                calls = self._extract_calls_from_dict(parsed)
                results.extend(calls)
                if results:
                    return self._deduplicate_and_filter(results)
        except (json.JSONDecodeError, TypeError):
            pass

        # Strategy 2: Extract and parse individual JSON objects
        for match in self._JSON_TOOL_CALL_RE.finditer(text):
            json_str = match.group(0)
            try:
                parsed = json.loads(json_str)
                if isinstance(parsed, dict):
                    calls = self._extract_calls_from_dict(parsed)
                    results.extend(calls)
            except (json.JSONDecodeError, TypeError):
                # Skip invalid JSON, continue to next
                logger.debug("Skipping invalid JSON: %s", json_str[:100])
                continue

        return self._deduplicate_and_filter(results)

    def _extract_calls_from_dict(self, data: dict[str, Any]) -> list[ParsedToolCall]:
        """Extract tool calls from a dictionary.

        Args:
            data: Parsed JSON dictionary.

        Returns:
            List of parsed tool calls from this dictionary.
        """
        if not isinstance(data, dict):
            return []

        # Extract tool name
        tool_name = self._extract_tool_name(data)
        if not tool_name:
            return []

        # Check if tool name is allowed
        if self._allowed_names and tool_name.lower() not in self._allowed_names:
            logger.debug("Tool '%s' not in allowed list, skipping", tool_name)
            return []

        # Extract arguments
        arguments = self._extract_arguments(data)
        if arguments is None:
            arguments = {}

        import uuid

        return [
            ParsedToolCall(
                id=str(uuid.uuid4()),
                name=tool_name,
                arguments=arguments,
                source="json_parser",
            )
        ]

    def _extract_tool_name(self, data: dict[str, Any]) -> str | None:
        """Extract tool name from dictionary.

        Args:
            data: Dictionary containing tool call data.

        Returns:
            Tool name if found, None otherwise.
        """
        # Normalize keys to lowercase for case-insensitive matching
        data_lower = {k.lower(): v for k, v in data.items()}
        for key in self.TOOL_NAME_KEYS:
            value = data_lower.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _extract_arguments(self, data: dict[str, Any]) -> dict[str, Any] | None:
        """Extract arguments from dictionary.

        Args:
            data: Dictionary containing tool call data.

        Returns:
            Arguments dictionary if found, None if not present.
        """
        # Normalize keys to lowercase for case-insensitive matching
        data_lower = {k.lower(): v for k, v in data.items()}
        for key in self.ARGUMENT_KEYS:
            value = data_lower.get(key)
            if value is None:
                continue
            if isinstance(value, dict):
                # Return original dict to preserve key casing
                # Find the original key
                for orig_key in data:
                    if orig_key.lower() == key:
                        return data[orig_key]
                return value
            # Try to parse string as JSON
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, dict):
                        return parsed
                except (json.JSONDecodeError, TypeError):
                    pass
        return None

    def _deduplicate_and_filter(
        self,
        calls: list[ParsedToolCall],
    ) -> list[ParsedToolCall]:
        """Remove duplicate tool calls.

        Args:
            calls: List of parsed tool calls.

        Returns:
            Deduplicated list with allowed tools only.
        """
        seen: set[str] = set()
        results: list[ParsedToolCall] = []

        for call in calls:
            # Filter by allowed names if specified
            if self._allowed_names and call.name.lower() not in self._allowed_names:
                continue

            # Deduplicate by name + arguments hash
            key = f"{call.name}::{json.dumps(call.arguments, sort_keys=True)}"
            if key not in seen:
                seen.add(key)
                results.append(call)

        return results


def parse_json_tool_calls(
    text: str,
    *,
    allowed_tool_names: Iterable[str] | None = None,
) -> list[ParsedToolCall]:
    """Convenience function to parse JSON tool calls from text.

    Args:
        text: Text containing JSON tool calls.
        allowed_tool_names: Optional whitelist of allowed tool names.

    Returns:
        List of parsed tool calls.

    Example:
        >>> calls = parse_json_tool_calls('{"name": "read", "args": {}}')
        >>> calls[0].name
        'read'
    """
    return JSONToolParser.parse(text, allowed_tool_names=allowed_tool_names)


def is_json_tool_call(text: str) -> bool:
    """Check if text appears to contain a JSON tool call.

    Args:
        text: Text to check.

    Returns:
        True if text looks like a JSON tool call, False otherwise.

    Example:
        >>> is_json_tool_call('{"name": "read", "args": {}}')
        True
        >>> is_json_tool_call('Hello, world!')
        False
    """
    if not text or not isinstance(text, str):
        return False

    stripped = text.strip()
    if not stripped.startswith("{"):
        return False

    # Quick check: does it have name and arguments keys?
    has_name = bool(JSONToolParser._HAS_NAME_RE.search(stripped))
    has_args = bool(JSONToolParser._HAS_ARGUMENTS_RE.search(stripped))

    return has_name and has_args


__all__ = [
    "JSONToolParser",
    "is_json_tool_call",
    "parse_json_tool_calls",
]
