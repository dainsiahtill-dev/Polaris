"""Polaris KernelOne exception hierarchy.

This module provides the unified exception hierarchy for all KernelOne and Cell errors.
It serves as the single source of truth for error classification and handling.

Design Principles:
- All KernelOne exceptions inherit from KernelOneError base class
- Cell-level exceptions inherit from CellError (which inherits from KernelOneError)
- Domain-specific exceptions inherit from appropriate category base classes
- Each exception carries structured metadata for error classification
- Exceptions are designed for easy identification and handling

Migration Target:
- This module replaces scattered error definitions across the codebase
- Existing exceptions should migrate to appropriate hierarchy branches
- Backward compatibility aliases should be maintained during migration

Hierarchy:
    KernelOneError (root)
    ├── ConfigurationError    - Configuration-related errors
    ├── ValidationError       - Validation-related errors
    ├── ExecutionError        - Execution-related errors
    ├── ResourceError         - Resource-related errors
    ├── CommunicationError    - Communication-related errors
    ├── CellError             - Cell-level errors (bridge to domain)
    │   ├── RoleCellError     - Role-specific cell errors
    │   ├── LLMCellError      - LLM-specific cell errors
    │   ├── AuditCellError    - Audit-specific cell errors
    │   └── PolicyCellError   - Policy-specific cell errors
    └── StateError            - State machine errors

Usage:
    from polaris.kernelone.errors import (
        KernelOneError,
        ConfigurationError,
        ValidationError,
        ExecutionError,
    )

    try:
        await some_kernel_operation()
    except KernelOneError as e:
        logger.error(f"KernelOne error: {e}")
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ============================================================================
# Error Category Enum (Single Source of Truth)
# ============================================================================


class ErrorCategory(str, Enum):
    """Canonical error categories for KernelOne.

    This enum unifies error categorization across all subsystems:
    - LLM errors (provider, timeout, rate limit, etc.)
    - Kernel errors (invalid input, not found, etc.)
    - Orchestration errors (transient vs permanent failures)
    - Tool execution errors (authorization, validation, etc.)

    Usage:
        from polaris.kernelone.errors import ErrorCategory

        def handle_error(error: Exception) -> ErrorCategory:
            if isinstance(error, asyncio.TimeoutError):
                return ErrorCategory.SYSTEM_TIMEOUT
            ...
    """

    # --- LLM-related errors ---
    PROVIDER_ERROR = "provider_error"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    INVALID_RESPONSE = "invalid_response"
    JSON_PARSE = "json_parse"
    CONFIG_ERROR = "config_error"
    NETWORK_ERROR = "network_error"

    # --- Kernel/system errors ---
    UNKNOWN = "unknown"
    INVALID_INPUT = "invalid_input"
    NOT_FOUND = "not_found"
    ALREADY_EXISTS = "already_exists"
    PERMISSION_DENIED = "permission_denied"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    FAILED_PRECONDITION = "failed_precondition"
    ABORTED = "aborted"
    OUT_OF_RANGE = "out_of_range"
    UNIMPLEMENTED = "unimplemented"
    INTERNAL_ERROR = "internal_error"
    UNAVAILABLE = "unavailable"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    TRANSPORT_ERROR = "transport_error"

    # --- Transient errors (can be retried) ---
    TRANSIENT_NETWORK = "transient_network"
    TRANSIENT_RATE_LIMIT = "transient_rate_limit"
    TRANSIENT_RESOURCE = "transient_resource"
    SERVICE_UNAVAILABLE = "service_unavailable"
    TEMPORARY_FAILURE = "temporary_failure"
    SYSTEM_TIMEOUT = "system_timeout"
    SYSTEM_CAPACITY = "system_capacity"
    SYSTEM_UNKNOWN = "system_unknown"

    # --- Permanent errors (retry won't help) ---
    PERMANENT_AUTH = "permanent_auth"
    PERMANENT_VALIDATION = "permanent_validation"
    PERMANENT_NOT_FOUND = "permanent_not_found"
    PERMANENT_CONFLICT = "permanent_conflict"

    # --- Authorization errors ---
    AUTHORIZATION = "authorization"
    VALIDATION = "validation"
    INVALID_ARGUMENT = "invalid_argument"
    UNSUPPORTED_OPERATION = "unsupported_operation"

    # --- Workflow errors ---
    WORKFLOW_DEADLOCK = "workflow_deadlock"
    WORKFLOW_CANCELED = "workflow_canceled"


# ============================================================================
# Error Classification Helper
# ============================================================================


def _category_from_llm_exception(error: Exception) -> ErrorCategory | None:
    """Extract ErrorCategory from LLMError subclasses.

    Returns None if the exception is not an LLMError or has no category mapping.
    """
    try:
        from polaris.kernelone.llm.exceptions import (
            CircuitBreakerOpenError,
            ConfigMigrationError,
            ConfigurationError,
            ConfigValidationError,
            JSONParseError,
            LLMError,
            LLMTimeoutError,
            NetworkError,
            ProviderError,
            RateLimitError,
            ResponseParseError,
            ToolParseError,
        )

        if isinstance(error, LLMTimeoutError):
            return ErrorCategory.TIMEOUT
        if isinstance(error, RateLimitError):
            return ErrorCategory.RATE_LIMIT
        if isinstance(error, (NetworkError, CircuitBreakerOpenError)):
            return ErrorCategory.NETWORK_ERROR
        if isinstance(error, (ConfigurationError, ConfigMigrationError, ConfigValidationError)):
            return ErrorCategory.CONFIG_ERROR
        if isinstance(error, (JSONParseError, ResponseParseError, ToolParseError)):
            return ErrorCategory.JSON_PARSE
        if isinstance(error, ProviderError):
            return ErrorCategory.PROVIDER_ERROR
        if isinstance(error, LLMError):
            return ErrorCategory.UNKNOWN
    except ImportError:
        pass
    return None


def classify_error(error: Exception) -> ErrorCategory:
    """Canonical error classifier for the KernelOne LLM subsystem.

    Single source of truth used by both ``executor`` and ``resilience``.
    First checks if the error is an LLMError subclass, then falls back
    to keyword-based classification.

    Args:
        error: The exception to classify.

    Returns:
        The appropriate ErrorCategory for the exception.
    """
    category = _category_from_llm_exception(error)
    if category is not None:
        return category

    error_str = str(error).lower()

    if "timeout" in error_str or "timed out" in error_str:
        return ErrorCategory.TIMEOUT
    if "rate limit" in error_str or "429" in error_str or "too many requests" in error_str:
        return ErrorCategory.RATE_LIMIT
    if "connection" in error_str or "network" in error_str:
        return ErrorCategory.NETWORK_ERROR
    if "config" in error_str or "configuration" in error_str:
        return ErrorCategory.CONFIG_ERROR
    if "json" in error_str or "parse" in error_str:
        return ErrorCategory.JSON_PARSE
    if "invalid" in error_str or "response" in error_str:
        return ErrorCategory.INVALID_RESPONSE

    return ErrorCategory.UNKNOWN


# ============================================================================
# Root Base Exception
# ============================================================================


class KernelOneError(Exception):
    """Base exception for all KernelOne and Cell errors.

    This is the single root exception for all KernelOne subsystems and Cells,
    providing a unified error hierarchy for catching and handling.

    Intent Separation (P1-014):
        此异常与 Kernel Cell 服务层异常（polaris.cells.roles.kernel.internal.services.contracts.KernelError）
        意图分离：
        - KernelOneError: KernelOne 运行时基础层异常（配置、事件、审计、基础设施）
        - KernelError: Kernel Cell 服务层业务异常（LLM调用、工具执行、策略控制）
        两者处于不同抽象层次，不存在继承关系。

    Attributes:
        message: Human-readable error description.
        code: Machine-readable error code (e.g., "CONFIG_LOAD_FAILED").
        cause: The original exception that triggered this error, if any.
        details: Additional context for debugging and classification.
        retryable: Whether the error is safe to retry without changes.

    Example:
        try:
            await some_kernel_operation()
        except KernelOneError as e:
            logger.error(f"KernelOne error [{e.code}]: {e.message}")
            if e.retryable:
                await retry_operation()
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "KERNEL_ERROR",
        cause: Exception | None = None,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.__cause__ = cause
        self._message = message
        self.details = details or {}
        self.retryable = retryable

    def __str__(self) -> str:
        return self._message

    def to_dict(self) -> dict[str, Any]:
        """Serialize exception to a JSON-compatible dictionary."""
        result: dict[str, Any] = {
            "type": self.__class__.__name__,
            "code": self.code,
            "message": self._message,
            "retryable": self.retryable,
        }
        if self.__cause__ is not None:
            result["cause"] = {
                "type": type(self.__cause__).__name__,
                "message": str(self.__cause__),
            }
        if self.details:
            result["details"] = self.details
        return result


# ============================================================================
# LLM Errors (Base for LLM-specific exceptions)
# ============================================================================


class LLMError(KernelOneError):
    """Base exception for all LLM module errors.

    Inherits from KernelOneError to provide unified exception hierarchy (P0-NEW-004 fix).
    This enables catching all KernelOne errors with:
        except KernelOneError:  # catches LLMError too!

    This is the base for LLM-specific parse errors (ToolParseError, ResponseParseError, JSONParseError).
    Subclasses should define more specific error codes.

    Attributes:
        message: Human-readable error description.
        cause: The original exception that triggered this error, if any.
        retryable: Whether the error is safe to retry without changes.
        details: Additional context for debugging and classification.
    """

    def __init__(
        self,
        message: str,
        *,
        cause: Exception | None = None,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code="LLM_ERROR", cause=cause, details=details, retryable=retryable)

    def to_dict(self) -> dict[str, Any]:
        """Serialize exception to a JSON-compatible dictionary."""
        return super().to_dict()


# ============================================================================
# Configuration Errors (Non-retryable)
# ============================================================================


class ConfigurationError(KernelOneError):
    """Configuration-related errors.

    Raised when configuration is invalid, missing, or cannot be loaded.
    These errors are typically non-retryable without fixing the configuration.

    Attributes:
        field: The configuration field that is invalid.
        config_path: Path to the configuration file, if applicable.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "CONFIG_ERROR",
        field: str = "",
        config_path: str = "",
        **kwargs,
    ) -> None:
        kwargs.setdefault("retryable", False)
        super().__init__(message, code=code, **kwargs)
        self.field = field
        self.config_path = config_path
        if field:
            self.details["field"] = field
        if config_path:
            self.details["config_path"] = config_path


class ConfigLoadError(ConfigurationError):
    """Configuration loading failed.

    Raised when configuration file cannot be read or parsed.
    """

    def __init__(
        self,
        message: str,
        *,
        config_path: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="CONFIG_LOAD_ERROR",
            config_path=config_path,
            **kwargs,
        )


class ConfigValidationError(ConfigurationError):
    """Configuration validation failed.

    Raised when configuration values fail validation checks.

    Attributes:
        validation_errors: List of specific validation error messages.
    """

    def __init__(
        self,
        message: str,
        *,
        validation_errors: list[str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(message, code="CONFIG_VALIDATION_ERROR", **kwargs)
        self.validation_errors = validation_errors or []
        if self.validation_errors:
            self.details["validation_errors"] = self.validation_errors


class ConfigMigrationError(ConfigurationError):
    """Configuration migration failed.

    Raised when upgrading or migrating configuration format fails.
    """

    def __init__(
        self,
        message: str,
        *,
        from_version: str = "",
        to_version: str = "",
        **kwargs,
    ) -> None:
        super().__init__(message, code="CONFIG_MIGRATION_ERROR", **kwargs)
        if from_version:
            self.details["from_version"] = from_version
        if to_version:
            self.details["to_version"] = to_version


# ============================================================================
# Validation Errors (Non-retryable)
# ============================================================================


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


# ============================================================================
# Execution Errors (Variable retryability)
# ============================================================================


class ExecutionError(KernelOneError):
    """Execution-related errors.

    Raised when an operation fails during execution.
    Retryability depends on the specific error type.

    Attributes:
        operation: The operation that failed.
        tool_name: The tool that was being executed, if applicable.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "EXECUTION_ERROR",
        operation: str = "",
        tool_name: str = "",
        **kwargs,
    ) -> None:
        super().__init__(message, code=code, **kwargs)
        self.operation = operation
        self.tool_name = tool_name
        if operation:
            self.details["operation"] = operation
        if tool_name:
            self.details["tool_name"] = tool_name


class ToolExecutionError(ExecutionError):
    """Tool execution failed.

    Raised when a tool call fails during execution.
    This can include file system errors, subprocess failures, etc.

    Attributes:
        exit_code: Exit code if the error came from a subprocess.
    """

    def __init__(
        self,
        message: str,
        *,
        tool_name: str = "",
        exit_code: int | None = None,
        retryable: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="TOOL_EXECUTION_ERROR",
            tool_name=tool_name,
            retryable=retryable,
            **kwargs,
        )
        self.exit_code = exit_code
        if exit_code is not None:
            self.details["exit_code"] = exit_code


class ShellDisallowedError(ExecutionError):
    """Shell command execution is disallowed.

    Raised when a shell command is attempted but policy prohibits it.
    """

    def __init__(
        self,
        message: str = "shell=True is not allowed in KernelOne",
        *,
        command: str = "",
        reason: str = "shell_execution_policy_violation",
        **kwargs,
    ) -> None:
        kwargs.setdefault("retryable", False)
        super().__init__(
            message,
            code="SHELL_DISALLOWED_ERROR",
            operation="shell_command",
            **kwargs,
        )
        if command:
            self.details["command"] = command
        self.details["reason"] = reason


class BudgetExceededError(ExecutionError):
    """Context budget exceeded.

    Raised when an operation would exceed available context budget.

    Attributes:
        limit: The hard limit that was exceeded.
        requested: The amount that was requested.
        current: Current usage before the operation.
    """

    def __init__(
        self,
        message: str,
        *,
        limit: int = 2000,
        requested: int = 0,
        current: int = 0,
        suggestion: str | None = None,
        **kwargs,
    ) -> None:
        kwargs.setdefault("retryable", True)
        super().__init__(
            message,
            code="BUDGET_EXCEEDED_ERROR",
            operation="context_budget_check",
            **kwargs,
        )
        self.limit = limit
        self.requested = requested
        self.current = current
        self.suggestion = suggestion
        self.details.update(
            {
                "limit": limit,
                "requested": requested,
                "current": current,
            }
        )
        if suggestion:
            self.details["suggestion"] = suggestion


# ============================================================================
# Resource Errors (Variable retryability)
# ============================================================================


class ResourceError(KernelOneError):
    """Resource-related errors.

    Raised when a resource is unavailable, exhausted, or cannot be accessed.

    Attributes:
        resource_type: Type of the resource (file, database, network, etc.).
        resource_id: Identifier of the resource.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "RESOURCE_ERROR",
        resource_type: str = "",
        resource_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(message, code=code, **kwargs)
        self.resource_type = resource_type
        self.resource_id = resource_id
        if resource_type:
            self.details["resource_type"] = resource_type
        if resource_id:
            self.details["resource_id"] = resource_id


class FileNotFoundError(ResourceError):
    """File not found.

    Raised when a required file does not exist.
    Note: This shadows Python's built-in FileNotFoundError intentionally
    for unified error handling within KernelOne.
    """

    def __init__(
        self,
        message: str,
        *,
        file_path: str = "",
        **kwargs,
    ) -> None:
        kwargs.setdefault("retryable", False)
        super().__init__(
            message,
            code="FILE_NOT_FOUND_ERROR",
            resource_type="file",
            resource_id=file_path,
            **kwargs,
        )
        self.file_path = file_path


class StateNotFoundError(ResourceError):
    """State not found in persistence layer.

    Raised when attempting to load state that doesn't exist.
    """

    def __init__(
        self,
        message: str,
        *,
        state_key: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="STATE_NOT_FOUND_ERROR",
            resource_type="state",
            resource_id=state_key,
            **kwargs,
        )


class EvidenceNotFoundError(ResourceError):
    """Evidence not found in audit store.

    Raised when attempting to retrieve evidence that doesn't exist.
    """

    def __init__(
        self,
        message: str,
        *,
        evidence_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="EVIDENCE_NOT_FOUND_ERROR",
            resource_type="evidence",
            resource_id=evidence_id,
            **kwargs,
        )


class DatabaseError(ResourceError):
    """Database-related errors.

    Raised when database operations fail.

    Attributes:
        database_name: Name of the database.
        operation: The database operation that failed.
    """

    def __init__(
        self,
        message: str,
        *,
        database_name: str = "",
        operation: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="DATABASE_ERROR",
            resource_type="database",
            resource_id=database_name,
            **kwargs,
        )
        self.database_name = database_name
        self.operation = operation
        if operation:
            self.details["operation"] = operation


class DatabasePathError(DatabaseError):
    """Database path resolution failed."""

    def __init__(
        self,
        message: str,
        *,
        path: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="DATABASE_PATH_ERROR",
            operation="path_resolution",
            **kwargs,
        )
        if path:
            self.details["path"] = path


class DatabasePolicyError(DatabaseError):
    """Database path violates storage policy."""

    def __init__(
        self,
        message: str,
        *,
        policy: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="DATABASE_POLICY_ERROR",
            operation="policy_check",
            **kwargs,
        )
        if policy:
            self.details["policy"] = policy


class DatabaseConnectionError(DatabaseError):
    """Database connection failed."""

    def __init__(
        self,
        message: str,
        **kwargs,
    ) -> None:
        kwargs.setdefault("retryable", True)
        super().__init__(
            message,
            code="DATABASE_CONNECTION_ERROR",
            operation="connect",
            **kwargs,
        )


class DatabaseDriverNotAvailableError(DatabaseError):
    """Database driver is missing."""

    def __init__(
        self,
        message: str,
        *,
        driver_name: str = "",
        **kwargs,
    ) -> None:
        kwargs.setdefault("retryable", False)
        super().__init__(
            message,
            code="DATABASE_DRIVER_NOT_AVAILABLE_ERROR",
            operation="driver_check",
            **kwargs,
        )
        if driver_name:
            self.details["driver_name"] = driver_name


# ============================================================================
# Communication Errors (Typically retryable)
# ============================================================================


class CommunicationError(KernelOneError):
    """Communication-related errors.

    Raised when network or inter-process communication fails.

    Attributes:
        endpoint: The communication endpoint.
        protocol: The protocol used (http, websocket, grpc, etc.).
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "COMMUNICATION_ERROR",
        endpoint: str = "",
        protocol: str = "",
        **kwargs,
    ) -> None:
        kwargs.setdefault("retryable", True)
        super().__init__(message, code=code, **kwargs)
        self.endpoint = endpoint
        self.protocol = protocol
        if endpoint:
            self.details["endpoint"] = endpoint
        if protocol:
            self.details["protocol"] = protocol


class NetworkError(CommunicationError):
    """Network connectivity error.

    Raised when network requests fail due to connectivity issues.

    Attributes:
        url: The URL that was being accessed.
    """

    def __init__(
        self,
        message: str,
        *,
        url: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="NETWORK_ERROR",
            endpoint=url,
            protocol="http",
            **kwargs,
        )
        self.url = url


class WebSocketSendError(CommunicationError):
    """WebSocket send failed.

    Raised when sending a WebSocket message fails.
    """

    def __init__(
        self,
        message: str,
        *,
        session_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="WEBSOCKET_SEND_ERROR",
            protocol="websocket",
            **kwargs,
        )
        if session_id:
            self.details["session_id"] = session_id


class TimeoutError(CommunicationError):
    """Operation timed out.

    Note: This shadows Python's built-in TimeoutError intentionally
    for unified error handling within KernelOne.

    Attributes:
        timeout_seconds: The configured timeout that was exceeded.
        operation: What was being performed when timeout occurred.
    """

    def __init__(
        self,
        message: str,
        *,
        timeout_seconds: float | None = None,
        operation: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="TIMEOUT_ERROR",
            **kwargs,
        )
        self.timeout_seconds = timeout_seconds
        self.operation = operation
        if timeout_seconds is not None:
            self.details["timeout_seconds"] = timeout_seconds
        if operation:
            self.details["operation"] = operation


class RateLimitError(CommunicationError):
    """Rate limit exceeded.

    Raised when API rate limits are hit.

    Attributes:
        retry_after: Seconds to wait before retrying.
        limit_type: Type of limit hit (requests, tokens, etc.).
    """

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        limit_type: str = "requests",
        **kwargs,
    ) -> None:
        super().__init__(message, code="RATE_LIMIT_ERROR", **kwargs)
        self.retry_after = retry_after
        self.limit_type = limit_type
        if retry_after is not None:
            self.details["retry_after"] = retry_after
        self.details["limit_type"] = limit_type


class CircuitBreakerOpenError(CommunicationError):
    """Circuit breaker is open.

    Raised when a circuit breaker has tripped and is refusing requests.

    Attributes:
        circuit_name: Name of the circuit breaker that is open.
        retry_after: Suggested seconds to wait before retrying.
    """

    def __init__(
        self,
        message: str = "Circuit breaker is open",
        *,
        circuit_name: str | None = None,
        retry_after: float | None = None,
        **kwargs,
    ) -> None:
        # Build detailed message if circuit_name is provided
        if message == "Circuit breaker is open" and circuit_name:
            message = f"Circuit breaker '{circuit_name}' is open"
            if retry_after is not None:
                message += f", retry after {retry_after:.1f}s"
        super().__init__(message, code="CIRCUIT_BREAKER_OPEN_ERROR", **kwargs)
        self.circuit_name = circuit_name
        self.retry_after = retry_after
        if circuit_name:
            self.details["circuit_name"] = circuit_name
        if retry_after is not None:
            self.details["retry_after"] = retry_after


class AuthenticationError(CommunicationError):
    """Authentication failed.

    Raised when API authentication fails.

    Attributes:
        provider: The provider that failed authentication.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        **kwargs,
    ) -> None:
        kwargs.setdefault("retryable", False)
        super().__init__(
            message,
            code="AUTHENTICATION_ERROR",
            **kwargs,
        )
        self.provider = provider
        if provider:
            self.details["provider"] = provider


# ============================================================================
# State Machine Errors
# ============================================================================


class StateError(KernelOneError):
    """State machine related errors.

    Raised when state transitions fail or invalid states are encountered.

    Attributes:
        current_state: The current state.
        target_state: The attempted target state.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "STATE_ERROR",
        current_state: str = "",
        target_state: str = "",
        **kwargs,
    ) -> None:
        kwargs.setdefault("retryable", False)
        super().__init__(message, code=code, **kwargs)
        self.current_state = current_state
        self.target_state = target_state
        if current_state:
            self.details["current_state"] = current_state
        if target_state:
            self.details["target_state"] = target_state


class InvalidStateTransitionError(StateError):
    """Invalid state transition attempted."""

    def __init__(
        self,
        message: str,
        *,
        current_state: str = "",
        target_state: str = "",
        allowed_transitions: list[str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="INVALID_STATE_TRANSITION_ERROR",
            current_state=current_state,
            target_state=target_state,
            **kwargs,
        )
        self.allowed_transitions = allowed_transitions or []
        if self.allowed_transitions:
            self.details["allowed_transitions"] = self.allowed_transitions


class InvalidTaskStateTransitionError(StateError):
    """Invalid task state transition."""

    def __init__(
        self,
        message: str,
        *,
        task_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="INVALID_TASK_STATE_TRANSITION_ERROR",
            **kwargs,
        )
        if task_id:
            self.details["task_id"] = task_id


class WorkerStateError(StateError):
    """Worker state error."""

    def __init__(
        self,
        message: str = "Invalid worker state transition",
        *,
        worker_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="WORKER_STATE_ERROR",
            **kwargs,
        )
        if worker_id:
            self.details["worker_id"] = worker_id


class TaskStateError(StateError):
    """Task state error."""

    def __init__(
        self,
        message: str = "Invalid task state transition",
        *,
        task_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="TASK_STATE_ERROR",
            **kwargs,
        )
        if task_id:
            self.details["task_id"] = task_id


class InvalidToolStateTransitionError(StateError):
    """Invalid tool state transition."""

    def __init__(
        self,
        message: str,
        *,
        tool_name: str = "",
        current_status: Any = None,
        target_status: Any = None,
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="INVALID_TOOL_STATE_TRANSITION_ERROR",
            **kwargs,
        )
        if tool_name:
            self.details["tool_name"] = tool_name
        if current_status is not None:
            self.current_status = current_status
        if target_status is not None:
            self.target_status = target_status


# ============================================================================
# Cell Errors (Bridge to Domain)
# ============================================================================


class CellError(KernelOneError):
    """Cell-level errors.

    Base class for all Cell-specific errors.
    Each Cell can define its own error subclass that inherits from this.

    Attributes:
        cell_name: Name of the Cell that raised the error.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "CELL_ERROR",
        cell_name: str = "",
        **kwargs,
    ) -> None:
        super().__init__(message, code=code, **kwargs)
        self.cell_name = cell_name
        if cell_name:
            self.details["cell_name"] = cell_name


# ============================================================================
# Audit Errors
# ============================================================================


class AuditError(KernelOneError):
    """Audit-related errors.

    Base class for audit subsystem errors.

    Attributes:
        audit_id: Identifier for the audit context.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "AUDIT_ERROR",
        audit_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(message, code=code, **kwargs)
        if audit_id:
            self.details["audit_id"] = audit_id


class KernelAuditWriteError(AuditError):
    """Kernel audit write failed."""

    def __init__(
        self,
        message: str,
        *,
        event_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="KERNEL_AUDIT_WRITE_ERROR",
            **kwargs,
        )
        if event_id:
            self.details["event_id"] = event_id


class AuditFieldError(AuditError):
    """Audit field type error."""

    def __init__(
        self,
        message: str,
        *,
        field_path: str = "",
        expected_type: str = "",
        actual_type: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="AUDIT_FIELD_ERROR",
            **kwargs,
        )
        if field_path:
            self.details["field_path"] = field_path
        if expected_type:
            self.details["expected_type"] = expected_type
        if actual_type:
            self.details["actual_type"] = actual_type


# ============================================================================
# Event Errors
# ============================================================================


class EventError(KernelOneError):
    """Event-related errors.

    Base class for event system errors.

    Attributes:
        event_name: Name of the event.
        event_id: Identifier of the event.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "EVENT_ERROR",
        event_name: str = "",
        event_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(message, code=code, **kwargs)
        if event_name:
            self.details["event_name"] = event_name
        if event_id:
            self.details["event_id"] = event_id


class EventPublishError(EventError):
    """Event publishing failed."""

    def __init__(
        self,
        message: str,
        *,
        event_name: str = "",
        failed_side: str = "",
        left_error: Exception | None = None,
        right_error: Exception | None = None,
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="EVENT_PUBLISH_ERROR",
            event_name=event_name,
            **kwargs,
        )
        self.failed_side = failed_side
        self.left_error = left_error
        self.right_error = right_error
        self.details.update(
            {
                "failed_side": failed_side,
                "registry_failed": left_error is not None,
                "message_bus_failed": right_error is not None,
            }
        )


class EventSourcingError(EventError):
    """Event sourcing error."""

    def __init__(
        self,
        message: str,
        *,
        stream: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="EVENT_SOURCING_ERROR",
            **kwargs,
        )
        if stream:
            self.details["stream"] = stream


# ============================================================================
# Bootstrap Errors
# ============================================================================


class BootstrapError(ConfigurationError):
    """Bootstrap initialization failed."""

    def __init__(
        self,
        message: str,
        *,
        phase: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="BOOTSTRAP_ERROR",
            **kwargs,
        )
        if phase:
            self.details["phase"] = phase


class BackendBootstrapError(BootstrapError):
    """Backend bootstrap failed."""

    pass


# ============================================================================
# Chaos Engineering Errors (Benchmark)
# ============================================================================


class ChaosError(KernelOneError):
    """Chaos engineering error.

    Base class for chaos testing errors.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "CHAOS_ERROR",
        chaos_type: str = "",
        **kwargs,
    ) -> None:
        super().__init__(message, code=code, **kwargs)
        if chaos_type:
            self.details["chaos_type"] = chaos_type


class ChaosInjectionError(ChaosError):
    """Chaos injection failed."""

    def __init__(
        self,
        message: str,
        *,
        injection_type: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="CHAOS_INJECTION_ERROR",
            chaos_type=injection_type,
            **kwargs,
        )


class ChaosSkippedError(ChaosError):
    """Chaos injection was skipped."""

    def __init__(
        self,
        message: str,
        *,
        reason: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="CHAOS_SKIPPED_ERROR",
            **kwargs,
        )
        if reason:
            self.details["reason"] = reason


class RateLimitExceededError(ChaosError):
    """Chaos rate limit exceeded."""

    def __init__(
        self,
        message: str,
        *,
        limit: int = 0,
        current: int = 0,
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="CHAOS_RATE_LIMIT_EXCEEDED_ERROR",
            chaos_type="rate_limiter",
            **kwargs,
        )
        self.limit = limit
        self.current = current
        if limit:
            self.details["limit"] = limit
        if current:
            self.details["current"] = current


class NetworkChaosError(ChaosError):
    """Network chaos error."""

    def __init__(
        self,
        message: str,
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="NETWORK_CHAOS_ERROR",
            chaos_type="network",
            **kwargs,
        )


class DeadlockDetectedError(ChaosError):
    """Deadlock detected."""

    def __init__(
        self,
        message: str,
        **kwargs,
    ) -> None:
        kwargs.setdefault("retryable", False)
        super().__init__(
            message,
            code="DEADLOCK_DETECTED_ERROR",
            chaos_type="deadlock",
            **kwargs,
        )


class LockTimeoutError(ChaosError):
    """Lock acquisition timeout."""

    def __init__(
        self,
        message: str,
        *,
        lock_name: str = "",
        timeout_seconds: float = 0,
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="LOCK_TIMEOUT_ERROR",
            chaos_type="lock",
            **kwargs,
        )
        if lock_name:
            self.details["lock_name"] = lock_name
        if timeout_seconds:
            self.details["timeout_seconds"] = timeout_seconds


class ChaosCircuitBreakerError(ChaosError):
    """Chaos circuit breaker error."""

    def __init__(
        self,
        message: str,
        *,
        circuit_name: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="CHAOS_CIRCUIT_BREAKER_ERROR",
            chaos_type="circuit_breaker",
            **kwargs,
        )
        if circuit_name:
            self.details["circuit_name"] = circuit_name


# ============================================================================
# Retry/Resilience Errors
# ============================================================================


class RetryableError(KernelOneError):
    """Error that can be safely retried."""

    def __init__(
        self,
        message: str,
        **kwargs,
    ) -> None:
        kwargs["retryable"] = True
        super().__init__(message, code="RETRYABLE_ERROR", **kwargs)


class NonRetryableError(KernelOneError):
    """Error that should not be retried."""

    def __init__(
        self,
        message: str,
        **kwargs,
    ) -> None:
        kwargs["retryable"] = False
        super().__init__(message, code="NON_RETRYABLE_ERROR", **kwargs)


# ============================================================================
# Shadow Replay Errors
# ============================================================================


class ShadowReplayError(KernelOneError):
    """Shadow replay error."""

    def __init__(
        self,
        message: str,
        *,
        replay_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="SHADOW_REPLAY_ERROR",
            **kwargs,
        )
        if replay_id:
            self.details["replay_id"] = replay_id


# ============================================================================
# Context Errors
# ============================================================================


class ContextError(KernelOneError):
    """Context-related errors."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "CONTEXT_ERROR",
        **kwargs,
    ) -> None:
        super().__init__(message, code=code, **kwargs)


class ContextOverflowError(ContextError):
    """Context overflow error."""

    def __init__(
        self,
        message: str,
        *,
        max_tokens: int = 0,
        current_tokens: int = 0,
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="CONTEXT_OVERFLOW_ERROR",
            **kwargs,
        )
        if max_tokens:
            self.details["max_tokens"] = max_tokens
        if current_tokens:
            self.details["current_tokens"] = current_tokens


class ContextCompilationError(ContextError):
    """Context compilation error."""

    def __init__(
        self,
        message: str,
        *,
        compilation_step: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="CONTEXT_COMPILATION_ERROR",
            **kwargs,
        )
        if compilation_step:
            self.details["compilation_step"] = compilation_step


# ============================================================================
# Reserved Key Violation Error
# ============================================================================


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


# ============================================================================
# Turn Decision Error
# ============================================================================


class TurnDecisionError(KernelOneError):
    """Turn decision error."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "TURN_DECISION_ERROR",
        turn_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(message, code=code, **kwargs)
        if turn_id:
            self.details["turn_id"] = turn_id


class TurnDecisionDecodeError(TurnDecisionError):
    """Turn decision decode error."""

    def __init__(
        self,
        message: str,
        *,
        raw_response: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="TURN_DECISION_DECODE_ERROR",
            **kwargs,
        )
        if raw_response:
            self.details["raw_response"] = raw_response[:500]  # Limit size


# ============================================================================
# Tool Authorization Error
# ============================================================================


class ToolAuthorizationError(ExecutionError):
    """Tool authorization failed."""

    def __init__(
        self,
        message: str,
        *,
        tool_name: str = "",
        role: str = "",
        reason: str = "",
        **kwargs,
    ) -> None:
        kwargs.setdefault("retryable", False)
        super().__init__(
            message,
            code="TOOL_AUTHORIZATION_ERROR",
            tool_name=tool_name,
            **kwargs,
        )
        if role:
            self.details["role"] = role
        if reason:
            self.details["reason"] = reason


# ============================================================================
# Testing Infrastructure Error
# ============================================================================


class TestingInfrastructureError(KernelOneError):
    """Testing infrastructure error."""

    def __init__(
        self,
        message: str,
        *,
        infrastructure_component: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="TESTING_INFRASTRUCTURE_ERROR",
            **kwargs,
        )
        if infrastructure_component:
            self.details["infrastructure_component"] = infrastructure_component


# ============================================================================
# Tool Error (Legacy Compatibility)
# ============================================================================


class ToolError(ToolExecutionError):
    """Legacy tool error (compatibility alias).

    This class is provided for backward compatibility.
    Prefer using ToolExecutionError for new code.
    """

    pass


# ============================================================================
# Path Security Error
# ============================================================================


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


# ============================================================================
# Permission Service Error
# ============================================================================


class PermissionError(KernelOneError):
    """Permission-related errors."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "PERMISSION_ERROR",
        permission_name: str = "",
        **kwargs,
    ) -> None:
        kwargs.setdefault("retryable", False)
        super().__init__(message, code=code, **kwargs)
        if permission_name:
            self.details["permission_name"] = permission_name


class PermissionServiceError(PermissionError):
    """Permission service error."""

    def __init__(
        self,
        message: str,
        *,
        service_name: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="PERMISSION_SERVICE_ERROR",
            **kwargs,
        )
        if service_name:
            self.details["service_name"] = service_name


# ============================================================================
# Workflow Runtime Errors
# ============================================================================


class WorkflowRuntimeError(KernelOneError):
    """Workflow runtime error."""

    def __init__(
        self,
        message: str,
        *,
        workflow_id: str = "",
        code: str = "WORKFLOW_RUNTIME_ERROR",
        **kwargs,
    ) -> None:
        super().__init__(message, code=code, **kwargs)
        if workflow_id:
            self.details["workflow_id"] = workflow_id


class WorkflowUnavailableError(WorkflowRuntimeError):
    """Workflow unavailable error."""

    def __init__(
        self,
        message: str,
        **kwargs,
    ) -> None:
        kwargs.setdefault("retryable", True)
        super().__init__(
            message,
            code="WORKFLOW_UNAVAILABLE_ERROR",
            **kwargs,
        )


class ProcessRunnerError(WorkflowRuntimeError):
    """Process runner error."""

    def __init__(
        self,
        message: str,
        *,
        process_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="PROCESS_RUNNER_ERROR",
            **kwargs,
        )
        if process_id:
            self.details["process_id"] = process_id


class LauncherError(WorkflowRuntimeError):
    """Launcher error."""

    def __init__(
        self,
        message: str,
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="LAUNCHER_ERROR",
            **kwargs,
        )


class OrchestrationError(WorkflowRuntimeError):
    """Orchestration error."""

    def __init__(
        self,
        message: str,
        *,
        orchestration_type: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="ORCHESTRATION_ERROR",
            **kwargs,
        )
        if orchestration_type:
            self.details["orchestration_type"] = orchestration_type


# ============================================================================
# Role Data Store Error
# ============================================================================


class RoleDataStoreError(ResourceError):
    """Role data store error."""

    def __init__(
        self,
        message: str,
        *,
        role_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="ROLE_DATA_STORE_ERROR",
            resource_type="role_data",
            resource_id=role_id,
            **kwargs,
        )


# ============================================================================
# Vision Service Error
# ============================================================================


class VisionServiceError(KernelOneError):
    """Vision service error."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "VISION_SERVICE_ERROR",
        **kwargs,
    ) -> None:
        super().__init__(message, code=code, **kwargs)


class VisionNotAvailableError(VisionServiceError):
    """Vision not available error."""

    def __init__(
        self,
        message: str,
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="VISION_NOT_AVAILABLE_ERROR",
            **kwargs,
        )


class InferenceEngineNotConfiguredError(VisionServiceError):
    """Inference engine not configured error."""

    def __init__(
        self,
        message: str,
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="INFERENCE_ENGINE_NOT_CONFIGURED_ERROR",
            **kwargs,
        )


# ============================================================================
# Code Generation Error
# ============================================================================


class CodeGenerationError(ExecutionError):
    """Code generation error."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "CODE_GENERATION_ERROR",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code=code,
            operation="code_generation",
            **kwargs,
        )


class CodeGenerationPolicyViolationError(CodeGenerationError):
    """Code generation policy violation error."""

    def __init__(
        self,
        message: str,
        *,
        policy_rule: str = "",
        **kwargs,
    ) -> None:
        kwargs.setdefault("retryable", False)
        super().__init__(
            message,
            code="CODE_GENERATION_POLICY_VIOLATION_ERROR",
            **kwargs,
        )
        if policy_rule:
            self.details["policy_rule"] = policy_rule


# ============================================================================
# Exports
# ============================================================================

__all__ = [
    # Root
    "ErrorCategory",
    "KernelOneError",
    # Configuration
    "ConfigurationError",
    "ConfigLoadError",
    "ConfigValidationError",
    "ConfigMigrationError",
    # Validation
    "ValidationError",
    "PathTraversalError",
    "WorkflowContractError",
    "ReservedKeyViolationError",
    "ConstitutionViolationError",
    "PathSecurityError",
    # Execution
    "ExecutionError",
    "ToolExecutionError",
    "ShellDisallowedError",
    "BudgetExceededError",
    "ToolAuthorizationError",
    "ToolError",  # Legacy compatibility
    "CodeGenerationError",
    "CodeGenerationPolicyViolationError",
    # Resource
    "ResourceError",
    "FileNotFoundError",
    "StateNotFoundError",
    "EvidenceNotFoundError",
    "DatabaseError",
    "DatabasePathError",
    "DatabasePolicyError",
    "DatabaseConnectionError",
    "DatabaseDriverNotAvailableError",
    "RoleDataStoreError",
    # Communication
    "CommunicationError",
    "NetworkError",
    "WebSocketSendError",
    "TimeoutError",
    "RateLimitError",
    "CircuitBreakerOpenError",
    "AuthenticationError",
    # State
    "StateError",
    "InvalidStateTransitionError",
    "InvalidTaskStateTransitionError",
    "WorkerStateError",
    "TaskStateError",
    "InvalidToolStateTransitionError",
    # Cell
    "CellError",
    # Audit
    "AuditError",
    "KernelAuditWriteError",
    "AuditFieldError",
    # Event
    "EventError",
    "EventPublishError",
    "EventSourcingError",
    # Bootstrap
    "BootstrapError",
    "BackendBootstrapError",
    # Chaos
    "ChaosError",
    "ChaosInjectionError",
    "ChaosSkippedError",
    "RateLimitExceededError",
    "NetworkChaosError",
    "DeadlockDetectedError",
    "LockTimeoutError",
    "ChaosCircuitBreakerError",
    # Retry/Resilience
    "RetryableError",
    "NonRetryableError",
    # Shadow Replay
    "ShadowReplayError",
    # Context
    "ContextError",
    "ContextOverflowError",
    "ContextCompilationError",
    # Turn Decision
    "TurnDecisionError",
    "TurnDecisionDecodeError",
    # Testing
    "TestingInfrastructureError",
    # Permission
    "PermissionError",
    "PermissionServiceError",
    # Workflow Runtime
    "WorkflowRuntimeError",
    "WorkflowUnavailableError",
    "ProcessRunnerError",
    "LauncherError",
    "OrchestrationError",
    # Vision Service
    "VisionServiceError",
    "VisionNotAvailableError",
    "InferenceEngineNotConfiguredError",
]
