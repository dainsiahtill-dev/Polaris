"""Worker service for managing worker lifecycle.

Migrated from ``polaris.cells.director.execution.internal.worker_pool_service``.

Handles worker pool management, health monitoring, and task assignment.

Phase 3 note:
    WorkerExecutor is migrated to director.tasking.internal (same Phase 3).
    This module updates the import to point to the new location.

Phase 4 note (2026-03-27):
    Task execution now uses execution_broker instead of run_in_executor() + asyncio.run().
    This provides true process isolation and cleaner integration with KernelOne runtime.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from polaris.cells.runtime.execution_broker.public.contracts import (
    LaunchExecutionProcessCommandV1,
)
from polaris.cells.runtime.execution_broker.public.service import get_execution_broker_service
from polaris.domain.entities import Task, TaskResult, Worker, WorkerCapabilities, WorkerStatus, WorkerType
from polaris.kernelone.constants import DEFAULT_MAX_WORKERS

logger = logging.getLogger(__name__)

# Re-export for backwards compatibility - import from polaris.kernelone.constants
_DEFAULT_MAX_WORKERS = DEFAULT_MAX_WORKERS


@dataclass
class WorkerPoolConfig:
    """Configuration for worker pool."""

    min_workers: int = 1
    max_workers: int = field(default_factory=lambda: _DEFAULT_MAX_WORKERS)
    max_consecutive_failures: int = 3
    heartbeat_timeout_seconds: int = 60
    enable_auto_scaling: bool = True


class WorkerService:
    """Service for managing the worker pool.

    Responsibilities:
    - Create and destroy workers
    - Monitor worker health
    - Handle worker failures
    - Auto-scale worker pool based on load
    """

    def __init__(
        self,
        config: WorkerPoolConfig,
        workspace: str = ".",
        task_service: Any = None,
        message_bus: Any = None,
    ) -> None:
        self.config = config
        self.workspace = workspace
        self._task_service = task_service
        self._bus = message_bus
        self._workers: dict[str, Worker] = {}
        self._worker_tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialize worker pool with minimum workers."""
        for _ in range(self.config.min_workers):
            await self.spawn_worker()

    async def shutdown(self) -> None:
        """Gracefully shutdown all workers."""
        async with self._lock:
            # Request stop for all workers
            for worker in self._workers.values():
                worker.request_stop()

            # Cancel worker tasks
            for task in self._worker_tasks.values():
                task.cancel()

            # Wait for completion
            if self._worker_tasks:
                await asyncio.gather(*self._worker_tasks.values(), return_exceptions=True)

            self._workers.clear()
            self._worker_tasks.clear()

    async def spawn_worker(
        self,
        worker_type: WorkerType = WorkerType.LOCAL,
        capabilities: WorkerCapabilities | None = None,
    ) -> Worker:
        """Spawn a new worker."""
        async with self._lock:
            if len(self._workers) >= self.config.max_workers:
                raise RuntimeError(f"Max workers ({self.config.max_workers}) reached")

            worker_id = f"worker-{uuid.uuid4().hex[:8]}"
            worker = Worker(
                id=worker_id,
                name=f"Worker {len(self._workers) + 1}",
                worker_type=worker_type,
                capabilities=capabilities or WorkerCapabilities(),
            )

            self._workers[worker_id] = worker

            # Start worker loop
            task = asyncio.create_task(
                self._worker_loop(worker),
                name=f"worker-loop-{worker_id}",
            )
            self._worker_tasks[worker_id] = task

            return worker

    async def destroy_worker(self, worker_id: str) -> bool:
        """Destroy a specific worker."""
        async with self._lock:
            worker = self._workers.get(worker_id)
            if not worker:
                return False

            worker.request_stop()

            if worker_id in self._worker_tasks:
                self._worker_tasks[worker_id].cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._worker_tasks[worker_id]
                del self._worker_tasks[worker_id]

            del self._workers[worker_id]
            return True

    async def get_available_worker(self, task: Task) -> Worker | None:
        """Find an available worker that can handle the task."""
        async with self._lock:
            for worker in self._workers.values():
                if worker.can_accept_task(task):
                    return worker
            return None

    async def get_workers(self, status: WorkerStatus | None = None) -> list[Worker]:
        """Get all workers, optionally filtered by status."""
        async with self._lock:
            workers = list(self._workers.values())
            if status:
                workers = [w for w in workers if w.status == status]
            return workers

    async def get_worker(self, worker_id: str) -> Worker | None:
        """Get a specific worker by ID."""
        async with self._lock:
            return self._workers.get(worker_id)

    async def update_heartbeat(self, worker_id: str) -> None:
        """Update worker heartbeat."""
        async with self._lock:
            worker = self._workers.get(worker_id)
            if worker:
                worker.update_heartbeat()

    async def check_health(self) -> list[tuple[str, bool, str]]:
        """Check health of all workers.

        Returns list of (worker_id, is_healthy, reason) tuples.
        """
        async with self._lock:
            results = []
            for worker in self._workers.values():
                is_healthy = worker.health.is_healthy(self.config.heartbeat_timeout_seconds)
                reason = ""
                if not is_healthy:
                    elapsed = (datetime.now(timezone.utc) - worker.health.last_heartbeat).total_seconds()
                    reason = f"No heartbeat for {elapsed:.0f}s"
                elif worker.health.consecutive_failures >= self.config.max_consecutive_failures:
                    is_healthy = False
                    reason = f"Too many consecutive failures ({worker.health.consecutive_failures})"

                results.append((worker.id, is_healthy, reason))
            return results

    async def handle_failed_workers(self) -> list[str]:
        """Check and handle failed workers.

        Returns list of worker IDs that were restarted.
        """
        health_results = await self.check_health()
        restarted = []

        for worker_id, is_healthy, reason in health_results:
            if not is_healthy:
                worker = await self.get_worker(worker_id)
                if worker:
                    worker.mark_failed(reason)
                    await self.destroy_worker(worker_id)
                    await self.spawn_worker(worker.worker_type, worker.capabilities)
                    restarted.append(worker_id)

        return restarted

    async def auto_scale(self, pending_task_count: int) -> dict[str, Any]:
        """Auto-scale worker pool based on pending tasks.

        Returns scaling actions taken.
        """
        if not self.config.enable_auto_scaling:
            return {"scaled_up": 0, "scaled_down": 0}

        actions: dict[str, int] = {"scaled_up": 0, "scaled_down": 0}

        async with self._lock:
            current_count = len(self._workers)
            idle_count = len([w for w in self._workers.values() if w.status == WorkerStatus.IDLE])
            should_scale_up = pending_task_count > idle_count and current_count < self.config.max_workers
            should_scale_down = (not should_scale_up) and idle_count > 1 and current_count > self.config.min_workers

        # IMPORTANT: never call spawn_worker/destroy_worker while holding self._lock.
        # Those methods acquire the same lock and would deadlock under auto-scale load.

        max_scale_iterations = 10

        if should_scale_up:
            iterations = 0
            while iterations < max_scale_iterations:
                async with self._lock:
                    current_count = len(self._workers)
                    idle_count = len([w for w in self._workers.values() if w.status == WorkerStatus.IDLE])
                    can_add = pending_task_count > idle_count and current_count < self.config.max_workers
                if not can_add:
                    break
                success = await self.spawn_worker()
                if not success:
                    break
                actions["scaled_up"] += 1
                iterations += 1
                # Brief yield to avoid CPU 100%
                await asyncio.sleep(0.05)
            return actions

        if should_scale_down:
            iterations = 0
            while iterations < max_scale_iterations:
                async with self._lock:
                    current_count = len(self._workers)
                    idle_workers = [w.id for w in self._workers.values() if w.status == WorkerStatus.IDLE]
                    can_remove = len(idle_workers) > 1 and current_count > self.config.min_workers
                    worker_id = idle_workers[0] if can_remove else None
                if not worker_id:
                    break
                removed = await self.destroy_worker(worker_id)
                if not removed:
                    break
                actions["scaled_down"] += 1
                iterations += 1
                await asyncio.sleep(0.05)
            return actions

        return actions

    async def _worker_loop(self, worker: Worker) -> None:
        """Main loop for a worker.

        Workers wait for tasks and execute them. This loop runs in a separate task.
        """

        try:
            while worker.status not in (WorkerStatus.STOPPED, WorkerStatus.FAILED):
                # Update heartbeat periodically
                worker.update_heartbeat()

                # Check if should stop
                if worker.status == WorkerStatus.STOPPING and not worker.current_task_id:
                    worker.status = WorkerStatus.STOPPED
                    break

                # Try to get and execute a task
                if worker.status == WorkerStatus.IDLE and self._task_service:
                    # Poll for tasks with short timeout to stay responsive
                    task_id = await self._task_service.get_next_ready_task(timeout=0.5)

                    if task_id:
                        task = await self._task_service.get_task(task_id)
                        if task and worker.can_accept_task(task):
                            # Claim the task
                            claimed = await self._task_service.on_task_claimed(task_id, worker.id)
                            if claimed:
                                # Mark task as started
                                started = await self._task_service.on_task_started(task_id)
                                if started:
                                    worker.current_task_id = task_id
                                    worker.status = WorkerStatus.BUSY

                                    try:
                                        logger.info(
                                            "[Worker-%s] Executing task: %s",
                                            worker.id,
                                            task.subject,
                                        )

                                        # Use execution_broker to run task in isolated subprocess.
                                        # This replaces the previous run_in_executor() + asyncio.run()
                                        # pattern which required thread pooling for async code.
                                        # The subprocess approach provides true process isolation
                                        # and cleaner integration with KernelOne runtime.
                                        broker = get_execution_broker_service()

                                        # Prepare task data for subprocess execution
                                        task_input = {
                                            "workspace": self.workspace,
                                            "worker_id": worker.id,
                                            "task": task.to_dict(),
                                        }

                                        # Build execution command
                                        # Use sys.executable to ensure same Python interpreter
                                        command = LaunchExecutionProcessCommandV1(
                                            name=f"director-task-{task.id}",
                                            args=(
                                                sys.executable,
                                                "-m",
                                                "polaris.cells.director.tasking.internal.task_execution_runner",
                                            ),
                                            workspace=self.workspace,
                                            timeout_seconds=task.timeout_seconds or 300.0,
                                            env=dict(os.environ),
                                            stdin_input=json.dumps(task_input, ensure_ascii=False),
                                            metadata={
                                                "cell": "director",
                                                "task_id": str(task.id),
                                                "worker_id": worker.id,
                                            },
                                        )

                                        # Launch and wait for subprocess
                                        launch_result = await broker.launch_process(command)
                                        if not launch_result.success or launch_result.handle is None:
                                            raise RuntimeError(
                                                f"Failed to launch task subprocess: {launch_result.error_message}"
                                            )

                                        wait_result = await broker.wait_process(
                                            launch_result.handle,
                                            timeout_seconds=task.timeout_seconds or 300.0,
                                        )

                                        # TODO(BLOCKER): The subprocess writes TaskResult JSON to stdout but
                                        # ExecutionProcessWaitResultV1 doesn't capture stdout. This means
                                        # output, duration_ms, and evidence are always empty/zero.
                                        # Fix: Extend ExecutionProcessWaitResultV1 to include stdout, capture
                                        # it in wait_process, and parse JSON here to build proper TaskResult.
                                        result = TaskResult(
                                            success=wait_result.success,
                                            output="<stdout not captured - see TODO>",
                                            exit_code=wait_result.exit_code or 0,
                                            duration_ms=0,
                                            evidence=(),
                                            error=wait_result.error_message,
                                        )

                                        # Complete the task
                                        await self._task_service.on_task_completed(
                                            task_id,
                                            result,
                                            evidence=None,
                                        )

                                        # Update health metrics (immutable pattern)
                                        worker.health = worker.health.with_updates(
                                            tasks_completed=worker.health.tasks_completed + 1,
                                            consecutive_failures=0,
                                        )
                                        logger.info(
                                            "[Worker-%s] Task completed: %s",
                                            worker.id,
                                            task.subject,
                                        )

                                    except (RuntimeError, ValueError) as e:
                                        logger.error(
                                            "[Worker-%s] Task failed: %s",
                                            worker.id,
                                            e,
                                        )
                                        await self._task_service.on_task_failed(task_id, str(e))
                                        # Update health metrics (immutable pattern)
                                        worker.health = worker.health.with_updates(
                                            tasks_failed=worker.health.tasks_failed + 1,
                                            consecutive_failures=worker.health.consecutive_failures + 1,
                                        )

                                    finally:
                                        worker.current_task_id = None
                                        worker.status = WorkerStatus.IDLE

                                    # Continue immediately to check for next task
                                    continue

                # Sleep briefly between polls
                await asyncio.sleep(0.1)

        except asyncio.CancelledError:
            worker.status = WorkerStatus.STOPPED
            raise
        except (RuntimeError, ValueError) as e:
            worker.mark_failed(str(e))
            raise
