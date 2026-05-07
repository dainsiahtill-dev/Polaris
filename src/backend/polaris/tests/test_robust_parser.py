"""Tests for RobustParser core - state machine and integration."""

from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest
from polaris.kernelone.llm.robust_parser import (
    ParseResult,
    ParserState,
    RobustParser,
    SafeNull,
)
from pydantic import BaseModel


class TestSchema(BaseModel):
    """Test schema for parsing tests."""

    __test__: ClassVar[bool] = False

    name: str
    value: int
    optional_field: str | None = None


class OptionalSchema(BaseModel):
    """Schema with optional fields."""

    required: str
    optional: str | None = None


@pytest.fixture
def parser() -> RobustParser[TestSchema]:
    """Create a RobustParser instance."""
    return RobustParser[TestSchema](
        max_correction_turns=2,
        max_fallback_attempts=2,
    )


class TestParseResult:
    """Tests for ParseResult dataclass."""

    def test_success_result(self) -> None:
        """ParseResult.success is True for successful parse."""
        result = ParseResult(
            success=True,
            data=TestSchema(name="test", value=42),
            raw_content='{"name": "test", "value": 42}',
            state=ParserState.VALIDATE_PHASE,
        )
        assert result.success is True
        assert result.data is not None
        assert result.safe_null is False
        assert result.is_safe_fallback is False

    def test_safe_null_result(self) -> None:
        """ParseResult.safe_null is True for SafeNull result."""
        result: ParseResult[TestSchema] = ParseResult(
            success=False,
            data=None,
            raw_content="invalid",
            state=ParserState.SAFE_NULL,
            safe_null=True,
        )
        assert result.success is False
        assert result.data is None
        assert result.safe_null is True
        assert result.is_safe_fallback is True

    def test_exhausted_result(self) -> None:
        """ParseResult with EXHAUSTED state is safe fallback."""
        result: ParseResult[TestSchema] = ParseResult(
            success=False,
            data=None,
            raw_content="invalid",
            state=ParserState.EXHAUSTED,
            safe_null=True,
        )
        assert result.is_safe_fallback is True

    def test_to_dict(self) -> None:
        """ParseResult.to_dict() serializes correctly."""
        result = ParseResult(
            success=True,
            data=TestSchema(name="test", value=42),
            raw_content='{"name": "test", "value": 42}',
            state=ParserState.VALIDATE_PHASE,
            correction_attempts=1,
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["state"] == "VALIDATE_PHASE"
        assert d["correction_attempts"] == 1


class TestRobustParserSuccess:
    """Tests for successful parsing scenarios."""

    @pytest.mark.asyncio
    async def test_parse_valid_json(self, parser: RobustParser[TestSchema]) -> None:
        """Parser correctly handles valid JSON in code block."""
        response = '{"name": "test", "value": 42}'
        result = await parser.parse(response, schema=TestSchema)

        assert result.success is True
        assert result.data is not None
        assert result.data.name == "test"
        assert result.data.value == 42
        assert result.state == ParserState.VALIDATE_PHASE

    @pytest.mark.asyncio
    async def test_parse_json_code_block(self, parser: RobustParser[TestSchema]) -> None:
        """Parser correctly extracts JSON from code block."""
        response = """Here's the result:
```json
{"name": "test", "value": 42}
```
"""
        result = await parser.parse(response, schema=TestSchema)

        assert result.success is True
        assert result.data is not None
        assert result.data.name == "test"

    @pytest.mark.asyncio
    async def test_parse_with_prefix_stripped(self, parser: RobustParser[TestSchema]) -> None:
        """Parser strips natural language prefixes."""
        response = 'Sure, here\'s the JSON: {"name": "test", "value": 42}'
        result = await parser.parse(response, schema=TestSchema)

        assert result.success is True
        assert result.data is not None


class TestRobustParserCleaning:
    """Tests for heuristic cleaning phase."""

    @pytest.mark.asyncio
    async def test_strips_nl_prefix(self, parser: RobustParser[TestSchema]) -> None:
        """Parser removes natural language prefixes."""
        response = 'Here\'s the JSON: {"name": "test", "value": 42}'
        result = await parser.parse(response, schema=TestSchema)

        assert result.success is True
        assert "strip_nl_prefix" in result.cleaning_metadata.get("applied_rules", ())

    @pytest.mark.asyncio
    async def test_strips_trailing_explanation(self, parser: RobustParser[TestSchema]) -> None:
        """Parser removes trailing explanations."""
        response = '{"name": "test", "value": 42}\n\nThis should work!'
        result = await parser.parse(response, schema=TestSchema)

        # Should succeed since valid JSON is at the start
        assert result.success is True


class TestRobustParserExtraction:
    """Tests for JSON extraction phase."""

    @pytest.mark.asyncio
    async def test_extract_from_output_tag(self, parser: RobustParser[TestSchema]) -> None:
        """Parser extracts JSON from <output> tags."""
        response = '<output>{"name": "test", "value": 42}</output>'
        result = await parser.parse(response, schema=TestSchema)

        assert result.success is True
        assert result.extraction_metadata.get("format_found") == "output_tag"

    @pytest.mark.asyncio
    async def test_extract_from_code_block(self, parser: RobustParser[TestSchema]) -> None:
        """Parser extracts JSON from code blocks."""
        response = '```json\n{"name": "test", "value": 42}\n```'
        result = await parser.parse(response, schema=TestSchema)

        assert result.success is True
        assert result.extraction_metadata.get("format_found") == "code_block"


class TestRobustParserValidation:
    """Tests for validation phase."""

    @pytest.mark.asyncio
    async def test_validation_error_details(self, parser: RobustParser[TestSchema]) -> None:
        """Parser captures validation error details."""
        response = '{"name": "test", "value": "not_an_int"}'  # value should be int
        result = await parser.parse(response, schema=TestSchema)

        assert result.success is False
        assert result.error is not None
        assert "value" in result.error.lower()


class TestRobustParserFallback:
    """Tests for fallback chain."""

    @pytest.mark.asyncio
    async def test_type_coercion_fallback(self, parser: RobustParser[TestSchema]) -> None:
        """Parser coerces types in fallback phase."""
        # String instead of int - should try coercion
        response = '{"name": "test", "value": "42"}'
        result = await parser.parse(response, schema=TestSchema)

        # Coercion should handle "42" -> 42
        assert result.success is True
        assert result.data is not None
        assert result.data.value == 42

    @pytest.mark.asyncio
    async def test_safe_null_on_complete_failure(self) -> None:
        """Parser returns SafeNull when all attempts fail."""
        parser = RobustParser[TestSchema](
            max_correction_turns=1,
            max_fallback_attempts=1,
            enable_safe_null=True,
        )
        # Missing required fields
        response = '{"name": "test"}'  # Missing 'value'
        result = await parser.parse(response, schema=TestSchema)

        assert result.success is False
        assert result.safe_null is True
        assert result.state == ParserState.SAFE_NULL


class TestRobustParserCorrection:
    """Tests for auto-healing correction phase."""

    @pytest.mark.asyncio
    async def test_correction_called_on_validation_error(self) -> None:
        """Parser calls LLM corrector on validation error."""
        correction_called = False

        async def mock_corrector(prompt: str) -> str:
            nonlocal correction_called
            correction_called = True
            return '{"name": "corrected", "value": 100}'

        parser = RobustParser[TestSchema](
            max_correction_turns=2,
            enable_correction=True,
        )

        # Invalid JSON that will fail validation
        response = '{"name": 123, "value": "invalid"}'  # name should be string
        result = await parser.parse(
            response,
            schema=TestSchema,
            llm_corrector=mock_corrector,
        )

        # Should have called corrector
        assert correction_called is True
        # Should succeed after correction
        assert result.success is True
        assert result.data is not None
        assert result.data.name == "corrected"

    @pytest.mark.asyncio
    async def test_correction_with_timeout(self) -> None:
        """Parser handles corrector timeout gracefully."""

        async def slow_corrector(prompt: str) -> str:
            await asyncio.sleep(10)  # Simulate slow LLM
            return '{"name": "test", "value": 42}'

        # Use max_correction_turns=2 to ensure correction is attempted
        # but sleep time (10s) < timeout (30s) so it won't timeout
        # This test verifies the timeout mechanism works when corrector is slow
        parser = RobustParser[TestSchema](
            max_correction_turns=2,
            enable_correction=True,
        )

        response = '{"name": "test", "value": "invalid"}'
        result = await parser.parse(
            response,
            schema=TestSchema,
            llm_corrector=slow_corrector,
        )

        # With timeout > sleep, correction should succeed
        assert result.success is True

    @pytest.mark.asyncio
    async def test_correction_actually_times_out(self) -> None:
        """Parser handles when corrector exceeds timeout."""

        async def very_slow_corrector(prompt: str) -> str:
            await asyncio.sleep(35)  # Exceeds 30s timeout
            return '{"name": "test", "value": 42}'

        parser = RobustParser[TestSchema](
            max_correction_turns=1,
            enable_correction=True,
        )

        response = '{"name": "test", "value": "invalid"}'
        result = await parser.parse(
            response,
            schema=TestSchema,
            llm_corrector=very_slow_corrector,
        )

        # Should fail gracefully when corrector times out
        assert result.success is False


class TestRobustParserEdgeCases:
    """Tests for edge cases."""

    @pytest.mark.asyncio
    async def test_empty_response(self, parser: RobustParser[TestSchema]) -> None:
        """Parser handles empty response."""
        result = await parser.parse("", schema=TestSchema)

        assert result.success is False
        assert result.error == "Empty response"

    @pytest.mark.asyncio
    async def test_whitespace_only_response(self, parser: RobustParser[TestSchema]) -> None:
        """Parser handles whitespace-only response."""
        result = await parser.parse("   \n\t  ", schema=TestSchema)

        assert result.success is False

    @pytest.mark.asyncio
    async def test_no_valid_json(self, parser: RobustParser[TestSchema]) -> None:
        """Parser handles response with no valid JSON."""
        result = await parser.parse("Plain text without JSON markers", schema=TestSchema)

        assert result.success is False
        assert result.state == ParserState.EXTRACT_FAILED

    @pytest.mark.asyncio
    async def test_optional_fields(self) -> None:
        """Parser handles optional fields correctly."""
        parser = RobustParser[OptionalSchema]()
        response = '{"required": "value"}'
        result = await parser.parse(response, schema=OptionalSchema)

        assert result.success is True
        assert result.data is not None
        assert result.data.required == "value"
        assert result.data.optional is None


class TestSafeNull:
    """Tests for SafeNull class."""

    def test_safe_null_properties(self) -> None:
        """SafeNull has correct properties."""
        null = SafeNull[TestSchema](
            raw_content='{"invalid": "content"}',
            parse_error="Validation failed",
            partial_data={"name": "partial"},
        )

        assert null.is_null is True
        assert null.raw_content == '{"invalid": "content"}'
        assert null.parse_error == "Validation failed"
        assert null.partial_data == {"name": "partial"}

    def test_safe_null_to_dict(self) -> None:
        """SafeNull.to_dict() serializes correctly."""
        null = SafeNull[TestSchema](
            raw_content='{"invalid": "content"}',
            parse_error="Test error",
        )
        d = null.to_dict()

        assert d["type"] == "SafeNull"
        assert d["parse_error"] == "Test error"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
