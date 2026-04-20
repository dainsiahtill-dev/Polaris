"""Dead Letter Queue for Workflow Task Failures.

When a task exhausts all retry attempts, it is routed to the DLQ instead of
silently failing. The DLQ provides:
- `enqueue`: Add a failed task to the queue
- `dequeue`: Retrieve a dead-lettered task for reprocessing
- `requeue`: Re-submit a task for retry
- `peek`: Inspect DLQ contents without consuming

The DLQ is designed to be swapped for a persistent implementation (e.g., SQLite,
Redis) via the DeadLetterQueuePort interface, without changing the WorkflowEngine.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

from polaris.kernelone.utils.time_utils import _now as _get_timestamp

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class DLQReason(str, Enum):
    """Reason why a task was routed to the DLQ."""

    RETRY_EXHAUSTED = "retry_exhausted"
    CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"
    WORKFLOW_CANCELLED = "workflow_cancelled"
    WORKFLOW_TIMEOUT = "workflow_timeout"
    TASK_CANCELLED = "task_cancelled"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DeadLetterItem:
    """Represents a task that has been routed to the dead letter queue.

    Attributes:
        task_id: Unique identifier of the task.
        workflow_id: ID of the workflow this task belongs to.
        handler_name: Name of the handler/activity that failed.
        input_payload: The input data passed to the task.
        error: Error message from the final failure.
        failed_at: ISO timestamp of when the task first failed.
        dlq_at: ISO timestamp of when the task was enqueued to DLQ.
        attempt: Final attempt number (1-indexed).
        max_attempts: Maximum retry attempts configured.
        dlq_reason: Reason for DLQ routing.
        metadata: Additional context for debugging/retry.
    """

    task_id: str
    workflow_id: str
    handler_name: str
    input_payload: dict[str, Any]
    error: str
    failed_at: str
    dlq_at: str
    attempt: int
    max_attempts: int
    dlq_reason: DLQReason = DLQReason.UNKNOWN
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "workflow_id": self.workflow_id,
            "handler_name": self.handler_name,
            "input_payload": self.input_payload,
            "error": self.error,
            "failed_at": self.failed_at,
            "dlq_at": self.dlq_at,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "dlq_reason": self.dlq_reason.value,
            "metadata": self.metadata,
        }


class DeadLetterQueuePort(Protocol):
    """Protocol for dead letter queue implementations.

    Implement this protocol to provide a persistent DLQ backed by SQLite,
    Redis, or any other storage mechanism.
    """

    async def enqueue(self, item: DeadLetterItem) -> None:
        """Add a dead letter item to the queue."""
        ...

    async def dequeue(self, timeout: float = 0.1) -> DeadLetterItem | None:
        """Retrieve and remove the next dead letter item.

        Args:
            timeout: Maximum seconds to wait for an item.

        Returns:
            The next DeadLetterItem, or None if queue is empty.
        """
        ...

    async def requeue(self, item: DeadLetterItem, delay_seconds: float = 0) -> None:
        """Re-submit a dead letter item for retry.

        Args:
            item: The item to requeue.
            delay_seconds: Optional delay before the item becomes available.
        """
        ...

    async def size(self) -> int:
        """Return the current number of items in the DLQ."""
        ...

    async def peek(self, limit: int = 10) -> list[DeadLetterItem]:
        """Inspect DLQ contents without removing items.

        Args:
            limit: Maximum number of items to return.

        Returns:
            List of DeadLetterItems (oldest first).
        """
        ...

    async def clear(self) -> int:
        """Clear all items from the DLQ.

        Returns:
            Number of items removed.
        """
        ...


class InMemoryDeadLetterQueue:
    """In-memory implementation of DeadLetterQueuePort.

    Suitable for single-instance workflows. For multi-instance or
    persistent DLQ, implement DeadLetterQueuePort protocol with SQLite/Redis.
    """

    def __init__(self, maxsize: int = 10000) -> None:
        self._queue: asyncio.Queue[DeadLetterItem] = asyncio.Queue(maxsize=maxsize)
        self._maxsize = max(1, maxsize)
        self._all_items: list[DeadLetterItem] = []
        self._lock = asyncio.Lock()
        logger.info("InMemoryDeadLetterQueue initialized (maxsize=%d)", self._maxsize)

    async def enqueue(self, item: DeadLetterItem) -> None:
        """Enqueue a dead letter item."""
        await self._queue.put(item)
        async with self._lock:
            self._all_items.append(item)
        logger.warning(
            "Task %s enqueued to DLQ (workflow=%s, reason=%s, attempts=%d/%d)",
            item.task_id,
            item.workflow_id,
            item.dlq_reason.value,
            item.attempt,
            item.max_attempts,
        )

    async def dequeue(self, timeout: float = 0.1) -> DeadLetterItem | None:
        """Dequeue the next dead letter item."""
        try:
            item = await asyncio.wait_for(self._queue.get(), timeout=max(0.0, timeout))
            return item
        except asyncio.TimeoutError:
            return None

    async def requeue(self, item: DeadLetterItem, delay_seconds: float = 0) -> None:
        """Requeue a dead letter item with optional delay."""
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
        await self.enqueue(
            DeadLetterItem(
                task_id=item.task_id,
                workflow_id=item.workflow_id,
                handler_name=item.handler_name,
                input_payload=item.input_payload,
                error=item.error,
                failed_at=item.failed_at,
                dlq_at=_get_timestamp(),
                attempt=0,  # Reset attempt counter for re-retry
                max_attempts=item.max_attempts,
                dlq_reason=DLQReason.RETRY_EXHAUSTED,
                metadata={**item.metadata, "requeued": True, "original_attempt": item.attempt},
            )
        )

    async def size(self) -> int:
        """Return current queue size."""
        return self._queue.qsize()

    async def peek(self, limit: int = 10) -> list[DeadLetterItem]:
        """Peek at the oldest items in the queue without removing them."""
        async with self._lock:
            return list(self._all_items[: max(1, limit)])

    async def clear(self) -> int:
        """Clear all items from the queue."""
        count = self._queue.qsize()
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        async with self._lock:
            self._all_items.clear()
        logger.info("DLQ cleared (%d items removed)", count)
        return count


# ---------------------------------------------------------------------------
# DLQ Event Helper (used by WorkflowEngine)
# ---------------------------------------------------------------------------


class EventStorePort(Protocol):
    """Minimal protocol for stores that can append workflow events."""

    async def append_event(
        self,
        workflow_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None: ...


async def append_dlq_event(
    store: EventStorePort,
    workflow_id: str,
    item: DeadLetterItem,
) -> None:
    """Append a task_dead_lettered event to the workflow store.

    This persists the DLQ routing in the workflow's event log for auditability.

    Args:
        store: WorkflowRuntimeStorePort implementing append_event.
        workflow_id: ID of the workflow.
        item: The DeadLetterItem that was enqueued.
    """
    await store.append_event(
        workflow_id,
        "task_dead_lettered",
        item.to_dict(),
    )


# ---------------------------------------------------------------------------
# DLQ Requeue Worker
# ---------------------------------------------------------------------------


class RequeueStrategy(str, Enum):
    """Strategy for handling DLQ items during requeue."""

    RETRY_NOW = "retry_now"  # Immediately retry the task
    REJECT = "reject"  # Keep in DLQ, do not retry


class DLQRequeueWorker:
    """Background worker that processes dead-lettered tasks.

    Polls the DLQ at a configurable interval and re-submits tasks
    for execution. Tasks that fail repeatedly are eventually rejected.

    The worker integrates with WorkflowEngine via ``resume_workflow``,
    which restores the workflow state from persisted store and continues
    execution from where it left off (skipping completed tasks).

    Example:
        worker = DLQRequeueWorker(
            dlq=dlq,
            workflow_id=workflow_id,
            max_requeue_attempts=3,
            poll_interval=10.0,
        )
        await worker.start()  # runs until stop() is called
    """

    def __init__(
        self,
        dlq: DeadLetterQueuePort,
        workflow_id: str,
        workflow_name: str,
        retry_handler: Callable[[DeadLetterItem], Awaitable[RequeueStrategy]],
        poll_interval: float = 10.0,
        max_requeue_attempts: int = 3,
    ) -> None:
        """Initialize the DLQ requeue worker.

        Args:
            dlq: The dead letter queue to process.
            workflow_id: ID of the workflow this worker serves.
            workflow_name: Name of the workflow for re-submission.
            retry_handler: Async callable that decides what to do with a DLQ item.
                Called with each dequeued item; returns RequeueStrategy.
            poll_interval: Seconds between polling cycles.
            max_requeue_attempts: Maximum times a single task can be requeued
                before being permanently rejected.
        """
        self._dlq = dlq
        self._workflow_id = workflow_id
        self._workflow_name = workflow_name
        self._retry_handler = retry_handler
        self._poll_interval = max(0.1, float(poll_interval))
        self._max_requeue_attempts = max(1, int(max_requeue_attempts))
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        # Track requeue counts per task to enforce max_requeue_attempts
        self._requeue_counts: dict[str, int] = {}

    async def start(self) -> None:
        """Start the background worker loop.

        Idempotent: calling start() while already running is a no-op.
        """
        async with self._lock:
            if self._running:
                return
            self._running = True
            self._task = asyncio.create_task(self._run_loop())
            logger.info(
                "DLQRequeueWorker started for workflow %s (poll_interval=%.1fs, max_requeue=%d)",
                self._workflow_id,
                self._poll_interval,
                self._max_requeue_attempts,
            )

    async def stop(self) -> None:
        """Stop the background worker loop.

        Waits for the current processing cycle to finish before returning.
        """
        async with self._lock:
            if not self._running:
                return
            self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("DLQRequeueWorker stopped for workflow %s", self._workflow_id)

    async def process_one(self) -> bool:
        """Process a single DLQ item (one polling cycle).

        This method can be called manually for testing or on-demand processing,
        independent of the background loop.

        Returns:
            True if an item was processed, False if the DLQ was empty.
        """
        item = await self._dlq.dequeue(timeout=0.1)
        if item is None:
            return False

        # Check requeue count
        count = self._requeue_counts.get(item.task_id, 0)
        if count >= self._max_requeue_attempts:
            logger.warning(
                "Task %s in workflow %s exceeded max requeue attempts (%d), permanently rejecting",
                item.task_id,
                item.workflow_id,
                self._max_requeue_attempts,
            )
            return True

        strategy = await self._retry_handler(item)
        if strategy == RequeueStrategy.RETRY_NOW:
            self._requeue_counts[item.task_id] = count + 1
            await self._dlq.requeue(item, delay_seconds=0)
            logger.info(
                "Task %s requeued for retry (attempt %d/%d)",
                item.task_id,
                count + 1,
                self._max_requeue_attempts,
            )
        else:
            # REJECT: leave in DLQ (do not re-enqueue), increment count to track
            self._requeue_counts[item.task_id] = count + 1
            logger.info(
                "Task %s rejected by retry handler, remains in DLQ (attempt %d/%d)",
                item.task_id,
                count + 1,
                self._max_requeue_attempts,
            )
        return True

    async def process_all(self, limit: int = 100) -> int:
        """Process all available DLQ items up to ``limit``.

        Args:
            limit: Maximum number of items to process in this batch.

        Returns:
            Number of items actually processed.
        """
        processed = 0
        for _ in range(limit):
            if not await self.process_one():
                break
            processed += 1
        return processed

    async def _run_loop(self) -> None:
        """Main worker loop. Runs until stop() is called."""
        while self._running:
            try:
                await asyncio.sleep(self._poll_interval)
                if not self._running:
                    break
                await self.process_one()
            except asyncio.CancelledError:
                break
            except (RuntimeError, ValueError):
                logger.exception(
                    "DLQRequeueWorker error processing workflow %s",
                    self._workflow_id,
                )

    @property
    def requeue_stats(self) -> dict[str, int]:
        """Return requeue attempt counts per task."""
        return dict(self._requeue_counts)


# ---------------------------------------------------------------------------
# WorkflowDLQRetryHandler - Integrates DLQ Requeue Worker with WorkflowEngine
# ---------------------------------------------------------------------------


class WorkflowDLQRetryHandler:
    """Retry handler that resumes a workflow from a dead-lettered task.

    This handler integrates ``DLQRequeueWorker`` with ``WorkflowEngine.resume_workflow``,
    enabling automatic workflow recovery after tasks land in the DLQ.

    The handler extracts ``workflow_name`` from ``item.metadata["workflow_name"]``
    (set when the ``DeadLetterItem`` is created in ``_execute_spec`` and ``_enqueue_pending_to_dlq``).

    Example:
        handler = WorkflowDLQRetryHandler(
            engine=engine,
            default_workflow_name="my_workflow",
        )
        worker = DLQRequeueWorker(
            dlq=dlq,
            workflow_id=workflow_id,
            workflow_name="my_workflow",
            retry_handler=handler,
        )
        await worker.start()
    """

    def __init__(
        self,
        engine: Any,
        default_workflow_name: str | None = None,
    ) -> None:
        """Initialize the retry handler.

        Args:
            engine: WorkflowEngine instance to use for resume_workflow calls.
            default_workflow_name: Fallback workflow name if not found in payload.
        """
        self._engine = engine
        self._default_workflow_name = default_workflow_name or ""

    async def __call__(self, item: DeadLetterItem) -> RequeueStrategy:
        """Handle a dead-lettered task by resuming its workflow.

        Args:
            item: The DeadLetterItem to process.

        Returns:
            RequeueStrategy.RETRY_NOW if resume was submitted successfully,
            RequeueStrategy.REJECT if resume failed.
        """
        workflow_id = item.workflow_id
        # workflow_name is stored in metadata by _execute_spec / _enqueue_pending_to_dlq
        workflow_name = (
            item.metadata.get("workflow_name") if isinstance(item.metadata, dict) else None
        ) or self._default_workflow_name

        if not workflow_name:
            logger.error(
                "Cannot retry task %s in workflow %s: workflow_name unknown and no default",
                item.task_id,
                workflow_id,
            )
            return RequeueStrategy.REJECT

        try:
            result = await self._engine.resume_workflow(
                workflow_name=workflow_name,
                workflow_id=workflow_id,
                payload=None,  # Use persisted task states directly
            )
            if result.submitted:
                logger.info(
                    "Workflow %s resumed for DLQ task %s (attempt %d)",
                    workflow_id,
                    item.task_id,
                    item.attempt,
                )
                return RequeueStrategy.RETRY_NOW
            else:
                logger.warning(
                    "resume_workflow for %s returned status=%s: %s",
                    workflow_id,
                    result.status,
                    result.error,
                )
                return RequeueStrategy.REJECT
        except (RuntimeError, ValueError):
            logger.exception(
                "Failed to resume workflow %s for DLQ task %s",
                workflow_id,
                item.task_id,
            )
            return RequeueStrategy.REJECT


__all__ = [
    "DLQReason",
    "DLQRequeueWorker",
    "DeadLetterItem",
    "DeadLetterQueuePort",
    "InMemoryDeadLetterQueue",
    "RequeueStrategy",
    "WorkflowDLQRetryHandler",
    "append_dlq_event",
]
