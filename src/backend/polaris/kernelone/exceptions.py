"""Unified exception namespace for KernelOne runtime.

This module serves as a unified namespace for all KernelOne exceptions,
aggregating exceptions from various submodules into a single import location.

Design Principles:
- All KernelOne exceptions inherit from KernelOneError (defined in errors.py)
- This module aggregates exceptions, not defines them
- LLM-related exceptions inherit from LLMError (in llm/exceptions.py)
- Each exception carries structured metadata for error classification

Usage:
    from polaris.kernelone.exceptions import (
        KernelOneError,
        EventPublishError,
        LLMError,
    )

    try:
        await adapter.emit_to_both(event)
    except EventPublishError as e:
        # Handle partial failure
        if e.left_error:
            logger.error(f"Registry emit failed: {e.left_error}")
        if e.right_error:
            logger.error(f"MessageBus emit failed: {e.right_error}")

Migration Notes:
- KernelError is now KernelOneError for clarity
- All new exceptions should inherit from appropriate category in errors.py
- Backward compatibility aliases are maintained for smooth migration
"""

from __future__ import annotations

# ============================================================================
# Core Exception Hierarchy (from errors.py)
# ============================================================================
from polaris.kernelone.errors import (
    # Audit errors
    AuditError,
    AuditFieldError,
    AuthenticationError,
    BackendBootstrapError,
    # Bootstrap errors
    BootstrapError,
    BudgetExceededError,
    # Cell errors
    CellError,
    ChaosCircuitBreakerError,
    # Chaos errors
    ChaosError,
    ChaosInjectionError,
    ChaosSkippedError,
    CircuitBreakerOpenError,
    CodeGenerationError,
    CodeGenerationPolicyViolationError,
    # Communication errors
    CommunicationError,
    ConfigLoadError,
    ConfigMigrationError,
    # Configuration errors
    ConfigurationError,
    ConfigValidationError,
    ConstitutionViolationError,
    ContextCompilationError,
    # Context errors
    ContextError,
    ContextOverflowError,
    DatabaseConnectionError,
    DatabaseDriverNotAvailableError,
    DatabaseError,
    DatabasePathError,
    DatabasePolicyError,
    DeadlockDetectedError,
    # Event errors
    EventError,
    EventPublishError,
    EventSourcingError,
    EvidenceNotFoundError,
    # Execution errors
    ExecutionError,
    FileNotFoundError,
    InferenceEngineNotConfiguredError,
    InvalidStateTransitionError,
    InvalidTaskStateTransitionError,
    InvalidToolStateTransitionError,
    KernelAuditWriteError,
    # Root base
    KernelOneError,
    LauncherError,
    LockTimeoutError,
    NetworkChaosError,
    NetworkError,
    NonRetryableError,
    OrchestrationError,
    PathSecurityError,
    PathTraversalError,
    # Permission errors
    PermissionError,
    PermissionServiceError,
    ProcessRunnerError,
    RateLimitError,
    RateLimitExceededError,
    ReservedKeyViolationError,
    # Resource errors
    ResourceError,
    # Retry/Resilience errors
    RetryableError,
    RoleDataStoreError,
    # Shadow Replay errors
    ShadowReplayError,
    ShellDisallowedError,
    # State errors
    StateError,
    StateNotFoundError,
    TaskStateError,
    # Testing errors
    TestingInfrastructureError,
    TimeoutError,
    ToolAuthorizationError,
    ToolError,
    ToolExecutionError,
    TurnDecisionDecodeError,
    # Turn Decision errors
    TurnDecisionError,
    # Validation errors
    ValidationError,
    VisionNotAvailableError,
    # Vision Service errors
    VisionServiceError,
    WebSocketSendError,
    WorkerStateError,
    WorkflowContractError,
    # Workflow Runtime errors
    WorkflowRuntimeError,
    WorkflowUnavailableError,
)

# ============================================================================
# EmitResult (re-exported from events/emit_result.py)
# ============================================================================
from polaris.kernelone.events.emit_result import EmitResult

# ============================================================================
# LLM Exceptions (from llm/exceptions.py)
# Note: These imports are lazy to avoid circular dependency.
# Import directly from polaris.kernelone.llm.exceptions when needed.
# ============================================================================

_LLM_EXCEPTIONS_LOADED = False
_LLM_EXCEPTIONS: dict[str, type] = {}


def _ensure_llm_exceptions() -> None:
    """Lazily load LLM exceptions to avoid circular import."""
    global _LLM_EXCEPTIONS_LOADED, _LLM_EXCEPTIONS
    if not _LLM_EXCEPTIONS_LOADED:
        from polaris.kernelone.llm.exceptions import (
            AuthenticationError as LLMAuthenticationError,
            BudgetExceededError as LLMBudgetExceededError,
            CircuitBreakerOpenError as LLMCircuitBreakerOpenError,
            JSONParseError,
            LLMError,
            LLMTimeoutError,
            ProviderError,
            RateLimitError as LLMRateLimitError,
            ResponseParseError,
            TimeoutError as LLMTimeoutErrorAlias,
            ToolExecutionError as LLMToolExecutionError,
            ToolParseError,
        )

        _LLM_EXCEPTIONS = {
            "LLMError": LLMError,
            "LLMTimeoutError": LLMTimeoutError,
            "JSONParseError": JSONParseError,
            "ResponseParseError": ResponseParseError,
            "ToolParseError": ToolParseError,
            "ProviderError": ProviderError,
            "LLMCircuitBreakerOpenError": LLMCircuitBreakerOpenError,
            "LLMRateLimitError": LLMRateLimitError,
            "LLMAuthenticationError": LLMAuthenticationError,
            "LLMTimeoutErrorAlias": LLMTimeoutErrorAlias,
            "LLMToolExecutionError": LLMToolExecutionError,
            "LLMBudgetExceededError": LLMBudgetExceededError,
        }
        _LLM_EXCEPTIONS_LOADED = True


# Lazy accessors for LLM exceptions
def __getattr__(name: str) -> type:
    if name in (
        "LLMError",
        "LLMTimeoutError",
        "JSONParseError",
        "ResponseParseError",
        "ToolParseError",
        "ProviderError",
        "LLMCircuitBreakerOpenError",
        "LLMRateLimitError",
        "LLMAuthenticationError",
        "LLMTimeoutErrorAlias",
        "LLMToolExecutionError",
        "LLMBudgetExceededError",
        "LLMException",
    ):
        _ensure_llm_exceptions()
        if name == "LLMException":
            return _LLM_EXCEPTIONS["LLMError"]
        return _LLM_EXCEPTIONS.get(name, _LLM_EXCEPTIONS["LLMError"])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Semantic alias for LLMError - prefer this for type hints
# Note: This is lazily loaded via __getattr__
LLMException = None  # type: ignore[assignment,misc]

# ============================================================================
# Backward Compatibility Aliases
# ============================================================================

# KernelError -> KernelOneError (renamed for clarity)
KernelError = KernelOneError

# ============================================================================
# Exports
# ============================================================================

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
    "EmitResult",
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
    "JSONParseError",
    "KernelAuditWriteError",
    "KernelError",  # Backward compatibility alias
    # Root base
    "KernelOneError",
    # LLM (from llm/exceptions.py)
    "LLMError",
    "LLMException",  # Semantic alias
    "LLMTimeoutError",
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
    "ProviderError",
    "RateLimitError",
    "RateLimitExceededError",
    "ReservedKeyViolationError",
    # Resource
    "ResourceError",
    "ResponseParseError",
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
    "ToolError",
    "ToolExecutionError",
    "ToolParseError",
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
