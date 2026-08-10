"""Validation, path security, and constitution/policy violation errors.

Internal submodule of :mod:`polaris.kernelone.errors`.
Public symbols are re-exported from the package ``__init__``.
"""

from __future__ import annotations

from typing import Any

from polaris.kernelone.errors._base import KernelOneError


class ValidationError(KernelOneError):
    """Validation-related errors.

    Raised when input data fails validation checks.
    These errors are typically non-retryable without fixing the input.

    Attributes:
        field: The field that failed validation.
        value: The value that was rejected, if applicable.
        constraint: The constraint that was violated.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "VALIDATION_ERROR",
        field: str = "",
        value: Any = None,
        constraint: str = "",
        **kwargs,
    ) -> None:
        kwargs.setdefault("retryable", False)
        super().__init__(message, code=code, **kwargs)
        self.field = field
        self.value = value
        self.constraint = constraint
        if field:
            self.details["field"] = field
        if constraint:
            self.details["constraint"] = constraint


class PathTraversalError(ValidationError):
    """Path traversal security violation.

    Raised when a path attempt to access files outside allowed directories.
    """

    def __init__(
        self,
        message: str,
        *,
        path: str = "",
        allowed_root: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="PATH_TRAVERSAL_ERROR",
            field="path",
            constraint="must_be_within_allowed_root",
            **kwargs,
        )
        if path:
            self.details["path"] = path
        if allowed_root:
            self.details["allowed_root"] = allowed_root


class WorkflowContractError(ValidationError):
    """Workflow contract validation failed.

    Raised when workflow definition violates contract constraints.

    Attributes:
        errors: List of specific contract violation messages.
    """

    def __init__(
        self,
        message: str,
        *,
        errors: list[str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="WORKFLOW_CONTRACT_ERROR",
            constraint="workflow_contract_violation",
            **kwargs,
        )
        self.errors = [str(item).strip() for item in errors or [] if str(item).strip()]
        if self.errors:
            self.details["errors"] = self.errors


class ReservedKeyViolationError(ValidationError):
    """Reserved key Violation in custom context keys."""

    def __init__(
        self,
        message: str,
        *,
        key: str = "",
        reserved_keys: list[str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="RESERVED_KEY_VIOLATION_ERROR",
            field="context_key",
            **kwargs,
        )
        self.key = key
        self.reserved_keys = reserved_keys or []
        if key:
            self.details["key"] = key
        if self.reserved_keys:
            self.details["reserved_keys"] = self.reserved_keys


# ============================================================================
# Constitution Violation Error
# ============================================================================


class ConstitutionViolationError(ValidationError):
    """Constitution violation error."""

    def __init__(
        self,
        message: str,
        *,
        rule_name: str = "",
        violation_type: str = "",
    ) -> None:
        super().__init__(
            message,
            code="CONSTITUTION_VIOLATION_ERROR",
            constraint="constitution_rule",
        )
        if rule_name:
            self.details["rule_name"] = rule_name
        if violation_type:
            self.details["violation_type"] = violation_type


class PathSecurityError(ValidationError):
    """Path security violation."""

    def __init__(
        self,
        message: str,
        *,
        path: str = "",
        violation_type: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="PATH_SECURITY_ERROR",
            field="path",
            constraint="security_policy",
            **kwargs,
        )
        if path:
            self.details["path"] = path
        if violation_type:
            self.details["violation_type"] = violation_type
