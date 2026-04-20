"""Unit tests for ToolGenerator - the tool code generator from requirements."""

from __future__ import annotations

import re

import pytest
from polaris.kernelone.tool_creation.code_generator import (
    GeneratedTool,
    ToolGenerator,
    ToolRequirement,
)


class TestToolRequirement:
    """Tests for ToolRequirement dataclass."""

    def test_creation_minimal(self) -> None:
        """Test creating a minimal ToolRequirement."""
        req = ToolRequirement(
            description="A simple tool",
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object", "properties": {}},
        )

        assert req.description == "A simple tool"
        assert req.constraints == ()

    def test_creation_full(self) -> None:
        """Test creating a ToolRequirement with all fields."""
        req = ToolRequirement(
            description="A complex tool",
            input_schema={
                "type": "object",
                "properties": {"arg1": {"type": "string"}},
                "required": ["arg1"],
            },
            output_schema={
                "type": "object",
                "properties": {"result": {"type": "string"}},
            },
            constraints=("idempotent", "read_only"),
        )

        assert req.description == "A complex tool"
        assert req.constraints == ("idempotent", "read_only")
        assert "arg1" in req.input_schema.get("required", [])


class TestGeneratedTool:
    """Tests for GeneratedTool dataclass."""

    def test_creation(self) -> None:
        """Test creating a GeneratedTool."""
        tool = GeneratedTool(
            name="test_tool",
            description="A test tool",
            code="print('hello')",
            tests="def test():\n    pass",
            spec='{"name": "test_tool"}',
            confidence=0.85,
            warnings=("warning1",),
        )

        assert tool.name == "test_tool"
        assert tool.confidence == 0.85
        assert "warning1" in tool.warnings


class TestToolGenerator:
    """Tests for ToolGenerator class."""

    @pytest.fixture
    def generator(self) -> ToolGenerator:
        """Create a ToolGenerator instance."""
        return ToolGenerator()

    @pytest.fixture
    def simple_requirement(self) -> ToolRequirement:
        """A simple tool requirement."""
        return ToolRequirement(
            description="Read a file from the filesystem",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read"},
                },
                "required": ["path"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "File content"},
                },
            },
        )

    @pytest.fixture
    def complex_requirement(self) -> ToolRequirement:
        """A complex tool requirement with constraints."""
        return ToolRequirement(
            description="Search for text in files matching a pattern",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                        "minLength": 1,
                        "maxLength": 100,
                    },
                    "path": {"type": "string", "description": "Directory to search"},
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum results",
                        "minimum": 1,
                        "maximum": 1000,
                    },
                },
                "required": ["query", "path"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "matches": {
                        "type": "array",
                        "description": "List of matches",
                    },
                    "count": {"type": "integer", "description": "Match count"},
                },
            },
            constraints=("idempotent", "read_only"),
        )

    @pytest.mark.asyncio
    async def test_generate_tool_simple(
        self,
        generator: ToolGenerator,
        simple_requirement: ToolRequirement,
    ) -> None:
        """Test generating a simple tool."""
        result = await generator.generate_tool(simple_requirement)

        assert result.name == "filesystem"
        assert "Read a file from the filesystem" in result.description
        assert "path" in result.code
        assert "def test_" in result.tests
        assert result.confidence > 0.5

    @pytest.mark.asyncio
    async def test_generate_tool_complex(
        self,
        generator: ToolGenerator,
        complex_requirement: ToolRequirement,
    ) -> None:
        """Test generating a complex tool with constraints."""
        result = await generator.generate_tool(complex_requirement)

        assert result.name == "search_text_files"
        assert "Search for text in files matching a pattern" in result.description
        assert "query" in result.code
        assert "path" in result.code
        assert "max_results" in result.code
        # Full requirement with all fields should have high confidence and no warnings
        assert result.confidence == 1.0
        assert result.warnings == ()

    @pytest.mark.asyncio
    async def test_generate_tool_validates_params(
        self,
        generator: ToolGenerator,
        simple_requirement: ToolRequirement,
    ) -> None:
        """Test that generated code validates required parameters."""
        result = await generator.generate_tool(simple_requirement)

        assert "params" in result.code
        assert "path" in result.code
        assert "raise ValueError" in result.code or "raise TypeError" in result.code

    @pytest.mark.asyncio
    async def test_validate_generated_tool_success(
        self,
        generator: ToolGenerator,
        simple_requirement: ToolRequirement,
    ) -> None:
        """Test validation of a correctly generated tool."""
        result = await generator.generate_tool(simple_requirement)
        is_valid, message = await generator.validate_generated_tool(result)

        assert is_valid is True
        assert "passed" in message.lower()

    @pytest.mark.asyncio
    async def test_validate_empty_name_fails(
        self,
        generator: ToolGenerator,
    ) -> None:
        """Test validation fails for empty tool name."""
        tool = GeneratedTool(
            name="",
            description="Test",
            code="print('test')",
            tests="",
            spec="",
        )

        is_valid, message = await generator.validate_generated_tool(tool)

        assert is_valid is False
        assert "empty" in message.lower()

    @pytest.mark.asyncio
    async def test_validate_invalid_syntax_fails(
        self,
        generator: ToolGenerator,
    ) -> None:
        """Test validation fails for invalid Python syntax."""
        tool = GeneratedTool(
            name="test_tool",
            description="Test",
            code="def broken(\n    return None",  # Syntax error
            tests="",
            spec="",
        )

        is_valid, message = await generator.validate_generated_tool(tool)

        assert is_valid is False
        assert "syntax" in message.lower()

    @pytest.mark.asyncio
    async def test_validate_invalid_name_fails(
        self,
        generator: ToolGenerator,
    ) -> None:
        """Test validation fails for invalid tool name."""
        tool = GeneratedTool(
            name="Invalid-Name-123!",  # Invalid Python identifier
            description="Test",
            code="print('test')",
            tests="",
            spec="",
        )

        is_valid, message = await generator.validate_generated_tool(tool)

        assert is_valid is False
        assert "naming convention" in message.lower()

    @pytest.mark.asyncio
    async def test_confidence_score(
        self,
        generator: ToolGenerator,
    ) -> None:
        """Test that confidence score is computed correctly."""
        # Minimal requirement - low confidence
        minimal = ToolRequirement(
            description="Tool",
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object", "properties": {}},
        )
        result_minimal = await generator.generate_tool(minimal)
        assert result_minimal.confidence <= 0.7

        # Full requirement - higher confidence
        full = ToolRequirement(
            description="This is a detailed tool description that explains what it does",
            input_schema={
                "type": "object",
                "properties": {"arg": {"type": "string"}},
                "required": ["arg"],
            },
            output_schema={
                "type": "object",
                "properties": {"result": {"type": "string"}},
            },
            constraints=("idempotent",),
        )
        result_full = await generator.generate_tool(full)
        assert result_full.confidence > result_minimal.confidence

    @pytest.mark.asyncio
    async def test_warnings_generated(
        self,
        generator: ToolGenerator,
    ) -> None:
        """Test that warnings are generated appropriately."""
        # Requirement without constraints should generate warning
        no_constraints = ToolRequirement(
            description="Tool without constraints",
            input_schema={
                "type": "object",
                "properties": {},
            },
            output_schema={"type": "object", "properties": {}},
        )
        result = await generator.generate_tool(no_constraints)

        assert len(result.warnings) > 0

    def test_sanitize_name(self, generator: ToolGenerator) -> None:
        """Test name sanitization from description."""
        assert generator._sanitize_name("Read a file") == "file"
        assert generator._sanitize_name("Search in directory") in ("directory", "search_directory")
        # Should not contain special characters
        name = generator._sanitize_name("Search @#$ in directory!")
        assert re.match(r"^[a-z_][a-z0-9_]*$", name) or name == "generated_tool"

    def test_generate_param_validation_required(
        self,
        generator: ToolGenerator,
    ) -> None:
        """Test parameter validation generation for required fields."""
        schema = {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["path"],
        }

        validation = generator._generate_param_validation(schema)

        assert "'path' not in params" in validation
        assert "raise ValueError" in validation

    def test_generate_param_validation_types(
        self,
        generator: ToolGenerator,
    ) -> None:
        """Test parameter type validation generation."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "count": {"type": "integer"},
                "items": {"type": "array"},
            },
            "required": ["name", "count", "items"],
        }

        validation = generator._generate_param_validation(schema)

        assert "isinstance" in validation

    def test_generate_param_validation_constraints(
        self,
        generator: ToolGenerator,
    ) -> None:
        """Test parameter constraint validation generation."""
        schema = {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 50,
                },
                "count": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                },
            },
            "required": ["name", "count"],
        }

        validation = generator._generate_param_validation(schema)

        assert "minLength" in validation or "too short" in validation
        assert "maxLength" in validation or "too long" in validation
        assert "minimum" in validation or "below minimum" in validation
        assert "maximum" in validation or "above maximum" in validation
