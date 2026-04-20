"""Tests for validators module."""

import pytest
from polaris.kernelone.tool_execution.validators import (
    ArrayValidator,
    BaseValidator,
    BooleanValidator,
    IntegerValidator,
    StringValidator,
    ValidationError,
    ValidationResult,
    get_validator,
    validate_value,
)


class TestStringValidator:
    """String validator tests."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.validator = StringValidator()

    def test_min_length_valid(self) -> None:
        """Test minimum length validation - valid case."""
        result = self.validator.validate("hello", {"min_length": 3})
        assert result.is_valid

    def test_min_length_invalid(self) -> None:
        """Test minimum length validation - invalid case."""
        result = self.validator.validate("hi", {"min_length": 3})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "MIN_LENGTH_VIOLATION"

    def test_min_length_boundary(self) -> None:
        """Test minimum length validation - boundary case."""
        result = self.validator.validate("hi", {"min_length": 2})
        assert result.is_valid

    def test_max_length_valid(self) -> None:
        """Test maximum length validation - valid case."""
        result = self.validator.validate("hello", {"max_length": 10})
        assert result.is_valid

    def test_max_length_invalid(self) -> None:
        """Test maximum length validation - invalid case."""
        result = self.validator.validate("hello world", {"max_length": 5})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "MAX_LENGTH_VIOLATION"

    def test_max_length_boundary(self) -> None:
        """Test maximum length validation - boundary case."""
        result = self.validator.validate("hello", {"max_length": 5})
        assert result.is_valid

    def test_pattern_valid(self) -> None:
        """Test pattern validation - valid case."""
        result = self.validator.validate("hello123", {"pattern": r"^[a-z0-9]+$"})
        assert result.is_valid

    def test_pattern_invalid(self) -> None:
        """Test pattern validation - invalid case."""
        result = self.validator.validate("hello!", {"pattern": r"^[a-z0-9]+$"})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "PATTERN_VIOLATION"

    def test_pattern_boundary(self) -> None:
        """Test pattern validation - empty pattern match."""
        result = self.validator.validate("", {"pattern": r"^$"})
        assert result.is_valid

    def test_combined_constraints_valid(self) -> None:
        """Test combined constraints - valid case."""
        result = self.validator.validate("hello", {"min_length": 3, "max_length": 10, "pattern": r"^[a-z]+$"})
        assert result.is_valid

    def test_combined_constraints_invalid_pattern(self) -> None:
        """Test combined constraints - pattern violation."""
        result = self.validator.validate("hello123", {"min_length": 3, "max_length": 10, "pattern": r"^[a-z]+$"})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "PATTERN_VIOLATION"

    def test_invalid_type(self) -> None:
        """Test type validation - non-string input."""
        result = self.validator.validate(123, {})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "INVALID_TYPE"

    def test_invalid_type_boolean(self) -> None:
        """Test type validation - boolean input."""
        result = self.validator.validate(True, {})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "INVALID_TYPE"

    def test_invalid_type_list(self) -> None:
        """Test type validation - list input."""
        result = self.validator.validate(["a", "b"], {})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "INVALID_TYPE"

    def test_invalid_regex(self) -> None:
        """Test invalid regex is caught."""
        result = self.validator.validate("test", {"pattern": r"[invalid"})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "INVALID_TYPE"

    def test_empty_constraints(self) -> None:
        """Test with empty constraints."""
        result = self.validator.validate("hello", {})
        assert result.is_valid

    def test_empty_string(self) -> None:
        """Test empty string validation."""
        result = self.validator.validate("", {"min_length": 0})
        assert result.is_valid

    def test_empty_string_invalid_min_length(self) -> None:
        """Test empty string with min_length > 0."""
        result = self.validator.validate("", {"min_length": 1})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "MIN_LENGTH_VIOLATION"

    def test_unicode_string(self) -> None:
        """Test unicode string validation."""
        result = self.validator.validate("你好世界", {})
        assert result.is_valid

    def test_special_characters(self) -> None:
        """Test special characters in string."""
        result = self.validator.validate("hello!@#$%^&*()", {})
        assert result.is_valid

    def test_none_without_required(self) -> None:
        """Test None value without required constraint."""
        result = self.validator.validate(None, {})
        assert result.is_valid

    def test_none_with_required(self) -> None:
        """Test None value with required constraint."""
        result = self.validator.validate(None, {"required": True})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "REQUIRED_MISSING"

    def test_invalid_min_length_type(self) -> None:
        """Test invalid min_length type."""
        result = self.validator.validate("test", {"min_length": "invalid"})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "INVALID_TYPE"

    def test_invalid_max_length_type(self) -> None:
        """Test invalid max_length type."""
        result = self.validator.validate("test", {"max_length": "invalid"})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "INVALID_TYPE"


class TestIntegerValidator:
    """Integer validator tests."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.validator = IntegerValidator()

    def test_minimum_valid(self) -> None:
        """Test minimum validation - valid case."""
        result = self.validator.validate(10, {"min": 5})
        assert result.is_valid

    def test_minimum_invalid(self) -> None:
        """Test minimum validation - invalid case."""
        result = self.validator.validate(3, {"min": 5})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "MINIMUM_VIOLATION"

    def test_minimum_boundary(self) -> None:
        """Test minimum validation - boundary case."""
        result = self.validator.validate(5, {"min": 5})
        assert result.is_valid

    def test_maximum_valid(self) -> None:
        """Test maximum validation - valid case."""
        result = self.validator.validate(50, {"max": 100})
        assert result.is_valid

    def test_maximum_invalid(self) -> None:
        """Test maximum validation - invalid case."""
        result = self.validator.validate(150, {"max": 100})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "MAXIMUM_VIOLATION"

    def test_maximum_boundary(self) -> None:
        """Test maximum validation - boundary case."""
        result = self.validator.validate(100, {"max": 100})
        assert result.is_valid

    def test_negative_value_valid(self) -> None:
        """Test negative value validation."""
        result = self.validator.validate(-10, {"min": -100})
        assert result.is_valid

    def test_negative_value_invalid(self) -> None:
        """Test negative value below minimum."""
        result = self.validator.validate(-150, {"min": -100})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "MINIMUM_VIOLATION"

    def test_zero_valid(self) -> None:
        """Test zero value validation."""
        result = self.validator.validate(0, {"min": 0})
        assert result.is_valid

    def test_zero_invalid(self) -> None:
        """Test zero value below minimum."""
        result = self.validator.validate(-1, {"min": 0})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "MINIMUM_VIOLATION"

    def test_large_numbers(self) -> None:
        """Test large number validation."""
        result = self.validator.validate(10**15, {"max": 10**16})
        assert result.is_valid

    def test_invalid_type_string(self) -> None:
        """Test type validation - string input."""
        result = self.validator.validate("123", {})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "INVALID_TYPE"

    def test_invalid_type_float(self) -> None:
        """Test type validation - float input."""
        result = self.validator.validate(3.14, {})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "INVALID_TYPE"

    def test_boolean_invalid(self) -> None:
        """Test boolean is rejected as integer."""
        result = self.validator.validate(True, {})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "INVALID_TYPE"
        result = self.validator.validate(False, {})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "INVALID_TYPE"

    def test_invalid_type_list(self) -> None:
        """Test type validation - list input."""
        result = self.validator.validate([1, 2, 3], {})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "INVALID_TYPE"

    def test_empty_constraints(self) -> None:
        """Test with empty constraints."""
        result = self.validator.validate(42, {})
        assert result.is_valid

    def test_none_without_required(self) -> None:
        """Test None value without required constraint."""
        result = self.validator.validate(None, {})
        assert result.is_valid

    def test_none_with_required(self) -> None:
        """Test None value with required constraint."""
        result = self.validator.validate(None, {"required": True})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "REQUIRED_MISSING"

    def test_float_integer_valid(self) -> None:
        """Test float that is integer value."""
        result = self.validator.validate(42.0, {})
        assert result.is_valid

    def test_invalid_min_type(self) -> None:
        """Test invalid min type."""
        result = self.validator.validate(10, {"min": "invalid"})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "INVALID_TYPE"

    def test_invalid_max_type(self) -> None:
        """Test invalid max type."""
        result = self.validator.validate(10, {"max": "invalid"})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "INVALID_TYPE"


class TestArrayValidator:
    """Array validator tests."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.validator = ArrayValidator()

    def test_min_items_valid(self) -> None:
        """Test minimum items validation - valid case."""
        result = self.validator.validate([1, 2, 3], {"min_length": 2})
        assert result.is_valid

    def test_min_items_invalid(self) -> None:
        """Test minimum items validation - invalid case."""
        result = self.validator.validate([1], {"min_length": 2})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "ARRAY_TOO_SHORT"

    def test_min_items_boundary(self) -> None:
        """Test minimum items validation - boundary case."""
        result = self.validator.validate([1, 2], {"min_length": 2})
        assert result.is_valid

    def test_max_items_valid(self) -> None:
        """Test maximum items validation - valid case."""
        result = self.validator.validate([1, 2], {"max_length": 5})
        assert result.is_valid

    def test_max_items_invalid(self) -> None:
        """Test maximum items validation - invalid case."""
        result = self.validator.validate([1, 2, 3, 4, 5, 6], {"max_length": 5})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "ARRAY_TOO_LONG"

    def test_max_items_boundary(self) -> None:
        """Test maximum items validation - boundary case."""
        result = self.validator.validate([1, 2, 3], {"max_length": 3})
        assert result.is_valid

    def test_empty_array_valid(self) -> None:
        """Test empty array validation."""
        result = self.validator.validate([], {"min_length": 0})
        assert result.is_valid

    def test_empty_array_invalid(self) -> None:
        """Test empty array with min_length > 0."""
        result = self.validator.validate([], {"min_length": 1})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "ARRAY_TOO_SHORT"

    def test_single_item_valid(self) -> None:
        """Test single item array."""
        result = self.validator.validate(["item"], {"min_length": 1})
        assert result.is_valid

    def test_large_array(self) -> None:
        """Test large array validation."""
        large_list = list(range(1000))
        result = self.validator.validate(large_list, {"max_length": 2000})
        assert result.is_valid

    def test_mixed_types_array(self) -> None:
        """Test array with mixed types."""
        result = self.validator.validate([1, "two", 3.0, True, None], {"min_length": 3})
        assert result.is_valid

    def test_nested_array(self) -> None:
        """Test nested array validation."""
        result = self.validator.validate([[1, 2], [3, 4]], {"min_length": 2})
        assert result.is_valid

    def test_invalid_type_string(self) -> None:
        """Test type validation - string input."""
        result = self.validator.validate("abc", {})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "INVALID_TYPE"

    def test_invalid_type_dict(self) -> None:
        """Test type validation - dict input."""
        result = self.validator.validate({"a": 1}, {})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "INVALID_TYPE"

    def test_invalid_type_int(self) -> None:
        """Test type validation - int input."""
        result = self.validator.validate(42, {})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "INVALID_TYPE"

    def test_invalid_type_tuple(self) -> None:
        """Test type validation - tuple input."""
        result = self.validator.validate((1, 2, 3), {})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "INVALID_TYPE"

    def test_empty_constraints(self) -> None:
        """Test with empty constraints."""
        result = self.validator.validate([1, 2, 3], {})
        assert result.is_valid

    def test_none_without_required(self) -> None:
        """Test None value without required constraint."""
        result = self.validator.validate(None, {})
        assert result.is_valid

    def test_none_with_required(self) -> None:
        """Test None value with required constraint."""
        result = self.validator.validate(None, {"required": True})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "REQUIRED_MISSING"


class TestBooleanValidator:
    """Boolean validator tests."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.validator = BooleanValidator()

    def test_valid_true(self) -> None:
        """Test validation of True."""
        result = self.validator.validate(True, {})
        assert result.is_valid

    def test_valid_false(self) -> None:
        """Test validation of False."""
        result = self.validator.validate(False, {})
        assert result.is_valid

    def test_invalid_type_integer(self) -> None:
        """Test type validation - integer input."""
        result = self.validator.validate(1, {})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "INVALID_TYPE"

    def test_invalid_type_zero(self) -> None:
        """Test type validation - zero (not boolean)."""
        result = self.validator.validate(0, {})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "INVALID_TYPE"

    def test_invalid_type_string_true(self) -> None:
        """Test type validation - string 'true' is not boolean."""
        result = self.validator.validate("true", {})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "INVALID_TYPE"

    def test_invalid_type_string_false(self) -> None:
        """Test type validation - string 'false' is not boolean."""
        result = self.validator.validate("false", {})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "INVALID_TYPE"

    def test_invalid_type_list(self) -> None:
        """Test type validation - list input."""
        result = self.validator.validate([True, False], {})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "INVALID_TYPE"

    def test_none_without_required(self) -> None:
        """Test None value without required constraint."""
        result = self.validator.validate(None, {})
        assert result.is_valid

    def test_none_with_required(self) -> None:
        """Test None value with required constraint."""
        result = self.validator.validate(None, {"required": True})
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "REQUIRED_MISSING"


class TestValidationResult:
    """ValidationResult tests."""

    def test_success(self) -> None:
        """Test success result creation."""
        result = ValidationResult.success()
        assert result.is_valid
        assert result.error is None

    def test_failure(self) -> None:
        """Test failure result creation."""
        result = ValidationResult.failure("TEST_ERROR", "Test message")
        assert not result.is_valid
        assert result.error is not None
        assert result.error.code == "TEST_ERROR"
        assert result.error.message == "Test message"

    def test_failure_error_details(self) -> None:
        """Test failure result contains correct error details."""
        result = ValidationResult.failure("CODE123", "Error description")
        assert result.error is not None
        assert result.error.code == "CODE123"
        assert result.error.message == "Error description"

    def test_multiple_success_results(self) -> None:
        """Test creating multiple success results."""
        result1 = ValidationResult.success()
        result2 = ValidationResult.success()
        assert result1.is_valid
        assert result2.is_valid
        assert result1.error is None
        assert result2.error is None

    def test_multiple_failure_results(self) -> None:
        """Test creating multiple failure results."""
        result1 = ValidationResult.failure("ERROR_A", "Message A")
        result2 = ValidationResult.failure("ERROR_B", "Message B")
        assert not result1.is_valid
        assert not result2.is_valid
        assert result1.error is not None
        assert result2.error is not None
        assert result1.error.code == "ERROR_A"
        assert result2.error.code == "ERROR_B"


class TestValidationError:
    """ValidationError tests."""

    def test_error_creation(self) -> None:
        """Test error creation."""
        error = ValidationError(code="ERR001", message="Test error")
        assert error.code == "ERR001"
        assert error.message == "Test error"

    def test_error_empty_code(self) -> None:
        """Test error with empty code."""
        error = ValidationError(code="", message="Error with empty code")
        assert error.code == ""
        assert error.message == "Error with empty code"

    def test_error_empty_message(self) -> None:
        """Test error with empty message."""
        error = ValidationError(code="ERR", message="")
        assert error.code == "ERR"
        assert error.message == ""


class TestBaseValidator:
    """BaseValidator abstract class tests."""

    def test_validator_subclasses_instantiable(self) -> None:
        """Test that concrete validator subclasses can be instantiated."""
        string_validator = StringValidator()
        integer_validator = IntegerValidator()
        array_validator = ArrayValidator()
        boolean_validator = BooleanValidator()
        assert string_validator is not None
        assert integer_validator is not None
        assert array_validator is not None
        assert boolean_validator is not None

    def test_base_validator_is_abstract(self) -> None:
        """Test that BaseValidator cannot be instantiated directly."""
        with pytest.raises(TypeError):
            BaseValidator()  # type: ignore


class TestValidatorRegistry:
    """Validator registry tests."""

    def test_get_validator_string(self) -> None:
        """Test getting string validator."""
        validator = get_validator("string")
        assert validator is not None
        assert isinstance(validator, StringValidator)

    def test_get_validator_integer(self) -> None:
        """Test getting integer validator."""
        validator = get_validator("integer")
        assert validator is not None
        assert isinstance(validator, IntegerValidator)

    def test_get_validator_array(self) -> None:
        """Test getting array validator."""
        validator = get_validator("array")
        assert validator is not None
        assert isinstance(validator, ArrayValidator)

    def test_get_validator_boolean(self) -> None:
        """Test getting boolean validator."""
        validator = get_validator("boolean")
        assert validator is not None
        assert isinstance(validator, BooleanValidator)

    def test_get_validator_unknown(self) -> None:
        """Test getting unknown validator."""
        validator = get_validator("unknown")
        assert validator is None

    def test_validate_value_string(self) -> None:
        """Test validate_value with string type."""
        result = validate_value("hello", "string")
        assert result.is_valid

    def test_validate_value_integer(self) -> None:
        """Test validate_value with integer type."""
        result = validate_value(42, "integer")
        assert result.is_valid

    def test_validate_value_array(self) -> None:
        """Test validate_value with array type."""
        result = validate_value([1, 2, 3], "array")
        assert result.is_valid

    def test_validate_value_boolean(self) -> None:
        """Test validate_value with boolean type."""
        result = validate_value(True, "boolean")
        assert result.is_valid

    def test_validate_value_unknown_type_passes(self) -> None:
        """Test validate_value with unknown type (passes by default)."""
        result = validate_value("test", "unknown")
        assert result.is_valid

    def test_validate_value_with_constraints(self) -> None:
        """Test validate_value with type-specific constraints."""
        result = validate_value("hello", "string", {"min_length": 3})
        assert result.is_valid
