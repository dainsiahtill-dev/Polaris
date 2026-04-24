"""Director application service (Cell Implementation).

Orchestrates the Director workflow using Actor model principles.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any

from polaris.cells.director.tasking.public import TaskQueueConfig, TaskService, WorkerPoolConfig, WorkerService
from polaris.domain.entities import Task, TaskPriority, TaskStatus, Worker, WorkerStatus
from polaris.domain.entities.capability import Role, RoleConfig, get_role_config
from polaris.domain.entities.policy import Policy
from polaris.domain.services import (
    SecurityService,
    TodoService,
    TokenService,
    TranscriptService,
    get_security_service,
    get_todo_service,
    get_token_service,
    get_transcript_service,
)
from polaris.kernelone.constants import DEFAULT_MAX_WORKERS
from polaris.kernelone.context.runtime_feature_flags import (
    CognitiveRuntimeMode,
    resolve_cognitive_runtime_mode,
)
from polaris.kernelone.events.message_bus import Message, MessageBus, MessageType
from polaris.kernelone.events.typed import (
    BudgetExceeded as TypedBudgetExceeded,
    DirectorStarted as TypedDirectorStarted,
    DirectorStopped as TypedDirectorStopped,
    NagReminder as TypedNagReminder,
    TaskCompleted as TypedTaskCompleted,
    TaskFailed as TypedTaskFailed,
    TaskStarted as TypedTaskStarted,
    TaskSubmitted as TypedTaskSubmitted,
    get_default_adapter as get_typed_adapter,
)
from polaris.kernelone.process.command_executor import CommandExecutionService

logger = logging.getLogger(__name__)

# Code intelligence integration (optional)
try:
    from polaris.cells.workspace.integrity.public.service import DirectorCodeIntelMixin

    CODE_INTEL_AVAILABLE = True
except (RuntimeError, ValueError) as exc:
    import logging

    _code_intel_logger = logging.getLogger(__name__)
    _code_intel_logger.debug("DirectorCodeIntelMixin unavailable (code intelligence disabled): %s", exc)
    CODE_INTEL_AVAILABLE = False

    class DirectorCodeIntelMixin:  # type: ignore[no-redef]
        def __init__(self, workspace: str, *args: object, **kwargs: object) -> None:
            pass


class DirectorState(Enum):
    """Director lifecycle state."""

    IDLE = auto()
    RUNNING = auto()
    PAUSED = auto()
    STOPPING = auto()
    STOPPED = auto()


AUTO_IDLE_GRACE_SECONDS = 1.5
PENDING_STALL_TIMEOUT_SECONDS = 120.0
EMPTY_QUEUE_STALL_TIMEOUT_SECONDS = 45.0

# Re-export for backwards compatibility - import from polaris.kernelone.constants
_DEFAULT_MAX_WORKERS = DEFAULT_MAX_WORKERS


@dataclass
class DirectorConfig:
    """Director configuration."""

    workspace: str
    max_workers: int = field(default_factory=lambda: _DEFAULT_MAX_WORKERS)
    task_poll_interval: float = 1.0
    enable_nag: bool = True
    enable_auto_compact: bool = True
    token_budget: int | None = None
    policy: Policy = field(default_factory=Policy)
    role: Role = field(default=Role.DIRECTOR)
    role_config: RoleConfig | None = None

    def __post_init__(self) -> None:
        if self.role_config is None:
            self.role_config = get_role_config(self.role, self.policy.to_dict())


class DirectorService(DirectorCodeIntelMixin):
    """Director service that orchestrates task execution."""

    def __init__(
        self,
        config: DirectorConfig,
        security: SecurityService | None = None,
        todo: TodoService | None = None,
        token: TokenService | None = None,
        transcript: TranscriptService | None = None,
        message_bus: MessageBus | None = None,
        task_service: TaskService | None = None,
        worker_service: WorkerService | None = None,
    ) -> None:
        DirectorCodeIntelMixin.__init__(self, config.workspace)

        self.config = config
        self.state = DirectorState.IDLE

        self.security = security or get_security_service(config.workspace)
        self.todo = todo or get_todo_service()
        self.token = token or get_token_service(budget_limit=config.token_budget)
        self.transcript = transcript or get_transcript_service()

        self._bus = message_bus or MessageBus()

        provided_task_service = task_service is not None
        self._task_service = task_service or TaskService(
            TaskQueueConfig(default_timeout_seconds=300),
            workspace=config.workspace,
        )
        if not provided_task_service:
            try:
                from polaris.bootstrap.config import get_settings
                from polaris.cells.audit.evidence.public.service import bind_audit_llm_to_task_service

                bind_audit_llm_to_task_service(
                    task_service=self._task_service,
                    settings=get_settings(),
                    workspace=config.workspace,
                )
            except (RuntimeError, ValueError) as exc:
                logger.debug("Failed to bind audit LLM caller in DirectorService: %s", exc)

        self._worker_service = worker_service or WorkerService(
            WorkerPoolConfig(min_workers=1, max_workers=config.max_workers),
            workspace=config.workspace,
            task_service=self._task_service,
            message_bus=self._bus,
        )

        self._stop_event = asyncio.Event()
        self._state_lock = asyncio.Lock()
        self._current_iteration = 0
        self._main_loop_task: asyncio.Task | None = None
        self._started_at: float | None = None
        self._stopped_at: float | None = None
        self._quiescence_started_at: float | None = None
        self._pending_stall_started_at: float | None = None
        self._empty_queue_started_at: float | None = None

        self._running_tasks: dict[str, asyncio.Task] = {}
        self._event_handlers_ready = False

        self._metrics = {
            "tasks_submitted": 0,
            "tasks_completed": 0,
            "tasks_failed": 0,
            "workers_restarted": 0,
            "nag_triggered": 0,
            "compact_triggered": 0,
            "auto_stopped_runs": 0,
            "deadlock_breaks": 0,
        }

    async def _emit_typed_event(self, event: Any) -> None:
        """Emit a typed event through the adapter if available.

        This method emits typed events while maintaining backward compatibility
        with the MessageBus. If the typed adapter is not available, the event
        is silently dropped (the MessageBus broadcast handles compatibility).

        Args:
            event: The typed event to emit (e.g., TaskSubmitted, DirectorStarted)
        """
        adapter = get_typed_adapter()
        if adapter is not None:
            try:
                await adapter.emit_to_both(event)
            except (RuntimeError, ValueError) as exc:
                logger.debug("Failed to emit typed event %s: %s", event.event_name, exc)

    async def _setup_event_handlers(self) -> None:
        await self._bus.subscribe(MessageType.TASK_COMPLETED, self._on_task_completed)
        await self._bus.subscribe(MessageType.TASK_FAILED, self._on_task_failed)
        await self._bus.subscribe(MessageType.WORKER_FAILED, self._on_worker_failed)

    async def _unsubscribe_event_handlers(self) -> None:
        await self._bus.unsubscribe(MessageType.TASK_COMPLETED, self._on_task_completed)
        await self._bus.unsubscribe(MessageType.TASK_FAILED, self._on_task_failed)
        await self._bus.unsubscribe(MessageType.WORKER_FAILED, self._on_worker_failed)

    async def start(self) -> None:
        if self.state not in {DirectorState.IDLE, DirectorState.STOPPED}:
            raise RuntimeError(f"Cannot start from state {self.state}")

        self._stop_event = asyncio.Event()
        self.state = DirectorState.RUNNING
        self._started_at = time.time()
        self._stopped_at = None

        try:
            if not self._event_handlers_ready:
                await self._setup_event_handlers()
                self._event_handlers_ready = True

            self.transcript.start_session(
                session_id=f"director-{uuid.uuid4().hex[:8]}",
                metadata={"workspace": self.config.workspace, "max_workers": self.config.max_workers},
            )

            await self._worker_service.initialize()
            self._main_loop_task = asyncio.create_task(self._main_loop())
            # Attach a done-callback so that when the loop exits for any reason
            # (natural convergence, external cancel, or unhandled exception) we
            # explicitly finalize the Director state.  This is the *only* place
            # RUNNING -> IDLE can be triggered from the loop-exit path (CQS).
            self._main_loop_task.add_done_callback(lambda _t: asyncio.ensure_future(self._try_finalize_idle()))

            # Emit events (typed + legacy MessageBus for backward compatibility)
            typed_event = TypedDirectorStarted.create(
                workspace=self.config.workspace,
                max_workers=self.config.max_workers,
                config={},
            )
            await self._emit_typed_event(typed_event)
            await self._bus.broadcast(MessageType.DIRECTOR_START, "director", {"workspace": self.config.workspace})
        except (RuntimeError, ValueError) as e:
            logger.error(
                "Director start failed, rolling back: error=%s, type=%s, workspace=%s",
                e,
                type(e).__name__,
                self.config.workspace,
            )
            self._stop_event.set()
            try:
                await self._worker_service.shutdown()
            except (RuntimeError, ValueError) as exc:
                logger.error(
                    "Director rollback: worker_service.shutdown failed: error=%s, type=%s",
                    exc,
                    type(exc).__name__,
                )
            self.state = DirectorState.IDLE
            self._stopped_at = time.time()
            self._main_loop_task = None
            if self._event_handlers_ready:
                try:
                    await self._unsubscribe_event_handlers()
                except (RuntimeError, ValueError) as exc:
                    logger.error(
                        "Director rollback: _unsubscribe_event_handlers failed: error=%s, type=%s",
                        exc,
                        type(exc).__name__,
                    )
                self._event_handlers_ready = False
            try:
                self.transcript.end_session()
            except (RuntimeError, ValueError) as exc:
                logger.error(
                    "Director rollback: transcript.end_session failed: error=%s, type=%s",
                    exc,
                    type(exc).__name__,
                )
            raise

    async def stop(self) -> None:
        if self.state in {DirectorState.IDLE, DirectorState.STOPPED}:
            return

        self.state = DirectorState.STOPPING
        self._stop_event.set()

        # Emit typed event for stop
        typed_event = TypedDirectorStopped.create(
            workspace=self.config.workspace,
            reason="stop_requested",
            auto=False,
            metrics=self._metrics.copy(),
        )
        await self._emit_typed_event(typed_event)
        await self._bus.broadcast(MessageType.DIRECTOR_STOP, "director")

        if self._main_loop_task:
            self._main_loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._main_loop_task

        if self._event_handlers_ready:
            await self._unsubscribe_event_handlers()
            self._event_handlers_ready = False

        await self._worker_service.shutdown()

        self.state = DirectorState.STOPPED
        self._stopped_at = time.time()
        self.transcript.end_session()

    async def submit_task(
        self,
        subject: str,
        description: str = "",
        command: str | None = None,
        priority: TaskPriority = TaskPriority.MEDIUM,
        blocked_by: list[int | str] | None = None,
        timeout_seconds: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Task:
        if command:
            check = self.security.is_command_safe(command)
            if not check.is_safe:
                raise RuntimeError(f"Command not allowed: {check.reason}")

        task = await self._task_service.create_task(
            subject=subject,
            description=description,
            command=command,
            priority=priority,
            blocked_by=blocked_by if blocked_by is not None else None,
            timeout_seconds=timeout_seconds,
            metadata=metadata or {},
        )

        self._metrics["tasks_submitted"] += 1
        # Emit typed event for task submission
        typed_event = TypedTaskSubmitted.create(
            task_id=str(task.id),
            subject=subject,
            priority=priority.name,
            timeout_seconds=timeout_seconds,
            blocked_by=[str(b) for b in (blocked_by or [])],
            workspace=self.config.workspace,
        )
        await self._emit_typed_event(typed_event)
        await self._bus.broadcast(MessageType.TASK_SUBMITTED, "director", {"task_id": task.id, "subject": subject})
        self.transcript.record_message(
            role="system", content=f"Task submitted: {subject}", metadata={"task_id": task.id}
        )
        self._emit_cognitive_runtime_shadow_task_artifacts(
            task=task,
            receipt_type="director_task_submitted",
            payload={
                "subject": subject,
                "priority": priority.name,
                "timeout_seconds": timeout_seconds,
                "blocked_by": list(blocked_by or []),
            },
            export_handoff=False,
        )

        return task

    async def get_task(self, task_id: str) -> Task | None:
        return await self._task_service.get_task(task_id)

    async def list_tasks(self, status: TaskStatus | None = None) -> list[dict]:
        tasks = await self._task_service.get_tasks(status=status)
        return [t.to_dict() for t in tasks]

    async def cancel_task(self, task_id: str) -> dict[str, Any]:
        """Cancel a task by ID.

        Returns:
            Dict with ok=True if cancelled, or ok=False with error details
        """
        try:
            success = await self._task_service.cancel_task(task_id)
            if success:
                logger.info("Task cancelled: %s", task_id)
                return {"ok": True, "task_id": task_id}
            logger.warning("Failed to cancel task %s (task may not exist or not be cancellable)", task_id)
            return {
                "ok": False,
                "error": "Task not found or not cancellable",
                "task_id": task_id,
            }
        except (RuntimeError, ValueError) as e:
            logger.error("Error cancelling task %s: %s", task_id, e)
            return {
                "ok": False,
                "error": str(e),
                "task_id": task_id,
            }

    async def get_worker(self, worker_id: str) -> Worker | None:
        return await self._worker_service.get_worker(worker_id)

    async def list_workers(self) -> list[Worker]:
        return await self._worker_service.get_workers()

    async def _try_finalize_idle(self) -> None:
        """Transition RUNNING -> IDLE when the main loop has exited without workers.

        This is an explicit lifecycle command, called only from the main loop's
        done-callback.  It must never be called from query paths (CQS).
        """
        async with self._state_lock:
            main_loop_done = bool(self._main_loop_task is not None and self._main_loop_task.done())
            if self.state == DirectorState.RUNNING and (self._main_loop_task is None or main_loop_done):
                workers = await self._worker_service.get_workers()
                if len(workers) == 0:
                    self.state = DirectorState.IDLE
                    self._stopped_at = time.time()
                    logger.debug("Director transitioned RUNNING -> IDLE (main loop exited, no workers)")

    async def get_status(self) -> dict[str, Any]:
        """Return a pure snapshot of Director state.  Does not modify any state (CQS)."""
        tasks = await self._task_service.get_tasks()
        workers = await self._worker_service.get_workers()

        return {
            "state": self.state.name,
            "started_at": self._started_at,
            "stopped_at": self._stopped_at,
            "workspace": self.config.workspace,
            "metrics": self._metrics.copy(),
            "tasks": {
                "total": len(tasks),
                "by_status": {status.name: len([t for t in tasks if t.status == status]) for status in TaskStatus},
                "ready_queue_size": await self._task_service.get_ready_task_count(),
                "task_rows": [task.to_dict() for task in tasks],
            },
            "workers": {
                "total": len(workers),
                "available": len([w for w in workers if w.is_available()]),
                "busy": len([w for w in workers if w.status == WorkerStatus.BUSY]),
                "worker_rows": [worker.to_dict() for worker in workers],
            },
            "token_budget": self.token.get_budget_status().to_dict(),
        }

    async def _main_loop(self) -> None:
        while not self._stop_event.is_set():
            self._current_iteration += 1
            try:
                if self.config.enable_nag:
                    await self._check_nag()
                await self._schedule_tasks()
                await self._check_workers()
                await self._check_budget()
                await self._auto_scale()
                should_stop = await self._check_run_convergence()
                if should_stop:
                    break
            except (RuntimeError, ValueError) as e:
                logger.error("Director main loop error on iteration %d: %s", self._current_iteration, e)
                self.transcript.record_message(
                    role="system", content=f"Director loop error: {e}", metadata={"error": str(e)}
                )

            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.config.task_poll_interval)

    async def _check_run_convergence(self) -> bool:
        if self.state != DirectorState.RUNNING:
            return False

        tasks = await self._task_service.get_tasks()
        if not tasks:
            now = time.time()
            if self._empty_queue_started_at is None:
                self._empty_queue_started_at = now
                return False
            if now - self._empty_queue_started_at >= EMPTY_QUEUE_STALL_TIMEOUT_SECONDS:
                await self._auto_stop(reason=f"no_tasks_submitted_for_{int(EMPTY_QUEUE_STALL_TIMEOUT_SECONDS)}s")
                return True
            return False
        self._empty_queue_started_at = None

        workers = await self._worker_service.get_workers()
        busy_workers = len([w for w in workers if w.status == WorkerStatus.BUSY])
        ready_queue_size = await self._task_service.get_ready_task_count()

        active_statuses = {TaskStatus.READY, TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS}
        terminal_statuses = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
        has_active_tasks = any(task.status in active_statuses for task in tasks)
        pending_tasks = [task for task in tasks if task.status == TaskStatus.PENDING]

        if busy_workers > 0 or ready_queue_size > 0 or has_active_tasks:
            self._quiescence_started_at = None
            self._pending_stall_started_at = None
            return False

        if pending_tasks:
            deadlocked_ids = self._collect_deadlocked_pending_ids(tasks)
            if deadlocked_ids:
                await self._fail_pending_tasks(deadlocked_ids, reason="Blocked by failed/cancelled dependencies")
                return False

            now = time.time()
            if self._pending_stall_started_at is None:
                self._pending_stall_started_at = now
                return False
            if now - self._pending_stall_started_at >= PENDING_STALL_TIMEOUT_SECONDS:
                await self._fail_pending_tasks([str(task.id) for task in pending_tasks], reason="Pending tasks stalled")
                self._pending_stall_started_at = None
            return False

        if not all(task.status in terminal_statuses for task in tasks):
            return False

        now = time.time()
        if self._quiescence_started_at is None:
            self._quiescence_started_at = now
            return False
        if now - self._quiescence_started_at < AUTO_IDLE_GRACE_SECONDS:
            return False

        await self._auto_stop(reason="all_tasks_terminal")
        return True

    def _collect_deadlocked_pending_ids(self, tasks: list[Task]) -> list[str]:
        status_by_id = {str(task.id): task.status for task in tasks}
        deadlocked: list[str] = []
        for task in tasks:
            if task.status != TaskStatus.PENDING:
                continue
            blockers = [str(dep).strip() for dep in (task.blocked_by or []) if str(dep).strip()]
            if blockers and all(
                status_by_id.get(dep) in {TaskStatus.FAILED, TaskStatus.CANCELLED, None} for dep in blockers
            ):
                deadlocked.append(str(task.id))
        return deadlocked

    async def _fail_pending_tasks(self, task_ids: list[str], reason: str) -> None:
        for task_id in task_ids:
            await self._task_service.on_task_failed(task_id, reason, recoverable=False)
            self._metrics["tasks_failed"] += 1
        self._metrics["deadlock_breaks"] += 1

    async def _auto_stop(self, reason: str) -> None:
        if self.state != DirectorState.RUNNING:
            return
        self._stop_event.set()
        await self._worker_service.shutdown()
        self.state = DirectorState.IDLE
        self._stopped_at = time.time()
        self._metrics["auto_stopped_runs"] += 1
        # Emit typed event for auto stop
        typed_event = TypedDirectorStopped.create(
            workspace=self.config.workspace,
            reason=reason,
            auto=True,
            metrics=self._metrics.copy(),
        )
        await self._emit_typed_event(typed_event)
        await self._bus.broadcast(MessageType.DIRECTOR_STOP, "director", {"reason": reason, "auto": True})
        self.transcript.end_session()

    async def _schedule_tasks(self) -> None:
        workers = await self._worker_service.get_workers()
        available_workers = [w for w in workers if w.is_available()]
        if not available_workers:
            return

        for _ in range(len(available_workers)):
            task_id = await self._task_service.get_next_ready_task(timeout=0)
            if not task_id:
                break
            task = await self._task_service.get_task(task_id)
            if not task or task.status != TaskStatus.READY:
                continue
            worker = available_workers.pop(0)
            if worker.can_accept_task(task):
                await self._assign_task(task, worker)
            if not available_workers:
                break

    async def _assign_task(self, task: Task, worker: Worker) -> None:
        task_id_str = str(task.id)
        success = await self._task_service.on_task_claimed(task_id_str, worker.id)
        if not success:
            return
        worker.claim_task(task_id_str)
        exec_task = asyncio.create_task(self._execute_task(task, worker))
        self._running_tasks[task_id_str] = exec_task
        exec_task.add_done_callback(lambda t: self._running_tasks.pop(task_id_str, None))

    async def _execute_task(self, task: Task, worker: Worker) -> None:
        task_id_str = str(task.id)
        await self._task_service.on_task_started(task_id_str)
        # Emit typed event for task started
        typed_started = TypedTaskStarted.create(
            task_id=task_id_str,
            worker_id=worker.id,
            workspace=self.config.workspace,
        )
        await self._emit_typed_event(typed_started)
        await self._bus.broadcast(
            MessageType.TASK_STARTED, "director", {"task_id": task_id_str, "worker_id": worker.id}
        )
        start_time = datetime.now(timezone.utc)
        try:
            from polaris.domain.entities import TaskResult

            result = await self._run_command(task.command, timeout=task.timeout_seconds)
            await self._task_service.on_task_completed(task_id_str, result)
            worker.release_task(result)
            self._metrics["tasks_completed"] += 1
            changed_files = [e.path for e in (result.evidence or []) if e.type == "file" and e.path]
            # Emit typed event for task completed
            typed_completed = TypedTaskCompleted.create(
                task_id=task_id_str,
                success=bool(result.success),
                changed_files=changed_files,
                duration_ms=int(getattr(result, "duration_ms", 0) or 0),
                workspace=self.config.workspace,
            )
            await self._emit_typed_event(typed_completed)
            await self._bus.broadcast(
                MessageType.TASK_COMPLETED,
                "director",
                {"task_id": task_id_str, "success": result.success, "changed_files": changed_files},
            )
            self._emit_cognitive_runtime_shadow_task_artifacts(
                task=task,
                receipt_type="director_task_completed",
                payload={
                    "worker_id": worker.id,
                    "success": bool(result.success),
                    "changed_files": changed_files,
                    "duration_ms": int(getattr(result, "duration_ms", 0) or 0),
                },
                export_handoff=True,
            )
        except (RuntimeError, ValueError) as e:
            from polaris.domain.entities import TaskResult

            result = TaskResult(
                success=False,
                output="",
                error=str(e),
                duration_ms=int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000),
            )
            await self._task_service.on_task_failed(task_id_str, str(e), recoverable=False)
            worker.release_task(result)
            self._metrics["tasks_failed"] += 1
            # Emit typed event for task failed
            typed_failed = TypedTaskFailed.create(
                task_id=task_id_str,
                error=str(e),
                duration_ms=int(getattr(result, "duration_ms", 0) or 0),
                workspace=self.config.workspace,
            )
            await self._emit_typed_event(typed_failed)
            await self._bus.broadcast(MessageType.TASK_FAILED, "director", {"task_id": task_id_str, "error": str(e)})
            self._emit_cognitive_runtime_shadow_task_artifacts(
                task=task,
                receipt_type="director_task_failed",
                payload={
                    "worker_id": worker.id,
                    "success": False,
                    "error": str(e),
                    "duration_ms": int(getattr(result, "duration_ms", 0) or 0),
                },
                export_handoff=True,
            )

    def _emit_cognitive_runtime_shadow_task_artifacts(
        self,
        *,
        task: Any,
        receipt_type: str,
        payload: dict[str, Any],
        export_handoff: bool,
    ) -> None:
        metadata = dict(getattr(task, "metadata", {}) or {})
        mode = resolve_cognitive_runtime_mode(metadata=metadata)
        if mode is CognitiveRuntimeMode.OFF:
            return
        session_id = str(metadata.get("session_id") or "").strip() or None
        run_id = str(metadata.get("run_id") or "").strip() or None
        turn_envelope = {}
        raw_turn_envelope = metadata.get("turn_envelope")
        if isinstance(raw_turn_envelope, dict):
            turn_envelope = dict(raw_turn_envelope)
        else:
            turn_id = str(metadata.get("turn_id") or "").strip()
            if turn_id:
                turn_envelope = {
                    "turn_id": turn_id,
                    "session_id": session_id,
                    "run_id": run_id,
                    "role": "director",
                    "task_id": str(getattr(task, "id", "") or "").strip() or None,
                }
        try:
            from polaris.cells.factory.cognitive_runtime.public.contracts import (
                ExportHandoffPackCommandV1,
                RecordRuntimeReceiptCommandV1,
            )
            from polaris.cells.factory.cognitive_runtime.public.service import (
                get_cognitive_runtime_public_service,
            )

            service = get_cognitive_runtime_public_service()
            try:
                receipt_result = service.record_runtime_receipt(
                    RecordRuntimeReceiptCommandV1(
                        workspace=self.config.workspace,
                        receipt_type=receipt_type,
                        session_id=session_id,
                        run_id=run_id,
                        payload={
                            "task_id": str(getattr(task, "id", "") or ""),
                            "source": "director.execution",
                            "cognitive_runtime_mode": mode.value,
                            **dict(payload or {}),
                        },
                        turn_envelope=turn_envelope,
                    )
                )
                if export_handoff and session_id:
                    handoff_turn_envelope = dict(turn_envelope)
                    receipt = getattr(receipt_result, "receipt", None)
                    receipt_id = str(getattr(receipt, "receipt_id", "") or "").strip()
                    if receipt_id:
                        receipt_ids = list(handoff_turn_envelope.get("receipt_ids") or [])
                        if receipt_id not in receipt_ids:
                            receipt_ids.append(receipt_id)
                        handoff_turn_envelope["receipt_ids"] = receipt_ids
                    service.export_handoff_pack(
                        ExportHandoffPackCommandV1(
                            workspace=self.config.workspace,
                            session_id=session_id,
                            run_id=run_id,
                            reason=f"director.execution:{receipt_type}",
                            turn_envelope=handoff_turn_envelope,
                        )
                    )
            finally:
                service.close()
        except (RuntimeError, ValueError):
            logger.warning(
                "Failed to emit Cognitive Runtime shadow task artifacts for task=%s type=%s",
                getattr(task, "id", ""),
                receipt_type,
                exc_info=True,
            )

    async def _run_command(self, command: str | None, timeout: int) -> Any:
        """Execute a command using the secure CommandExecutionService.

        Args:
            command: The command string to execute.
            timeout: Maximum execution time in seconds.

        Returns:
            TaskResult with execution outcome.
        """
        from polaris.domain.entities import TaskResult

        if not command:
            return TaskResult(success=True, output="No command", duration_ms=0)

        start = datetime.now(timezone.utc)
        cmd_svc = CommandExecutionService(self.config.workspace)

        try:
            request = cmd_svc.parse_command(command, timeout_seconds=timeout)
        except ValueError as e:
            return TaskResult(
                success=False,
                output="",
                error=f"Command parse error: {e}",
                duration_ms=int((datetime.now(timezone.utc) - start).total_seconds() * 1000),
            )

        try:
            result = cmd_svc.run(request)
            duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)

            if result.get("timed_out"):
                return TaskResult(
                    success=False,
                    output=result.get("stdout", ""),
                    error=f"Timed out after {timeout}s",
                    duration_ms=duration_ms,
                )

            return TaskResult(
                success=result.get("ok", False) and result.get("returncode", -1) == 0,
                output=result.get("stdout", ""),
                error=result.get("stderr", "") or result.get("error", ""),
                duration_ms=duration_ms,
            )
        except (RuntimeError, ValueError) as e:
            return TaskResult(
                success=False,
                output="",
                error=str(e),
                duration_ms=int((datetime.now(timezone.utc) - start).total_seconds() * 1000),
            )

    async def _check_nag(self) -> None:
        nag = self.todo.on_round_complete()
        if nag:
            self._metrics["nag_triggered"] += 1
            # Emit typed event for nag reminder
            typed_event = TypedNagReminder.create(
                message=nag,
                workspace=self.config.workspace,
            )
            await self._emit_typed_event(typed_event)
            await self._bus.broadcast(MessageType.NAG_REMINDER, "director", {"message": nag})

    async def _check_workers(self) -> None:
        restarted = await self._worker_service.handle_failed_workers()
        self._metrics["workers_restarted"] += len(restarted)

    async def _check_budget(self) -> None:
        if not self.config.token_budget:
            return
        status = self.token.get_budget_status()
        if status.is_exceeded and self.state == DirectorState.RUNNING:
            self.state = DirectorState.PAUSED
            # Emit typed event for budget exceeded
            typed_event = TypedBudgetExceeded.create(
                used_tokens=status.used_tokens,
                budget_limit=status.budget_limit if status.budget_limit is not None else 0,
                workspace=self.config.workspace,
            )
            await self._emit_typed_event(typed_event)
            await self._bus.broadcast(
                MessageType.BUDGET_EXCEEDED, "director", {"used": status.used_tokens, "limit": status.budget_limit}
            )

    async def _auto_scale(self) -> None:
        ready_count = await self._task_service.get_ready_task_count()
        await self._worker_service.auto_scale(ready_count)

    async def _on_task_completed(self, message: Message) -> None:
        pass

    async def _on_task_failed(self, message: Message) -> None:
        pass

    async def _on_worker_failed(self, message: Message) -> None:
        pass
