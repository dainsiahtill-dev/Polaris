"""ValidationError corrector - transforms Pydantic errors into correction prompts.

This module analyzes Pydantic ValidationError instances and generates
natural language correction prompts that can be sent back to the LLM
for auto-healing retries.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CorrectionPrompt:
    """A structured correction prompt generated from ValidationError."""

    message: str
    errors: tuple[dict[str, Any], ...]
    schema_summary: str
    suggested_fix: str | None

    def to_message(self) -> str:
        """Format as a user message for LLM."""
        return self.message


class ValidationErrorCorrector:
    """Transforms Pydantic ValidationError into actionable correction prompts.

    This corrector analyzes the structured error details from Pydantic's
    ValidationError and generates natural language prompts that explain
    exactly what went wrong and how to fix it.
    """

    # Error type to human-readable message mapping
    _ERROR_MESSAGES: dict[str, str] = {
        "missing": "Missing required field",
        "string_type": "Expected string value",
        "int_type": "Expected integer value",
        "float_type": "Expected float value",
        "bool_type": "Expected boolean value",
        "list_type": "Expected list/array value",
        "dict_type": "Expected object/dict value",
        "json_invalid": "Invalid JSON syntax",
        "literal_error": "Value not in allowed set",
        "enum_error": "Value not in allowed options",
        "value_error": "Value validation failed",
    }

    def __init__(
        self,
        *,
        include_schema: bool = True,
        max_schema_preview: int = 800,
        max_input_preview: int = 100,
    ) -> None:
        """Initialize corrector.

        Args:
            include_schema: Include schema in correction prompt
            max_schema_preview: Max chars of schema to include
            max_input_preview: Max chars of invalid input to show
        """
        self._include_schema = include_schema
        self._max_schema_preview = max_schema_preview
        self._max_input_preview = max_input_preview

    def build_correction_prompt(
        self,
        error: ValidationError,
        schema: type[BaseModel],
    ) -> CorrectionPrompt:
        """Build a correction prompt from ValidationError.

        Args:
            error: Pydantic ValidationError
            schema: The target schema that failed validation

        Returns:
            CorrectionPrompt with detailed correction instructions
        """
        error_list: list[dict[str, Any]] = []
        line_messages: list[str] = ["Your previous output had the following issues:"]

        for err in error.errors():
            loc = ".".join(str(loc_part) for loc_part in err["loc"])
            err_type = err["type"]
            msg = err["msg"]
            inp = err.get("input")

            # Build human-readable error message
            base_msg = self._ERROR_MESSAGES.get(err_type, msg)

            # Truncate input preview
            input_preview = ""
            if inp is not None:
                inp_str = str(inp)
                if len(inp_str) > self._max_input_preview:
                    inp_str = inp_str[: self._max_input_preview] + "..."
                input_preview = f" (got: {inp_str})"

            line = f"- {base_msg} at '{loc}':{input_preview}"
            line_messages.append(line)

            error_list.append(
                {
                    "location": loc,
                    "type": err_type,
                    "message": msg,
                    "input_preview": input_preview,
                }
            )

        # Add schema if enabled
        schema_summary = ""
        if self._include_schema:
            try:
                schema_dict = schema.model_json_schema()
                schema_json = json.dumps(schema_dict, indent=2, ensure_ascii=False)
                if len(schema_json) > self._max_schema_preview:
                    schema_json = schema_json[: self._max_schema_preview] + "..."
                schema_summary = f"\n\nRequired schema:\n```json\n{schema_json}\n```"
            except (RuntimeError, ValueError):
                schema_summary = ""

        # Build suggested fix
        suggested_fix = self._build_suggested_fix(error_list)

        # Assemble final message
        line_messages.append(f"\nPlease output ONLY valid JSON matching the schema.{schema_summary}")
        if suggested_fix:
            line_messages.append(f"\nHint: {suggested_fix}")

        return CorrectionPrompt(
            message="\n".join(line_messages),
            errors=tuple(error_list),
            schema_summary=schema_summary,
            suggested_fix=suggested_fix,
        )

    def _build_suggested_fix(self, errors: list[dict[str, Any]]) -> str | None:
        """Build a suggested fix hint based on error types."""
        missing_fields = [e for e in errors if e["type"] == "missing"]
        type_errors = [e for e in errors if "type" in ("string_type", "int_type", "float_type", "bool_type")]

        hints: list[str] = []

        if missing_fields:
            field_names = [e["location"].split(".")[-1] for e in missing_fields]
            hints.append(f"Add missing required fields: {', '.join(field_names)}")

        if type_errors:
            hints.append(
                "Ensure all values match their declared types (use strings for text, "
                "numbers without quotes for numeric values, true/false for booleans)"
            )

        if hints:
            return "; ".join(hints)
        return None

    def build_partial_data_prompt(
        self,
        partial_data: dict[str, Any],
        missing_fields: list[str],
        schema: type[BaseModel],
    ) -> CorrectionPrompt:
        """Build prompt for partial data - fields present but some missing nested ones.

        Args:
            partial_data: Data that was successfully parsed
            missing_fields: List of top-level fields that were missing
            schema: Target schema

        Returns:
            CorrectionPrompt focused on missing fields
        """
        present_fields = list(partial_data.keys())

        messages = [
            f"Partial data extracted successfully. Present fields: {present_fields}",
            f"Missing required fields: {missing_fields}",
            "\nPlease output ONLY the complete JSON with all required fields filled in.",
        ]

        if self._include_schema:
            try:
                schema_dict = schema.model_json_schema()
                schema_json = json.dumps(schema_dict, indent=2, ensure_ascii=False)[: self._max_schema_preview]
                messages.append(f"\nRequired schema:\n```json\n{schema_json}\n```")
            except (RuntimeError, ValueError) as e:
                logger.debug("Schema preview generation failed: %s", e)

        return CorrectionPrompt(
            message="\n".join(messages),
            errors=tuple(
                {"location": f, "type": "missing", "message": "Required field missing", "input_preview": ""}
                for f in missing_fields
            ),
            schema_summary="",
            suggested_fix=f"Fill in the missing fields: {', '.join(missing_fields)}",
        )
