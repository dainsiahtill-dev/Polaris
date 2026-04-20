"""Application layer for Polaris.

Role: Use-case orchestration and application service facade.
In this branch, the primary capability carriers are Cells and KernelOne;
the application layer provides a thin facade for cross-cutting concerns.

Call chain (correct pattern):
    delivery -> application -> domain/kernelone/cells

Current branch status (migration):
    delivery currently calls Cells directly in some paths; this is
    acceptable during migration. New code should go through Cells
    (not application), since Cells are the primary capability carriers.

Sub-packages:
    llm/          - LLM evaluation and tooling (cell-backed)
    orchestration/ - Workflow orchestration (cell-backed)
    resident/     - Resident/daemon services (cell-backed)
    roles/        - Role engine and kernel components (cell-backed)

This layer intentionally contains no new implementations in this branch.
Do NOT add business logic or new use-case implementations here.
Main capability implementations live in polaris/cells/ and polaris/kernelone/.

Re-exported aliases (for cross-cutting convenience):
    - Domain entities and services consumed by multiple Cells
    - KernelOne contracts needed by delivery-layer dependency injection

Architecture constraints:
    - delivery must NOT import infrastructure adapters directly
    - delivery must NOT bypass Cells for business orchestration
    - application may NOT contain Polaris business semantics
    - application must NOT implement port/adapter patterns already in Cells
"""

from __future__ import annotations

# Re-export domain entities for cross-cutting convenience.
# The canonical source for each type is in the submodule.
from polaris.domain.entities import (
    Task,
    TaskEvidence,
    TaskPriority,
    TaskResult,
    TaskStateError,
    TaskStatus,
)
from polaris.domain.entities.worker import (
    Worker,
    WorkerCapabilities,
    WorkerHealth,
    WorkerStateError,
    WorkerStatus,
    WorkerType,
)
from polaris.domain.exceptions import (
    AuthenticationError,
    BusinessRuleError,
    ConfigurationError,
    ConflictError,
    DomainException,
    ExternalServiceError,
    InfrastructureError,
    LLMError,
    NetworkError,
    NotFoundError,
    PermissionDeniedError,
    ProcessAlreadyRunningError,
    ProcessError,
    ProcessNotRunningError,
    RateLimitError,
    ServiceUnavailableError,
    StateError,
    StorageError,
    TimeoutError,
    ValidationError,
)
from polaris.domain.models import ConfigSnapshot, ConfigValidationResult
from polaris.domain.services.background_task import (
    BackgroundTask,
    BackgroundTaskService,
    ExecutionResult,
)
from polaris.domain.services.llm_compact_service import LLMCompactService
from polaris.domain.services.security_service import SecurityService
from polaris.domain.services.tool_timeout_service import ToolTimeoutService
from polaris.domain.services.transcript_service import TranscriptService
from polaris.infrastructure.llm.token_service import TokenService
from polaris.kernelone.contracts.technical import (
    Effect,
    EffectTracker,
    Envelope,
    KernelError,
    KernelOneError,
    LockAcquireResult,
    LockOptions,
    LockPort,
    Result,
    ScheduledTask,
    ScheduleResult,
    SchedulerPort,
    StreamChunk,
    SubsystemHealth,
    TaggedError,
    TraceContext,
)

__all__ = [
    "AuthenticationError",
    "BackgroundTask",
    "BackgroundTaskService",
    "BusinessRuleError",
    "ConfigSnapshot",
    "ConfigValidationResult",
    "ConfigurationError",
    "ConflictError",
    "DomainException",
    "Effect",
    "EffectTracker",
    "Envelope",
    "ExecutionResult",
    "ExternalServiceError",
    "InfrastructureError",
    "KernelError",
    "KernelOneError",
    "LLMCompactService",
    "LLMError",
    "LockAcquireResult",
    "LockOptions",
    "LockPort",
    "NetworkError",
    "NotFoundError",
    "PermissionDeniedError",
    "ProcessAlreadyRunningError",
    "ProcessError",
    "ProcessNotRunningError",
    "RateLimitError",
    "Result",
    "ScheduleResult",
    "ScheduledTask",
    "SchedulerPort",
    "SecurityService",
    "ServiceUnavailableError",
    "StateError",
    "StorageError",
    "StreamChunk",
    "SubsystemHealth",
    "TaggedError",
    "Task",
    "TaskEvidence",
    "TaskPriority",
    "TaskResult",
    "TaskStateError",
    "TaskStatus",
    "TimeoutError",
    "TokenService",
    "ToolTimeoutService",
    "TraceContext",
    "TranscriptService",
    "ValidationError",
    "Worker",
    "WorkerCapabilities",
    "WorkerHealth",
    "WorkerStateError",
    "WorkerStatus",
    "WorkerType",
]
