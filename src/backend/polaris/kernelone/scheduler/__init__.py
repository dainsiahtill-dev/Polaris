"""KernelOne in-process task scheduler.

Provides a lightweight in-process scheduler implementing SchedulerPort.
Supports ONCE, PERIODIC, DELAYED, and simple CRON-like scheduling.

Design constraints:
- KernelOne-only: no Polaris business semantics
- No bare except: all errors caught with specific exception types
- Explicit UTF-8: all file I/O uses encoding="utf-8"
- Async-first: uses asyncio for timer management
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from polaris.kernelone.contracts.technical import (
    ScheduledTask,
    ScheduleKind,
    ScheduleResult,
    SchedulerPort,
    ScheduleSpec,
)
from polaris.kernelone.utils.time_utils import utc_now as _utc_now

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


class SimpleScheduler(SchedulerPort):
    """Lightweight in-process async scheduler.

    Manages scheduled tasks in memory. For distributed scenarios,
    replace with a persistent scheduler backed by Redis, NATS JetStream,
    or a database.

    Thread-safety note: all public methods are async and run on the
    asyncio event loop, so no additional locking is needed for the
    internal dict.

    Usage::

        scheduler = SimpleScheduler()
        task = ScheduledTask(
            handler="kernelone.audit.cleanup",
            payload={"max_age_days": 30},
            schedule=ScheduleSpec(kind=ScheduleKind.PERIODIC, interval_seconds=3600),
        )
        result = await scheduler.schedule(task)
        await scheduler.start()   # starts background loop
        await scheduler.stop()   # stops and cancels all pending
    """

    def __init__(self) -> None:
        self._tasks: dict[str, ScheduledTask] = {}
        self._timers: dict[str, asyncio.Task[None]] = {}
        self._cancellations: dict[str, asyncio.Event] = {}
        self._next_runs: dict[str, datetime] = {}
        self._started = False
        self._stopping = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started_event: asyncio.Event | None = None
        self._handler_registry: dict[str, Callable[..., Any]] = {}

    # -------------------------------------------------------------------------
    # SchedulerPort implementation
    # -------------------------------------------------------------------------

    async def schedule(self, task: ScheduledTask) -> ScheduleResult:
        if self._stopping:
            return ScheduleResult(scheduled=False, error="scheduler is stopping")
        if task.task_id in self._tasks:
            return ScheduleResult(
                scheduled=False,
                task_id=task.task_id,
                error="task_id already exists",
            )

        spec = task.schedule
        next_run = self._calc_next_run(spec)
        self._tasks[task.task_id] = task
        if next_run is not None:
            self._next_runs[task.task_id] = next_run

        if self._started:
            self._schedule_loop(task.task_id)

        return ScheduleResult(
            scheduled=True,
            task_id=task.task_id,
            next_run_at=next_run,
        )

    async def cancel(self, task_id: str) -> bool:
        timer = self._timers.pop(task_id, None)
        if timer is not None and not timer.done():
            cancel_evt = self._cancellations.get(task_id)
            if cancel_evt is not None:
                cancel_evt.set()
            timer.cancel()
        self._tasks.pop(task_id, None)
        self._next_runs.pop(task_id, None)
        self._cancellations.pop(task_id, None)
        return task_id in self._tasks or timer is not None

    async def get_next_run(self, task_id: str) -> datetime | None:
        return self._next_runs.get(task_id)

    async def list_tasks(self) -> list[ScheduledTask]:
        return list(self._tasks.values())

    # -------------------------------------------------------------------------
    # Handler registry
    # -------------------------------------------------------------------------

    def register_handler(self, name: str, handler: Callable[..., Any]) -> None:
        """Register a handler callable for a named task key.

        Args:
            name: Handler key, e.g. ``"kernelone.audit.cleanup"``.
            handler: Sync or async callable invoked as ``handler(**task.payload)``.
        """
        if not callable(handler):
            raise TypeError(f"handler for '{name}' must be callable, got {type(handler).__name__}")
        self._handler_registry[name] = handler
        logger.debug("Registered handler: %s", name)

    def unregister_handler(self, name: str) -> None:
        """Remove a registered handler."""
        self._handler_registry.pop(name, None)
        logger.debug("Unregistered handler: %s", name)

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def start(self) -> None:
        """Start the scheduler background loop.

        Idempotent: calling start() when already started is a no-op.
        """
        if self._started:
            return
        self._loop = asyncio.get_running_loop()
        self._started_event = asyncio.Event()
        self._started = True
        self._started_event.set()

        for task_id in list(self._tasks.keys()):
            self._schedule_loop(task_id)

        logger.info("SimpleScheduler started with %d tasks", len(self._tasks))

    async def stop(self, *, cancel_pending: bool = True) -> None:
        """Stop the scheduler and optionally cancel all pending tasks."""
        self._stopping = True
        if cancel_pending:
            for task_id in list(self._tasks.keys()):
                await self.cancel(task_id)
        self._started = False
        self._stopping = False
        logger.info("SimpleScheduler stopped")

    # -------------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------------

    def _calc_next_run(self, spec: ScheduleSpec) -> datetime | None:
        now = _utc_now()
        if spec.kind == ScheduleKind.ONCE:
            return None
        if spec.kind == ScheduleKind.DELAYED:
            return datetime.fromtimestamp(now.timestamp() + spec.delay_seconds, tz=timezone.utc)
        if spec.kind == ScheduleKind.PERIODIC:
            return now
        if spec.kind == ScheduleKind.CRON:
            # Simple cron: interpret interval_seconds as period for now
            return now
        return None

    def _calc_delay_seconds(self, spec: ScheduleSpec) -> float:
        if spec.kind == ScheduleKind.ONCE:
            return 0.0
        if spec.kind == ScheduleKind.DELAYED:
            return spec.delay_seconds
        if spec.kind == ScheduleKind.PERIODIC:
            return max(0.1, spec.interval_seconds)
        if spec.kind == ScheduleKind.CRON:
            return max(0.1, spec.interval_seconds)
        return 0.0

    def _schedule_loop(self, task_id: str) -> None:
        if self._loop is None or self._stopping:
            return
        task = self._tasks.get(task_id)
        if task is None:
            return
        timer = self._loop.create_task(self._run_task(task))
        self._timers[task_id] = timer

    async def _run_task(self, task: ScheduledTask) -> None:
        spec = task.schedule
        cancel_evt = asyncio.Event()
        self._cancellations[task.task_id] = cancel_evt

        run_count = 0
        max_runs = spec.max_runs or 0

        while not self._stopping:
            delay = self._calc_delay_seconds(spec)
            if delay > 0:
                try:
                    await asyncio.wait_for(
                        cancel_evt.wait(),
                        timeout=delay,
                    )
                    # Cancelled: clean up and exit
                    self._cancellations.pop(task.task_id, None)
                    return
                except asyncio.TimeoutError:
                    pass  # Normal: delay elapsed, run the task

            if self._stopping:
                break

            run_count += 1
            try:
                await self._execute_handler(task)
            except (RuntimeError, ValueError) as exc:
                logger.warning(
                    "Scheduler task %s handler %s raised: %s",
                    task.task_id,
                    task.handler,
                    exc,
                )

            self._next_runs[task.task_id] = _utc_now()

            if spec.kind == ScheduleKind.ONCE:
                break

            if max_runs > 0 and run_count >= max_runs:
                logger.info(
                    "Scheduler task %s reached max_runs=%d, removing",
                    task.task_id,
                    max_runs,
                )
                self._tasks.pop(task.task_id, None)
                self._next_runs.pop(task.task_id, None)
                break

    async def _execute_handler(self, task: ScheduledTask) -> None:
        """Dispatch the registered handler for ``task.handler`` with ``task.payload``.

        Supports both sync and async callables. If no handler is registered for
        the task key, logs at WARNING level and silently skips.
        """
        handler_key = task.handler
        if not handler_key:
            logger.warning(
                "Scheduler task %s has no handler key — skipping",
                task.task_id,
            )
            return

        handler = self._handler_registry.get(handler_key)
        if handler is None:
            logger.warning(
                "No handler registered for key '%s' (task_id=%s) — skipping dispatch",
                handler_key,
                task.task_id,
            )
            return

        payload = task.payload if isinstance(task.payload, dict) else {}
        try:
            result = handler(**payload)
            if inspect.isawaitable(result):
                await result
            logger.debug(
                "Scheduler dispatched handler '%s' for task_id=%s with payload=%s",
                handler_key,
                task.task_id,
                payload,
            )
        except (RuntimeError, ValueError) as exc:
            # Re-raise so _run_task's except block handles it uniformly
            raise RuntimeError(f"Handler '{handler_key}' failed for task_id={task.task_id}: {exc}") from exc


__all__ = ["SimpleScheduler"]
