"""Tests for json_utils module."""

from __future__ import annotations

import json

from polaris.kernelone.utils.json_utils import (
    format_json,
    parse_json_payload,
    safe_json_loads,
)


class TestSafeJsonLoads:
    """Tests for safe_json_loads function."""

    def test_valid_dict(self) -> None:
        """Parse a valid JSON object."""
        result = safe_json_loads('{"key": "value"}')
        assert result == {"key": "value"}

    def test_valid_list(self) -> None:
        """Parse a valid JSON array."""
        result = safe_json_loads("[1, 2, 3]")
        assert result == [1, 2, 3]

    def test_valid_string(self) -> None:
        """Parse a valid JSON string."""
        result = safe_json_loads('"hello"')
        assert result == "hello"

    def test_valid_number(self) -> None:
        """Parse a valid JSON number."""
        result = safe_json_loads("42")
        assert result == 42

    def test_valid_boolean(self) -> None:
        """Parse a valid JSON boolean."""
        result = safe_json_loads("true")
        assert result is True

    def test_valid_null(self) -> None:
        """Parse a valid JSON null."""
        result = safe_json_loads("null")
        assert result is None

    def test_invalid_json_returns_none(self) -> None:
        """Invalid JSON returns None by default."""
        result = safe_json_loads("not json")
        assert result is None

    def test_invalid_json_returns_default(self) -> None:
        """Invalid JSON returns the provided default."""
        result = safe_json_loads("not json", default={})
        assert result == {}

    def test_empty_string_returns_default(self) -> None:
        """Empty string returns default."""
        result = safe_json_loads("")
        assert result is None

    def test_empty_string_with_custom_default(self) -> None:
        """Empty string returns custom default."""
        result = safe_json_loads("", default=[])
        assert result == []

    def test_none_input_returns_default(self) -> None:
        """None input returns default (falsy check)."""
        result = safe_json_loads(None)  # type: ignore[arg-type]
        assert result is None

    def test_nested_structure(self) -> None:
        """Parse deeply nested JSON."""
        data = {"a": {"b": {"c": [1, 2, {"d": True}]}}}
        result = safe_json_loads(json.dumps(data))
        assert result == data

    def test_unicode_content(self) -> None:
        """Parse JSON with unicode characters."""
        result = safe_json_loads('{"text": "你好世界"}')
        assert result == {"text": "你好世界"}

    def test_malformed_json_partial(self) -> None:
        """Partial JSON is rejected."""
        result = safe_json_loads('{"key": "val')
        assert result is None


class TestParseJsonPayload:
    """Tests for parse_json_payload function."""

    def test_raw_json(self) -> None:
        """Parse raw JSON string."""
        result = parse_json_payload('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_in_markdown_block(self) -> None:
        """Parse JSON inside markdown code block."""
        text = "```json\n{\"key\": \"value\"}\n```"
        result = parse_json_payload(text)
        assert result == {"key": "value"}

    def test_json_in_plain_markdown_block(self) -> None:
        """Parse JSON inside plain markdown code block."""
        text = "```\n{\"key\": \"value\"}\n```"
        result = parse_json_payload(text)
        assert result == {"key": "value"}

    def test_empty_string_returns_none(self) -> None:
        """Empty string returns None."""
        result = parse_json_payload("")
        assert result is None

    def test_none_input_returns_none(self) -> None:
        """None input returns None."""
        result = parse_json_payload(None)  # type: ignore[arg-type]
        assert result is None

    def test_json_embedded_in_text(self) -> None:
        """Extract JSON from surrounding text."""
        text = 'Here is the result: {"result": "ok"} Thanks!'
        result = parse_json_payload(text)
        assert result == {"result": "ok"}

    def test_no_json_found_returns_none(self) -> None:
        """Text without JSON returns None."""
        result = parse_json_payload("This is just plain text")
        assert result is None

    def test_empty_markdown_block(self) -> None:
        """Empty markdown block returns None."""
        result = parse_json_payload("```json\n\n```")
        assert result is None

    def test_whitespace_only(self) -> None:
        """Whitespace-only input returns None."""
        result = parse_json_payload("   ")
        assert result is None

    def test_nested_json_extraction(self) -> None:
        """Extract nested JSON from text."""
        text = 'Start {"outer": {"inner": 1}} End'
        result = parse_json_payload(text)
        assert result == {"outer": {"inner": 1}}

    def test_invalid_json_in_block_returns_none(self) -> None:
        """Invalid JSON in markdown block returns None."""
        text = "```json\nnot valid json\n```"
        result = parse_json_payload(text)
        assert result is None


class TestFormatJson:
    """Tests for format_json function."""

    def test_format_dict(self) -> None:
        """Format a dictionary."""
        result = format_json({"key": "value"})
        assert '"key": "value"' in result

    def test_format_list(self) -> None:
        """Format a list."""
        result = format_json([1, 2, 3])
        assert result == "[\n  1,\n  2,\n  3\n]"

    def test_format_with_custom_indent(self) -> None:
        """Format with custom indentation."""
        result = format_json({"a": 1}, indent=4)
        assert "    \"a\": 1" in result

    def test_unicode_preserved(self) -> None:
        """Unicode characters are preserved."""
        result = format_json({"text": "你好"})
        assert "你好" in result

    def test_format_none(self) -> None:
        """Format None value."""
        result = format_json(None)
        assert result == "null"

    def test_format_boolean(self) -> None:
        """Format boolean value."""
        result = format_json(True)
        assert result == "true"


class TestModuleExports:
    """Tests for module public API."""

    def test_all_exports_present(self) -> None:
        """All expected functions are importable."""
        from polaris.kernelone.utils import json_utils

        assert hasattr(json_utils, "safe_json_loads")
        assert hasattr(json_utils, "parse_json_payload")
        assert hasattr(json_utils, "format_json")
        assert hasattr(json_utils, "_safe_json_loads")
        assert hasattr(json_utils, "_parse_json_payload")

    def test_backward_compatibility_aliases(self) -> None:
        """Backward compatibility aliases work."""
        from polaris.kernelone.utils.json_utils import _parse_json_payload, _safe_json_loads

        assert _safe_json_loads is safe_json_loads
        assert _parse_json_payload is parse_json_payload
