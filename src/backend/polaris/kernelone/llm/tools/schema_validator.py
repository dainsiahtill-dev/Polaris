"""JSON Schema export and static validation for tool definitions.

Exports tool schemas from Pydantic-like tool definitions to JSON Schema
format for validation at startup and in CI pipelines.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# Type aliases for clarity
SchemaDict = dict[str, Any]
ValidationError = str
ValidationResult = tuple[bool, list[ValidationError]]


# JSON Schema draft-07 keywords that we validate
_REQUIRED_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "type",
    }
)

_OPTIONAL_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "description",
        "properties",
        "required",
        "enum",
        "items",
        "additionalProperties",
        "default",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "pattern",
        "format",
        "$ref",
        "allOf",
        "anyOf",
        "oneOf",
    }
)

_VALID_TYPES: Final[frozenset[str]] = frozenset(
    {
        "string",
        "number",
        "integer",
        "boolean",
        "array",
        "object",
        "null",
    }
)


@dataclass(frozen=True)
class ToolSchema:
    """A validated tool schema.

    Attributes:
        name: Tool name.
        description: Tool description.
        parameters_schema: JSON Schema for tool parameters.
        raw_definition: Original tool definition.
        validation_errors: Any validation errors found.
    """

    name: str
    description: str
    parameters_schema: SchemaDict
    raw_definition: Any
    validation_errors: tuple[str, ...]


@dataclass(frozen=True)
class ToolSchemaValidationResult:
    """Result of validating a tool schema.

    Attributes:
        valid: Whether the schema is valid.
        errors: List of validation error messages.
        tool_name: The tool that was validated.
        schema: The validated schema (if valid).

    Note: This is distinct from SchemaValidationResult in context_os/schemas.py
    which validates suite YAML/JSON files.
    """

    valid: bool
    errors: tuple[str, ...]
    tool_name: str
    schema: SchemaDict | None


# Backward compatibility alias (deprecated)
SchemaValidationResult = ToolSchemaValidationResult


def export_tool_to_json_schema(
    tool_def: Any,
    *,
    strict: bool = False,
) -> ToolSchema:
    """Export a tool definition to JSON Schema format.

    Args:
        tool_def: A tool definition object with name, description,
            and parameters attributes.
        strict: If True, apply stricter validation rules.

    Returns:
        ToolSchema with exported JSON Schema.

    Raises:
        ValueError: If tool_def is invalid.
    """
    if not hasattr(tool_def, "name"):
        raise ValueError("tool_def must have a 'name' attribute")
    if not hasattr(tool_def, "description"):
        raise ValueError("tool_def must have a 'description' attribute")

    name = str(tool_def.name or "").strip()
    if not name:
        raise ValueError("tool name cannot be empty")
    if not re.match(r"^[a-z][a-z0-9_]*$", name):
        raise ValueError(f"tool name '{name}' must match pattern ^[a-z][a-z0-9_]*$")

    description = str(tool_def.description or "").strip()
    parameters = getattr(tool_def, "parameters", [])
    if not isinstance(parameters, (list, tuple)):
        parameters = []

    # Build the parameters schema
    properties: dict[str, Any] = {}
    required: list[str] = []

    for param in parameters:
        if not hasattr(param, "name"):
            continue
        param_name = str(param.name or "").strip()
        if not param_name:
            continue

        param_schema: dict[str, Any] = {
            "type": _normalize_param_type(getattr(param, "type", "string")),
            "description": str(getattr(param, "description", "") or "").strip(),
        }

        # Handle enum values
        enum_values = getattr(param, "enum", None)
        if enum_values is not None and isinstance(enum_values, (list, tuple)):
            param_schema["enum"] = list(enum_values)

        # Handle default values
        default_value = getattr(param, "default", ...)
        if default_value is not ...:
            param_schema["default"] = default_value

        # Handle array items
        items = getattr(param, "items", None)
        if items is not None and isinstance(items, dict):
            param_schema["items"] = items

        # Handle object properties
        properties_param = getattr(param, "properties", None)
        if properties_param is not None and isinstance(properties_param, dict):
            param_schema["properties"] = properties_param

        properties[param_name] = param_schema

        # Track required parameters
        is_required = getattr(param, "required", True)
        if is_required:
            required.append(param_name)

    # Build the full schema
    schema: SchemaDict = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }

    if required:
        schema["required"] = required

    # Validate the generated schema
    errors = _validate_schema_structure(schema, name, strict=strict)

    return ToolSchema(
        name=name,
        description=description,
        parameters_schema=schema,
        raw_definition=tool_def,
        validation_errors=tuple(errors),
    )


def _normalize_param_type(param_type: Any) -> str:
    """Normalize a parameter type to JSON Schema type string.

    Args:
        param_type: The parameter type to normalize.

    Returns:
        JSON Schema type string.
    """
    type_str = str(param_type or "string").lower().strip()

    type_mapping = {
        "str": "string",
        "text": "string",
        "int": "integer",
        "float": "number",
        "bool": "boolean",
        "arr": "array",
        "list": "array",
        "dict": "object",
        "obj": "object",
        "object": "object",
        "null": "null",
    }

    return type_mapping.get(type_str, type_str if type_str in _VALID_TYPES else "string")


def _validate_schema_structure(
    schema: SchemaDict,
    tool_name: str,
    *,
    strict: bool = False,
) -> list[str]:
    """Validate the structure of a JSON Schema.

    Args:
        schema: The JSON Schema to validate.
        tool_name: Name of the tool for error messages.
        strict: Apply stricter validation rules.

    Returns:
        List of validation error messages (empty if valid).
    """
    errors: list[str] = []

    if not isinstance(schema, dict):
        errors.append(f"[{tool_name}] schema must be a dict")
        return errors

    # Check required top-level keywords
    for keyword in _REQUIRED_KEYWORDS:
        if keyword not in schema and (strict or keyword == "type"):
            errors.append(f"[{tool_name}] missing required keyword '{keyword}'")

    # Validate type
    schema_type = schema.get("type")
    if schema_type is not None and schema_type not in _VALID_TYPES:
        errors.append(f"[{tool_name}] invalid type '{schema_type}', expected one of {sorted(_VALID_TYPES)}")

    # Validate properties
    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            errors.append(f"[{tool_name}] 'properties' must be a dict")
        else:
            for prop_name, prop_schema in properties.items():
                if not isinstance(prop_schema, dict):
                    errors.append(f"[{tool_name}] property '{prop_name}' schema must be a dict")
                else:
                    # Validate property type
                    prop_type = prop_schema.get("type")
                    if prop_type is not None and prop_type not in _VALID_TYPES:
                        errors.append(f"[{tool_name}] property '{prop_name}' has invalid type '{prop_type}'")

                    # In strict mode, properties must have descriptions
                    if strict and not prop_schema.get("description"):
                        errors.append(f"[{tool_name}] property '{prop_name}' must have a description in strict mode")

    # Validate required
    required = schema.get("required")
    if required is not None:
        if not isinstance(required, (list, tuple)):
            errors.append(f"[{tool_name}] 'required' must be a list")
        elif not all(isinstance(r, str) for r in required):
            errors.append(f"[{tool_name}] 'required' must contain only strings")
        elif properties is not None:
            # Check that required properties exist
            prop_names = set(properties.keys())
            for req in required:
                if req not in prop_names:
                    errors.append(f"[{tool_name}] required property '{req}' is not defined in properties")

    # Validate enum values
    if properties is not None:
        for prop_name, prop_schema in properties.items():
            enum_values = prop_schema.get("enum")
            if enum_values is not None and not isinstance(enum_values, (list, tuple)):
                errors.append(f"[{tool_name}] property '{prop_name}' enum must be a list")

    return errors


def validate_tool_schema(schema: SchemaDict, tool_name: str) -> SchemaValidationResult:
    """Validate a single tool schema.

    Args:
        schema: The JSON Schema to validate.
        tool_name: Name of the tool for error messages.

    Returns:
        SchemaValidationResult with validation outcome.
    """
    errors = _validate_schema_structure(schema, tool_name, strict=False)

    return SchemaValidationResult(
        valid=len(errors) == 0,
        errors=tuple(errors),
        tool_name=tool_name,
        schema=schema if len(errors) == 0 else None,
    )


def validate_all_tool_schemas(
    tools: Sequence[Any],
    *,
    strict: bool = False,
) -> tuple[bool, list[SchemaValidationResult]]:
    """Validate all tool schemas.

    Args:
        tools: Sequence of tool definition objects.
        strict: Apply stricter validation rules.

    Returns:
        Tuple of (all_valid, list of validation results).
    """
    results: list[SchemaValidationResult] = []
    all_valid = True

    for tool_def in tools:
        try:
            tool_schema = export_tool_to_json_schema(tool_def, strict=strict)
            if tool_schema.validation_errors:
                all_valid = False
            results.append(
                SchemaValidationResult(
                    valid=len(tool_schema.validation_errors) == 0,
                    errors=tool_schema.validation_errors,
                    tool_name=tool_schema.name,
                    schema=tool_schema.parameters_schema,
                )
            )
        except (ValueError, TypeError, AttributeError) as e:
            all_valid = False
            tool_name = getattr(tool_def, "name", "unknown")
            results.append(
                SchemaValidationResult(
                    valid=False,
                    errors=(str(e),),
                    tool_name=str(tool_name),
                    schema=None,
                )
            )

    return all_valid, results


def export_all_tools_to_json_schema(
    tools: Sequence[Any],
    *,
    indent: int | None = 2,
) -> str:
    """Export all tool definitions to a JSON Schema file.

    Args:
        tools: Sequence of tool definition objects.
        indent: JSON indentation. None for compact output.

    Returns:
        JSON string containing all tool schemas.
    """
    all_schemas: list[SchemaDict] = []
    errors: list[str] = []

    for tool_def in tools:
        try:
            tool_schema = export_tool_to_json_schema(tool_def)
            all_schemas.append(
                {
                    "name": tool_schema.name,
                    "description": tool_schema.description,
                    "parameters": tool_schema.parameters_schema,
                }
            )
            if tool_schema.validation_errors:
                errors.extend(tool_schema.validation_errors)
        except (ValueError, TypeError, AttributeError) as e:
            tool_name = getattr(tool_def, "name", "unknown")
            errors.append(f"[{tool_name}] {e}")

    output = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "KernelOne Tool Schemas",
        "description": "JSON Schema definitions for KernelOne tool runtime",
        "tools": all_schemas,
    }

    if errors:
        output["_validation_errors"] = errors

    return json.dumps(output, ensure_ascii=False, indent=indent)


class SchemaValidator:
    """Validator for tool schemas with caching and reporting."""

    def __init__(self, *, strict: bool = False) -> None:
        """Initialize the validator.

        Args:
            strict: Apply stricter validation rules.
        """
        self._strict = strict
        self._cache: dict[str, SchemaValidationResult] = {}

    def validate(
        self,
        schema: SchemaDict,
        tool_name: str,
    ) -> SchemaValidationResult:
        """Validate a schema, using cache if available.

        Args:
            schema: The JSON Schema to validate.
            tool_name: Name of the tool.

        Returns:
            SchemaValidationResult with validation outcome.
        """
        cache_key = f"{tool_name}:{json.dumps(schema, sort_keys=True)}"

        if cache_key in self._cache:
            return self._cache[cache_key]

        result = validate_tool_schema(schema, tool_name)
        self._cache[cache_key] = result
        return result

    def validate_batch(
        self,
        schemas: Mapping[str, SchemaDict],
    ) -> dict[str, SchemaValidationResult]:
        """Validate multiple schemas.

        Args:
            schemas: Mapping of tool names to schemas.

        Returns:
            Dict of tool names to validation results.
        """
        results: dict[str, SchemaValidationResult] = {}
        for tool_name, schema in schemas.items():
            results[tool_name] = self.validate(schema, tool_name)
        return results

    def clear_cache(self) -> None:
        """Clear the validation cache."""
        self._cache.clear()


__all__ = [
    "SchemaValidationResult",
    "SchemaValidator",
    "ToolSchema",
    "export_all_tools_to_json_schema",
    "export_tool_to_json_schema",
    "validate_all_tool_schemas",
    "validate_tool_schema",
]
