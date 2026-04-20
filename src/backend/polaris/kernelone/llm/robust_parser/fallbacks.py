"""Fallback chain and SafeNull implementation for robust parsing.

This module provides progressive fallback strategies when primary parsing fails,
including type coercion and the SafeNull pattern to prevent cascade failures.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class SafeNull(Generic[T]):
    """A safe null object that prevents cascade failures.

    SafeNull[T] is returned when parsing fails after all correction attempts.
    It preserves the raw content and metadata so callers can handle the
    fallback gracefully without risking downstream crashes.

    Attributes:
        raw_content: The original unparseable content
        parse_error: Description of what went wrong
        partial_data: Any fields that were successfully extracted
        metadata: Additional context about the parse attempt
    """

    raw_content: str
    parse_error: str | None = None
    partial_data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_null(self) -> bool:
        """Always True for SafeNull."""
        return True

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for logging/debugging."""
        return {
            "type": "SafeNull",
            "raw_content": self.raw_content[:500] if self.raw_content else "",
            "parse_error": self.parse_error,
            "partial_data": self.partial_data,
            "metadata": self.metadata,
        }


@dataclass
class FallbackAttempt:
    """Record of a single fallback attempt."""

    strategy: str
    input_data: dict[str, Any] | None
    output_data: dict[str, Any] | None
    success: bool
    error: str | None


class FallbackChain:
    """Progressive fallback chain with type coercion.

    Strategies applied in order:
    1. Strict: Parse with exact schema
    2. Lenient: Coerce types loosely (int → str, etc.)
    3. Partial: Extract only present fields
    4. SafeNull: Return safe null with preserved raw content
    """

    def __init__(
        self,
        *,
        max_attempts: int = 3,
        coerce_types: bool = True,
        extract_partial: bool = True,
    ) -> None:
        """Initialize fallback chain.

        Args:
            max_attempts: Max number of fallback strategies to try
            coerce_types: Whether to attempt type coercion
            extract_partial: Whether to extract partial data on failure
        """
        self._max_attempts = max_attempts
        self._coerce_types = coerce_types
        self._extract_partial = extract_partial
        self._attempts: list[FallbackAttempt] = []

    @property
    def attempts(self) -> tuple[FallbackAttempt, ...]:
        """Get record of all fallback attempts."""
        return tuple(self._attempts)

    def try_parse(
        self,
        data: dict[str, Any],
        schema: type[T],
        attempt_number: int,
    ) -> T | SafeNull[T] | None:
        """Try to parse data with progressive fallback.

        Args:
            data: Extracted JSON data
            schema: Target Pydantic model
            attempt_number: Current attempt number (1-based)

        Returns:
            Parsed instance, SafeNull, or None if should continue trying
        """
        self._attempts.clear()

        # Attempt 1: Strict parsing
        strict_result = self._try_strict(data, schema)
        if strict_result is not None:
            return strict_result

        if attempt_number >= self._max_attempts:
            return self._create_safe_null(data, "Max fallback attempts reached")

        # Attempt 2: Type coercion
        if self._coerce_types:
            coerced_result = self._try_coerce(data, schema)
            if coerced_result is not None:
                return coerced_result

        # Attempt 3: Partial extraction
        if self._extract_partial:
            partial_result = self._try_partial(data, schema)
            if partial_result is not None:
                return partial_result

        return None

    def _try_strict(
        self,
        data: dict[str, Any],
        schema: type[T],
    ) -> T | SafeNull[T] | None:
        """Try strict parsing with schema."""
        try:
            instance = schema.model_validate(data)
            self._attempts.append(
                FallbackAttempt(
                    strategy="strict",
                    input_data=data,
                    output_data=instance.model_dump() if hasattr(instance, "model_dump") else data,
                    success=True,
                    error=None,
                )
            )
            return instance
        except (RuntimeError, ValueError) as e:
            self._attempts.append(
                FallbackAttempt(
                    strategy="strict",
                    input_data=data,
                    output_data=None,
                    success=False,
                    error=str(e),
                )
            )
            logger.debug("Strict parsing failed: %s", e)
            return None

    def _try_coerce(
        self,
        data: dict[str, Any],
        schema: type[T],
    ) -> T | SafeNull[T] | None:
        """Try parsing with type coercion."""
        coerced = self._coerce_data_types(data, schema)
        try:
            instance = schema.model_validate(coerced)
            self._attempts.append(
                FallbackAttempt(
                    strategy="coerce",
                    input_data=data,
                    output_data=instance.model_dump() if hasattr(instance, "model_dump") else coerced,
                    success=True,
                    error=None,
                )
            )
            return instance
        except (RuntimeError, ValueError) as e:
            self._attempts.append(
                FallbackAttempt(
                    strategy="coerce",
                    input_data=data,
                    output_data=None,
                    success=False,
                    error=str(e),
                )
            )
            logger.debug("Coerce parsing failed: %s", e)
            return None

    def _try_partial(
        self,
        data: dict[str, Any],
        schema: type[T],
    ) -> T | SafeNull[T] | None:
        """Try partial parsing - extract only fields that match schema."""
        schema_fields = self._get_schema_fields(schema)
        partial: dict[str, Any] = {}

        for field_name in schema_fields:
            if field_name in data:
                partial[field_name] = data[field_name]

        if not partial:
            return None

        try:
            instance = schema.model_validate(partial)
            self._attempts.append(
                FallbackAttempt(
                    strategy="partial",
                    input_data=data,
                    output_data=partial,
                    success=True,
                    error=None,
                )
            )
            return instance
        except (RuntimeError, ValueError) as e:
            self._attempts.append(
                FallbackAttempt(
                    strategy="partial",
                    input_data=data,
                    output_data=partial,
                    success=False,
                    error=str(e),
                )
            )
            logger.debug("Partial parsing failed: %s", e)
            return None

    def _coerce_data_types(
        self,
        data: dict[str, Any],
        schema: type[T],
    ) -> dict[str, Any]:
        """Coerce data types to match schema where possible."""
        coerced: dict[str, Any] = {}
        schema_fields = self._get_schema_fields(schema)

        for key, value in data.items():
            if key not in schema_fields:
                coerced[key] = value
                continue

            # Get expected type from schema
            expected_type = schema_fields[key]
            coerced[key] = self._coerce_value(value, expected_type)

        return coerced

    def _coerce_value(self, value: Any, expected_type: str) -> Any:
        """Coerce a single value to expected type."""
        if expected_type == "string" and not isinstance(value, str):
            return str(value)
        elif expected_type == "integer" and not isinstance(value, int):
            try:
                return int(float(value))  # Handle "42.0" → 42
            except (ValueError, TypeError):
                return value
        elif expected_type == "number" and not isinstance(value, (int, float)):
            try:
                return float(value)
            except (ValueError, TypeError):
                return value
        elif expected_type == "boolean" and not isinstance(value, bool):
            if isinstance(value, str):
                return value.lower() in ("true", "1", "yes", "on")
            return bool(value)

        return value

    def _get_schema_fields(self, schema: type[T]) -> dict[str, str]:
        """Extract field names and expected types from schema."""
        try:
            schema_dict = schema.model_json_schema()
            properties = schema_dict.get("properties", {})
            return {name: prop.get("type", "any") for name, prop in properties.items()}
        except (RuntimeError, ValueError):
            return {}

    def _create_safe_null(
        self,
        data: dict[str, Any],
        error: str,
    ) -> SafeNull[T]:
        """Create SafeNull with preserved data."""
        return SafeNull[T](
            raw_content=json.dumps(data) if isinstance(data, dict) else str(data),
            parse_error=error,
            partial_data=data,
            metadata={"attempts": [a.strategy for a in self._attempts]},
        )
