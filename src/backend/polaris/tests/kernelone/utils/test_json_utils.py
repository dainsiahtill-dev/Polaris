"""Tests for polaris.kernelone.utils.json_utils - edge cases and integration."""

from __future__ import annotations

import json

from polaris.kernelone.utils.json_utils import (
    format_json,
    parse_json_payload,
    safe_json_loads,
)


class TestSafeJsonLoadsEdgeCases:
    def test_whitespace_only_string(self) -> None:
        assert safe_json_loads("   ") is None

    def test_null_json(self) -> None:
        assert safe_json_loads("null") is None

    def test_boolean_json(self) -> None:
        assert safe_json_loads("true") is True
        assert safe_json_loads("false") is False

    def test_number_json(self) -> None:
        assert safe_json_loads("42") == 42
        assert safe_json_loads("3.14") == 3.14

    def test_string_json(self) -> None:
        assert safe_json_loads('"hello"') == "hello"

    def test_nested_json(self) -> None:
        data = {"a": {"b": [1, 2, {"c": "d"}]}}
        assert safe_json_loads(json.dumps(data)) == data

    def test_custom_default_for_empty(self) -> None:
        assert safe_json_loads("", default=[]) == []

    def test_malformed_with_default(self) -> None:
        assert safe_json_loads("{invalid}", default={"fallback": True}) == {"fallback": True}


class TestParseJsonPayloadEdgeCases:
    def test_none_input(self) -> None:
        assert parse_json_payload("") is None

    def test_whitespace_only(self) -> None:
        assert parse_json_payload("   ") is None

    def test_markdown_with_language_and_extra_whitespace(self) -> None:
        text = '```python\n{"key": "value"}\n```'
        assert parse_json_payload(text) == {"key": "value"}

    def test_json_with_leading_trailing_text(self) -> None:
        text = 'Before text {"key": "value"} after text'
        assert parse_json_payload(text) == {"key": "value"}

    def test_nested_json_in_markdown(self) -> None:
        text = '```json\n{"outer": {"inner": 1}}\n```'
        assert parse_json_payload(text) == {"outer": {"inner": 1}}

    def test_invalid_then_valid_json_substring(self) -> None:
        text = 'not json {"valid": true} trailing'
        assert parse_json_payload(text) == {"valid": True}

    def test_no_braces_at_all(self) -> None:
        assert parse_json_payload("no braces here") is None

    def test_empty_braces(self) -> None:
        assert parse_json_payload("{}") == {}


class TestFormatJsonEdgeCases:
    def test_format_none(self) -> None:
        assert format_json(None) == "null"

    def test_format_nested_structure(self) -> None:
        data = {"list": [1, 2], "nested": {"a": "b"}}
        result = format_json(data)
        parsed = json.loads(result)
        assert parsed == data

    def test_custom_indent(self) -> None:
        data = {"key": "value"}
        result = format_json(data, indent=4)
        assert '    "key"' in result

    def test_format_list(self) -> None:
        data = [1, 2, 3]
        result = format_json(data)
        assert json.loads(result) == data

    def test_unicode_characters(self) -> None:
        data = {"emoji": "\ud83c\udf00", "chinese": "\u4e2d\u6587"}
        result = format_json(data)
        assert "\ud83c\udf00" in result
        assert "\u4e2d\u6587" in result
