"""Tests for Dead Letter Queue implementation.

Covers:
- InMemoryDeadLetterQueue enqueue/dequeue/requeue/size/peek/clear
- DeadLetterItem.to_dict()
- DLQRequeueWorker start/stop/process_one/process_all
- WorkflowDLQRetryHandler resume integration
- append_dlq_event()
- RequeueStrategy enum
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest
from polaris.kernelone.workflow.dlq import (
    DeadLetterItem,
    DLQReason,
    DLQRequeueWorker,
    InMemoryDeadLetterQueue,
    RequeueStrategy,
    WorkflowDLQRetryHandler,
    append_dlq_event,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_item() -> DeadLetterItem:
    """Create a sample DeadLetterItem for testing."""
    return DeadLetterItem(
        task_id="task-1",
        workflow_id="wf-1",
        handler_name="test_handler",
        input_payload={"key": "value"},
        error="task failed after all retries",
        failed_at="2026-04-04T00:00:00Z",
        dlq_at="2026-04-04T00:01:00Z",
        attempt=3,
        max_attempts=3,
        dlq_reason=DLQReason.RETRY_EXHAUSTED,
        metadata={"task_type": "activity", "workflow_name": "test_workflow"},
    )


@pytest.fixture
def dlq() -> InMemoryDeadLetterQueue:
    """Create a fresh InMemoryDeadLetterQueue."""
    return InMemoryDeadLetterQueue(maxsize=100)


# ---------------------------------------------------------------------------
# DeadLetterItem
# ---------------------------------------------------------------------------


def test_dead_letter_item_to_dict(sample_item: DeadLetterItem) -> None:
    """DeadLetterItem.to_dict() must return all fields as a flat dict."""
    d = sample_item.to_dict()
    assert d["task_id"] == "task-1"
    assert d["workflow_id"] == "wf-1"
    assert d["handler_name"] == "test_handler"
    assert d["input_payload"] == {"key": "value"}
    assert d["error"] == "task failed after all retries"
    assert d["failed_at"] == "2026-04-04T00:00:00Z"
    assert d["dlq_at"] == "2026-04-04T00:01:00Z"
    assert d["attempt"] == 3
    assert d["max_attempts"] == 3
    assert d["dlq_reason"] == "retry_exhausted"
    assert d["metadata"] == {"task_type": "activity", "workflow_name": "test_workflow"}


def test_dead_letter_item_metadata_default() -> None:
    """DeadLetterItem metadata defaults to empty dict."""
    item = DeadLetterItem(
        task_id="t",
        workflow_id="w",
        handler_name="h",
        input_payload={},
        error="err",
        failed_at="2026-04-04T00:00:00Z",
        dlq_at="2026-04-04T00:00:00Z",
        attempt=1,
        max_attempts=3,
        dlq_reason=DLQReason.UNKNOWN,
    )
    assert item.metadata == {}


# ---------------------------------------------------------------------------
# InMemoryDeadLetterQueue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dlq_enqueue_single(dlq: InMemoryDeadLetterQueue, sample_item: DeadLetterItem) -> None:
    """enqueue() must add item to queue and size reflects count."""
    assert await dlq.size() == 0
    await dlq.enqueue(sample_item)
    assert await dlq.size() == 1


@pytest.mark.asyncio
async def test_dlq_dequeue_single(dlq: InMemoryDeadLetterQueue, sample_item: DeadLetterItem) -> None:
    """dequeue() must return and remove the enqueued item."""
    await dlq.enqueue(sample_item)
    retrieved = await dlq.dequeue(timeout=0.5)
    assert retrieved is not None
    assert retrieved.task_id == sample_item.task_id
    assert retrieved.workflow_id == sample_item.workflow_id
    assert await dlq.size() == 0


@pytest.mark.asyncio
async def test_dlq_dequeue_empty_returns_none(dlq: InMemoryDeadLetterQueue) -> None:
    """dequeue() must return None when queue is empty."""
    result = await dlq.dequeue(timeout=0.05)
    assert result is None


@pytest.mark.asyncio
async def test_dlq_dequeue_fifo_order(dlq: InMemoryDeadLetterQueue) -> None:
    """dequeue() must return items in FIFO order."""
    items = [
        DeadLetterItem(
            task_id=f"task-{i}",
            workflow_id="wf-1",
            handler_name="h",
            input_payload={},
            error="err",
            failed_at="2026-04-04T00:00:00Z",
            dlq_at="2026-04-04T00:00:00Z",
            attempt=1,
            max_attempts=3,
            dlq_reason=DLQReason.RETRY_EXHAUSTED,
        )
        for i in range(3)
    ]
    for item in items:
        await dlq.enqueue(item)

    for expected in items:
        retrieved = await dlq.dequeue(timeout=0.5)
        assert retrieved is not None
        assert retrieved.task_id == expected.task_id


@pytest.mark.asyncio
async def test_dlq_requeue_resets_attempt(dlq: InMemoryDeadLetterQueue, sample_item: DeadLetterItem) -> None:
    """requeue() must create a new item with attempt=0."""
    await dlq.enqueue(sample_item)
    await dlq.requeue(sample_item, delay_seconds=0)
    assert await dlq.size() == 2

    # The requeued item should have attempt=0
    retrieved = await dlq.dequeue(timeout=0.5)
    assert retrieved is not None
    assert retrieved.task_id == sample_item.task_id
    # Only the requeued one has attempt=0
    items = []
    while True:
        item = await dlq.dequeue(timeout=0.05)
        if item is None:
            break
        items.append(item)
    assert len(items) == 1
    assert items[0].attempt == 0


@pytest.mark.asyncio
async def test_dlq_requeue_with_delay(dlq: InMemoryDeadLetterQueue, sample_item: DeadLetterItem) -> None:
    """requeue() with delay_seconds > 0 must sleep before re-enqueueing the item."""
    await dlq.enqueue(sample_item)

    # Start requeue but don't await yet - to test that the item is not immediately available
    requeue_task = asyncio.create_task(dlq.requeue(sample_item, delay_seconds=0.1))

    # During the sleep, the queue should still have only the original item
    # (we can't easily check mid-await, but we can verify order after completion)

    # Now await the requeue - sleep completes and new item is enqueued
    await requeue_task

    # After requeue completes, we should have 2 items: original first, then requeued copy
    first = await dlq.dequeue(timeout=0.5)
    second = await dlq.dequeue(timeout=0.5)

    assert first is not None
    assert second is not None
    assert first.task_id == sample_item.task_id
    assert second.task_id == sample_item.task_id
    # Original has the original attempt; requeued copy has attempt=0
    assert first.attempt == sample_item.attempt
    assert second.attempt == 0


@pytest.mark.asyncio
async def test_dlq_peek_does_not_remove(dlq: InMemoryDeadLetterQueue, sample_item: DeadLetterItem) -> None:
    """peek() must not remove items from the queue."""
    await dlq.enqueue(sample_item)
    peeked = await dlq.peek(limit=10)
    assert len(peeked) == 1
    assert await dlq.size() == 1


@pytest.mark.asyncio
async def test_dlq_clear(dlq: InMemoryDeadLetterQueue, sample_item: DeadLetterItem) -> None:
    """clear() must remove all items and return the count."""
    for _ in range(5):
        await dlq.enqueue(sample_item)

    assert await dlq.size() == 5
    cleared = await dlq.clear()
    assert cleared == 5
    assert await dlq.size() == 0


# ---------------------------------------------------------------------------
# DLQRequeueWorker
# ---------------------------------------------------------------------------


class MockRetryHandler:
    """Records calls for assertions."""

    def __init__(self, strategy: RequeueStrategy = RequeueStrategy.RETRY_NOW) -> None:
        self._strategy = strategy
        self.calls: list[DeadLetterItem] = []

    async def __call__(self, item: DeadLetterItem) -> RequeueStrategy:
        self.calls.append(item)
        return self._strategy


@pytest.mark.asyncio
async def test_worker_process_one_requeues(
    dlq: InMemoryDeadLetterQueue,
    sample_item: DeadLetterItem,
) -> None:
    """process_one() must call retry_handler and requeue on RETRY_NOW."""
    handler = MockRetryHandler(RequeueStrategy.RETRY_NOW)
    worker = DLQRequeueWorker(
        dlq=dlq,
        workflow_id="wf-1",
        workflow_name="test_workflow",
        retry_handler=handler,
        poll_interval=60.0,
        max_requeue_attempts=3,
    )

    await dlq.enqueue(sample_item)
    result = await worker.process_one()

    assert result is True
    assert len(handler.calls) == 1
    assert handler.calls[0].task_id == sample_item.task_id


@pytest.mark.asyncio
async def test_worker_process_one_rejects(
    dlq: InMemoryDeadLetterQueue,
    sample_item: DeadLetterItem,
) -> None:
    """process_one() must NOT requeue on REJECT strategy."""
    handler = MockRetryHandler(RequeueStrategy.REJECT)
    worker = DLQRequeueWorker(
        dlq=dlq,
        workflow_id="wf-1",
        workflow_name="test_workflow",
        retry_handler=handler,
        poll_interval=60.0,
        max_requeue_attempts=3,
    )

    await dlq.enqueue(sample_item)
    result = await worker.process_one()

    assert result is True
    assert len(handler.calls) == 1
    # Item should NOT be requeued on REJECT
    assert await dlq.size() == 0


@pytest.mark.asyncio
async def test_worker_process_one_empty_queue(dlq: InMemoryDeadLetterQueue) -> None:
    """process_one() must return False when queue is empty."""
    handler = MockRetryHandler()
    worker = DLQRequeueWorker(
        dlq=dlq,
        workflow_id="wf-1",
        workflow_name="test_workflow",
        retry_handler=handler,
        poll_interval=60.0,
        max_requeue_attempts=3,
    )

    result = await worker.process_one()
    assert result is False


@pytest.mark.asyncio
async def test_worker_max_requeue_attempts(dlq: InMemoryDeadLetterQueue, sample_item: DeadLetterItem) -> None:
    """process_one() must reject after max_requeue_attempts is exceeded."""
    handler = MockRetryHandler(RequeueStrategy.RETRY_NOW)
    worker = DLQRequeueWorker(
        dlq=dlq,
        workflow_id="wf-1",
        workflow_name="test_workflow",
        retry_handler=handler,
        poll_interval=60.0,
        max_requeue_attempts=2,
    )

    # Simulate multiple requeue attempts by manually manipulating requeue_counts
    worker._requeue_counts[sample_item.task_id] = 2  # Already at limit

    await dlq.enqueue(sample_item)
    result = await worker.process_one()

    assert result is True  # Processed (rejected, not requeued)
    # retry_handler should NOT be called when max is exceeded
    assert len(handler.calls) == 0


@pytest.mark.asyncio
async def test_worker_process_all(
    dlq: InMemoryDeadLetterQueue,
    sample_item: DeadLetterItem,
) -> None:
    """process_all() must process up to limit items."""
    handler = MockRetryHandler(RequeueStrategy.REJECT)

    # Enqueue 3 items
    for i in range(3):
        item = DeadLetterItem(
            task_id=f"task-{i}",
            workflow_id="wf-1",
            handler_name="h",
            input_payload={},
            error="err",
            failed_at="2026-04-04T00:00:00Z",
            dlq_at="2026-04-04T00:00:00Z",
            attempt=1,
            max_attempts=3,
            dlq_reason=DLQReason.RETRY_EXHAUSTED,
        )
        await dlq.enqueue(item)

    worker = DLQRequeueWorker(
        dlq=dlq,
        workflow_id="wf-1",
        workflow_name="test_workflow",
        retry_handler=handler,
        poll_interval=60.0,
        max_requeue_attempts=3,
    )

    processed = await worker.process_all(limit=10)
    assert processed == 3
    assert len(handler.calls) == 3
    assert await dlq.size() == 0


@pytest.mark.asyncio
async def test_worker_requeue_stats(dlq: InMemoryDeadLetterQueue, sample_item: DeadLetterItem) -> None:
    """requeue_stats must reflect requeue attempt counts per task."""
    handler = MockRetryHandler(RequeueStrategy.RETRY_NOW)
    worker = DLQRequeueWorker(
        dlq=dlq,
        workflow_id="wf-1",
        workflow_name="test_workflow",
        retry_handler=handler,
        poll_interval=60.0,
        max_requeue_attempts=3,
    )

    await dlq.enqueue(sample_item)
    await worker.process_one()

    stats = worker.requeue_stats
    assert stats[sample_item.task_id] == 1


# ---------------------------------------------------------------------------
# WorkflowDLQRetryHandler
# ---------------------------------------------------------------------------


@dataclass
class MockResumeResult:
    submitted: bool
    status: str
    error: str = ""


class MockEngine:
    def __init__(self, submit_result: MockResumeResult = None) -> None:
        self.calls: list[tuple[str, str, Any]] = []  # (workflow_name, workflow_id, payload)
        self._result = submit_result or MockResumeResult(submitted=True, status="started")

    async def resume_workflow(
        self,
        workflow_name: str,
        workflow_id: str,
        payload: Any = None,
    ) -> MockResumeResult:
        self.calls.append((workflow_name, workflow_id, payload))
        return self._result


@pytest.mark.asyncio
async def test_workflow_dlq_retry_handler_resumes(
    sample_item: DeadLetterItem,
) -> None:
    """WorkflowDLQRetryHandler must call engine.resume_workflow() on RETRY_NOW."""
    engine = MockEngine()
    handler = WorkflowDLQRetryHandler(engine=engine, default_workflow_name="default_wf")

    strategy = await handler(sample_item)

    assert strategy == RequeueStrategy.RETRY_NOW
    assert len(engine.calls) == 1
    name, wid, _payload = engine.calls[0]
    assert name == "test_workflow"  # from metadata
    assert wid == "wf-1"


@pytest.mark.asyncio
async def test_workflow_dlq_retry_handler_uses_metadata_workflow_name(
    sample_item: DeadLetterItem,
) -> None:
    """Workflow name must be read from item.metadata["workflow_name"]."""
    engine = MockEngine()
    handler = WorkflowDLQRetryHandler(engine=engine, default_workflow_name="ignored")

    await handler(sample_item)

    name, _, _ = engine.calls[0]
    assert name == "test_workflow"  # from metadata, not default


@pytest.mark.asyncio
async def test_workflow_dlq_retry_handler_rejects_when_not_submitted(
    sample_item: DeadLetterItem,
) -> None:
    """Must return REJECT when resume_workflow returns submitted=False."""
    engine = MockEngine(MockResumeResult(submitted=False, status="already_running", error="wf already running"))
    handler = WorkflowDLQRetryHandler(engine=engine)

    strategy = await handler(sample_item)

    assert strategy == RequeueStrategy.REJECT


@pytest.mark.asyncio
async def test_workflow_dlq_retry_handler_rejects_on_exception(
    sample_item: DeadLetterItem,
) -> None:
    """Must return REJECT when resume_workflow raises."""

    async def failing_resume(
        workflow_name: str,
        workflow_id: str,
        payload: Any = None,
    ) -> MockResumeResult:
        raise RuntimeError("engine error")

    engine = MockEngine()
    engine.resume_workflow = failing_resume  # type: ignore[assignment]
    handler = WorkflowDLQRetryHandler(engine=engine)

    strategy = await handler(sample_item)

    assert strategy == RequeueStrategy.REJECT


@pytest.mark.asyncio
async def test_workflow_dlq_retry_handler_rejects_unknown_workflow(
    sample_item: DeadLetterItem,
) -> None:
    """Must return REJECT when workflow_name is unknown and no default."""
    # Create item with no workflow_name in metadata
    item = DeadLetterItem(
        task_id="task-1",
        workflow_id="wf-1",
        handler_name="h",
        input_payload={},
        error="err",
        failed_at="2026-04-04T00:00:00Z",
        dlq_at="2026-04-04T00:00:00Z",
        attempt=1,
        max_attempts=3,
        dlq_reason=DLQReason.RETRY_EXHAUSTED,
        metadata={},  # No workflow_name
    )
    engine = MockEngine()
    handler = WorkflowDLQRetryHandler(engine=engine, default_workflow_name="")  # No default either

    strategy = await handler(item)

    assert strategy == RequeueStrategy.REJECT
    assert len(engine.calls) == 0


@pytest.mark.asyncio
async def test_workflow_dlq_retry_handler_uses_default_when_missing(
    sample_item: DeadLetterItem,
) -> None:
    """Must use default_workflow_name when metadata lacks workflow_name."""
    # item without workflow_name in metadata
    item = DeadLetterItem(
        task_id="task-1",
        workflow_id="wf-1",
        handler_name="h",
        input_payload={},
        error="err",
        failed_at="2026-04-04T00:00:00Z",
        dlq_at="2026-04-04T00:00:00Z",
        attempt=1,
        max_attempts=3,
        dlq_reason=DLQReason.RETRY_EXHAUSTED,
        metadata={},
    )
    engine = MockEngine()
    handler = WorkflowDLQRetryHandler(engine=engine, default_workflow_name="fallback_wf")

    await handler(item)

    name, _, _ = engine.calls[0]
    assert name == "fallback_wf"


# ---------------------------------------------------------------------------
# append_dlq_event
# ---------------------------------------------------------------------------


class MockEventStore:
    """Minimal EventStore mock for append_dlq_event testing."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    async def append_event(
        self,
        workflow_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        self.events.append((workflow_id, event_type, payload))


@pytest.mark.asyncio
async def test_append_dlq_event_writes_task_dead_lettered(
    sample_item: DeadLetterItem,
) -> None:
    """append_dlq_event must write a task_dead_lettered event."""
    store = MockEventStore()
    await append_dlq_event(store, "wf-1", sample_item)

    assert len(store.events) == 1
    wf_id, event_type, payload = store.events[0]
    assert wf_id == "wf-1"
    assert event_type == "task_dead_lettered"
    assert payload["task_id"] == "task-1"
    assert payload["workflow_id"] == "wf-1"
    assert payload["dlq_reason"] == "retry_exhausted"


# ---------------------------------------------------------------------------
# RequeueStrategy & DLQReason enums
# ---------------------------------------------------------------------------


def test_requeue_strategy_values() -> None:
    """RequeueStrategy enum must have expected values."""
    assert RequeueStrategy.RETRY_NOW.value == "retry_now"
    assert RequeueStrategy.REJECT.value == "reject"


def test_dlq_reason_values() -> None:
    """DLQReason enum must have all expected variants."""
    assert DLQReason.RETRY_EXHAUSTED.value == "retry_exhausted"
    assert DLQReason.CIRCUIT_BREAKER_OPEN.value == "circuit_breaker_open"
    assert DLQReason.WORKFLOW_CANCELLED.value == "workflow_cancelled"
    assert DLQReason.WORKFLOW_TIMEOUT.value == "workflow_timeout"
    assert DLQReason.TASK_CANCELLED.value == "task_cancelled"
    assert DLQReason.UNKNOWN.value == "unknown"
