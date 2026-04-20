"""Task Queue - in-memory runtime task queues.

The current runtime model uses one canonical ``asyncio.Queue`` per logical task
queue name. This removes the old producer/waiter split that caused consumers to
block on a different queue than producers were writing to.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Task:
    """Task envelope stored in the runtime queue."""

    task_id: str
    task_queue: str
    payload: dict[str, Any]
    created_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    priority: int = 0


@dataclass
class TaskResult:
    """Task completion result."""

    task_id: str
    success: bool
    result: Any = None
    error: str | None = None


@dataclass(order=True)
class _QueuedTask:
    """Priority queue entry.

    ``asyncio.PriorityQueue`` retrieves the lowest item first, so
    ``priority_key`` stores the negative priority to preserve the public
    contract where higher priority values should run before lower ones.
    """

    priority_key: int
    sequence: int
    task: Task = field(compare=False)


class TaskQueue:
    """In-memory task queues keyed by logical queue name."""

    def __init__(self, name: str = "default", maxsize: int = 1000) -> None:
        self._name = str(name or "default").strip() or "default"
        self._queues: dict[str, asyncio.PriorityQueue[_QueuedTask]] = {}
        self._maxsize = max(1, int(maxsize))  # 有界队列防止内存泄漏
        self._running = True
        self._sequence = 0
        logger.info("TaskQueue '%s' initialized with maxsize=%d", self._name, self._maxsize)

    @property
    def name(self) -> str:
        return self._name

    def _get_or_create_queue(self, task_queue: str) -> asyncio.PriorityQueue[_QueuedTask]:
        queue_name = str(task_queue or "").strip() or self._name
        return self._queues.setdefault(queue_name, asyncio.PriorityQueue(maxsize=self._maxsize))

    async def add_task(
        self,
        task_queue: str,
        task_id: str,
        payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        priority: int = 0,
    ) -> None:
        """Add a task into the canonical queue for ``task_queue``."""

        queue_name = str(task_queue or "").strip() or self._name
        task = Task(
            task_id=str(task_id or "").strip(),
            task_queue=queue_name,
            payload=payload if isinstance(payload, dict) else {},
            metadata=metadata if isinstance(metadata, dict) else {},
            priority=int(priority or 0),
        )
        self._sequence += 1
        queued_task = _QueuedTask(
            priority_key=-task.priority,
            sequence=self._sequence,
            task=task,
        )
        await self._get_or_create_queue(queue_name).put(queued_task)
        logger.debug(
            "Added task %s to queue %s with priority %s",
            task.task_id,
            queue_name,
            task.priority,
        )

    async def poll_task(
        self,
        task_queue: str,
        timeout: float | None = None,
    ) -> Task | None:
        """Poll a single task from the canonical queue."""

        queue_name = str(task_queue or "").strip() or self._name
        queue = self._get_or_create_queue(queue_name)
        try:
            if timeout is None:
                queued_task = await queue.get()
            else:
                queued_task = await asyncio.wait_for(
                    queue.get(),
                    timeout=max(0.0, float(timeout)),
                )
        except asyncio.TimeoutError:
            return None

        task = queued_task.task
        logger.debug("Polled task %s from queue %s", task.task_id, queue_name)
        return task

    async def poll_tasks_batch(
        self,
        task_queue: str,
        max_count: int = 10,
        timeout: float = 0.1,
    ) -> list[Task]:
        """Poll up to ``max_count`` tasks from a single canonical queue.

        Behavior:
        1. Drain any tasks already queued.
        2. If still under-filled, wait until the remaining deadline for more.
        3. After each awaited task, opportunistically drain any newly available
           tasks without blocking again.
        """

        queue_name = str(task_queue or "").strip() or self._name
        queue = self._get_or_create_queue(queue_name)
        limit = max(1, int(max_count or 1))
        deadline = asyncio.get_running_loop().time() + max(0.0, float(timeout))
        tasks: list[Task] = []

        def _drain_available() -> None:
            while len(tasks) < limit:
                try:
                    tasks.append(queue.get_nowait().task)
                except asyncio.QueueEmpty:
                    break

        _drain_available()
        while len(tasks) < limit:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                queued_task = await asyncio.wait_for(queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            tasks.append(queued_task.task)
            _drain_available()

        if tasks:
            logger.debug("Polled %d tasks from queue %s", len(tasks), queue_name)
        return tasks

    async def complete_task(
        self,
        task_id: str,
        task_queue: str,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        """Record task completion in logs.

        The embedded runtime currently does not keep a separate completion store;
        workflow execution state is persisted by the workflow engine itself.
        """

        success = error is None
        logger.debug(
            "Completed task %s in queue %s: success=%s",
            str(task_id or "").strip(),
            str(task_queue or "").strip() or self._name,
            success,
        )
        _ = result

    async def get_queue_size(self, task_queue: str) -> int:
        """Get the size of a logical queue."""

        queue = self._queues.get(str(task_queue or "").strip() or self._name)
        return queue.qsize() if queue else 0

    async def get_queue_sizes(self) -> dict[str, int]:
        """Get all logical queue sizes managed by this instance."""

        return {queue_name: queue.qsize() for queue_name, queue in self._queues.items()}

    async def clear_queue(self, task_queue: str) -> int:
        """Remove a logical queue and return its queued task count."""

        queue_name = str(task_queue or "").strip() or self._name
        queue = self._queues.pop(queue_name, None)
        if queue is None:
            return 0
        removed = queue.qsize()
        logger.info("Cleared queue %s, removed %d tasks", queue_name, removed)
        return removed

    def is_empty(self, task_queue: str) -> bool:
        """Return whether a logical queue is empty."""

        queue = self._queues.get(str(task_queue or "").strip() or self._name)
        return queue.empty() if queue else True

    async def shutdown(self) -> None:
        """Shut down the queue and discard in-memory state."""

        self._running = False
        self._queues.clear()
        logger.info("TaskQueue '%s' shutdown", self._name)


class TaskQueueManager:
    """Canonical manager for named runtime task queues."""

    def __init__(self) -> None:
        self._queues: dict[str, TaskQueue] = {}
        self._lock = asyncio.Lock()

    async def get_queue(self, name: str) -> TaskQueue:
        """Get or create a named task queue."""

        queue_name = str(name or "default").strip() or "default"
        async with self._lock:
            queue = self._queues.get(queue_name)
            if queue is None:
                queue = TaskQueue(queue_name)
                self._queues[queue_name] = queue
            return queue

    async def list_queues(self) -> list[str]:
        """List all managed queue names."""

        async with self._lock:
            return sorted(self._queues.keys())

    async def get_queue_stats(self) -> dict[str, dict[str, Any]]:
        """Get per-queue statistics for the managed queues."""

        async with self._lock:
            items = list(self._queues.items())
        stats: dict[str, dict[str, Any]] = {}
        for name, queue in items:
            subqueues = await queue.get_queue_sizes()
            stats[name] = {
                "size": sum(subqueues.values()),
                "subqueues": subqueues,
            }
        return stats

    async def shutdown(self) -> None:
        """Shut down all managed queues."""

        async with self._lock:
            queues = list(self._queues.values())
            self._queues.clear()
        for queue in queues:
            await queue.shutdown()
