"""Domain exceptions for Polaris backend.

This module defines a hierarchy of domain-specific exceptions that are used
across the application. All domain exceptions inherit from DomainException
and can be converted to appropriate HTTP responses at the API boundary.

Exception Hierarchy:
    DomainException (base)
    ├── ValidationError
    ├── NotFoundError
    ├── ConflictError
    ├── PermissionDeniedError
    ├── ServiceUnavailableError
    ├── BusinessRuleError
    ├── ProcessError
    ├── LLMError  (bridged to llm.exceptions.LLMError — P0-NEW-004)
    └── InfrastructureError
        ├── StorageError
        ├── NetworkError
        └── ExternalServiceError

Note on LLMError (P0-NEW-004):
    domain.LLMError inherits from llm.exceptions.LLMError, bridging the
    domain and LLM exception hierarchies. This ensures that catching
    llm.exceptions.LLMError also catches domain-level LLM errors.
"""

from __future__ import annotations

from typing import Any

# Import canonical LLMError to bridge exception hierarchies.
# domain.LLMError is now a subclass of kernelone.errors.LLMError,
# fixing the silent exception catching bug (P0-NEW-004).
# Import from kernelone.errors directly to avoid lazy-loading issues with mypy.
from polaris.kernelone.errors import LLMError as _LLMErrorCanonical


class DomainException(Exception):
    """Base exception for all domain errors.

    Attributes:
        code: A machine-readable error code
        message: Human-readable error message
        details: Additional error details (optional)
    """

    code: str = "DOMAIN_ERROR"
    status_code: int = 500

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.code
        self.details = details or {}
        self.cause = cause

    def to_dict(self) -> dict[str, Any]:
        """Convert exception to a dictionary for serialization."""
        result: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.details:
            result["details"] = self.details
        return result

    def __str__(self) -> str:
        if self.details:
            return f"[{self.code}] {self.message} - {self.details}"
        return f"[{self.code}] {self.message}"


# =============================================================================
# Client/Validation Errors (4xx)
# =============================================================================


class ValidationError(DomainException):
    """Raised when input validation fails."""

    code = "VALIDATION_ERROR"
    status_code = 422

    def __init__(
        self,
        message: str = "Validation failed",
        *,
        field: str | None = None,
        value: Any | None = None,
        **kwargs,
    ) -> None:
        details = kwargs.pop("details", {})
        if field:
            details["field"] = field
        if value is not None:
            details["value"] = str(value)
        super().__init__(message, details=details, **kwargs)


class NotFoundError(DomainException):
    """Raised when a requested resource is not found."""

    code = "NOT_FOUND"
    status_code = 404

    def __init__(
        self,
        resource_type: str,
        resource_id: str,
        *,
        message: str | None = None,
        **kwargs,
    ) -> None:
        msg = message or f"{resource_type} '{resource_id}' not found"
        details = {"resource_type": resource_type, "resource_id": resource_id}
        details.update(kwargs.pop("details", {}))
        super().__init__(msg, details=details, **kwargs)


class ConflictError(DomainException):
    """Raised when there's a conflict with the current state."""

    code = "CONFLICT"
    status_code = 409

    def __init__(
        self,
        message: str = "Resource conflict",
        *,
        resource_type: str | None = None,
        **kwargs,
    ) -> None:
        details = {}
        if resource_type:
            details["resource_type"] = resource_type
        details.update(kwargs.pop("details", {}))
        super().__init__(message, details=details, **kwargs)


class PermissionDeniedError(DomainException):
    """Raised when the user doesn't have permission for an action."""

    code = "PERMISSION_DENIED"
    status_code = 403

    def __init__(
        self,
        message: str = "Permission denied",
        *,
        action: str | None = None,
        resource: str | None = None,
        **kwargs,
    ) -> None:
        details = {}
        if action:
            details["action"] = action
        if resource:
            details["resource"] = resource
        details.update(kwargs.pop("details", {}))
        super().__init__(message, details=details, **kwargs)


class AuthenticationError(DomainException):
    """Raised when authentication fails."""

    code = "AUTHENTICATION_ERROR"
    status_code = 401


class RateLimitError(DomainException):
    """Raised when rate limit is exceeded."""

    code = "RATE_LIMIT_EXCEEDED"
    status_code = 429

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        *,
        retry_after: int | None = None,
        **kwargs,
    ) -> None:
        details = {}
        if retry_after:
            details["retry_after"] = retry_after
        details.update(kwargs.pop("details", {}))
        super().__init__(message, details=details, **kwargs)


# =============================================================================
# Business Logic Errors
# =============================================================================


class BusinessRuleError(DomainException):
    """Raised when a business rule is violated."""

    code = "BUSINESS_RULE_VIOLATION"
    status_code = 400


class StateError(DomainException):
    """Raised when an operation is invalid for the current state."""

    code = "INVALID_STATE"
    status_code = 409

    def __init__(
        self,
        message: str,
        *,
        current_state: str | None = None,
        required_state: str | None = None,
        **kwargs,
    ) -> None:
        details = {}
        if current_state:
            details["current_state"] = current_state
        if required_state:
            details["required_state"] = required_state
        details.update(kwargs.pop("details", {}))
        super().__init__(message, details=details, **kwargs)


class ProcessError(DomainException):
    """Raised when a process operation fails."""

    code = "PROCESS_ERROR"
    status_code = 500

    def __init__(
        self,
        message: str = "Process operation failed",
        *,
        process_name: str | None = None,
        exit_code: int | None = None,
        **kwargs,
    ) -> None:
        details: dict[str, Any] = {}
        if process_name:
            details["process"] = process_name
        if exit_code is not None:
            details["exit_code"] = exit_code
        details.update(kwargs.pop("details", {}))
        super().__init__(message, details=details, **kwargs)


class ProcessAlreadyRunningError(StateError):
    """Raised when trying to start a process that's already running."""

    code = "PROCESS_ALREADY_RUNNING"
    status_code = 409

    def __init__(
        self,
        process_name: str,
        pid: int | None = None,
        **kwargs,
    ) -> None:
        details: dict[str, Any] = {"process": process_name}
        if pid:
            details["pid"] = pid
        details.update(kwargs.pop("details", {}))
        super().__init__(
            f"Process '{process_name}' is already running",
            details=details,
            **kwargs,
        )


class ProcessNotRunningError(StateError):
    """Raised when trying to operate on a process that's not running."""

    code = "PROCESS_NOT_RUNNING"
    status_code = 409

    def __init__(self, process_name: str, **kwargs) -> None:
        super().__init__(
            f"Process '{process_name}' is not running",
            details={"process": process_name},
            **kwargs,
        )


# =============================================================================
# Infrastructure Errors (5xx)
# =============================================================================


class InfrastructureError(DomainException):
    """Base class for infrastructure-related errors."""

    code = "INFRASTRUCTURE_ERROR"
    status_code = 500


class StorageError(InfrastructureError):
    """Raised when a storage operation fails."""

    code = "STORAGE_ERROR"

    def __init__(
        self,
        message: str = "Storage operation failed",
        *,
        path: str | None = None,
        operation: str | None = None,
        **kwargs,
    ) -> None:
        details = {}
        if path:
            details["path"] = path
        if operation:
            details["operation"] = operation
        details.update(kwargs.pop("details", {}))
        super().__init__(message, details=details, **kwargs)


class NetworkError(InfrastructureError):
    """Raised when a network operation fails."""

    code = "NETWORK_ERROR"

    def __init__(
        self,
        message: str = "Network operation failed",
        *,
        url: str | None = None,
        **kwargs,
    ) -> None:
        details = {}
        if url:
            details["url"] = url
        details.update(kwargs.pop("details", {}))
        super().__init__(message, details=details, **kwargs)


class ExternalServiceError(InfrastructureError):
    """Raised when an external service call fails."""

    code = "EXTERNAL_SERVICE_ERROR"

    def __init__(
        self,
        service: str,
        message: str | None = None,
        *,
        status_code: int | None = None,
        **kwargs,
    ) -> None:
        msg = message or f"External service '{service}' error"
        details: dict[str, Any] = {"service": service}
        if status_code:
            details["status_code"] = status_code
        details.update(kwargs.pop("details", {}))
        super().__init__(msg, details=details, **kwargs)


class ServiceUnavailableError(InfrastructureError):
    """Raised when a required service is unavailable."""

    code = "SERVICE_UNAVAILABLE"
    status_code = 503

    def __init__(
        self,
        service: str,
        *,
        message: str | None = None,
        **kwargs,
    ) -> None:
        msg = message or f"Service '{service}' is unavailable"
        super().__init__(msg, details={"service": service}, **kwargs)


class ConfigurationError(InfrastructureError):
    """Raised when there's a configuration error."""

    code = "CONFIGURATION_ERROR"
    status_code = 500

    def __init__(
        self,
        message: str,
        *,
        setting: str | None = None,
        **kwargs,
    ) -> None:
        details = {}
        if setting:
            details["setting"] = setting
        details.update(kwargs.pop("details", {}))
        super().__init__(message, details=details, **kwargs)


class TimeoutError(InfrastructureError):
    """Raised when an operation times out."""

    code = "TIMEOUT_ERROR"
    status_code = 504

    def __init__(
        self,
        message: str = "Operation timed out",
        *,
        timeout_seconds: float | None = None,
        operation: str | None = None,
        **kwargs,
    ) -> None:
        details: dict[str, Any] = {}
        if timeout_seconds is not None:
            details["timeout_seconds"] = timeout_seconds
        if operation:
            details["operation"] = operation
        details.update(kwargs.pop("details", {}))
        super().__init__(message, details=details, **kwargs)


class LLMError(_LLMErrorCanonical):  # type: ignore[misc] # mypy sees _LLMErrorCanonical as Any due to ignore_missing_imports
    """Raised when an LLM operation fails.

    Inherits from the canonical llm.exceptions.LLMError, bridging the
    domain and LLM exception hierarchies (P0-NEW-004 fix).
    Preserves domain-specific provider/model attributes.
    """

    def __init__(
        self,
        message: str = "LLM operation failed",
        *,
        provider: str | None = None,
        model: str | None = None,
        **kwargs,
    ) -> None:
        details: dict[str, Any] = {}
        if provider:
            details["provider"] = provider
        if model:
            details["model"] = model
        existing = kwargs.pop("details", None)
        if existing:
            details.update(existing)
        # LLM errors are non-retryable by default
        kwargs.setdefault("retryable", False)
        super().__init__(message, details=details, **kwargs)
