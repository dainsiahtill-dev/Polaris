"""Legacy textual tool-call sanitizer/detector for `roles.kernel`.

Native provider/tool-call payloads are the only executable runtime protocol.
This module remains for transcript sanitization, wrapper detection, and
migration-period auditing. It must not be treated as an executable tool source
in runtime paths.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from polaris.kernelone.llm.toolkit.tool_normalization import (
    normalize_tool_arguments,
    normalize_tool_name,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

_WRAPPER_PATTERN = re.compile(
    r"\[(?P<bracket_tag>tool_calls?|TOOL_CALLS?)\](?P<bracket_payload>.*?)\[/\1\]"
    r"|<(?P<angle_tag>tool_calls?)>(?P<angle_payload>.*?)</\3>",
    re.IGNORECASE | re.DOTALL,
)

# XML-style tool call pattern: <tool_name><args>...</args></tool_name>
# Supports nested elements like: <file><path>...</path></file>
_XML_TOOL_CALL_PATTERN = re.compile(
    r"<(?P<tool_name>[a-z_][a-z0-9_]*)\s*>"
    r"(?P<args_content>.*?)"
    r"</(?P=tool_name)\s*>",
    re.IGNORECASE | re.DOTALL,
)

_CODE_BLOCK_PATTERN = re.compile(r"(?P<fence>```|''').*?(?P=fence)", re.DOTALL)
_TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_META_KEYS = {
    "id",
    "tool",
    "name",
    "type",
    "function",
    "arguments",
    "args",
    "input",
    "reasoning",
    "source",
    "tool_calls",
    "calls",
}


@dataclass(frozen=True, slots=True)
class CanonicalToolCall:
    tool: str
    args: dict[str, Any]
    raw: str = ""


class CanonicalToolCallParser:
    """Parses textual tool-call envelopes for sanitization, detection, and audit only."""

    @classmethod
    def parse_text_calls(
        cls,
        text: str,
        *,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> list[CanonicalToolCall]:
        calls, _ = cls.extract_text_calls_and_remainder(
            text,
            allowed_tool_names=allowed_tool_names,
        )
        return calls

    @classmethod
    def extract_text_calls_and_remainder(
        cls,
        text: str,
        *,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> tuple[list[CanonicalToolCall], str]:
        source = str(text or "")
        if not source.strip():
            return [], ""

        allowed = {
            normalize_tool_name(str(item or ""))
            for item in (allowed_tool_names or [])
            if normalize_tool_name(str(item or ""))
        }
        protected_spans = cls._build_protected_spans(source)
        seen: set[tuple[str, str]] = set()
        calls: list[CanonicalToolCall] = []
        accepted_ranges: list[tuple[int, int]] = []

        for match in _WRAPPER_PATTERN.finditer(source):
            if cls._is_match_protected(source, match.start(), protected_spans):
                continue
            payload = str(match.group("bracket_payload") or match.group("angle_payload") or "").strip()
            decoded_calls = cls._decode_payload(payload)
            accepted_here = False
            for decoded in decoded_calls:
                tool_name = normalize_tool_name(str(decoded.get("tool") or ""))
                if not tool_name or not _TOOL_NAME_PATTERN.fullmatch(tool_name):
                    continue
                if allowed and tool_name not in allowed:
                    continue
                args = normalize_tool_arguments(tool_name, decoded.get("args"))
                signature = (
                    tool_name,
                    json.dumps(args, ensure_ascii=False, sort_keys=True),
                )
                if signature in seen:
                    continue
                seen.add(signature)
                calls.append(
                    CanonicalToolCall(
                        tool=tool_name,
                        args=args,
                        raw=str(match.group(0) or ""),
                    )
                )
                accepted_here = True
            if accepted_here:
                accepted_ranges.append((match.start(), match.end()))

        # Parse XML-style tool calls: <tool_name><args>...</args></tool_name>
        for match in _XML_TOOL_CALL_PATTERN.finditer(source):
            if cls._is_match_protected(source, match.start(), protected_spans):
                continue
            tool_name = normalize_tool_name(str(match.group("tool_name") or "").strip())
            if not tool_name or not _TOOL_NAME_PATTERN.fullmatch(tool_name):
                continue
            if allowed and tool_name not in allowed:
                continue

            args_content = str(match.group("args_content") or "").strip()
            raw_args = cls._parse_xml_args(args_content, tool_name)
            args = normalize_tool_arguments(tool_name, raw_args)

            # Expand array arguments into multiple tool calls
            # e.g., read_file with file=['a.py', 'b.py'] -> 2 separate calls
            expanded_calls = cls._expand_array_args(tool_name, args, str(match.group(0) or ""))

            for expanded_args, expanded_raw in expanded_calls:
                signature = (
                    tool_name,
                    json.dumps(expanded_args, ensure_ascii=False, sort_keys=True),
                )
                if signature in seen:
                    continue
                seen.add(signature)
                calls.append(
                    CanonicalToolCall(
                        tool=tool_name,
                        args=expanded_args,
                        raw=expanded_raw,
                    )
                )
            accepted_ranges.append((match.start(), match.end()))

        return calls, cls._strip_ranges(source, accepted_ranges).strip()

    @classmethod
    def _decode_payload(cls, payload: str) -> list[dict[str, Any]]:
        parsed = cls._decode_json_value(payload)
        if isinstance(parsed, dict):
            nested_calls = parsed.get("tool_calls")
            if isinstance(nested_calls, list):
                return [item for item in (cls._normalize_call_item(raw) for raw in nested_calls) if item]
            nested_calls = parsed.get("calls")
            if isinstance(nested_calls, list):
                return [item for item in (cls._normalize_call_item(raw) for raw in nested_calls) if item]
            single = cls._normalize_call_item(parsed)
            return [single] if single else []
        if isinstance(parsed, list):
            return [item for item in (cls._normalize_call_item(raw) for raw in parsed) if item]
        return []

    @classmethod
    def _normalize_call_item(cls, raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None

        function_payload = raw.get("function")
        if not isinstance(function_payload, dict):
            function_payload = {}

        tool_name = (
            str(raw.get("tool") or "").strip()
            or str(raw.get("name") or "").strip()
            or str(function_payload.get("name") or "").strip()
        )
        if not tool_name:
            return None

        args = cls._coerce_arguments(raw.get("arguments"))
        if not args:
            args = cls._coerce_arguments(raw.get("args"))
        if not args:
            args = cls._coerce_arguments(raw.get("input"))
        if not args:
            args = cls._coerce_arguments(function_payload.get("arguments"))
        if not args:
            args = cls._coerce_arguments(function_payload.get("args"))
        if not args:
            args = cls._coerce_arguments(function_payload.get("input"))
        if not args:
            args = {str(key): value for key, value in raw.items() if str(key) not in _META_KEYS}

        return {
            "tool": tool_name,
            "args": args,
        }

    @staticmethod
    def _coerce_arguments(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        decoded = CanonicalToolCallParser._decode_json_value(value)
        if isinstance(decoded, dict):
            return dict(decoded)
        return {}

    @staticmethod
    def _decode_json_value(value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return value
        if not isinstance(value, str):
            return None
        token = value.strip()
        if not token:
            return None
        try:
            return json.loads(token)
        except (RuntimeError, ValueError):
            return None

    @classmethod
    def _parse_xml_args(cls, args_content: str, tool_name: str) -> dict[str, Any]:
        """Parse XML-style arguments into a dictionary.

        Supports formats like:
        - <file><path>config.py</path></file>
        - <file><path>config.py</path></file><file><path>server.py</path></file>
        - <query>search term</query><path>src/</path>
        - <args>...</args> wrapper
        """
        if not args_content.strip():
            return {}

        result: dict[str, Any] = {}
        # Track arrays for elements that appear multiple times (like <file>)
        array_elements: dict[str, Any] = {}

        # Remove <args> wrapper if present
        content = args_content.strip()
        if content.startswith("<args>") and ("</args>" in content or "</ARGS>" in content.upper()):
            # Find the closing </args> tag
            end_match = re.search(r"</args\s*>", content, re.IGNORECASE)
            if end_match:
                content = content[6 : end_match.start()].strip()

        # Pattern to match XML-style key-value or nested elements
        # Handles: <key>value</key> or <key><nested>value</nested></key>
        xml_element_pattern = re.compile(r"<([a-z_][a-z0-9_]*)\s*>(.*?)</\1\s*>", re.IGNORECASE | re.DOTALL)

        for match in xml_element_pattern.finditer(content):
            key = match.group(1).lower()
            inner = match.group(2).strip()

            # Check if inner content has nested elements
            nested_pattern = re.compile(r"<([a-z_][a-z0-9_]*)\s*>(.*?)</\1\s*>", re.IGNORECASE | re.DOTALL)
            has_nested = bool(nested_pattern.search(inner))

            if has_nested:
                # Parse nested structure recursively WITHOUT normalization
                nested_result = cls._parse_xml_args(inner, tool_name)

                # Handle special case: <file><path>...</path></file> should extract path value
                # Check for "path" in raw result before normalization converts it
                if key == "file" and "path" in nested_result:
                    # Extract the path value from nested structure
                    path_value = nested_result["path"]
                    existing = array_elements.get(key)
                    if existing is None:
                        array_elements[key] = [path_value]
                    elif isinstance(existing, list):
                        existing.append(path_value)
                    else:
                        # Was a single value, convert to list
                        array_elements[key] = [existing, path_value]
                else:
                    existing = array_elements.get(key)
                    if existing is None:
                        array_elements[key] = [nested_result]
                    elif isinstance(existing, list):
                        existing.append(nested_result)
                    else:
                        # Was a single value, convert to list
                        array_elements[key] = [existing, nested_result]
            else:
                # Simple key-value: <path>config.py</path>
                existing_val = array_elements.get(key)
                if existing_val is None:
                    array_elements[key] = inner
                elif isinstance(existing_val, list):
                    existing_val.append(inner)
                else:
                    # Was a single value, convert to list
                    array_elements[key] = [existing_val, inner]

        # Convert arrays to lists, single values stay as-is
        for key, value in array_elements.items():
            if isinstance(value, list) and len(value) > 0:
                if isinstance(value[0], dict):
                    # Array of objects: [{"path": "..."}, {"path": "..."}]
                    result[key] = value
                elif all(isinstance(v, str) for v in value):
                    # Multiple simple values: ["a", "b", "c"]
                    result[key] = value
                else:
                    result[key] = value
            else:
                result[key] = value

        # Only normalize at the end, and only for the top-level call
        return result

    @classmethod
    def _expand_array_args(
        cls,
        tool_name: str,
        args: dict[str, Any],
        raw: str,
    ) -> list[tuple[dict[str, Any], str]]:
        """Expand array arguments into multiple tool calls.

        For example:
        - read_file with file=['a.py', 'b.py'] -> 2 calls with file='a.py' and file='b.py'
        - search_code with file_patterns=['*.py', '*.js'] -> 1 call (keep as array)

        Returns list of (expanded_args, expanded_raw) tuples.
        """
        # Tools that support array arguments
        array_support_tools = {"search_code", "grep", "ripgrep", "glob", "list_directory"}

        if tool_name in array_support_tools:
            # These tools can handle arrays
            return [(args, raw)]

        # Find array arguments that should be expanded
        expandable_keys = []
        for key, value in args.items():
            if (
                isinstance(value, list)
                and len(value) > 0
                and all(isinstance(v, str) for v in value)
                and key in ("file", "files", "path", "paths", "filepath", "filepaths")
            ):
                expandable_keys.append(key)

        if not expandable_keys:
            return [(args, raw)]

        # Expand the first expandable key into multiple calls
        expand_key = expandable_keys[0]
        values = args[expand_key]
        results = []

        for value in values:
            expanded_args = {**args, expand_key: value}
            # Remove the key from args when creating individual calls
            results.append((expanded_args, raw))

        return results

    @staticmethod
    def _build_protected_spans(text: str) -> list[tuple[int, int]]:
        return [(match.start(), match.end()) for match in _CODE_BLOCK_PATTERN.finditer(text)]

    @classmethod
    def _is_match_protected(
        cls,
        text: str,
        start: int,
        spans: list[tuple[int, int]],
    ) -> bool:
        if cls._is_quoted_line(text, start):
            return True
        return any(span_start <= start < span_end for span_start, span_end in spans)

    @staticmethod
    def _is_quoted_line(text: str, start: int) -> bool:
        line_start = text.rfind("\n", 0, start)
        line_start = 0 if line_start < 0 else line_start + 1
        line_end = text.find("\n", start)
        if line_end < 0:
            line_end = len(text)
        return text[line_start:line_end].lstrip().startswith(">")

    @staticmethod
    def _strip_ranges(text: str, ranges: list[tuple[int, int]]) -> str:
        if not ranges:
            return text
        ordered = sorted(ranges, key=lambda item: item[0])
        merged: list[tuple[int, int]] = [ordered[0]]
        for start, end in ordered[1:]:
            last_start, last_end = merged[-1]
            if start <= last_end:
                merged[-1] = (last_start, max(last_end, end))
            else:
                merged.append((start, end))

        parts: list[str] = []
        cursor = 0
        for start, end in merged:
            if start > cursor:
                parts.append(text[cursor:start])
            cursor = end
        if cursor < len(text):
            parts.append(text[cursor:])
        return "".join(parts)


__all__ = [
    "CanonicalToolCall",
    "CanonicalToolCallParser",
]
