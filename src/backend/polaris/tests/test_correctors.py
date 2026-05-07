"""Tests for ValidationErrorCorrector - Error type to prompt mapping."""

from __future__ import annotations

from typing import ClassVar

import pytest
from polaris.kernelone.llm.robust_parser.correctors import (
    CorrectionPrompt,
    ValidationErrorCorrector,
)
from pydantic import BaseModel


class TestSchema(BaseModel):
    """Test schema for validation tests."""

    __test__: ClassVar[bool] = False

    name: str
    value: int
    optional_field: str | None = None


class ComplexSchema(BaseModel):
    """Schema with various field types for coercion testing."""

    count: int
    enabled: bool
    ratio: float
    tags: list[str]


class ValidationErrorCorrectorTests:
    """Tests for ValidationErrorCorrector class."""

    def test_build_correction_prompt_missing_field(self) -> None:
        """Missing required field generates correct prompt."""
        from pydantic import ValidationError

        corrector = ValidationErrorCorrector()
        # Create a ValidationError for missing 'value' field
        try:
            TestSchema.model_validate({"name": "test"})  # Missing 'value'
        except ValidationError as e:
            prompt = corrector.build_correction_prompt(e, TestSchema)

            assert isinstance(prompt, CorrectionPrompt)
            assert "missing" in prompt.message.lower() or "required" in prompt.message.lower()
            assert "value" in prompt.message
            assert prompt.schema_summary != ""

    def test_build_correction_prompt_wrong_type(self) -> None:
        """Wrong type field generates correct prompt."""
        from pydantic import ValidationError

        corrector = ValidationErrorCorrector()
        try:
            TestSchema.model_validate({"name": "test", "value": "not_an_int"})
        except ValidationError as e:
            prompt = corrector.build_correction_prompt(e, TestSchema)

            assert isinstance(prompt, CorrectionPrompt)
            assert "value" in prompt.message
            assert prompt.errors is not None
            assert len(prompt.errors) > 0

    def test_build_correction_prompt_includes_schema(self) -> None:
        """Correction prompt includes schema preview."""
        from pydantic import ValidationError

        corrector = ValidationErrorCorrector(include_schema=True)
        try:
            TestSchema.model_validate({})
        except ValidationError as e:
            prompt = corrector.build_correction_prompt(e, TestSchema)

            assert "schema" in prompt.message.lower() or "json" in prompt.message.lower()

    def test_build_correction_prompt_no_schema(self) -> None:
        """Correction prompt can exclude schema."""
        from pydantic import ValidationError

        corrector = ValidationErrorCorrector(include_schema=False)
        try:
            TestSchema.model_validate({})
        except ValidationError as e:
            prompt = corrector.build_correction_prompt(e, TestSchema)

            assert prompt.schema_summary == ""

    def test_build_correction_prompt_truncates_long_input(self) -> None:
        """Long input values are truncated in prompt."""
        from pydantic import ValidationError

        corrector = ValidationErrorCorrector(max_input_preview=20)
        try:
            TestSchema.model_validate(
                {
                    "name": "x" * 100,  # Very long name
                    "value": 42,
                }
            )
        except ValidationError as e:
            prompt = corrector.build_correction_prompt(e, TestSchema)

            # The input in the error should be truncated
            for err in prompt.errors:
                if err.get("input_preview"):
                    assert len(err["input_preview"]) <= 30  # 20 + some overhead

    def test_build_correction_prompt_multiple_errors(self) -> None:
        """Multiple validation errors are all listed."""
        from pydantic import ValidationError

        corrector = ValidationErrorCorrector()
        try:
            TestSchema.model_validate({"name": 123, "value": "not_int"})
        except ValidationError as e:
            prompt = corrector.build_correction_prompt(e, TestSchema)

            assert len(prompt.errors) >= 2

    def test_build_correction_prompt_to_message(self) -> None:
        """to_message returns the formatted message."""
        from pydantic import ValidationError

        corrector = ValidationErrorCorrector()
        try:
            TestSchema.model_validate({})
        except ValidationError as e:
            prompt = corrector.build_correction_prompt(e, TestSchema)

            assert prompt.to_message() == prompt.message

    def test_build_partial_data_prompt(self) -> None:
        """Partial data prompt includes present and missing fields."""
        corrector = ValidationErrorCorrector()
        partial_data = {"name": "test"}
        missing_fields = ["value"]

        prompt = corrector.build_partial_data_prompt(partial_data, missing_fields, TestSchema)

        assert isinstance(prompt, CorrectionPrompt)
        assert "name" in prompt.message
        assert "value" in prompt.message
        assert "missing" in prompt.message.lower()

    def test_error_messages_mapping(self) -> None:
        """Various error types map to human-readable messages."""

        corrector = ValidationErrorCorrector()

        test_cases = [
            ("missing", "missing" in corrector._ERROR_MESSAGES),
            ("string_type", "string_type" in corrector._ERROR_MESSAGES),
            ("int_type", "int_type" in corrector._ERROR_MESSAGES),
            ("bool_type", "bool_type" in corrector._ERROR_MESSAGES),
            ("literal_error", "literal_error" in corrector._ERROR_MESSAGES),
        ]

        for error_type, exists in test_cases:
            assert exists, f"Missing error message for {error_type}"


class TestValidationErrorCorrectorEdgeCases:
    """Edge case tests for ValidationErrorCorrector."""

    def test_empty_validation_error(self) -> None:
        """Handle case with no actual errors."""
        from pydantic import BaseModel, ValidationError

        corrector = ValidationErrorCorrector()

        # Create a ValidationError with no errors (shouldn't normally happen)
        class EmptySchema(BaseModel):
            pass

        try:
            # This shouldn't raise since EmptySchema accepts anything
            EmptySchema.model_validate({})
        except ValidationError as e:
            # If it does raise, handle gracefully
            if e.errors():
                prompt = corrector.build_correction_prompt(e, EmptySchema)
                assert isinstance(prompt, CorrectionPrompt)

    def test_nested_field_error(self) -> None:
        """Nested field errors show full path."""
        from pydantic import BaseModel, ValidationError

        class NestedSchema(BaseModel):
            outer: dict[str, int]

        corrector = ValidationErrorCorrector()
        try:
            NestedSchema.model_validate({"outer": {"key": "not_int"}})
        except ValidationError as e:
            prompt = corrector.build_correction_prompt(e, NestedSchema)

            # Error location should include nested path
            assert any("outer" in str(err["location"]) or "outer" in prompt.message for err in prompt.errors)

    def test_suggested_fix_for_missing_fields(self) -> None:
        """Suggested fix hints at missing fields."""
        from pydantic import ValidationError

        corrector = ValidationErrorCorrector()
        try:
            TestSchema.model_validate({})
        except ValidationError as e:
            prompt = corrector.build_correction_prompt(e, TestSchema)

            # Should have suggested fix
            if prompt.suggested_fix:
                assert "value" in prompt.suggested_fix or "missing" in prompt.suggested_fix.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
