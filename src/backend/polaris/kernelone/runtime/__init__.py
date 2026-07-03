"""KernelOne Runtime - Agent OS infrastructure layer.

Provides shared runtime utilities for agent lifecycle, execution,
and cross-cutting concerns following ACGA 2.0 architecture.

Migration notice (2026-03-22):
    ``Result`` has been migrated.

    - ``Result`` is now re-exported from
      ``polaris.kernelone.contracts.technical.master_types.Result``.
      The canonical source is the contracts layer.

    Example migration::

        # Canonical
        from polaris.kernelone.contracts.technical import Result, TaggedError
        Result.err(TaggedError("REVIEW_NOT_FOUND", "message"))
"""

from __future__ import annotations

# Result is now canonical from the contracts layer
from polaris.kernelone.contracts.technical import (
    KernelError,
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
from polaris.kernelone.runtime.execution_runtime import (
    ExecutionHandle,
    ExecutionLane,
    ExecutionRuntime,
    ExecutionSnapshot,
    ExecutionStatus,
    get_shared_execution_runtime,
    reset_shared_execution_runtime,
)

# Instance-scoped runtime state helpers.
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

__all__ = [
    # Unified execution substrate
    # High-level facade for migration/integration
    "AsyncTaskSpec",
    "BatchCancelResult",
    "BatchWaitResult",
    "BlockingIoSpec",
    # Bounded cache for preventing memory leaks
    "BoundedCache",
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
