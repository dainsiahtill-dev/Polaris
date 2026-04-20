"""Polaris KernelOne constants.

Centralized constants to eliminate magic numbers scattered across the codebase.
All values are chosen based on empirical defaults and safety limits.
"""

from __future__ import annotations

import os
from enum import StrEnum

# ═══════════════════════════════════════════════════════════════════
# Timeouts
# ═══════════════════════════════════════════════════════════════════

#: Default timeout for LLM and network operations (seconds)
DEFAULT_OPERATION_TIMEOUT_SECONDS: int = 300

#: Default timeout for Director role LLM calls (seconds)
#: 660 seconds = 11 minutes, larger buffer for code generation workloads
DIRECTOR_TIMEOUT_SECONDS: float = 660.0

#: Default backend server port
DEFAULT_BACKEND_PORT: int = 49977

#: Default renderer port (for Electron UI)
DEFAULT_RENDERER_PORT: int = 5173

#: Default NATS messaging server URL
DEFAULT_NATS_URL: str = "nats://localhost:4222"

#: Stale lock threshold for file-based distributed locks (seconds)
LOCK_STALE_THRESHOLD_SECONDS: float = 3600.0

#: JSONL lock stale threshold (seconds)
JSONL_LOCK_STALE_SECONDS: float = 120.0

#: JSONL buffer enabled by default
JSONL_BUFFER_ENABLED: bool = True

#: JSONL flush interval (seconds)
JSONL_FLUSH_INTERVAL_SECONDS: float = 1.0

#: JSONL flush batch size
JSONL_FLUSH_BATCH_SIZE: int = 50

#: JSONL max buffer size
JSONL_MAX_BUFFER_SIZE: int = 2000

#: JSONL buffer TTL (seconds)
JSONL_BUFFER_TTL_SECONDS: float = 300.0

#: JSONL max paths
JSONL_MAX_PATHS: int = 100

#: Default mailbox poll interval for agents (seconds)
AGENT_MAILBOX_POLL_INTERVAL_SECONDS: float = 0.05

#: Maximum consecutive processing errors before backoff
AGENT_MAX_CONSECUTIVE_ERRORS: int = 3

#: Backoff delay on consecutive errors (seconds)
AGENT_ERROR_BACKOFF_DELAY_SECONDS: float = 1.0

#: Default task timeout for orchestrator (seconds)
ORCHESTRATOR_DEFAULT_TASK_TIMEOUT_SECONDS: float = 120.0

#: Default maximum delegation depth
ORCHESTRATOR_DEFAULT_MAX_DELEGATION_DEPTH: int = 5

#: Minimum confidence threshold for accepting results
ORCHESTRATOR_DEFAULT_CONFIDENCE_THRESHOLD: float = 0.7

#: Maximum retained states in execution runtime
EXECUTION_MAX_RETAINED_STATES: int = 1000

#: Maximum terminal states to retain
EXECUTION_MAX_TERMINAL_STATES: int = 500

#: Cleanup threshold ratio (0.0-1.0) for triggering cleanup
EXECUTION_CLEANUP_THRESHOLD: float = 0.8

#: Default async concurrency
EXECUTION_DEFAULT_ASYNC_CONCURRENCY: int = 32

#: Default blocking concurrency
EXECUTION_DEFAULT_BLOCKING_CONCURRENCY: int = 8

#: Default process concurrency
EXECUTION_DEFAULT_PROCESS_CONCURRENCY: int = 4

#: Default process timeout (seconds)
EXECUTION_DEFAULT_PROCESS_TIMEOUT_SECONDS: float = 300.0

#: Default timeout for short operations like health checks (seconds)
DEFAULT_SHORT_TIMEOUT_SECONDS: float = 30.0

#: Maximum timeout for long-running workflows (seconds)
MAX_WORKFLOW_TIMEOUT_SECONDS: int = 3600

#: Default timeout for HTTP requests (seconds)
DEFAULT_HTTP_TIMEOUT_SECONDS: float = 30.0

#: Default timeout for CLI subprocess operations (seconds)
DEFAULT_CLI_TIMEOUT_SECONDS: float = 60.0

#: Default poll interval for async polling loops (seconds)
DEFAULT_POLL_INTERVAL_SECONDS: float = 1.0

#: Poll interval for background polling operations (seconds)
BACKGROUND_POLL_INTERVAL_SECONDS: float = 10.0

# ═══════════════════════════════════════════════════════════════════
# File Sizes
# ═══════════════════════════════════════════════════════════════════

#: Maximum file size to read for indexing/parsing (10MB)
#: Prevents blocking on large binary or log files
MAX_FILE_SIZE_BYTES: int = 10 * 1024 * 1024

#: Maximum file size for content search operations (2MB)
#: Lower limit for performance in search-heavy operations
MAX_SEARCH_FILE_SIZE_BYTES: int = 2 * 1024 * 1024

#: Default chunk size for file reading operations (bytes)
DEFAULT_CHUNK_SIZE_BYTES: int = 8192

#: 1 MB in bytes (helper constant for size calculations)
ONE_MB_BYTES: int = 1024 * 1024

#: Direct result size threshold (10KB)
#: Results below this threshold are returned directly without storage
DIRECT_RESULT_SIZE_THRESHOLD_BYTES: int = 10 * 1024

# ═══════════════════════════════════════════════════════════════════
# Retries
# ═══════════════════════════════════════════════════════════════════

#: Default maximum retries for transient failures
DEFAULT_MAX_RETRIES: int = 3

#: Maximum retries for rate-limited operations (more patience)
RATE_LIMIT_MAX_RETRIES: int = 5

#: Default retry delay for transient network errors (seconds)
DEFAULT_RETRY_DELAY_SECONDS: float = 1.0

#: Retry delay for rate limit errors (seconds)
RATE_LIMIT_RETRY_DELAY_SECONDS: float = 5.0

# ═══════════════════════════════════════════════════════════════════
# Batching
# ═══════════════════════════════════════════════════════════════════

#: Default batch size for bulk operations
DEFAULT_BATCH_SIZE: int = 100

#: Maximum batch size for embedding computations
MAX_EMBEDDING_BATCH_SIZE: int = 32

#: Maximum items in metadata collections (signatures, parameters, etc.)
MAX_METADATA_ITEMS: int = 24

#: Maximum signature characters
MAX_SIGNATURE_CHARS: int = 240

# ═══════════════════════════════════════════════════════════════════
# Observability
# ═══════════════════════════════════════════════════════════════════

#: Maximum completed spans to retain in distributed tracer
MAX_COMPLETED_SPANS: int = 10000

#: Maximum traces to retain in distributed tracer
MAX_TRACES: int = 1000

#: Maximum metric points to retain in metrics collector
MAX_METRIC_POINTS: int = 10000


# ═══════════════════════════════════════════════════════════════════
# File Read Limits
# ═══════════════════════════════════════════════════════════════════

#: Warning threshold for file read lines (triggers downgrade suggestion)
FILE_READ_WARN_LINES: int = 500

#: Hard limit for file reads (enforces truncation)
FILE_READ_HARD_LIMIT: int = 2000

#: Maximum context lines for file operations
FILE_MAX_CONTEXT_LINES: int = 4000

#: Number of recent reads to track for read-before-edit enforcement
FILE_READ_SEQUENCE_WINDOW: int = 2


# ═══════════════════════════════════════════════════════════════════
# Stream Timeouts
# ═══════════════════════════════════════════════════════════════════

#: Default timeout for stream operations (seconds)
STREAM_TIMEOUT_SECONDS: int = 300


# ═══════════════════════════════════════════════════════════════════
# Circuit Breaker Configuration
# ═══════════════════════════════════════════════════════════════════

#: Default failure threshold before circuit opens
CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = 5

#: Default recovery timeout before attempting reset (seconds)
CIRCUIT_BREAKER_RECOVERY_TIMEOUT: float = 60.0

#: Default max test requests in half-open state
CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS: int = 3

#: Default successes needed in half-open to close circuit
CIRCUIT_BREAKER_SUCCESS_THRESHOLD: int = 2

#: Default sliding window for failure counting (seconds)
CIRCUIT_BREAKER_WINDOW_SECONDS: float = 120.0


# ═══════════════════════════════════════════════════════════════════
# Retry Jitter
# ═══════════════════════════════════════════════════════════════════

#: Minimum jitter factor for retry delays (70% base)
RETRY_JITTER_MIN: float = 0.7

#: Maximum jitter factor for retry delays (adds up to 30%)
RETRY_JITTER_MAX: float = 1.0


# ═══════════════════════════════════════════════════════════════════
# Validation Thresholds
# ═══════════════════════════════════════════════════════════════════

#: Maximum supported schema version for config validation
MAX_SUPPORTED_SCHEMA_VERSION: int = 10

#: Minimum similarity threshold for file name suggestions
SIMILARITY_THRESHOLD: float = 0.6

#: Workspace file count threshold for showing full file list
WORKSPACE_FILES_SMALL_THRESHOLD: int = 30


# ═══════════════════════════════════════════════════════════════════
# Failure Budget Configuration
# ═══════════════════════════════════════════════════════════════════

#: Default max failures per tool before blocking
FAILURE_BUDGET_MAX_PER_TOOL: int = 3

#: Default max repeats for the same error pattern
FAILURE_BUDGET_MAX_SAME_PATTERN: int = 2

#: Default max total failures per turn
FAILURE_BUDGET_MAX_TOTAL_PER_TURN: int = 10


# ═══════════════════════════════════════════════════════════════════
# Storage Retention (days)
# ═══════════════════════════════════════════════════════════════════

#: Factory current run retention days
STORAGE_RETENTION_FACTORY: int = 200

#: Runtime state retention days (ephemeral)
STORAGE_RETENTION_RUNTIME_STATE: int = 7

#: Runtime status retention days (ephemeral)
STORAGE_RETENTION_RUNTIME_STATUS: int = 1

#: Runtime control retention days (ephemeral, immediate cleanup)
STORAGE_RETENTION_RUNTIME_CONTROL: int = 0


# ═══════════════════════════════════════════════════════════════════
# File Operations
# ═══════════════════════════════════════════════════════════════════

#: Buffer size for seek operations (bytes)
SEEK_BUFFER_SIZE: int = 8192

#: SSE event ID hash modulo to prevent large IDs
SSE_EVENT_ID_MODULO: int = 1000000

#: Maximum acceptable ratio of bad characters (replacement char) in text
BAD_CHAR_THRESHOLD: float = 0.02


# ═══════════════════════════════════════════════════════════════════
# Role Identifiers (P2-003)
# ═══════════════════════════════════════════════════════════════════

#: Maximum syntax unit lines (prevent oversized snippets)
MAX_SYNTAX_UNIT_LINES: int = 320

#: Maximum snippet characters for display
MAX_SNIPPET_CHARS: int = 1200

#: Maximum line characters for display
MAX_LINE_CHARS: int = 400

#: Maximum files to process in code map generation
MAX_CODE_MAP_FILES: int = 200

#: Maximum results for code search
MAX_SEARCH_RESULTS: int = 50

#: Broadcast max payload size (bytes)
BROADCAST_MAX_SIZE_BYTES: int = 1024 * 1024

# ═══════════════════════════════════════════════════════════════════
# Worker Configuration
# ═══════════════════════════════════════════════════════════════════

#: Default max workers for worker pools (4-32, scaled by CPU cores)
#: Canonical definition for all worker pool default sizes.
#: Replaces duplicated _DEFAULT_MAX_WORKERS in director/tasking, director/execution, pm_dispatch
DEFAULT_MAX_WORKERS: int = min(32, max(4, (os.cpu_count() or 4) * 2))

#: Default director parallelism (number of parallel director workers)
#: Used as default for director pool sizing and CLI defaults
DEFAULT_DIRECTOR_MAX_PARALLELISM: int = 3

#: Default max idle time for async workers (seconds)
DEFAULT_WORKER_MAX_IDLE_SECONDS: int = 10

#: Default timeout for async worker operations (seconds)
DEFAULT_WORKER_TIMEOUT_SECONDS: float = 30.0

# ═══════════════════════════════════════════════════════════════════
# Memory
# ═══════════════════════════════════════════════════════════════════

#: Default memory limit for batchers (MB)
DEFAULT_BATCHER_MEMORY_MB: int = 10

# ═══════════════════════════════════════════════════════════════════
# Buffers (P2-008)
# ═══════════════════════════════════════════════════════════════════

#: Default buffer size for LLM stream operations (chunks/responses)
#: 1000 chunks provides adequate buffering for most streaming scenarios
DEFAULT_LLM_STREAM_BUFFER_SIZE: int = 1000

#: Default buffer size for telemetry and observability event buffers
#: 100 events provides a reasonable balance between memory and data retention
DEFAULT_TELEMETRY_BUFFER_SIZE: int = 100

#: Default maximum message history in message bus
MESSAGE_BUS_MAX_HISTORY: int = 1000

#: Default maximum dead letters in message bus
MESSAGE_BUS_MAX_DEAD_LETTERS: int = 1000

#: Default timeout for file lock operations (seconds)
DEFAULT_LOCK_TIMEOUT_SECONDS: float = 5.0

#: Poll interval for file lock polling (seconds)
LOCK_POLL_INTERVAL_SECONDS: float = 0.05


# ═══════════════════════════════════════════════════════════════════
# Role Identifiers (P2-003)
# ═══════════════════════════════════════════════════════════════════


class RoleId(StrEnum):
    """Canonical role identifiers for Polaris agents.

    Replaces hardcoded string literals like "pm", "director", "qa",
    "architect", "chief_engineer" scattered throughout the codebase.

    Usage::

        from polaris.kernelone.constants import RoleId

        def get_role_name(role: RoleId) -> str:
            return role.value

        # Compare with string (StrEnum provides __eq__ with str)
        if role == "pm":
            ...
    """

    PM = "pm"
    DIRECTOR = "director"
    QA = "qa"
    ARCHITECT = "architect"
    CHIEF_ENGINEER = "chief_engineer"
    SCOUT = "scout"
    SYSTEM = "system"
    DEFAULT = "default"

    @classmethod
    def from_string(cls, value: str) -> RoleId:
        """Convert string to RoleId, case-insensitive.

        Args:
            value: Role string like "PM", "pm", "Chief_Engineer"

        Returns:
            Corresponding RoleId enum member

        Raises:
            ValueError: If value is not a valid role
        """
        # Try exact match first
        for member in cls:
            if member.value == value:
                return member
        # Try case-insensitive match
        for member in cls:
            if member.value.lower() == value.lower():
                return member
        # Try with underscores/hyphens normalized
        normalized = value.lower().replace("-", "_")
        for member in cls:
            if member.value == normalized:
                return member
        raise ValueError(f"Invalid role: {value!r}. Valid roles: {[m.value for m in cls]}")

    @classmethod
    def is_valid(cls, value: str) -> bool:
        """Check if a string is a valid role identifier.

        Args:
            value: Role string to validate

        Returns:
            True if value is a valid role
        """
        try:
            cls.from_string(value)
            return True
        except ValueError:
            return False


# ═══════════════════════════════════════════════════════════════════
# Workflow Task Status - P2-002
# ═══════════════════════════════════════════════════════════════════
# WorkflowTaskStatus and ActivityStatus are defined in:
# polaris/kernelone/workflow/task_status.py
#
# Import them from there:
#   from polaris.kernelone.workflow.task_status import WorkflowTaskStatus, ActivityStatus
#
# Re-exported here for convenience (lazy import to avoid circular deps):
#   from polaris.kernelone.workflow.task_status import WorkflowTaskStatus, ActivityStatus


__all__ = [
    "AGENT_ERROR_BACKOFF_DELAY_SECONDS",
    # Agent Configuration
    "AGENT_MAILBOX_POLL_INTERVAL_SECONDS",
    "AGENT_MAX_CONSECUTIVE_ERRORS",
    "BACKGROUND_POLL_INTERVAL_SECONDS",
    "BAD_CHAR_THRESHOLD",
    "BROADCAST_MAX_SIZE_BYTES",
    # Circuit Breaker Configuration
    "CIRCUIT_BREAKER_FAILURE_THRESHOLD",
    "CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS",
    "CIRCUIT_BREAKER_RECOVERY_TIMEOUT",
    "CIRCUIT_BREAKER_SUCCESS_THRESHOLD",
    "CIRCUIT_BREAKER_WINDOW_SECONDS",
    # Ports
    "DEFAULT_BACKEND_PORT",
    # Memory
    "DEFAULT_BATCHER_MEMORY_MB",
    # Batching
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_CHUNK_SIZE_BYTES",
    "DEFAULT_CLI_TIMEOUT_SECONDS",
    "DEFAULT_DIRECTOR_MAX_PARALLELISM",
    "DEFAULT_HTTP_TIMEOUT_SECONDS",
    # Buffers
    "DEFAULT_LLM_STREAM_BUFFER_SIZE",
    # Locking
    "DEFAULT_LOCK_TIMEOUT_SECONDS",
    # Retries
    "DEFAULT_MAX_RETRIES",
    # Worker
    "DEFAULT_MAX_WORKERS",
    "DEFAULT_NATS_URL",
    # Timeouts
    "DEFAULT_OPERATION_TIMEOUT_SECONDS",
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "DEFAULT_RENDERER_PORT",
    "DEFAULT_RETRY_DELAY_SECONDS",
    "DEFAULT_SHORT_TIMEOUT_SECONDS",
    "DEFAULT_TELEMETRY_BUFFER_SIZE",
    "DEFAULT_WORKER_MAX_IDLE_SECONDS",
    "DEFAULT_WORKER_TIMEOUT_SECONDS",
    "DIRECTOR_TIMEOUT_SECONDS",
    "DIRECT_RESULT_SIZE_THRESHOLD_BYTES",
    "EXECUTION_CLEANUP_THRESHOLD",
    "EXECUTION_DEFAULT_ASYNC_CONCURRENCY",
    "EXECUTION_DEFAULT_BLOCKING_CONCURRENCY",
    "EXECUTION_DEFAULT_PROCESS_CONCURRENCY",
    "EXECUTION_DEFAULT_PROCESS_TIMEOUT_SECONDS",
    # Execution Runtime
    "EXECUTION_MAX_RETAINED_STATES",
    "EXECUTION_MAX_TERMINAL_STATES",
    # Failure Budget Configuration
    "FAILURE_BUDGET_MAX_PER_TOOL",
    "FAILURE_BUDGET_MAX_SAME_PATTERN",
    "FAILURE_BUDGET_MAX_TOTAL_PER_TURN",
    "FILE_MAX_CONTEXT_LINES",
    "FILE_READ_HARD_LIMIT",
    "FILE_READ_SEQUENCE_WINDOW",
    # File Read Limits
    "FILE_READ_WARN_LINES",
    "JSONL_BUFFER_ENABLED",
    "JSONL_BUFFER_TTL_SECONDS",
    "JSONL_FLUSH_BATCH_SIZE",
    "JSONL_FLUSH_INTERVAL_SECONDS",
    "JSONL_LOCK_STALE_SECONDS",
    "JSONL_MAX_BUFFER_SIZE",
    "JSONL_MAX_PATHS",
    "LOCK_POLL_INTERVAL_SECONDS",
    # Locking
    "LOCK_STALE_THRESHOLD_SECONDS",
    "MAX_CODE_MAP_FILES",
    # Observability
    "MAX_COMPLETED_SPANS",
    "MAX_EMBEDDING_BATCH_SIZE",
    # File Sizes
    "MAX_FILE_SIZE_BYTES",
    "MAX_LINE_CHARS",
    "MAX_METADATA_ITEMS",
    "MAX_METRIC_POINTS",
    "MAX_SEARCH_FILE_SIZE_BYTES",
    "MAX_SEARCH_RESULTS",
    "MAX_SIGNATURE_CHARS",
    "MAX_SNIPPET_CHARS",
    # Validation Thresholds
    "MAX_SUPPORTED_SCHEMA_VERSION",
    # Limits
    "MAX_SYNTAX_UNIT_LINES",
    "MAX_TRACES",
    "MAX_WORKFLOW_TIMEOUT_SECONDS",
    "MESSAGE_BUS_MAX_DEAD_LETTERS",
    # Message Bus
    "MESSAGE_BUS_MAX_HISTORY",
    "ONE_MB_BYTES",
    "ORCHESTRATOR_DEFAULT_CONFIDENCE_THRESHOLD",
    "ORCHESTRATOR_DEFAULT_MAX_DELEGATION_DEPTH",
    "ORCHESTRATOR_DEFAULT_TASK_TIMEOUT_SECONDS",
    "RATE_LIMIT_MAX_RETRIES",
    "RATE_LIMIT_RETRY_DELAY_SECONDS",
    "RETRY_JITTER_MAX",
    # Retry Jitter
    "RETRY_JITTER_MIN",
    # File Operations
    "SEEK_BUFFER_SIZE",
    "SIMILARITY_THRESHOLD",
    "SSE_EVENT_ID_MODULO",
    # Storage Retention
    "STORAGE_RETENTION_FACTORY",
    "STORAGE_RETENTION_RUNTIME_CONTROL",
    "STORAGE_RETENTION_RUNTIME_STATE",
    "STORAGE_RETENTION_RUNTIME_STATUS",
    # Stream Timeouts
    "STREAM_TIMEOUT_SECONDS",
    "WORKSPACE_FILES_SMALL_THRESHOLD",
    # Role Identifiers
    "RoleId",
]
