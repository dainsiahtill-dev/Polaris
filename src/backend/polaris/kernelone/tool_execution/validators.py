"""参数验证器模块。

提供统一的参数验证逻辑，支持：
- 字符串验证（长度、正则）
- 整数验证（范围）
- 数组验证（长度）
"""

from __future__ import annotations

__all__ = [
    "ERROR_ARRAY_TOO_LONG",
    "ERROR_ARRAY_TOO_SHORT",
    "ERROR_INTEGER_TOO_LARGE",
    "ERROR_INTEGER_TOO_SMALL",
    # Error codes
    "ERROR_INVALID_TYPE",
    "ERROR_REQUIRED_MISSING",
    "ERROR_STRING_PATTERN_MISMATCH",
    "ERROR_STRING_TOO_LONG",
    "ERROR_STRING_TOO_SHORT",
    "ArrayValidator",
    "BaseValidator",
    "BooleanValidator",
    "IntegerValidator",
    "StringValidator",
    # Classes
    "ValidationError",
    "ValidationResult",
    # Functions
    "get_validator",
    "validate_value",
]

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from re import Pattern
from typing import Any

# Error code constants
ERROR_INVALID_TYPE = "INVALID_TYPE"
ERROR_REQUIRED_MISSING = "REQUIRED_MISSING"
ERROR_STRING_TOO_SHORT = "MIN_LENGTH_VIOLATION"
ERROR_STRING_TOO_LONG = "MAX_LENGTH_VIOLATION"
ERROR_STRING_PATTERN_MISMATCH = "PATTERN_VIOLATION"
ERROR_INTEGER_TOO_SMALL = "MINIMUM_VIOLATION"
ERROR_INTEGER_TOO_LARGE = "MAXIMUM_VIOLATION"
ERROR_ARRAY_TOO_SHORT = "ARRAY_TOO_SHORT"
ERROR_ARRAY_TOO_LONG = "ARRAY_TOO_LONG"


@dataclass
class ValidationError:
    """验证错误结果。"""

    code: str
    message: str


@dataclass
class ToolArgValidationResult:
    """Validation result for tool argument validation.

    Note: This is distinct from other ValidationResult types:
    - ProviderConfigValidationResult: Provider configuration validation
    - FileOpValidationResult: File operation validation
    - LaunchValidationResult: Bootstrap launch validation
    - SchemaValidationResult: Schema validation
    """

    is_valid: bool
    error: ValidationError | None = None

    @classmethod
    def success(cls) -> ToolArgValidationResult:
        """Create a successful validation result."""
        return cls(is_valid=True)

    @classmethod
    def failure(cls, code: str, message: str) -> ToolArgValidationResult:
        """Create a failed validation result."""
        return cls(is_valid=False, error=ValidationError(code=code, message=message))


# Backward compatibility alias (deprecated)
ValidationResult = ToolArgValidationResult


class BaseValidator(ABC):
    """参数验证器基类。

    Uses Template Method pattern: validate() defines the algorithm skeleton,
    subclasses provide type-specific implementations via abstract methods.
    """

    @abstractmethod
    def _validate_type(self, value: Any) -> ValidationResult | None:
        """Validate the type of the value.

        Args:
            value: The value to validate.

        Returns:
            ValidationResult.failure if type is invalid, None if valid.
        """
        ...

    @abstractmethod
    def _validate_constraints(self, value: Any, spec: dict[str, Any]) -> ValidationResult | None:
        """Validate type-specific constraints (min/max, pattern, etc.).

        Args:
            value: The value to validate (already type-checked).
            spec: The parameter specification.

        Returns:
            ValidationResult.failure if constraint is violated, None if valid.
        """
        ...

    def validate(self, value: Any, spec: dict[str, Any]) -> ValidationResult:
        """Validate parameter value using Template Method pattern.

        Args:
            value: 待验证的值
            spec: 参数规格定义

        Returns:
            验证结果
        """
        required_result = self._check_required(value, spec)
        if required_result is not None:
            return required_result

        if value is None:
            return ValidationResult.success()

        type_result = self._validate_type(value)
        if type_result is not None:
            return type_result

        constraint_result = self._validate_constraints(value, spec)
        if constraint_result is not None:
            return constraint_result

        return ValidationResult.success()

    def _check_required(self, value: Any, spec: dict[str, Any]) -> ValidationResult | None:
        """检查必需字段。

        Args:
            value: 待验证的值。
            spec: 参数规格定义。

        Returns:
            如果验证失败返回 ValidationResult，否则返回 None。
        """
        required = spec.get("required", False)
        if required and value is None:
            return ValidationResult.failure(ERROR_REQUIRED_MISSING, "Value is required but was None")
        return None

    def _get_type_name(self, value: Any) -> str:
        """获取值的类型名称。

        Args:
            value: 任意值。

        Returns:
            类型名称字符串。
        """
        if value is None:
            return "None"
        return type(value).__name__


class StringValidator(BaseValidator):
    """字符串参数验证器。

    支持以下验证规则：
    - min_length: 最小长度
    - max_length: 最大长度
    - pattern: 正则表达式模式
    - required: 是否必需
    """

    def _validate_type(self, value: Any) -> ValidationResult | None:
        if not isinstance(value, str):
            return ValidationResult.failure(ERROR_INVALID_TYPE, f"Expected string, got {self._get_type_name(value)}")
        return None

    def _validate_constraints(self, value: Any, spec: dict[str, Any]) -> ValidationResult | None:
        min_length = spec.get("min_length")
        if min_length is not None:
            try:
                min_len = int(min_length)
                if len(value) < min_len:
                    return ValidationResult.failure(
                        ERROR_STRING_TOO_SHORT, f"String length {len(value)} is less than minimum {min_len}"
                    )
            except (TypeError, ValueError) as e:
                return ValidationResult.failure(ERROR_INVALID_TYPE, f"Invalid min_length value: {e}")

        max_length = spec.get("max_length")
        if max_length is not None:
            try:
                max_len = int(max_length)
                if len(value) > max_len:
                    return ValidationResult.failure(
                        ERROR_STRING_TOO_LONG, f"String length {len(value)} exceeds maximum {max_len}"
                    )
            except (TypeError, ValueError) as e:
                return ValidationResult.failure(ERROR_INVALID_TYPE, f"Invalid max_length value: {e}")

        pattern = spec.get("pattern")
        if pattern is not None:
            try:
                regex: Pattern[str] = re.compile(pattern)
                if not regex.search(value):
                    return ValidationResult.failure(
                        ERROR_STRING_PATTERN_MISMATCH, f"String does not match required pattern: {pattern}"
                    )
            except re.error as e:
                return ValidationResult.failure(ERROR_INVALID_TYPE, f"Invalid regex pattern: {e}")

        return None


class IntegerValidator(BaseValidator):
    """整数参数验证器。

    支持以下验证规则：
    - min: 最小值
    - max: 最大值
    - required: 是否必需
    """

    def _validate_type(self, value: Any) -> ValidationResult | None:
        if isinstance(value, bool):
            return ValidationResult.failure(ERROR_INVALID_TYPE, f"Expected integer, got {self._get_type_name(value)}")
        if isinstance(value, int):
            return None
        if isinstance(value, float) and value.is_integer():
            return None
        return ValidationResult.failure(ERROR_INVALID_TYPE, f"Expected integer, got {self._get_type_name(value)}")

    def _validate_constraints(self, value: Any, spec: dict[str, Any]) -> ValidationResult | None:
        int_value = int(value) if isinstance(value, float) else value

        min_value = spec.get("min")
        if min_value is not None:
            try:
                min_val = int(min_value)
                if int_value < min_val:
                    return ValidationResult.failure(
                        ERROR_INTEGER_TOO_SMALL, f"Value {int_value} is less than minimum {min_val}"
                    )
            except (TypeError, ValueError) as e:
                return ValidationResult.failure(ERROR_INVALID_TYPE, f"Invalid min value: {e}")

        max_value = spec.get("max")
        if max_value is not None:
            try:
                max_val = int(max_value)
                if int_value > max_val:
                    return ValidationResult.failure(
                        ERROR_INTEGER_TOO_LARGE, f"Value {int_value} exceeds maximum {max_val}"
                    )
            except (TypeError, ValueError) as e:
                return ValidationResult.failure(ERROR_INVALID_TYPE, f"Invalid max value: {e}")

        return None


class ArrayValidator(BaseValidator):
    """数组参数验证器。

    支持以下验证规则：
    - min_length: 最小长度
    - max_length: 最大长度
    - required: 是否必需
    """

    def _validate_type(self, value: Any) -> ValidationResult | None:
        if not isinstance(value, list):
            return ValidationResult.failure(
                ERROR_INVALID_TYPE, f"Expected array (list), got {self._get_type_name(value)}"
            )
        return None

    def _validate_constraints(self, value: Any, spec: dict[str, Any]) -> ValidationResult | None:
        length = len(value)

        min_length = spec.get("min_length")
        if min_length is not None:
            try:
                min_len = int(min_length)
                if length < min_len:
                    return ValidationResult.failure(
                        ERROR_ARRAY_TOO_SHORT, f"Array length {length} is less than minimum {min_len}"
                    )
            except (TypeError, ValueError) as e:
                return ValidationResult.failure(ERROR_INVALID_TYPE, f"Invalid min_length value: {e}")

        max_length = spec.get("max_length")
        if max_length is not None:
            try:
                max_len = int(max_length)
                if length > max_len:
                    return ValidationResult.failure(
                        ERROR_ARRAY_TOO_LONG, f"Array length {length} exceeds maximum {max_len}"
                    )
            except (TypeError, ValueError) as e:
                return ValidationResult.failure(ERROR_INVALID_TYPE, f"Invalid max_length value: {e}")

        return None


class BooleanValidator(BaseValidator):
    """布尔参数验证器。

    支持以下验证规则：
    - required: 是否必需
    """

    def _validate_type(self, value: Any) -> ValidationResult | None:
        if not isinstance(value, bool):
            return ValidationResult.failure(ERROR_INVALID_TYPE, f"Expected boolean, got {self._get_type_name(value)}")
        return None

    def _validate_constraints(self, value: Any, spec: dict[str, Any]) -> ValidationResult | None:
        return None


# Registry for convenient access
_VALIDATORS: dict[str, BaseValidator] = {
    "string": StringValidator(),
    "integer": IntegerValidator(),
    "array": ArrayValidator(),
    "boolean": BooleanValidator(),
}


def get_validator(value_type: str) -> BaseValidator | None:
    """获取指定类型的验证器。

    Args:
        value_type: 值类型名称，如 "string", "integer", "array"。

    Returns:
        对应的验证器实例，如果类型不支持则返回 None。
    """
    return _VALIDATORS.get(value_type)


def validate_value(value: Any, value_type: str, spec: dict[str, Any] | None = None) -> ValidationResult:
    """根据类型验证值。

    Args:
        value: 待验证的值。
        value_type: 值类型名称。
        spec: 额外的规格定义。

    Returns:
        验证结果。
    """
    validator = get_validator(value_type)
    if validator is None:
        # If unknown type, pass through without validation
        return ValidationResult.success()

    return validator.validate(value, spec or {})
