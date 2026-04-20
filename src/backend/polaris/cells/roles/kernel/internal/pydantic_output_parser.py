"""Pydantic Output Parser - Type-safe fallback for structured output parsing.

This module provides PydanticOutputParser as a fallback when Instructor schemas
are unavailable. It uses GenericRoleResponse to ensure type-safe output parsing.

Design rationale:
- Frozen dataclass ensures immutability and thread-safety
- Consistent interface with InstructorOutputParser
- Graceful degradation when specialized schemas are not available
- Internally uses JSONExtractor from robust_parser for JSON extraction convergence
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any

from polaris.kernelone.llm.robust_parser.extractors import JSONExtractor

logger = logging.getLogger(__name__)


@dataclass
class ParsedOutput:
    """Result of parsing LLM output with type-safe schema.

    Attributes:
        success: Whether parsing succeeded
        data: Parsed data as dictionary (None if failed)
        error: Error message if parsing failed (None if success)
        raw_content: Original content that was parsed
    """

    success: bool
    data: dict[str, Any] | None
    error: str | None
    raw_content: str


class PydanticOutputParser:
    """Type-safe output parser using frozen dataclass schemas.

    This parser serves as a fallback when Instructor-based schemas are
    unavailable. It parses JSON from LLM output and validates against
    GenericRoleResponse or any frozen dataclass schema.

    Internally uses JSONExtractor from robust_parser for JSON extraction,
    ensuring convergence with the Entropy Reduction Matrix pipeline.

    The parser is designed to:
    1. Extract JSON from various formats (code blocks, <output> tags)
    2. Validate against the provided schema
    3. Return structured, type-safe results

    Example:
        >>> parser = PydanticOutputParser()
        >>> result = parser.parse('{"content": "Hello"}')
        >>> if result.success:
        ...     response = GenericRoleResponse(**result.data)
    """

    def __init__(self, schema: type | None = None) -> None:
        """Initialize the parser with an optional schema.

        Args:
            schema: Optional dataclass type to validate against.
                   If None, GenericRoleResponse is used as default.
        """
        self._schema = schema
        self._extractor = JSONExtractor()

    @property
    def schema(self) -> type | None:
        """Get the current schema."""
        return self._schema

    def parse(self, content: str) -> ParsedOutput:
        """Parse LLM output content into structured data.

        Args:
            content: Raw output from LLM (may contain JSON in various formats)

        Returns:
            ParsedOutput with success status, parsed data, and error if any
        """
        if not content or not str(content).strip():
            return ParsedOutput(
                success=False,
                data=None,
                error="Empty content provided",
                raw_content=content or "",
            )

        # Extract JSON from content
        extracted = self._extract_json(str(content))
        if extracted is None:
            return ParsedOutput(
                success=False,
                data=None,
                error="Failed to extract valid JSON from content",
                raw_content=content,
            )

        # Validate against schema if provided
        if self._schema is not None:
            try:
                if is_dataclass(self._schema):
                    # Frozen dataclass validation
                    validated = self._schema(**extracted)
                    return ParsedOutput(
                        success=True,
                        data=asdict(validated) if is_dataclass(validated) else extracted,
                        error=None,
                        raw_content=content,
                    )
                else:
                    # Pydantic model validation
                    validated = self._schema.model_validate(extracted)  # type: ignore[attr-defined]
                    return ParsedOutput(
                        success=True,
                        data=validated.model_dump() if hasattr(validated, "model_dump") else extracted,
                        error=None,
                        raw_content=content,
                    )
            except (RuntimeError, ValueError, TypeError) as e:
                logger.warning(
                    "Schema validation failed (schema=%s): %s",
                    self._schema.__name__ if hasattr(self._schema, "__name__") else str(self._schema),
                    e,
                )
                return ParsedOutput(
                    success=False,
                    data=None,
                    error=f"Schema validation failed: {e}",
                    raw_content=content,
                )

        # No schema provided, return extracted data as-is
        return ParsedOutput(
            success=True,
            data=extracted,
            error=None,
            raw_content=content,
        )

    def _extract_json(self, content: str) -> dict[str, Any] | None:
        """Extract JSON object from content using JSONExtractor.

        Uses JSONExtractor from robust_parser which supports:
        - ```json ... ``` code blocks
        - '''json ... ''' code blocks
        - <output>...</output> tags
        - <result>...</result> tags
        - Inline JSON objects
        - Raw JSON

        Args:
            content: Text containing JSON data

        Returns:
            Parsed JSON dictionary or None if extraction failed
        """
        result = self._extractor.extract(content)
        if result.data is not None and isinstance(result.data, dict):
            return result.data
        return None

    def parse_with_fallback(
        self,
        content: str,
        default_schema: type | None = None,
    ) -> dict[str, Any]:
        """Parse with fallback to GenericRoleResponse on failure.

        This method ensures type-safe parsing even when the primary schema
        validation fails. It falls back to GenericRoleResponse which captures
        the raw content in a structured way.

        Args:
            content: Raw output from LLM
            default_schema: Schema to use as fallback (defaults to GenericRoleResponse)

        Returns:
            Parsed dictionary conforming to the schema or GenericRoleResponse format
        """
        # Determine fallback schema
        if default_schema is None:
            from polaris.cells.roles.kernel.public.contracts import GenericRoleResponse

            default_schema = GenericRoleResponse

        # Try primary parse first
        result = self.parse(content)
        if result.success and result.data is not None:
            return result.data

        # Fallback: use GenericRoleResponse format
        logger.info(
            "PydanticOutputParser falling back to GenericRoleResponse for content of length %d",
            len(content),
        )

        # Extract content and tool_calls from raw content if possible
        fallback_data: dict[str, Any] = {
            "content": content.strip() if content else "",
            "tool_calls": None,
            "metadata": {"fallback_used": True, "parse_error": result.error},
        }

        # Try to extract tool_calls from raw content
        try:
            extracted = self._extract_json(content)
            if extracted and isinstance(extracted, dict):
                if "tool_calls" in extracted:
                    fallback_data["tool_calls"] = extracted["tool_calls"]
                if "content" in extracted and not fallback_data["content"]:
                    fallback_data["content"] = extracted["content"]
                # Merge any extra fields into metadata
                for key, value in extracted.items():
                    if key not in fallback_data:
                        fallback_data["metadata"][key] = value
        except (RuntimeError, ValueError) as e:
            logger.debug("Failed to extract tool_calls from fallback content: %s", e)

        # Validate against fallback schema
        try:
            if is_dataclass(default_schema):
                validated = default_schema(**fallback_data)
                return asdict(validated) if is_dataclass(validated) else fallback_data
            else:
                # Pydantic model
                validated = default_schema.model_validate(fallback_data)  # type: ignore[attr-defined]
                return validated.model_dump() if hasattr(validated, "model_dump") else fallback_data
        except (RuntimeError, ValueError) as e:
            logger.warning(
                "Fallback schema validation also failed: %s, returning raw fallback_data",
                e,
            )
            return fallback_data

    def validate_schema_compatibility(self, schema: type) -> bool:
        """Check if a schema is compatible with this parser.

        A schema is compatible if it is:
        1. A frozen dataclass, or
        2. A Pydantic BaseModel

        Args:
            schema: Schema type to check

        Returns:
            True if schema is compatible, False otherwise
        """
        # Check for dataclass
        if is_dataclass(schema):
            return True

        # Check for Pydantic BaseModel
        try:
            from pydantic import BaseModel

            return issubclass(schema, BaseModel)
        except ImportError:
            logger.debug("Pydantic not available, skipping BaseModel check")

        return False


def create_fallback_parser(schema: type | None = None) -> PydanticOutputParser:
    """Factory function to create a PydanticOutputParser with fallback support.

    Args:
        schema: Optional schema to validate against

    Returns:
        Configured PydanticOutputParser instance
    """
    return PydanticOutputParser(schema=schema)
