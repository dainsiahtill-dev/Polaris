"""Application layer for Polaris.

Role
----
Thin use-case orchestration and facade layer.  The application layer sits
between *delivery* (HTTP / CLI / WebSocket) and the capability carriers
(*Cells*, *KernelOne*, *domain*).  It exposes cohesive **admin service
facades** so that delivery code never needs to import Cell internals,
infrastructure adapters, or low-level KernelOne modules directly.

Call chain (canonical pattern)::

    delivery  ->  application  ->  cells.*.public / domain / kernelone

Application services
~~~~~~~~~~~~~~~~~~~~
- ``RuntimeAdminService``  -- orchestrator and role-runtime facade
- ``StorageAdminService``  -- storage layout resolution facade
- ``SessionAdminService``  -- session lifecycle management facade
- ``CognitiveRuntimeService`` -- cognitive-runtime orchestration (sub-pkg)
- ``health``               -- runtime health checks (non-facade utility)

Re-exported aliases
~~~~~~~~~~~~~~~~~~~
Domain entities, domain services and KernelOne contracts that are consumed
by multiple Cells or needed for delivery-layer dependency injection are
re-exported here for convenience.  The canonical source for each type
remains in its originating module.

Architecture constraints
~~~~~~~~~~~~~~~~~~~~~~~~
- delivery must NOT import infrastructure adapters directly
- delivery must NOT bypass Cells for business orchestration
- application may NOT contain Polaris business semantics
- application must NOT implement port/adapter patterns already in Cells
- All text I/O must use explicit UTF-8
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Application services (lazy-import friendly)
# ---------------------------------------------------------------------------
from polaris.application.runtime_admin import (
    RuntimeAdminError,
    RuntimeAdminService,
)
from polaris.application.session_admin import (
    SessionAdminError,
    SessionAdminService,
)
from polaris.application.storage_admin import (
    StorageAdminError,
    StorageAdminService,
)
from polaris.application.traceability_admin import (
    TraceabilityAdminError,
    TraceabilityAdminService,
)

# ---------------------------------------------------------------------------
# Re-exported domain entities for cross-cutting convenience.
# The canonical source for each type is in the submodule.
# ---------------------------------------------------------------------------
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
    # -- Domain entities ----------------------------------------------------
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
    # -- Application services -----------------------------------------------
    "RuntimeAdminError",
    "RuntimeAdminService",
    "ScheduleResult",
    "ScheduledTask",
    "SchedulerPort",
    "SecurityService",
    "ServiceUnavailableError",
    "SessionAdminError",
    "SessionAdminService",
    "StateError",
    "StorageAdminError",
    "StorageAdminService",
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
