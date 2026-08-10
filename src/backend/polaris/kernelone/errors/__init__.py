"""Polaris KernelOne exception hierarchy.

This package provides the unified exception hierarchy for all KernelOne and Cell errors.
It serves as the single source of truth for error classification and handling.

Design Principles:
- All KernelOne exceptions inherit from KernelOneError base class
- Cell-level exceptions inherit from CellError (which inherits from KernelOneError)
- Domain-specific exceptions inherit from appropriate category base classes
- Each exception carries structured metadata for error classification
- Exceptions are designed for easy identification and handling

Migration Target:
- This package replaces scattered error definitions across the codebase
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

This package is the lossless successor of the former ``errors`` module.
It re-exports every previously-public symbol from the same import path so
that ``import polaris.kernelone.errors`` and ``from polaris.kernelone.errors import X``
keep resolving identically for all external importers.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from polaris.kernelone.errors._audit_event import (
    AuditError,
    AuditFieldError,
    CellError,
    EventError,
    EventPublishError,
    EventSourcingError,
    KernelAuditWriteError,
)
from polaris.kernelone.errors._base import (
    ErrorCategory,
    KernelOneError,
    LLMError,
    _category_from_llm_exception,
    classify_error,
)
from polaris.kernelone.errors._chaos import (
    ChaosCircuitBreakerError,
    ChaosError,
    ChaosInjectionError,
    ChaosSkippedError,
    DeadlockDetectedError,
    LockTimeoutError,
    NetworkChaosError,
    NonRetryableError,
    RateLimitExceededError,
    RetryableError,
    ShadowReplayError,
)
from polaris.kernelone.errors._communication import (
    AuthenticationError,
    CircuitBreakerOpenError,
    CommunicationError,
    NetworkError,
    RateLimitError,
    TimeoutError,
    WebSocketSendError,
)
from polaris.kernelone.errors._config import (
    BackendBootstrapError,
    BootstrapError,
    ConfigLoadError,
    ConfigMigrationError,
    ConfigurationError,
    ConfigValidationError,
)
from polaris.kernelone.errors._context import (
    ContextCompilationError,
    ContextError,
    ContextOverflowError,
    TurnDecisionDecodeError,
    TurnDecisionError,
)
from polaris.kernelone.errors._execution import (
    BudgetExceededError,
    CodeGenerationError,
    CodeGenerationPolicyViolationError,
    ExecutionError,
    ShellDisallowedError,
    ToolAuthorizationError,
    ToolError,
    ToolExecutionError,
)
from polaris.kernelone.errors._permission import (
    PermissionError,
    PermissionServiceError,
    TestingInfrastructureError,
)
from polaris.kernelone.errors._resource import (
    DatabaseConnectionError,
    DatabaseDriverNotAvailableError,
    DatabaseError,
    DatabasePathError,
    DatabasePolicyError,
    EvidenceNotFoundError,
    FileNotFoundError,
    ResourceError,
    RoleDataStoreError,
    StateNotFoundError,
)
from polaris.kernelone.errors._state import (
    InvalidStateTransitionError,
    InvalidTaskStateTransitionError,
    InvalidToolStateTransitionError,
    StateError,
    TaskStateError,
    WorkerStateError,
)
from polaris.kernelone.errors._validation import (
    ConstitutionViolationError,
    PathSecurityError,
    PathTraversalError,
    ReservedKeyViolationError,
    ValidationError,
    WorkflowContractError,
)
from polaris.kernelone.errors._workflow import (
    InferenceEngineNotConfiguredError,
    LauncherError,
    OrchestrationError,
    ProcessRunnerError,
    VisionNotAvailableError,
    VisionServiceError,
    WorkflowRuntimeError,
    WorkflowUnavailableError,
)

# Preserve module-level logger identity (name == polaris.kernelone.errors).
logger = logging.getLogger(__name__)

__all__ = [
    # Audit
    "AuditError",
    "AuditFieldError",
    "AuthenticationError",
    "BackendBootstrapError",
    # Bootstrap
    "BootstrapError",
    "BudgetExceededError",
    # Cell
    "CellError",
    "ChaosCircuitBreakerError",
    # Chaos
    "ChaosError",
    "ChaosInjectionError",
    "ChaosSkippedError",
    "CircuitBreakerOpenError",
    "CodeGenerationError",
    "CodeGenerationPolicyViolationError",
    # Communication
    "CommunicationError",
    "ConfigLoadError",
    "ConfigMigrationError",
    "ConfigValidationError",
    # Configuration
    "ConfigurationError",
    "ConstitutionViolationError",
    "ContextCompilationError",
    # Context
    "ContextError",
    "ContextOverflowError",
    "DatabaseConnectionError",
    "DatabaseDriverNotAvailableError",
    "DatabaseError",
    "DatabasePathError",
    "DatabasePolicyError",
    "DeadlockDetectedError",
    # Root
    "ErrorCategory",
    # Event
    "EventError",
    "EventPublishError",
    "EventSourcingError",
    "EvidenceNotFoundError",
    # Execution
    "ExecutionError",
    "FileNotFoundError",
    "InferenceEngineNotConfiguredError",
    "InvalidStateTransitionError",
    "InvalidTaskStateTransitionError",
    "InvalidToolStateTransitionError",
    "KernelAuditWriteError",
    "KernelOneError",
    "LauncherError",
    "LockTimeoutError",
    "NetworkChaosError",
    "NetworkError",
    "NonRetryableError",
    "OrchestrationError",
    "PathSecurityError",
    "PathTraversalError",
    # Permission
    "PermissionError",
    "PermissionServiceError",
    "ProcessRunnerError",
    "RateLimitError",
    "RateLimitExceededError",
    "ReservedKeyViolationError",
    # Resource
    "ResourceError",
    # Retry/Resilience
    "RetryableError",
    "RoleDataStoreError",
    # Shadow Replay
    "ShadowReplayError",
    "ShellDisallowedError",
    # State
    "StateError",
    "StateNotFoundError",
    "TaskStateError",
    # Testing
    "TestingInfrastructureError",
    "TimeoutError",
    "ToolAuthorizationError",
    "ToolError",  # Legacy compatibility
    "ToolExecutionError",
    "TurnDecisionDecodeError",
    # Turn Decision
    "TurnDecisionError",
    # Validation
    "ValidationError",
    "VisionNotAvailableError",
    # Vision Service
    "VisionServiceError",
    "WebSocketSendError",
    "WorkerStateError",
    "WorkflowContractError",
    # Workflow Runtime
    "WorkflowRuntimeError",
    "WorkflowUnavailableError",
]
