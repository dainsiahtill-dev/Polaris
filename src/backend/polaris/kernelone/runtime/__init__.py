"""KernelOne Runtime - Agent OS infrastructure layer.

Provides shared runtime utilities for agent lifecycle, execution,
and cross-cutting concerns following ACGA 2.0 architecture.

Migration notice (2026-03-22):
    ``Result`` and ``ErrorCodes`` have been migrated.

    - ``Result`` is now re-exported from
      ``polaris.kernelone.contracts.technical.master_types.Result``.
      The canonical source is the contracts layer.
    - ``ErrorCodes`` is now deprecated. Use ``TaggedError`` or ``KernelError``
      from ``polaris.kernelone.contracts.technical.master_types`` instead.

    Example migration::

        # Old (deprecated)
        from polaris.kernelone.runtime import Result, ErrorCodes
        Result.err("message", code=ErrorCodes.REVIEW_NOT_FOUND)

        # New (canonical)
        from polaris.kernelone.contracts.technical import Result, TaggedError
        Result.err(TaggedError("REVIEW_NOT_FOUND", "message"))
"""

from __future__ import annotations

from polaris.kernelone.constants import (
    EXECUTION_DEFAULT_ASYNC_CONCURRENCY,
    EXECUTION_DEFAULT_BLOCKING_CONCURRENCY,
    EXECUTION_DEFAULT_PROCESS_CONCURRENCY,
    EXECUTION_DEFAULT_PROCESS_TIMEOUT_SECONDS,
)

# Result is now canonical from the contracts layer
from polaris.kernelone.contracts.technical import (
    ErrorCategory,
    KernelError,
    KernelOneError,
    Result,
    TaggedError,
)
from polaris.kernelone.runtime.bounded_cache import BoundedCache
from polaris.kernelone.runtime.execution_facade import (
    AsyncTaskSpec,
    BatchCancelResult,
    BatchWaitResult,
    BlockingIoSpec,
    ExecutionFacade,
    ExecutionSpec,
    ProcessRunResult,
    ProcessSpec,
    get_shared_execution_facade,
    reset_shared_execution_facade,
    run_sync,
)

# ErrorCodes is preserved for backward compatibility only — do not add new codes here
from polaris.kernelone.runtime.execution_runtime import (
    ExecutionHandle,
    ExecutionLane,
    ExecutionRuntime,
    ExecutionSnapshot,
    ExecutionStatus,
    get_shared_execution_runtime,
    reset_shared_execution_runtime,
)

# Backward compatibility aliases
from polaris.kernelone.runtime.instance_state import (
    InstanceScopedStateStore,
    get_current_instance_id,
    normalize_workspace_instance_id,
    scoped_instance,
)
from polaris.kernelone.runtime.metrics import (
    ExecutionMetrics,
    get_metrics,
    reset_metrics,
)
from polaris.kernelone.runtime.result import ErrorCodes

DEFAULT_ASYNC_CONCURRENCY = EXECUTION_DEFAULT_ASYNC_CONCURRENCY
DEFAULT_BLOCKING_CONCURRENCY = EXECUTION_DEFAULT_BLOCKING_CONCURRENCY
DEFAULT_PROCESS_CONCURRENCY = EXECUTION_DEFAULT_PROCESS_CONCURRENCY
DEFAULT_PROCESS_TIMEOUT_SECONDS = EXECUTION_DEFAULT_PROCESS_TIMEOUT_SECONDS

__all__ = [
    # Unified execution substrate
    "DEFAULT_ASYNC_CONCURRENCY",
    "DEFAULT_BLOCKING_CONCURRENCY",
    "DEFAULT_PROCESS_CONCURRENCY",
    "DEFAULT_PROCESS_TIMEOUT_SECONDS",
    # High-level facade for migration/integration
    "AsyncTaskSpec",
    "BatchCancelResult",
    "BatchWaitResult",
    "BlockingIoSpec",
    # Bounded cache for preventing memory leaks
    "BoundedCache",
    "ErrorCategory",
    # Deprecated — use TaggedError or KernelError instead
    "ErrorCodes",
    "ExecutionFacade",
    "ExecutionHandle",
    "ExecutionLane",
    # Runtime metrics for observability
    "ExecutionMetrics",
    "ExecutionRuntime",
    "ExecutionSnapshot",
    "ExecutionSpec",
    "ExecutionStatus",
    "InstanceScopedStateStore",
    "KernelError",
    "KernelOneError",
    "ProcessRunResult",
    "ProcessSpec",
    # Canonical Result and error types (from contracts layer)
    "Result",
    "TaggedError",
    "get_current_instance_id",
    "get_metrics",
    "get_shared_execution_facade",
    "get_shared_execution_runtime",
    "normalize_workspace_instance_id",
    "reset_metrics",
    "reset_shared_execution_facade",
    "reset_shared_execution_runtime",
    "run_sync",
    "scoped_instance",
]
