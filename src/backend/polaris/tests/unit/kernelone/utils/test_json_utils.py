"""Tests for polaris.kernelone.utils.json_utils."""

from __future__ import annotations

from polaris.kernelone.utils.json_utils import (
    format_json,
    parse_json_payload,
    safe_json_loads,
)


class TestSafeJsonLoads:
    def test_valid_json(self) -> None:
        result = safe_json_loads('{"key": "value"}')
        assert result == {"key": "value"}

    def test_invalid_json(self) -> None:
        assert safe_json_loads("invalid json") is None

    def test_empty_string(self) -> None:
        assert safe_json_loads("") is None

    def test_custom_default(self) -> None:
        assert safe_json_loads("bad", default={}) == {}

    def test_list_json(self) -> None:
        assert safe_json_loads("[1, 2, 3]") == [1, 2, 3]


class TestParseJsonPayload:
    def test_plain_json(self) -> None:
        result = parse_json_payload('{"key": "value"}')
        assert result == {"key": "value"}

    def test_markdown_json_block(self) -> None:
        result = parse_json_payload('```json\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_markdown_plain_block(self) -> None:
        result = parse_json_payload('```\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_empty_string(self) -> None:
        assert parse_json_payload("") is None

    def test_json_embedded_in_text(self) -> None:
        result = parse_json_payload('Here is the result: {"key": "value"} Thanks!')
        assert result == {"key": "value"}

    def test_invalid_json(self) -> None:
        assert parse_json_payload("not json here") is None


class TestFormatJson:
    def test_format_dict(self) -> None:
        result = format_json({"key": "value"})
        assert result == '{\n  "key": "value"\n}'

    def test_format_list(self) -> None:
        result = format_json([1, 2, 3])
        assert "1" in result
        assert "2" in result

    def test_unicode_preserved(self) -> None:
        result = format_json({"msg": "你好"})
        assert "你好" in result
