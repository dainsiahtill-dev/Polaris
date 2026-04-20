"""Shared utilities for workflow engines.

This module contains static and utility methods extracted from WorkflowEngine
and SagaWorkflowEngine to reduce code duplication. These are pure functions
that can be safely shared between both engine implementations.

References:
- WorkflowEngine: kernelone/workflow/engine.py
- SagaWorkflowEngine: kernelone/workflow/saga_engine.py
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import random
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from polaris.kernelone.utils.time_utils import _now

if TYPE_CHECKING:
    from .contracts import TaskSpec
    from .engine import TaskExecutionOutcome

from .task_status import WorkflowTaskStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Static Utilities
# ---------------------------------------------------------------------------


def norm_result(value: Any) -> dict[str, Any]:
    """Normalize a handler result to a dict with string keys.

    This is a pure function that can be safely shared between engines.
    """
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {"value": value}


def calculate_retry_delay(spec: TaskSpec, attempt: int) -> float:
    """Calculate retry delay with exponential backoff and jitter.

    This is a pure function that can be safely shared between engines.

    Args:
        spec: Task specification containing retry policy.
        attempt: Current attempt number (1-indexed).

    Returns:
        Delay in seconds before the next retry attempt.
    """
    policy = spec.retry_policy
    delay = min(
        policy.initial_interval_seconds * (policy.backoff_coefficient ** max(0, attempt - 1)),
        policy.max_interval_seconds,
    )
    if policy.jitter_ratio > 0:
        delay = max(0.0, delay * random.uniform(1.0 - policy.jitter_ratio, 1.0 + policy.jitter_ratio))
    return delay


# ---------------------------------------------------------------------------
# Shared Async Helpers
# ---------------------------------------------------------------------------


async def unwrap_task_outcome(
    fut: asyncio.Task[Any],
    task_id: str,
    now_func: Callable[[], str] | None = None,
) -> TaskExecutionOutcome:
    """Unwrap a completed task's result, handling cancellation gracefully.

    This helper can be shared between engines to handle task outcome unwrapping.

    Args:
        fut: The completed asyncio task.
        task_id: ID of the task for error reporting.
        now_func: Function to get current timestamp.

    Returns:
        Task result or a cancelled/failed outcome.
    """
    # Import here to avoid circular imports at module level
    from .engine import TaskExecutionOutcome

    _now_func = now_func if now_func is not None else _now
    try:
        result = fut.result()
        # The task result should be TaskExecutionOutcome but is typed as Any
        return result  # type: ignore[return-value]
    except asyncio.CancelledError:
        now = _now_func()
        return TaskExecutionOutcome(task_id, WorkflowTaskStatus.CANCELLED.value, 0, now, now, error="Task cancelled")
    except (RuntimeError, ValueError) as exc:
        now = _now_func()
        return TaskExecutionOutcome(task_id, WorkflowTaskStatus.FAILED.value, 0, now, now, error=str(exc))


async def cancel_running_tasks(
    running: dict[str, asyncio.Task[Any]],
    timeout: float = 5.0,
) -> None:
    """Cancel running tasks and wait for them to settle.

    Issues task.cancel() to all running tasks, then waits for cancellation
    to propagate using wait_for with a grace period. This avoids orphaning
    tasks that are stuck in blocking synchronous code.

    This helper can be shared between engines.

    Args:
        running: Dictionary of task_id -> asyncio.Task.
        timeout: Grace period in seconds for cancellation to settle.
    """
    if not running:
        return
    for task in running.values():
        task.cancel()
    # Wait for cancellation to propagate with a grace period.
    # Use wait() to avoid blocking indefinitely on uncooperative tasks.
    _done, pending = await asyncio.wait(
        list(running.values()),
        timeout=timeout,
        return_when=asyncio.ALL_COMPLETED,
    )
    # Cancel any tasks that didn't finish within the grace period
    for task in pending:
        task.cancel()
    if pending:
        # Wait for the second wave of cancellations
        await asyncio.wait(list(running.values()), timeout=2.0, return_when=asyncio.ALL_COMPLETED)
    running.clear()


async def invoke_handler(
    handler: Any,
    *,
    workflow_id: str,
    payload: dict[str, Any],
    timeout_seconds: float,
    runtime_engine: Any | None = None,
) -> Any:
    """Invoke a workflow or activity handler with flexible signature support.

    This helper can be shared between engines. It handles multiple handler
    signatures gracefully:
    - handler(workflow_id, payload, runtime_engine)
    - handler(workflow_id, payload)
    - handler(payload)

    Args:
        handler: The handler callable to invoke.
        workflow_id: ID of the workflow.
        payload: Input payload for the handler.
        timeout_seconds: Timeout for async handlers.
        runtime_engine: Optional runtime engine reference.

    Returns:
        Handler result (already awaited if async).

    Raises:
        asyncio.TimeoutError: If handler exceeds timeout.
    """
    try:
        result = handler(
            workflow_id=workflow_id,
            payload=payload,
            runtime_engine=runtime_engine,
        )
    except TypeError:
        try:
            result = handler(workflow_id, payload)
        except TypeError:
            result = handler(payload)
    if inspect.isawaitable(result):
        return await asyncio.wait_for(result, timeout=timeout_seconds)
    return result


# ---------------------------------------------------------------------------
# Resume Workflow Helpers
# ---------------------------------------------------------------------------


async def load_persisted_task_states(
    store: Any,
    workflow_id: str,
) -> dict[str, Any]:
    """Load persisted task states from store and index by task_id.

    Args:
        store: WorkflowRuntimeStorePort implementation.
        workflow_id: ID of the workflow.

    Returns:
        Dictionary mapping task_id to persisted task state.
    """
    persisted_states = await store.list_task_states(workflow_id)
    persisted_by_id: dict[str, Any] = {}
    for s in persisted_states:
        raw_tid = getattr(s, "task_id", None)
        task_id_key = str(raw_tid) if raw_tid is not None else ""
        if task_id_key:
            persisted_by_id[task_id_key] = s
    return persisted_by_id


def extract_existing_payload(existing: Any) -> dict[str, Any]:
    """Extract normalized payload from existing execution record.

    Args:
        existing: Existing execution record (dataclass or dict).

    Returns:
        Normalized payload dict or empty dict if not found.
    """
    if existing is None:
        return {}
    existing_payload = (
        getattr(existing, "payload", None)
        if hasattr(existing, "payload")
        else (existing.get("payload") if isinstance(existing, dict) else None)
    )
    return existing_payload if isinstance(existing_payload, dict) else {}


def normalize_resume_payload(
    payload: dict[str, Any] | None,
    existing_payload: dict[str, Any],
) -> dict[str, Any]:
    """Normalize payload for resume, preferring new payload over existing.

    Args:
        payload: New payload provided at resume time (may be None).
        existing_payload: Original payload from persisted execution.

    Returns:
        Normalized payload dict.
    """
    if isinstance(payload, dict):
        return payload
    return existing_payload if isinstance(existing_payload, dict) else {}
