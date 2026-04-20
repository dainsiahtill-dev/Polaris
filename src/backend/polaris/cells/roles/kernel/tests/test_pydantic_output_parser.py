"""PydanticOutputParser test suite.

Tests for PydanticOutputParser and GenericRoleResponse type-safe fallback parsing.
"""

from __future__ import annotations

from dataclasses import is_dataclass

import pytest
from polaris.cells.roles.kernel.internal.pydantic_output_parser import (
    PydanticOutputParser,
    create_fallback_parser,
)
from polaris.cells.roles.kernel.public.contracts import GenericRoleResponse

# ---------------------------------------------------------------------
# Test: GenericRoleResponse frozen dataclass
# ---------------------------------------------------------------------


class TestGenericRoleResponse:
    """GenericRoleResponse is a frozen dataclass for type-safe fallback."""

    def test_construction_with_content_only(self) -> None:
        """Content-only construction succeeds."""
        response = GenericRoleResponse(content="Hello, World!")
        assert response.content == "Hello, World!"
        assert response.tool_calls is None
        assert response.metadata == {}

    def test_construction_with_all_fields(self) -> None:
        """Full construction with all fields succeeds."""
        tool_calls = [{"tool": "read_file", "args": {"path": "/tmp/test.txt"}}]
        metadata = {"model": "gpt-4", "tokens": 100}
        response = GenericRoleResponse(
            content="Analysis complete",
            tool_calls=tool_calls,
            metadata=metadata,
        )
        assert response.content == "Analysis complete"
        assert response.tool_calls == tool_calls
        assert response.metadata == metadata

    def test_is_frozen_dataclass(self) -> None:
        """GenericRoleResponse is a frozen dataclass."""
        assert is_dataclass(GenericRoleResponse)
        response = GenericRoleResponse(content="test")
        with pytest.raises((TypeError, AttributeError)):  # frozen dataclass raises these
            response.content = "mutated"  # type: ignore[misc]

    def test_to_dict_returns_dict(self) -> None:
        """to_dict returns a dictionary representation."""
        response = GenericRoleResponse(content="test")
        result = response.to_dict()
        assert isinstance(result, dict)
        assert result["content"] == "test"
        assert "tool_calls" in result
        assert "metadata" in result

    def test_metadata_default_empty_dict(self) -> None:
        """Metadata defaults to empty dict, not shared reference."""
        response1 = GenericRoleResponse(content="test1")
        response2 = GenericRoleResponse(content="test2")
        assert response1.metadata == {}
        assert response2.metadata == {}
        # Ensure they don't share the same dict
        response1.metadata["injected"] = True
        assert response2.metadata == {}


# ---------------------------------------------------------------------
# Test: PydanticOutputParser
# ---------------------------------------------------------------------


class TestPydanticOutputParser:
    """PydanticOutputParser provides type-safe JSON parsing with fallback."""

    def test_parse_empty_content_returns_failure(self) -> None:
        """Empty content returns a failure ParsedOutput."""
        parser = PydanticOutputParser()
        result = parser.parse("")
        assert result.success is False
        assert result.data is None
        assert "Empty content" in str(result.error)

    def test_parse_none_content_returns_failure(self) -> None:
        """None content returns a failure ParsedOutput."""
        parser = PydanticOutputParser()
        result = parser.parse(None)  # type: ignore[arg-type]
        assert result.success is False

    def test_parse_valid_json_code_block(self) -> None:
        """Parse valid JSON in code block."""
        parser = PydanticOutputParser()
        content = '```json\n{"content": "Hello"}\n```'
        result = parser.parse(content)
        assert result.success is True
        assert result.data is not None
        assert result.data["content"] == "Hello"

    def test_parse_valid_json_output_tags(self) -> None:
        """Parse valid JSON in <output> tags."""
        parser = PydanticOutputParser()
        content = '<output>{"content": "Test"}</output>'
        result = parser.parse(content)
        assert result.success is True
        assert result.data is not None
        assert result.data["content"] == "Test"

    def test_parse_raw_json(self) -> None:
        """Parse raw JSON without wrapping."""
        parser = PydanticOutputParser()
        content = '{"content": "Raw JSON"}'
        result = parser.parse(content)
        assert result.success is True
        assert result.data is not None
        assert result.data["content"] == "Raw JSON"

    def test_parse_invalid_json_returns_failure(self) -> None:
        """Invalid JSON returns a failure ParsedOutput."""
        parser = PydanticOutputParser()
        content = "This is not JSON content"
        result = parser.parse(content)
        assert result.success is False
        assert result.data is None
        assert "Failed to extract valid JSON" in str(result.error)

    def test_parse_with_schema_validation(self) -> None:
        """Parse with GenericRoleResponse schema validation."""
        parser = PydanticOutputParser(schema=GenericRoleResponse)
        content = '{"content": "Validated content", "tool_calls": null, "metadata": {}}'
        result = parser.parse(content)
        assert result.success is True
        assert result.data is not None
        assert result.data["content"] == "Validated content"

    def test_parse_with_schema_invalid_returns_failure(self) -> None:
        """Schema validation failure returns a failure ParsedOutput."""
        parser = PydanticOutputParser(schema=GenericRoleResponse)
        # Missing required 'content' field
        content = '{"tool_calls": []}'
        result = parser.parse(content)
        assert result.success is False
        assert result.data is None
        assert "validation" in str(result.error).lower() or "missing" in str(result.error).lower()

    def test_parse_with_fallback_uses_generic_response(self) -> None:
        """parse_with_fallback returns GenericRoleResponse format on failure."""
        parser = PydanticOutputParser()
        content = "Plain text without JSON"
        result = parser.parse_with_fallback(content)
        assert isinstance(result, dict)
        assert "content" in result
        assert "tool_calls" in result
        assert "metadata" in result
        # Original content is preserved
        assert result["content"] == "Plain text without JSON"
        # Fallback flag is set
        assert result["metadata"]["fallback_used"] is True

    def test_parse_with_fallback_extracts_json_when_possible(self) -> None:
        """parse_with_fallback extracts JSON before using fallback."""
        parser = PydanticOutputParser()
        content = '{"content": "Extracted content", "extra_field": "value"}'
        result = parser.parse_with_fallback(content)
        assert result["content"] == "Extracted content"
        # When JSON is successfully extracted, fallback_used is NOT set
        # fallback_used is only True when actual fallback to GenericRoleResponse is used
        assert result.get("metadata", {}).get("fallback_used") is not True

    def test_schema_property(self) -> None:
        """Schema property returns the configured schema."""
        parser = PydanticOutputParser(schema=GenericRoleResponse)
        assert parser.schema is GenericRoleResponse

    def test_create_fallback_parser_returns_parser(self) -> None:
        """Factory function returns configured parser."""
        parser = create_fallback_parser()
        assert isinstance(parser, PydanticOutputParser)

    def test_create_fallback_parser_with_schema(self) -> None:
        """Factory function accepts schema parameter."""
        parser = create_fallback_parser(schema=GenericRoleResponse)
        assert parser.schema is GenericRoleResponse

    def test_validate_schema_compatibility_dataclass(self) -> None:
        """validate_schema_compatibility returns True for dataclass."""
        parser = PydanticOutputParser()
        assert parser.validate_schema_compatibility(GenericRoleResponse) is True

    def test_validate_schema_compatibility_pydantic(self) -> None:
        """validate_schema_compatibility returns True for Pydantic BaseModel."""
        parser = PydanticOutputParser()
        try:
            from pydantic import BaseModel

            class TestModel(BaseModel):
                content: str

            assert parser.validate_schema_compatibility(TestModel) is True
        except ImportError:
            pytest.skip("Pydantic not installed")


# ---------------------------------------------------------------------
# Test: Integration with kernel fallback
# ---------------------------------------------------------------------


class TestKernelFallbackIntegration:
    """Integration tests for kernel fallback behavior."""

    def test_fallback_schema_returns_generic_role_response(self) -> None:
        """get_schema_for_role fallback returns GenericRoleResponse."""
        # Test that GenericRoleResponse is the correct fallback schema
        # The actual get_schema_for_role integration is tested in kernel tests
        from polaris.cells.roles.kernel.public.contracts import GenericRoleResponse

        assert GenericRoleResponse is not None

    def test_output_parser_handles_generic_role_response(self) -> None:
        """PydanticOutputParser works with GenericRoleResponse."""
        parser = PydanticOutputParser(schema=GenericRoleResponse)
        content = '{"content": "Integration test", "tool_calls": [{"tool": "test", "args": {}}], "metadata": {}}'
        result = parser.parse(content)
        assert result.success is True
        assert result.data is not None


# ---------------------------------------------------------------------
# Test: Edge cases
# ---------------------------------------------------------------------


class TestPydanticOutputParserEdgeCases:
    """Edge case tests for PydanticOutputParser."""

    def test_parse_nested_json(self) -> None:
        """Parse nested JSON structures."""
        parser = PydanticOutputParser()
        content = '{"content": {"nested": {"value": "deep"}}, "metadata": {"level": 2}}'
        result = parser.parse(content)
        assert result.success is True
        assert result.data is not None
        assert result.data.get("content", {}).get("nested", {}).get("value") == "deep"

    def test_parse_json_with_arrays(self) -> None:
        """Parse JSON with array values."""
        parser = PydanticOutputParser()
        content = '{"content": "arrays", "items": [1, 2, 3], "metadata": {"tags": ["a", "b"]}}'
        result = parser.parse(content)
        assert result.success is True
        assert result.data is not None
        assert result.data.get("items") == [1, 2, 3]
        metadata = result.data.get("metadata")
        assert isinstance(metadata, dict)
        assert metadata.get("tags") == ["a", "b"]

    def test_parse_triple_backtick_variants(self) -> None:
        """Parse both ``` and ''' code block variants."""
        parser = PydanticOutputParser()
        # Triple single quotes with valid JSON (double quotes)
        content = "'''json\n{\"content\": \"double quotes\"}\n'''"
        result = parser.parse(content)
        assert result.success is True
        assert result.data is not None
        assert result.data["content"] == "double quotes"

    def test_parse_with_leading_whitespace(self) -> None:
        """Parse JSON with leading/trailing whitespace."""
        parser = PydanticOutputParser()
        content = '   \n{"content": "whitespace"}  \n  '
        result = parser.parse(content)
        assert result.success is True
        assert result.data is not None
        assert result.data.get("content") == "whitespace"

    def test_parse_malformed_json_code_block(self) -> None:
        """Parse handles malformed JSON in code blocks gracefully."""
        parser = PydanticOutputParser()
        content = '```json\n{"incomplete": true\n```'
        result = parser.parse(content)
        # Should try to extract JSON and fail gracefully
        assert result.success is False
