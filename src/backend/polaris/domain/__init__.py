"""Domain layer for Polaris.

Role: Business entities, value objects, domain services, and domain rules.
The domain layer is the innermost layer that owns business semantics.
It must not depend on application, delivery, infrastructure, or Cells.

Design constraints:
    - Domain entities own business rules and invariants
    - Domain services encapsulate business logic that doesn't fit in entities
    - Domain must NOT import from polaris.application, polaris.delivery,
      polaris.infrastructure, or polaris.cells
    - Domain may import from polaris.kernelone (technical contracts only)
    - All text I/O must be UTF-8

Key modules:
    entities/     - Task, Worker, EvidenceBundle, Policy, etc.
    models/       - Value objects (ConfigSnapshot, Task, Resident)
    services/     - BackgroundTaskService, TokenService, etc.
    exceptions.py - DomainException hierarchy
    state_machine/ - Task phase and phase executor
    verification/ - Business validators, evidence collectors, gates
    utils/        - Language utilities
    director/     - Director-specific business logic (migrated from KernelOne)
"""

from __future__ import annotations

# Director-specific exports (migrated from KernelOne)
from polaris.domain.director import (
    # Constants
    AGENTS_DRAFT_REL,
    AGENTS_FEEDBACK_REL,
    CHANNEL_FILES,
    DEFAULT_DIALOGUE,
    DEFAULT_DIRECTOR_LIFECYCLE,
    DEFAULT_DIRECTOR_LLM_EVENTS,
    DEFAULT_DIRECTOR_STATUS,
    DEFAULT_DIRECTOR_SUBPROCESS_LOG,
    DEFAULT_ENGINE_STATUS,
    DEFAULT_GAP,
    DEFAULT_OLLAMA,
    DEFAULT_PLAN,
    DEFAULT_PLANNER,
    DEFAULT_PM_LLM_EVENTS,
    DEFAULT_PM_LOG,
    DEFAULT_PM_OUT,
    DEFAULT_PM_REPORT,
    DEFAULT_PM_SUBPROCESS_LOG,
    DEFAULT_QA,
    DEFAULT_REQUIREMENTS,
    DEFAULT_RUNLOG,
    DEFAULT_RUNTIME_EVENTS,
    DIRECTOR_CONTRACTS_DIR,
    DIRECTOR_EVENTS_DIR,
    DIRECTOR_LOGS_DIR,
    DIRECTOR_OUTPUT_DIR,
    DIRECTOR_RESULTS_DIR,
    DIRECTOR_RUNTIME_DIR,
    DIRECTOR_STATUS_DIR,
    NEW_CHANNEL_METADATA,
    WORKSPACE_STATUS_REL,
    # Lifecycle
    DirectorLifecycleManager,
    DirectorPhase,
    LifecycleEvent,
    LifecycleState,
    read as read_lifecycle,
    update as update_lifecycle,
)
from polaris.domain.entities import (
    Task,
    TaskEvidence,
    TaskPriority,
    TaskResult,
    TaskStateError,
    TaskStatus,
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
    ValidationError,
)
from polaris.domain.models import (
    ConfigSnapshot,
    ConfigValidationResult,
    FrozenInstanceError,
    SourceType,
)
from polaris.domain.services import (
    # Background task
    BackgroundTask,
    BackgroundTaskService,
    ExecutionResult,
    # LLM compact
    LLMCompactService,
    # Security
    SecurityService,
    # Skill template
    SkillTemplateService,
    # Todo
    TodoService,
    # Token
    TokenService,
    ToolTier,
    # Tool timeout
    ToolTimeoutService,
    # Transcript
    TranscriptService,
    estimate_tokens,
    get_security_service,
    get_skill_template_service,
    get_todo_service,
    get_token_service,
    get_tool_timeout_service,
    get_transcript_service,
    is_dangerous_command,
    reset_security_service,
    reset_skill_template_service,
    reset_todo_service,
    reset_token_service,
    reset_tool_timeout_service,
    reset_transcript_service,
)
from polaris.kernelone.contracts.technical import (
    Effect,
    EffectTracker,
    LockPort,
    Result,
    SchedulerPort,
    TaggedError,
)

__all__ = [
    # Director constants
    "AGENTS_DRAFT_REL",
    "AGENTS_FEEDBACK_REL",
    "CHANNEL_FILES",
    "DEFAULT_DIALOGUE",
    "DEFAULT_DIRECTOR_LIFECYCLE",
    "DEFAULT_DIRECTOR_LLM_EVENTS",
    "DEFAULT_DIRECTOR_STATUS",
    "DEFAULT_DIRECTOR_SUBPROCESS_LOG",
    "DEFAULT_ENGINE_STATUS",
    "DEFAULT_GAP",
    "DEFAULT_OLLAMA",
    "DEFAULT_PLAN",
    "DEFAULT_PLANNER",
    "DEFAULT_PM_LLM_EVENTS",
    "DEFAULT_PM_LOG",
    "DEFAULT_PM_OUT",
    "DEFAULT_PM_REPORT",
    "DEFAULT_PM_SUBPROCESS_LOG",
    "DEFAULT_QA",
    "DEFAULT_REQUIREMENTS",
    "DEFAULT_RUNLOG",
    "DEFAULT_RUNTIME_EVENTS",
    "DIRECTOR_CONTRACTS_DIR",
    "DIRECTOR_EVENTS_DIR",
    "DIRECTOR_LOGS_DIR",
    "DIRECTOR_OUTPUT_DIR",
    "DIRECTOR_RESULTS_DIR",
    "DIRECTOR_RUNTIME_DIR",
    "DIRECTOR_STATUS_DIR",
    "NEW_CHANNEL_METADATA",
    "WORKSPACE_STATUS_REL",
    # Exceptions
    "AuthenticationError",
    # Services
    "BackgroundTask",
    "BackgroundTaskService",
    "BusinessRuleError",
    # Models
    "ConfigSnapshot",
    "ConfigValidationResult",
    "ConfigurationError",
    "ConflictError",
    # Director lifecycle
    "DirectorLifecycleManager",
    "DirectorPhase",
    "DomainException",
    # KernelOne contracts (domain may use them)
    "Effect",
    "EffectTracker",
    "ExecutionResult",
    "ExternalServiceError",
    "FrozenInstanceError",
    "InfrastructureError",
    "LLMCompactService",
    "LLMError",
    "LifecycleEvent",
    "LifecycleState",
    "LockPort",
    "NetworkError",
    "NotFoundError",
    "PermissionDeniedError",
    "ProcessAlreadyRunningError",
    "ProcessError",
    "ProcessNotRunningError",
    "RateLimitError",
    "Result",
    "SchedulerPort",
    "SecurityService",
    "ServiceUnavailableError",
    "SourceType",
    "StateError",
    "StorageError",
    "TaggedError",
    # Entities
    "Task",
    "TaskEvidence",
    "TaskPriority",
    "TaskResult",
    "TaskStateError",
    "TaskStatus",
    "TodoService",
    "TokenService",
    "ToolTier",
    "ToolTimeoutService",
    "TranscriptService",
    "ValidationError",
    "Worker",
    "WorkerCapabilities",
    "WorkerHealth",
    "WorkerStateError",
    "WorkerStatus",
    "WorkerType",
    "estimate_tokens",
    "get_security_service",
    "get_skill_template_service",
    "get_todo_service",
    "get_token_service",
    "get_tool_timeout_service",
    "get_transcript_service",
    "is_dangerous_command",
    "read_lifecycle",
    "reset_security_service",
    "reset_skill_template_service",
    "reset_todo_service",
    "reset_token_service",
    "reset_tool_timeout_service",
    "reset_transcript_service",
    "update_lifecycle",
]
