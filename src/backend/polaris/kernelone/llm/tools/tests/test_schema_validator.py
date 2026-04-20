"""Tests for schema validator."""

from dataclasses import dataclass, field
from typing import Any

import pytest
from polaris.kernelone.llm.tools.schema_validator import (
    SchemaValidator,
    _normalize_param_type,
    export_all_tools_to_json_schema,
    export_tool_to_json_schema,
    validate_all_tool_schemas,
    validate_tool_schema,
)


@dataclass
class MockToolParameter:
    """Mock tool parameter for testing."""

    name: str
    type: str
    description: str
    required: bool = True
    enum: list | None = None
    default: Any = None


@dataclass
class MockToolDefinition:
    """Mock tool definition for testing."""

    name: str
    description: str
    parameters: list = field(default_factory=list)


class TestExportToolToJsonSchema:
    """Tests for export_tool_to_json_schema function."""

    def test_export_basic_tool(self) -> None:
        """Should export a basic tool to JSON Schema."""
        tool = MockToolDefinition(
            name="test_tool",
            description="A test tool",
            parameters=[
                MockToolParameter("arg1", "string", "First argument"),
                MockToolParameter("arg2", "integer", "Second argument", required=False),
            ],
        )

        schema = export_tool_to_json_schema(tool)

        assert schema.name == "test_tool"
        assert schema.description == "A test tool"
        assert "properties" in schema.parameters_schema
        assert "arg1" in schema.parameters_schema["properties"]
        assert "arg2" in schema.parameters_schema["properties"]
        assert "required" in schema.parameters_schema
        assert "arg1" in schema.parameters_schema["required"]

    def test_export_invalid_name(self) -> None:
        """Should raise ValueError for invalid tool name."""
        tool = MockToolDefinition(
            name="Invalid-Name",  # Contains hyphen
            description="Test",
            parameters=[],
        )

        with pytest.raises(ValueError, match="must match pattern"):
            export_tool_to_json_schema(tool)

    def test_export_empty_name(self) -> None:
        """Should raise ValueError for empty name."""
        tool = MockToolDefinition(name="", description="Test", parameters=[])

        with pytest.raises(ValueError, match="name cannot be empty"):
            export_tool_to_json_schema(tool)

    def test_export_with_enum(self) -> None:
        """Should export parameters with enum values."""
        tool = MockToolDefinition(
            name="enum_tool",
            description="Tool with enum",
            parameters=[
                MockToolParameter("mode", "string", "Mode", enum=["fast", "slow"]),
            ],
        )

        schema = export_tool_to_json_schema(tool)
        arg1_schema = schema.parameters_schema["properties"]["mode"]
        assert "enum" in arg1_schema
        assert "fast" in arg1_schema["enum"]

    def test_export_with_default(self) -> None:
        """Should export parameters with default values."""
        tool = MockToolDefinition(
            name="default_tool",
            description="Tool with defaults",
            parameters=[
                MockToolParameter("timeout", "integer", "Timeout", default=30),
            ],
        )

        schema = export_tool_to_json_schema(tool)
        timeout_schema = schema.parameters_schema["properties"]["timeout"]
        assert timeout_schema.get("default") == 30


class TestNormalizeParamType:
    """Tests for _normalize_param_type function."""

    def test_normalize_string_types(self) -> None:
        """Should normalize string type aliases."""
        assert _normalize_param_type("str") == "string"
        assert _normalize_param_type("text") == "string"

    def test_normalize_numeric_types(self) -> None:
        """Should normalize numeric type aliases."""
        assert _normalize_param_type("int") == "integer"
        assert _normalize_param_type("float") == "number"

    def test_normalize_boolean_type(self) -> None:
        """Should normalize boolean type."""
        assert _normalize_param_type("bool") == "boolean"

    def test_normalize_collection_types(self) -> None:
        """Should normalize collection type aliases."""
        assert _normalize_param_type("arr") == "array"
        assert _normalize_param_type("dict") == "object"

    def test_preserve_valid_types(self) -> None:
        """Should preserve valid JSON Schema types."""
        assert _normalize_param_type("string") == "string"
        assert _normalize_param_type("integer") == "integer"
        assert _normalize_param_type("boolean") == "boolean"
        assert _normalize_param_type("array") == "array"
        assert _normalize_param_type("object") == "object"

    def test_default_for_unknown(self) -> None:
        """Should default to string for unknown types."""
        assert _normalize_param_type("unknown_type") == "string"


class TestValidateToolSchema:
    """Tests for validate_tool_schema function."""

    def test_validate_valid_schema(self) -> None:
        """Should validate a correct schema."""
        schema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Tool name"},
            },
            "required": ["name"],
        }

        result = validate_tool_schema(schema, "test_tool")

        assert result.valid is True
        assert len(result.errors) == 0
        assert result.schema is not None

    def test_validate_invalid_type(self) -> None:
        """Should catch invalid type values."""
        schema = {
            "type": "invalid_type",
            "properties": {},
        }

        result = validate_tool_schema(schema, "test_tool")

        assert result.valid is False
        assert any("invalid type" in e for e in result.errors)

    def test_validate_missing_required_keyword(self) -> None:
        """Should catch missing required keywords in strict mode."""
        schema = {
            "properties": {"name": {"type": "string"}},
        }

        result = validate_tool_schema(schema, "test_tool")

        assert result.valid is False
        assert any("missing required keyword" in e for e in result.errors)

    def test_validate_nonexistent_required_property(self) -> None:
        """Should catch required properties not in properties."""
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["nonexistent"],
        }

        result = validate_tool_schema(schema, "test_tool")

        assert result.valid is False
        assert any("not defined in properties" in e for e in result.errors)


class TestValidateAllToolSchemas:
    """Tests for validate_all_tool_schemas function."""

    def test_validate_multiple_tools(self) -> None:
        """Should validate multiple tools."""
        tools = [
            MockToolDefinition(
                "tool1",
                "Tool 1",
                [
                    MockToolParameter("arg1", "string", "Arg 1"),
                ],
            ),
            MockToolDefinition(
                "tool2",
                "Tool 2",
                [
                    MockToolParameter("arg1", "string", "Arg 1"),
                ],
            ),
        ]

        _all_valid, results = validate_all_tool_schemas(tools)

        assert len(results) == 2
        assert all(r.valid for r in results)

    def test_validate_with_errors(self) -> None:
        """Should report errors in tools."""
        tools = [
            MockToolDefinition(
                "valid_tool",
                "Valid",
                [
                    MockToolParameter("arg1", "string", "Arg 1"),
                ],
            ),
            MockToolDefinition("", "Empty name", []),  # Invalid
        ]

        all_valid, results = validate_all_tool_schemas(tools)

        assert all_valid is False
        assert not results[1].valid


class TestExportAllToolsToJsonSchema:
    """Tests for export_all_tools_to_json_schema function."""

    def test_export_all_tools(self) -> None:
        """Should export all tools to JSON."""
        tools = [
            MockToolDefinition(
                "tool1",
                "Tool 1",
                [
                    MockToolParameter("arg1", "string", "Arg 1"),
                ],
            ),
        ]

        json_output = export_all_tools_to_json_schema(tools)
        assert "tool1" in json_output
        assert "Tool 1" in json_output


class TestSchemaValidator:
    """Tests for SchemaValidator class."""

    def test_validator_caching(self) -> None:
        """Should cache validation results."""
        validator = SchemaValidator()
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }

        result1 = validator.validate(schema, "tool1")
        result2 = validator.validate(schema, "tool1")

        assert result1 is result2  # Same cached instance

    def test_validator_clear_cache(self) -> None:
        """Should clear cache on request."""
        validator = SchemaValidator()
        schema = {"type": "object", "properties": {}}

        validator.validate(schema, "tool1")
        assert len(validator._cache) == 1

        validator.clear_cache()
        assert len(validator._cache) == 0

    def test_validator_batch(self) -> None:
        """Should validate multiple schemas in batch."""
        validator = SchemaValidator()
        schemas = {
            "tool1": {"type": "object", "properties": {}},
            "tool2": {"type": "object", "properties": {}},
        }

        results = validator.validate_batch(schemas)

        assert len(results) == 2
        assert "tool1" in results
        assert "tool2" in results


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
