"""Tool code generator for KernelOne - generates tool implementations from requirements."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolRequirement:
    """Tool generation requirement."""

    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    constraints: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class GeneratedTool:
    """Generated tool code and metadata."""

    name: str
    description: str
    code: str
    tests: str
    spec: str
    confidence: float = 0.5
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class ToolGenerator:
    """Generates tool code from requirements."""

    _SKELETON_TEMPLATE: str = '''
"""Auto-generated tool: {tool_name}"""

from __future__ import annotations

from typing import Any


async def {tool_name}_execute(
    params: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute {tool_name}.

    {description}

    Args:
        params: Input parameters matching schema.
        context: Optional execution context.

    Returns:
        Tool result matching output schema.
    """
    # Validate input parameters
    {input_validation}

    # Execute tool logic
    raise NotImplementedError("Tool logic must be implemented")

    # Validate output (unreachable - implement before raising if needed)
    {output_validation}

    return result  # noqa: unreachable


def get_{tool_name}_spec() -> dict[str, Any]:
    """Get tool specification for {tool_name}."""
    return {{
        "canonical_name": "{tool_name}",
        "aliases": (),
        "description": "{description}",
        "parameters": {input_schema},
        "categories": ("read",),
        "dangerous_patterns": (),
        "handler_module": "{handler_module}",
        "handler_function": "{tool_name}_execute",
        "response_format_hint": "",
    }}
'''

    _TEST_TEMPLATE: str = '''
"""Unit tests for auto-generated tool: {tool_name}"""

import pytest
from typing import Any


class Test{tool_name_pascal}:
    """Tests for {tool_name} tool."""

    @pytest.fixture
    def valid_params(self) -> dict[str, Any]:
        """Valid input parameters."""
        return {valid_params}

    @pytest.fixture
    def context(self) -> dict[str, Any]:
        """Execution context."""
        return {{}}

    @pytest.mark.asyncio
    async def test_execute_success(
        self,
        valid_params: dict[str, Any],
        context: dict[str, Any],
    ) -> None:
        """Test successful execution with valid params."""
        from polaris.kernelone.tool_creation.tests.test_code_generator import (
            {tool_name}_execute,
        )

        result = await {tool_name}_execute(valid_params, context)

        assert result["status"] == "success"
        assert "data" in result

    @pytest.mark.asyncio
    async def test_invalid_params_raises(
        self,
        context: dict[str, Any],
    ) -> None:
        """Test that invalid params raise appropriate error."""
        from polaris.kernelone.tool_creation.tests.test_code_generator import (
            {tool_name}_execute,
        )

        invalid_params = {{"invalid": "param"}}

        with pytest.raises((ValueError, TypeError)):
            await {tool_name}_execute(invalid_params, context)

    def test_spec_matches_schema(self) -> None:
        """Test that tool spec has correct schema structure."""
        from polaris.kernelone.tool_creation.tests.test_code_generator import (
            get_{tool_name}_spec,
        )

        spec = get_{tool_name}_spec()

        assert spec["canonical_name"] == "{tool_name}"
        assert "parameters" in spec
        assert spec["parameters"]["type"] == "object"
'''

    def _generate_param_validation(self, schema: dict[str, Any]) -> str:
        """Generate input parameter validation code."""
        lines: list[str] = []
        required = schema.get("required", [])
        properties = schema.get("properties", {})

        if not required and not properties:
            return "    # No validation needed"

        for param_name, param_spec in properties.items():
            param_type = param_spec.get("type", "string")
            is_required = param_name in required

            if is_required:
                lines.append(f"    # Validate required param: {param_name}")
                lines.append(f"    if '{param_name}' not in params:")
                lines.append(f'        raise ValueError("Missing required parameter: {param_name}")')

            # Type validation
            if param_type == "string":
                lines.append(f"    if '{param_name}' in params and not isinstance(params['{param_name}'], str):")
                lines.append(f'        raise TypeError("{param_name} must be a string")')
            elif param_type == "integer":
                lines.append(f"    if '{param_name}' in params and not isinstance(params['{param_name}'], int):")
                lines.append(f'        raise TypeError("{param_name} must be an integer")')
            elif param_type == "array":
                lines.append(f"    if '{param_name}' in params and not isinstance(params['{param_name}'], list):")
                lines.append(f'        raise TypeError("{param_name} must be an array")')

            # String constraints
            if param_type == "string":
                if "minLength" in param_spec:
                    lines.append(
                        f"    if '{param_name}' in params and len(params['{param_name}']) < {param_spec['minLength']}:"
                    )
                    lines.append(f'        raise ValueError("{param_name} is too short")')
                if "maxLength" in param_spec:
                    lines.append(
                        f"    if '{param_name}' in params and len(params['{param_name}']) > {param_spec['maxLength']}:"
                    )
                    lines.append(f'        raise ValueError("{param_name} is too long")')
                if "pattern" in param_spec:
                    lines.append(
                        f"    if '{param_name}' in params and not __import__('re').match("
                        f'"{param_spec["pattern"]}", params["{param_name}"]):'
                    )
                    lines.append(f'        raise ValueError("{param_name} does not match required pattern")')

            # Number constraints
            if param_type == "integer":
                if "minimum" in param_spec:
                    lines.append(
                        f"    if '{param_name}' in params and params['{param_name}'] < {param_spec['minimum']}:"
                    )
                    lines.append(f'        raise ValueError("{param_name} is below minimum")')
                if "maximum" in param_spec:
                    lines.append(
                        f"    if '{param_name}' in params and params['{param_name}'] > {param_spec['maximum']}:"
                    )
                    lines.append(f'        raise ValueError("{param_name} is above maximum")')

        if not lines:
            return "    # No validation needed"
        return "\n".join(lines)

    def _generate_output_validation(self, schema: dict[str, Any]) -> str:
        """Generate output validation code."""
        # Provide stub validation - developer must implement
        return "    # Output validation: implement according to output_schema"

    def _sanitize_name(self, description: str) -> str:
        """Generate a valid tool name from description."""
        # Extract key words from description
        words = re.findall(r"[a-zA-Z]+", description.lower())
        if not words:
            return "generated_tool"

        # Filter common words, keep significant ones
        stop_words = {"a", "an", "the", "to", "for", "of", "and", "in", "on", "with", "from"}
        significant = [w for w in words if w not in stop_words]

        name = (significant[-1] if len(significant) <= 3 else "_".join(significant[:3])) if significant else words[0]

        # Sanitize: only alphanumeric and underscores, must start with letter
        name = re.sub(r"[^a-z0-9_]", "_", name)
        name = re.sub(r"^[0-9_]+", "", name)
        return name or "generated_tool"

    def _generate_params_example(self, schema: dict[str, Any]) -> str:
        """Generate example parameters for tests."""
        import json

        properties = schema.get("properties", {})
        required = schema.get("required", [])

        example: dict[str, Any] = {}
        for param_name, param_spec in properties.items():
            if param_name in required:
                param_type = param_spec.get("type", "string")
                if param_type == "string":
                    example[param_name] = param_spec.get("example", "test_value")
                elif param_type == "integer":
                    example[param_name] = param_spec.get("example", 42)
                elif param_type == "boolean":
                    example[param_name] = param_spec.get("example", True)
                elif param_type == "array":
                    example[param_name] = param_spec.get("example", [])
                elif param_type == "object":
                    example[param_name] = param_spec.get("example", {})

        return json.dumps(example, indent=4)

    def _compute_confidence(self, requirement: ToolRequirement) -> float:
        """Compute confidence score based on requirement completeness."""
        score = 0.5  # Base score

        # Well-formed description
        if len(requirement.description) > 20:
            score += 0.1

        # Has input schema
        if requirement.input_schema.get("properties"):
            score += 0.15

        # Has output schema
        if requirement.output_schema.get("properties"):
            score += 0.15

        # Has constraints
        if requirement.constraints:
            score += 0.1

        return min(score, 1.0)

    def _generate_warnings(self, requirement: ToolRequirement) -> tuple[str, ...]:
        """Generate warnings for potential issues."""
        warnings: list[str] = []

        if not requirement.input_schema.get("required"):
            warnings.append("Input schema has no required fields - consider adding validation")

        if not requirement.output_schema.get("properties"):
            warnings.append("Output schema has no properties defined")

        if not requirement.constraints:
            warnings.append("No constraints specified - generated code may need manual review")

        return tuple(warnings)

    async def generate_tool(
        self,
        requirement: ToolRequirement,
    ) -> GeneratedTool:
        """Generate tool implementation from requirement.

        Takes:
        - description: What the tool should do
        - input_schema: Expected input parameters
        - output_schema: Expected output format
        - constraints: Additional constraints

        Returns:
        - Tool implementation code
        - Unit tests
        - Tool specification
        """
        tool_name = self._sanitize_name(requirement.description)
        input_validation = self._generate_param_validation(requirement.input_schema)
        output_validation = self._generate_output_validation(requirement.output_schema)
        valid_params = self._generate_params_example(requirement.input_schema)
        confidence = self._compute_confidence(requirement)
        warnings = self._generate_warnings(requirement)

        # Generate tool code
        code = self._SKELETON_TEMPLATE.format(
            tool_name=tool_name,
            description=requirement.description,
            input_validation=input_validation,
            output_validation=output_validation,
            input_schema=requirement.input_schema,
            handler_module="polaris.kernelone.tool_creation.generated",
        )

        # Generate tests
        tests = self._TEST_TEMPLATE.format(
            tool_name=tool_name,
            tool_name_pascal=tool_name.replace("_", " ").title().replace(" ", ""),
            valid_params=valid_params,
        )

        # Generate spec (as string representation)
        spec_dict = {
            "canonical_name": tool_name,
            "description": requirement.description,
            "input_schema": requirement.input_schema,
            "output_schema": requirement.output_schema,
            "constraints": list(requirement.constraints),
        }

        return GeneratedTool(
            name=tool_name,
            description=requirement.description,
            code=code.strip(),
            tests=tests.strip(),
            spec=str(spec_dict),
            confidence=confidence,
            warnings=warnings,
        )

    async def validate_generated_tool(
        self,
        tool: GeneratedTool,
    ) -> tuple[bool, str]:
        """Validate that generated tool is syntactically correct and follows conventions."""
        errors: list[str] = []

        # 1. Check code is not empty
        if not tool.name:
            errors.append("Tool name is empty")

        if not tool.code:
            errors.append("Generated code is empty")

        # 2. Validate Python syntax using ast
        if tool.code:
            try:
                ast.parse(tool.code)
            except SyntaxError as e:
                errors.append(f"Python syntax error in generated code: {e}")

        # 3. Validate test code syntax
        if tool.tests:
            try:
                ast.parse(tool.tests)
            except SyntaxError as e:
                errors.append(f"Python syntax error in generated tests: {e}")

        # 4. Check code follows naming conventions
        if not re.match(r"^[a-z_][a-z0-9_]*$", tool.name):
            errors.append(
                f"Tool name '{tool.name}' does not follow Python naming conventions "
                "(should be lowercase with underscores)"
            )

        # 5. Check confidence is in valid range
        if not 0.0 <= tool.confidence <= 1.0:
            errors.append(f"Confidence {tool.confidence} is outside valid range [0.0, 1.0]")

        if errors:
            return False, "; ".join(errors)

        return True, "Validation passed"
