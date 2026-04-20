"""Tests for JSONExtractor - JSON variant coverage."""

from __future__ import annotations

import pytest
from polaris.kernelone.llm.robust_parser.extractors import (
    ExtractionResult,
    JSONExtractor,
)


class TestJSONExtractor:
    """Tests for JSONExtractor class."""

    def test_extract_from_code_block(self) -> None:
        """Extracts JSON from ```json ... ``` blocks."""
        extractor = JSONExtractor()
        result = extractor.extract('```json\n{"key": "value"}\n```')

        assert result.data is not None
        assert result.data == {"key": "value"}
        assert result.format_found == "code_block"

    def test_extract_from_triple_quote_code_block(self) -> None:
        """Extracts JSON from ``` ... ``` without language spec."""
        extractor = JSONExtractor()
        result = extractor.extract('```\n{"key": "value"}\n```')

        assert result.data is not None
        assert result.data == {"key": "value"}
        assert result.format_found == "code_block"

    def test_extract_from_output_tag(self) -> None:
        """Extracts JSON from <output> tags."""
        extractor = JSONExtractor()
        result = extractor.extract('<output>{"key": "value"}</output>')

        assert result.data is not None
        assert result.data == {"key": "value"}
        assert result.format_found == "output_tag"

    def test_extract_from_result_tag(self) -> None:
        """Extracts JSON from <result> tags."""
        extractor = JSONExtractor()
        result = extractor.extract('<result>{"key": "value"}</result>')

        assert result.data is not None
        assert result.data == {"key": "value"}
        assert result.format_found == "result_tag"

    def test_extract_inline_json(self) -> None:
        """Extracts inline JSON object."""
        extractor = JSONExtractor()
        result = extractor.extract('Here is the data: {"key": "value"}')

        assert result.data is not None
        assert result.data == {"key": "value"}
        assert result.format_found == "inline"

    def test_extract_raw_json(self) -> None:
        """Extracts raw JSON without wrapper."""
        extractor = JSONExtractor()
        result = extractor.extract('{"key": "value"}')

        assert result.data is not None
        assert result.data == {"key": "value"}
        # May be inline or raw depending on extraction order
        assert result.format_found in ("raw", "inline")

    def test_extract_json_array(self) -> None:
        """Extracts JSON array."""
        extractor = JSONExtractor()
        result = extractor.extract('["item1", "item2", "item3"]')

        assert result.data is not None
        assert result.data == ["item1", "item2", "item3"]

    def test_extract_nested_json(self) -> None:
        """Extracts nested JSON objects."""
        extractor = JSONExtractor()
        nested = '{"outer": {"inner": {"deep": "value"}}}'
        result = extractor.extract(nested)

        assert result.data is not None
        assert isinstance(result.data, dict)
        assert result.data["outer"]["inner"]["deep"] == "value"

    def test_extract_with_whitespace_in_code_block(self) -> None:
        """Handles whitespace padding in code blocks."""
        extractor = JSONExtractor()
        result = extractor.extract('  ```json  \n{"key": "value"}\n  ```  ')

        assert result.data is not None
        assert result.data == {"key": "value"}


class TestJSONExtractorEdgeCases:
    """Tests for edge cases."""

    def test_empty_input(self) -> None:
        """Handles empty input."""
        extractor = JSONExtractor()
        result = extractor.extract("")

        assert result.data is None
        assert result.error == "Empty input text"

    def test_whitespace_only_input(self) -> None:
        """Handles whitespace-only input."""
        extractor = JSONExtractor()
        result = extractor.extract("   \n\t  ")

        assert result.data is None
        assert result.error == "Empty input text"

    def test_no_json_found(self) -> None:
        """Returns error when no JSON found."""
        extractor = JSONExtractor()
        result = extractor.extract("This is not JSON at all")

        assert result.data is None
        assert result.error is not None

    def test_invalid_json_in_code_block(self) -> None:
        """Returns error for invalid JSON in code block."""
        extractor = JSONExtractor()
        result = extractor.extract("```json\ninvalid json{ broken\n```")

        assert result.data is None
        assert result.error is not None

    def test_multiple_code_blocks_takes_first(self) -> None:
        """Takes first valid JSON from multiple code blocks."""
        extractor = JSONExtractor()
        text = """```json
{"first": true}
```
Some text
```json
{"second": true}
```"""
        result = extractor.extract(text)

        assert result.data is not None
        assert isinstance(result.data, dict)
        assert result.data.get("first") is True

    def test_case_insensitive_tags(self) -> None:
        """Handles case-insensitive tag names."""
        extractor = JSONExtractor()
        result = extractor.extract('<OUTPUT>{"key": "value"}</OUTPUT>')
        assert result.format_found == "output_tag"

    def test_json_with_unicode(self) -> None:
        """Handles JSON with Unicode characters."""
        extractor = JSONExtractor()
        result = extractor.extract('{"emoji": "😀", "chinese": "中文"}')

        assert result.data is not None
        assert isinstance(result.data, dict)
        assert result.data["emoji"] == "😀"
        assert result.data["chinese"] == "中文"

    def test_json_with_escaped_characters(self) -> None:
        """Handles JSON with escaped characters."""
        extractor = JSONExtractor()
        result = extractor.extract('{"path": "C:\\\\Users\\\\test", "newline": "line1\\nline2"}')

        assert result.data is not None
        assert "path" in result.data

    def test_json_with_nested_quotes(self) -> None:
        """Handles JSON with quotes inside strings."""
        extractor = JSONExtractor()
        result = extractor.extract('{"quote": "He said \\"Hello\\""}')

        assert result.data is not None
        assert isinstance(result.data, dict)
        assert result.data["quote"] == 'He said "Hello"'


class TestJSONExtractorConfiguration:
    """Tests for extractor configuration."""

    def test_disable_code_blocks(self) -> None:
        """Can disable code block extraction."""
        extractor = JSONExtractor(use_code_blocks=False)
        result = extractor.extract('```json\n{"key": "value"}\n```')

        assert result.format_found != "code_block"

    def test_disable_output_tags(self) -> None:
        """Can disable output tag extraction."""
        extractor = JSONExtractor(use_output_tags=False)
        result = extractor.extract('<output>{"key": "value"}</output>')

        assert result.format_found != "output_tag"

    def test_disable_result_tags(self) -> None:
        """Can disable result tag extraction."""
        extractor = JSONExtractor(use_result_tags=False)
        result = extractor.extract('<result>{"key": "value"}</result>')

        assert result.format_found != "result_tag"

    def test_disable_inline(self) -> None:
        """Can disable inline extraction."""
        extractor = JSONExtractor(use_inline=False)
        # Should fall back to raw
        result = extractor.extract('{"key": "value"}')

        assert result.format_found == "raw"

    def test_disable_raw(self) -> None:
        """Can disable raw extraction."""
        extractor = JSONExtractor(use_raw=False)
        # No code block or tags, just text
        result = extractor.extract("No JSON here")

        assert result.data is None


class TestExtractionResult:
    """Tests for ExtractionResult dataclass."""

    def test_frozen_immutable(self) -> None:
        """ExtractionResult is frozen."""
        result = ExtractionResult(data={"key": "value"}, format_found="raw", raw_match=None, error=None)

        with pytest.raises(AttributeError):
            result.data = {"modified": True}  # type: ignore

    def test_error_preserved(self) -> None:
        """Error message is preserved."""
        result = ExtractionResult(
            data=None,
            format_found=None,
            raw_match="some text",
            error="No JSON found",
        )

        assert result.error == "No JSON found"

    def test_raw_match_truncated(self) -> None:
        """raw_match can be used to see what was matched."""
        result = ExtractionResult(
            data={"key": "value"},
            format_found="raw",
            raw_match='{"key": "value"}',
            error=None,
        )

        assert result.raw_match is not None
        assert len(result.raw_match) <= 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
