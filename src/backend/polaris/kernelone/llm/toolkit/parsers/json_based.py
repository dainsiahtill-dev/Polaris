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
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from polaris.kernelone.llm.toolkit.parsers.utils import (
    ParsedToolCall,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)


def _iter_top_level_json_objects(text: str) -> list[str]:
    """Return each top-level ``{...}`` substring, brace-depth + string aware.

    Replaces a single-level-nesting regex that could not match an object whose
    arguments contain depth>=2 braces — extremely common when a ``write_file``
    body holds JS/JSON like ``{a: {b: 1}}``. The regex silently skipped such a
    leading object and recovered only later, simpler calls, dropping the write.
    This scanner respects string literals and escapes, so braces inside string
    values never affect depth. A truncated trailing object (opened, never
    closed) is simply not emitted — the leading complete objects still are.
    """
    objects: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escape = False
    for index, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = index
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                objects.append(text[start : index + 1])
                start = -1
    return objects


def _normalize_tool_name_for_matching(tool_name: str) -> str:
    """Return the canonical tool name used for filtering and namespace checks."""
    from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_name

    normalized = str(normalize_tool_name(tool_name) or "").strip()
    return normalized.lower()


def _tool_name_match_keys(tool_name: str) -> set[str]:
    """Return raw and canonical lowercase forms accepted by allow-list filters."""
    raw = str(tool_name or "").strip().lower()
    canonical = _normalize_tool_name_for_matching(tool_name)
    return {item for item in (raw, canonical) if item}


def _normalize_allowed_tool_names_for_matching(
    allowed_tool_names: Iterable[str] | None,
) -> set[str]:
    """Normalize an allow-list through the registered tool-name resolver."""
    names: set[str] = set()
    for item in allowed_tool_names or []:
        names.update(_tool_name_match_keys(str(item or "")))
    return names


def _tool_argument_namespace(tool_name: str) -> set[str]:
    """Return canonical and alias argument names for a registered tool."""
    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    spec = ToolSpecRegistry.get_all_specs().get(tool_name) or {}
    keys: set[str] = set()
    arguments = spec.get("arguments", [])
    if isinstance(arguments, list):
        for arg in arguments:
            if not isinstance(arg, Mapping):
                continue
            name = str(arg.get("name") or "").strip().lower()
            if name:
                keys.add(name)

    arg_aliases = spec.get("arg_aliases", {})
    if isinstance(arg_aliases, Mapping):
        for alias, canonical in arg_aliases.items():
            alias_key = str(alias or "").strip().lower()
            canonical_key = str(canonical or "").strip().lower()
            if alias_key:
                keys.add(alias_key)
            if canonical_key:
                keys.add(canonical_key)
    return keys


class JSONToolParser:
    """JSON format tool call parser.

    Parses tool calls from JSON text format. This serves as a fallback
    when native tool calling protocols and XML tags are not available.

    Attributes:
        ARGUMENT_KEYS: Accepted keys for arguments field.
        TOOL_NAME_KEYS: Accepted keys for tool name field.
    """

    # Keys that indicate the arguments field
    ARGUMENT_KEYS: frozenset[str] = frozenset(
        {
            "arguments",
            "args",
            "params",
            "parameters",
            "input",
            "kwargs",
            "tool_input",
            "tool_arguments",
            "tool_args",
            "function_arguments",
            "function_args",
        }
    )

    # Keys that indicate the tool name field
    TOOL_NAME_KEYS: frozenset[str] = frozenset({"name", "tool", "function", "action", "tool_name"})

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
        self._allowed_names = _normalize_allowed_tool_names_for_matching(allowed_tool_names)

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

        # Strategy 2: Extract and parse individual top-level JSON objects.
        # Brace-depth + string aware so an object with deeply-nested arguments
        # (a write_file body containing ``{a: {b: 1}}``) is matched too, instead
        # of being silently skipped by a single-level-nesting regex.
        for json_str in _iter_top_level_json_objects(text):
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
        canonical_tool_name = _normalize_tool_name_for_matching(tool_name)

        # Check if tool name is allowed
        if self._allowed_names and not (_tool_name_match_keys(tool_name) & self._allowed_names):
            logger.debug("Tool '%s' not in allowed list, skipping", tool_name)
            return []

        # Extract arguments
        data_lower = {k.lower(): v for k, v in data.items()}
        has_argument_container = any(key in data_lower for key in self.ARGUMENT_KEYS)
        if not has_argument_container:
            sibling_arguments = self._extract_sibling_arguments(data, canonical_tool_name)
            if sibling_arguments is None:
                return []
            arguments: dict[str, Any] = sibling_arguments
        else:
            extracted_arguments = self._extract_arguments(data)
            arguments = {} if extracted_arguments is None else extracted_arguments

        import uuid

        return [
            ParsedToolCall(
                id=str(uuid.uuid4()),
                name=tool_name,
                arguments=arguments,
                source="json_parser",
            )
        ]

    def _extract_sibling_arguments(
        self,
        data: dict[str, Any],
        tool_name: str,
    ) -> dict[str, Any] | None:
        """Extract flat sibling arguments when they match a registered tool schema.

        Weak models sometimes emit ``{"name": "readFile", "path": "x"}``
        instead of wrapping the payload under ``arguments``. The fallback stays
        fail-closed by requiring every sibling key to belong to the registered
        tool's canonical argument or alias namespace.
        """
        namespace = _tool_argument_namespace(tool_name)
        if not namespace:
            return None

        meta_keys = {key.lower() for key in self.TOOL_NAME_KEYS | self.ARGUMENT_KEYS} | {
            "call_id",
            "id",
            "source",
            "tool_call_id",
            "type",
        }
        candidates = {key: value for key, value in data.items() if key.lower() not in meta_keys}
        if not candidates:
            return None
        if not all(key.lower() in namespace for key in candidates):
            return None
        return candidates

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
            if self._allowed_names and not (_tool_name_match_keys(call.name) & self._allowed_names):
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

    # Quick check: does it have name and explicit arguments keys?
    has_name = bool(JSONToolParser._HAS_NAME_RE.search(stripped))
    has_args = bool(JSONToolParser._HAS_ARGUMENTS_RE.search(stripped))
    if has_name and has_args:
        return True

    # Slow path: flat sibling arguments must pass the same registered-tool
    # namespace guard as the parser, so package metadata remains non-tool JSON.
    if has_name:
        return bool(JSONToolParser.parse(stripped))
    return False


__all__ = [
    "JSONToolParser",
    "is_json_tool_call",
    "parse_json_tool_calls",
]
