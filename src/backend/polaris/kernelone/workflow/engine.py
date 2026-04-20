"""Workflow Engine - KernelOne workflow DAG/sequential executor.

Migrated from polaris/cells/orchestration/workflow_runtime/internal/runtime_engine/runtime/embedded/engine.py
as part of ACGA 2.0 Cell-split Task #21.

Design principles:
- DI-only: all registries are injected via HandlerRegistry protocol, never imported directly.
- KernelOne-owned state dataclasses live here; Cell-specific state stays in the Cell.
- The SqliteRuntimeStore is an infrastructure adapter imported freely.
- Shared utilities extracted to _engine_utils.py to reduce duplication with SagaWorkflowEngine.

DATA CONTAINER PATTERN NOTE:
    WorkflowRuntimeState and TaskRuntimeState are @dataclass data containers.
    State transitions are managed by WorkflowEngine, not by the dataclasses themselves.
    This design allows WorkflowEngine to implement complex DAG execution logic
    while the state dataclasses remain simple data holders.

    The state dataclasses implement these compatible patterns:
    - status field: String-based status values (pending, running, completed, etc.)
    - is_terminal checks via _TASK_TERMINAL set

    If you need a class-based state machine with internal transition logic,
    see BaseStateMachine (polaris/kernelone/state_machine.py).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Protocol

from polaris.kernelone.constants import DEFAULT_OPERATION_TIMEOUT_SECONDS
from polaris.kernelone.trace import create_task_with_context
from polaris.kernelone.utils import _now

from ._engine_utils import (
    calculate_retry_delay,
    cancel_running_tasks,
    extract_existing_payload,
    invoke_handler,
    load_persisted_task_states,
    norm_result,
    normalize_resume_payload,
    unwrap_task_outcome,
)
from .base import RuntimeSubmissionResult, WorkflowSnapshot
from .contracts import TaskSpec, WorkflowContract, WorkflowContractError
from .dlq import (
    DeadLetterItem,
    DeadLetterQueuePort,
    DLQReason,
    DLQRequeueWorker,
    WorkflowDLQRetryHandler,
    append_dlq_event,
)
from .task_status import TERMINAL_STATUSES, WorkflowTaskStatus

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from .activity_runner import ActivityRunner
    from .task_queue import TaskQueueManager
    from .timer_wheel import TimerWheel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HandlerRegistry Protocol (DI boundary - Cell provides the implementation)
# ---------------------------------------------------------------------------


class WorkflowRegistryOpsPort(Protocol):
    """Minimal read-only ops required from a workflow registry."""

    def list_workflows(self) -> list[str]:
        """List all registered workflow names."""
        ...

    def get(self, name: str) -> Any | None:
        """Get a workflow definition by name; result has a `.handler` attribute."""
        ...


class ActivityRegistryOpsPort(Protocol):
    """Minimal read-only ops required from an activity registry."""

    def list_activities(self) -> list[str]:
        """List all registered activity names."""
        ...

    def get(self, name: str) -> Any | None:
        """Get an activity definition by name; result has a `.handler` attribute."""
        ...


class HandlerRegistryPort(Protocol):
    """DI protocol for workflow + activity registry access.

    The Cell owning the registries provides a concrete implementation;
    WorkflowEngine never imports the registry modules directly.
    """

    @property
    def workflows(self) -> WorkflowRegistryOpsPort: ...

    @property
    def activities(self) -> ActivityRegistryOpsPort: ...


class WorkflowRuntimeStorePort(Protocol):
    """Minimal workflow runtime store contract required by WorkflowEngine."""

    def init_schema(self) -> None: ...
    async def get_execution(self, workflow_id: str) -> Any: ...
    async def create_execution(
        self,
        workflow_id: str,
        workflow_name: str,
        payload: dict[str, Any],
    ) -> None: ...
    async def append_event(
        self,
        workflow_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None: ...
    async def update_execution(
        self,
        workflow_id: str,
        *,
        status: str,
        result: dict[str, Any],
        close_time: str,
    ) -> None: ...
    async def upsert_task_state(
        self,
        *,
        workflow_id: str,
        task_id: str,
        task_type: str,
        handler_name: str,
        status: str,
        attempt: int,
        max_attempts: int,
        started_at: str | None,
        ended_at: str | None,
        result: dict[str, Any] | None,
        error: str,
        metadata: dict[str, Any],
    ) -> None: ...
    async def create_snapshot(self, workflow_id: str) -> WorkflowSnapshot: ...
    async def list_task_states(self, workflow_id: str) -> list[Any]: ...
    async def get_events(self, workflow_id: str, *, limit: int = 100) -> list[Any]: ...


# ---------------------------------------------------------------------------
# State dataclasses (KernelOne-owned)
# ---------------------------------------------------------------------------


@dataclass
class TaskRuntimeState:
    """Per-task runtime state tracked by the WorkflowEngine."""

    task_id: str
    task_type: str
    handler_name: str
    status: str = WorkflowTaskStatus.PENDING.value
    attempt: int = 0
    max_attempts: int = 1
    started_at: str | None = None
    ended_at: str | None = None
    result: dict[str, Any] | None = None
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowRuntimeState:
    """Per-workflow runtime state tracked by the WorkflowEngine."""

    workflow_id: str
    workflow_name: str
    payload: dict[str, Any]
    contract: WorkflowContract
    task_states: dict[str, TaskRuntimeState] = field(default_factory=dict)
    task_outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending_signals: list[dict[str, Any]] = field(default_factory=list)
    paused: bool = False
    pause_event: asyncio.Event = field(default_factory=asyncio.Event)
    cancel_requested: bool = False
    fail_fast_triggered: bool = False
    last_error: str = ""
    start_time: str = ""


@dataclass
class TaskExecutionOutcome:
    """Outcome returned by _execute_spec."""

    task_id: str
    status: str
    attempt: int
    started_at: str
    ended_at: str
    result: dict[str, Any] | None = None
    error: str = ""


# ---------------------------------------------------------------------------
# WorkflowEngine
# ---------------------------------------------------------------------------


class WorkflowEngine:
    """Self-hosted workflow runtime with DAG and sequential handler execution support.

    Dependencies are injected via constructor or post-construction registration.
    Registry access is DI-only (see HandlerRegistryPort protocol above).
    """

    def __init__(
        self,
        store: WorkflowRuntimeStorePort,
        timer_wheel: TimerWheel,
        task_queue_manager: TaskQueueManager,
        activity_runner: ActivityRunner,
        handler_registry: HandlerRegistryPort | None = None,
        dead_letter_queue: DeadLetterQueuePort | None = None,
        *,
        max_concurrent_workflows: int = 100,
        default_task_timeout_seconds: float = DEFAULT_OPERATION_TIMEOUT_SECONDS,
        checkpoint_interval_seconds: float = 60.0,
    ) -> None:
        self._store = store
        self._timer_wheel = timer_wheel
        self._task_queue_manager = task_queue_manager
        self._activity_runner = activity_runner
        self._handler_registry = handler_registry
        self._dead_letter_queue = dead_letter_queue
        self._default_task_timeout_seconds = max(0.01, float(default_task_timeout_seconds))
        self._checkpoint_interval = max(0.0, float(checkpoint_interval_seconds))
        self._workflow_tasks: dict[str, asyncio.Task[None]] = {}
        self._workflow_state: dict[str, WorkflowRuntimeState] = {}
        self._workflow_handlers: dict[str, Callable[..., Any]] = {}
        self._activity_handlers: dict[str, Callable[..., Awaitable[Any]]] = {}
        self._workflow_contexts: dict[str, Any] = {}
        self._workflow_snapshot_cache: dict[str, dict[str, Any]] = {}
        self._workflow_semaphore = asyncio.Semaphore(max(1, int(max_concurrent_workflows)))
        self._lock = asyncio.Lock()
        self._running = False
        self._dlq_worker: DLQRequeueWorker | None = None

    def set_handler_registry(self, registry: HandlerRegistryPort) -> None:
        """Set the handler registry after construction (allows DI frameworks to wire it)."""
        self._handler_registry = registry

    def _now(self) -> str:
        """Return current UTC timestamp as ISO string."""
        return _now()

    async def start(self) -> None:
        if self._running:
            return
        self._store.init_schema()
        await self._timer_wheel.start()
        await self._activity_runner.start()
        self._bootstrap_handlers()
        # Start DLQ requeue worker if DLQ is configured
        if self._dead_letter_queue is not None:
            retry_handler = WorkflowDLQRetryHandler(
                engine=self,
                default_workflow_name="_engine_dlq_recovery",
            )
            self._dlq_worker = DLQRequeueWorker(
                dlq=self._dead_letter_queue,
                workflow_id="_engine_dlq",
                workflow_name="_engine_dlq_recovery",
                retry_handler=retry_handler,
                poll_interval=10.0,
                max_requeue_attempts=3,
            )
            await self._dlq_worker.start()
            logger.info("DLQ requeue worker started")
        self._running = True
        logger.info("WorkflowEngine started")

    async def stop(self) -> None:
        if not self._running:
            return
        # Stop DLQ worker first - no new DLQ items should be created after this
        if self._dlq_worker is not None:
            await self._dlq_worker.stop()
            self._dlq_worker = None
            logger.info("DLQ requeue worker stopped")
        async with self._lock:
            tasks = list(self._workflow_tasks.values())
            self._workflow_tasks.clear()
            for task in tasks:
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._timer_wheel.stop()
        await self._activity_runner.stop()
        async with self._lock:
            self._workflow_state.clear()
        self._running = False
        logger.info("WorkflowEngine stopped")

    def _bootstrap_handlers(self) -> None:
        """Register all handlers from the injected HandlerRegistry.

        This method is called once at start().  If no registry is injected
        the engine relies on explicit register_workflow / register_activity calls.
        """
        if self._handler_registry is None:
            return
        try:
            for name in self._handler_registry.workflows.list_workflows():
                definition = self._handler_registry.workflows.get(name)
                if definition is not None:
                    handler = getattr(definition, "handler", None)
                    if callable(handler):
                        self.register_workflow(name, handler)
        except (RuntimeError, ValueError) as exc:
            logger.debug("Workflow bootstrap from registry skipped: %s", exc)

        try:
            for name in self._handler_registry.activities.list_activities():
                definition = self._handler_registry.activities.get(name)
                if definition is not None:
                    handler = getattr(definition, "handler", None)
                    if callable(handler):
                        self.register_activity(name, handler)
        except (RuntimeError, ValueError) as exc:
            logger.debug("Activity bootstrap from registry skipped: %s", exc)

    def register_workflow(self, workflow_name: str, handler: Callable[..., Any]) -> None:
        name = str(workflow_name or "").strip()
        if not name:
            raise ValueError("workflow_name is required")
        self._workflow_handlers[name] = handler

    def register_activity(self, activity_name: str, handler: Callable[..., Awaitable[Any]]) -> None:
        name = str(activity_name or "").strip()
        if not name:
            raise ValueError("activity_name is required")
        self._activity_runner.register_handler(name, handler)
        self._activity_handlers[name] = handler

    async def start_workflow(
        self,
        workflow_name: str,
        workflow_id: str,
        payload: dict[str, Any],
    ) -> RuntimeSubmissionResult:
        wid = str(workflow_id or "").strip()
        wname = str(workflow_name or "").strip()
        normalized_payload = payload if isinstance(payload, dict) else {}
        if not wid or not wname:
            return RuntimeSubmissionResult(
                submitted=False,
                status="invalid_request",
                workflow_id=wid,
                error="workflow_name and workflow_id are required",
            )
        try:
            contract = WorkflowContract.from_payload(
                normalized_payload,
                default_timeout_seconds=self._default_task_timeout_seconds,
            )
        except WorkflowContractError as exc:
            errors: list[str] = []
            if hasattr(exc, "errors"):
                errors = list(exc.errors)  # type: ignore[attr-defined]
            return RuntimeSubmissionResult(
                submitted=False,
                status="invalid_contract",
                workflow_id=wid,
                error=str(exc),
                details={"errors": errors},
            )

        async with self._lock:
            if await self._store.get_execution(wid):
                return RuntimeSubmissionResult(
                    submitted=False,
                    status="already_exists",
                    workflow_id=wid,
                    error=f"Workflow {wid} already exists",
                )
            self._workflow_snapshot_cache.pop(wid, None)
            await self._store.create_execution(wid, wname, normalized_payload)
            now = self._now()
            state = WorkflowRuntimeState(
                workflow_id=wid,
                workflow_name=wname,
                payload=normalized_payload,
                contract=contract,
                start_time=now,
                task_states={
                    spec.task_id: TaskRuntimeState(
                        task_id=spec.task_id,
                        task_type=spec.task_type,
                        handler_name=spec.handler_name,
                        max_attempts=spec.retry_policy.max_attempts,
                    )
                    for spec in contract.task_specs
                },
            )
            self._workflow_state[wid] = state
            for task_state in state.task_states.values():
                await self._persist_task_state(wid, task_state)
            task = create_task_with_context(self._run_workflow(wid))
            self._workflow_tasks[wid] = task

        await self._store.append_event(
            wid,
            "workflow_contract_loaded",
            {
                "mode": contract.mode,
                "task_count": contract.task_count,
                "max_concurrency": contract.max_concurrency,
                "continue_on_error": contract.continue_on_error,
            },
        )
        return RuntimeSubmissionResult(
            submitted=True,
            status="started",
            workflow_id=wid,
            run_id=wid,
            details={
                "mode": contract.mode,
                "task_count": contract.task_count,
                "max_concurrency": contract.max_concurrency,
            },
        )

    async def resume_workflow(
        self,
        workflow_name: str,
        workflow_id: str,
        payload: dict[str, Any] | None = None,
    ) -> RuntimeSubmissionResult:
        """Resume a workflow from a persisted checkpoint.

        Loads workflow state from the store and continues execution,
        skipping tasks that are already in a terminal state.

        Args:
            workflow_name: Name of the workflow (must match original).
            workflow_id: ID of the workflow to resume.
            payload: Optional new payload (merged with original).

        Returns:
            RuntimeSubmissionResult indicating whether resume was submitted.
        """
        wid = str(workflow_id or "").strip()
        wname = str(workflow_name or "").strip()
        if not wid or not wname:
            return RuntimeSubmissionResult(
                submitted=False,
                status="invalid_request",
                workflow_id=wid,
                error="workflow_name and workflow_id are required",
            )

        async with self._lock:
            existing = await self._store.get_execution(wid)
            if existing is None:
                return RuntimeSubmissionResult(
                    submitted=False,
                    status="not_found",
                    workflow_id=wid,
                    error=f"Workflow {wid} not found - cannot resume",
                )
            # Check if already running
            if wid in self._workflow_state:
                return RuntimeSubmissionResult(
                    submitted=False,
                    status="already_running",
                    workflow_id=wid,
                    error=f"Workflow {wid} is already running",
                )

            # Load task states from store
            persisted_by_id = await load_persisted_task_states(self._store, wid)

            # Reconstruct contract from payload
            # If new payload provided, use it; otherwise preserve original payload from existing execution
            existing_payload = extract_existing_payload(existing)
            normalized_payload = normalize_resume_payload(payload, existing_payload)
            try:
                contract = WorkflowContract.from_payload(
                    normalized_payload,
                    default_timeout_seconds=self._default_task_timeout_seconds,
                )
            except WorkflowContractError as exc:
                return RuntimeSubmissionResult(
                    submitted=False,
                    status="invalid_contract",
                    workflow_id=wid,
                    error=str(exc),
                )

            # Build task_states from persisted + contract defaults
            task_states: dict[str, TaskRuntimeState] = {}
            for spec in contract.task_specs:
                pstate = persisted_by_id.get(spec.task_id)
                if pstate is not None:
                    # Restore persisted state
                    task_states[spec.task_id] = TaskRuntimeState(
                        task_id=getattr(pstate, "task_id", spec.task_id),
                        task_type=getattr(pstate, "task_type", spec.task_type),
                        handler_name=getattr(pstate, "handler_name", spec.handler_name),
                        status=getattr(pstate, "status", WorkflowTaskStatus.PENDING.value),
                        attempt=int(getattr(pstate, "attempt", 0)),
                        max_attempts=int(getattr(pstate, "max_attempts", spec.retry_policy.max_attempts)),
                        started_at=str(getattr(pstate, "started_at", None) or ""),
                        ended_at=str(getattr(pstate, "ended_at", None) or ""),
                        result=getattr(pstate, "result", None),
                        error=str(getattr(pstate, "error", "") or ""),
                        metadata=getattr(pstate, "metadata", {}) or {},
                    )
                else:
                    task_states[spec.task_id] = TaskRuntimeState(
                        task_id=spec.task_id,
                        task_type=spec.task_type,
                        handler_name=spec.handler_name,
                        max_attempts=spec.retry_policy.max_attempts,
                    )

            # Load events to get original start_time and outputs
            events = await self._store.get_events(wid, limit=10000)
            original_start_time = ""
            task_outputs: dict[str, dict[str, Any]] = {}
            for event in events:
                etype = getattr(event, "event_type", None)
                if etype == "workflow_contract_loaded":
                    original_start_time = getattr(event, "created_at", "") or self._now()
                elif etype == "task_finished":
                    pl = getattr(event, "payload", {}) or {}
                    tid = pl.get("task_id", "")
                    if tid and pl.get("status") == WorkflowTaskStatus.COMPLETED.value:
                        # Try to restore output from task state
                        ts = task_outputs.get(tid) or persisted_by_id.get(tid)
                        if ts is not None:
                            task_outputs[tid] = getattr(ts, "result", {}) or {}

            now = self._now()
            state = WorkflowRuntimeState(
                workflow_id=wid,
                workflow_name=wname,
                payload=normalized_payload if normalized_payload else {},
                contract=contract,
                start_time=original_start_time or now,
                task_states=task_states,
                task_outputs=task_outputs,
            )
            self._workflow_state[wid] = state

            # Mark running and failed (DLQ) tasks as pending for retry on resume.
            # Terminal states (completed, skipped) are preserved as-is.
            for ts in state.task_states.values():
                if ts.status in (WorkflowTaskStatus.RUNNING.value, WorkflowTaskStatus.FAILED.value):
                    ts.status = WorkflowTaskStatus.PENDING.value
                    ts.attempt = 0
                    ts.error = ""  # Clear error so retry starts fresh

            task = create_task_with_context(self._run_workflow(wid))
            self._workflow_tasks[wid] = task

        await self._store.append_event(
            wid,
            "workflow_resumed",
            {
                "mode": contract.mode,
                "task_count": contract.task_count,
                "resumed_at": self._now(),
            },
        )
        return RuntimeSubmissionResult(
            submitted=True,
            status="resumed",
            workflow_id=wid,
            run_id=wid,
            details={
                "mode": contract.mode,
                "task_count": contract.task_count,
            },
        )

    async def _run_workflow(self, workflow_id: str) -> None:
        async with self._workflow_semaphore:
            state = self._workflow_state.get(workflow_id)
            if state is None:
                return
            status = WorkflowTaskStatus.FAILED.value
            result: dict[str, Any] = {"status": WorkflowTaskStatus.FAILED.value}
            try:
                await self._store.append_event(workflow_id, "workflow_execution_started", {"mode": state.contract.mode})
                if state.contract.is_dag:
                    status, result = await self._run_dag(state)
                else:
                    status, result = await self._run_sequential(state)
            except (RuntimeError, ValueError) as exc:
                state.last_error = str(exc)
                logger.exception("Workflow %s failed", workflow_id)
                status = WorkflowTaskStatus.FAILED.value
                result = self._build_result(state, status=status, error=state.last_error)
            finally:
                await self._store.update_execution(
                    workflow_id,
                    status=status,
                    result=result,
                    close_time=self._now(),
                )
                await self._store.append_event(workflow_id, "workflow_execution_finished", {"status": status})
                await self._timer_wheel.cancel_workflow_timers(workflow_id)
                async with self._lock:
                    self._workflow_tasks.pop(workflow_id, None)
                    self._workflow_state.pop(workflow_id, None)
                    self._workflow_contexts.pop(workflow_id, None)

    def bind_workflow_context(self, workflow_id: str, context: Any) -> None:
        wid = str(workflow_id or "").strip()
        if not wid:
            return
        self._workflow_contexts[wid] = context

    def unbind_workflow_context(self, workflow_id: str) -> None:
        wid = str(workflow_id or "").strip()
        if not wid:
            return
        self._workflow_contexts.pop(wid, None)

    def cache_workflow_snapshot(
        self,
        workflow_id: str,
        snapshot: dict[str, Any] | None,
    ) -> None:
        wid = str(workflow_id or "").strip()
        if not wid:
            return
        if isinstance(snapshot, dict):
            self._workflow_snapshot_cache[wid] = {str(key): value for key, value in snapshot.items()}

    async def _create_periodic_snapshot(self, state: WorkflowRuntimeState) -> None:
        """Persist a checkpoint snapshot of the current workflow state.

        This is called periodically during DAG execution to enable
        workflow resume after engine restart. Failures are logged but
        do not interrupt the workflow.
        """
        try:
            snapshot = await self._store.create_snapshot(state.workflow_id)
            snapshot_dict = {
                "workflow_id": snapshot.workflow_id,
                "workflow_name": snapshot.workflow_name,
                "status": snapshot.status,
                "run_id": snapshot.run_id,
                "start_time": snapshot.start_time,
                "close_time": snapshot.close_time,
                "result": snapshot.result,
                "pending_actions": snapshot.pending_actions,
            }
            cached = self._workflow_snapshot_cache.get(state.workflow_id) or {}
            cached.update(snapshot_dict)
            self._workflow_snapshot_cache[state.workflow_id] = cached
            logger.debug(
                "Periodic checkpoint saved for workflow %s (pending_actions=%d)",
                state.workflow_id,
                len(snapshot.pending_actions),
            )
        except (RuntimeError, ValueError):
            logger.exception(
                "Failed to create periodic checkpoint for workflow %s",
                state.workflow_id,
            )

    async def _run_sequential(self, state: WorkflowRuntimeState) -> tuple[str, dict[str, Any]]:
        handler = self._workflow_handlers.get(state.workflow_name)
        if handler is None:
            raise RuntimeError(f"No handler for workflow `{state.workflow_name}`")
        timeout_seconds = self._coerce_float(
            state.payload.get("timeout_seconds"),
            default=self._default_task_timeout_seconds,
        )
        result = await self._invoke_handler(
            handler,
            workflow_id=state.workflow_id,
            payload=dict(state.payload),
            timeout_seconds=timeout_seconds,
            runtime_engine=self,
        )
        norm_result = self._norm(result)
        # Checkpoint after handler completes to persist completion state.
        # If engine crashes after this point, resume will detect already-completed
        # workflow and return immediately (no re-execution).
        if self._checkpoint_interval > 0:
            await self._create_periodic_snapshot(state)
        return WorkflowTaskStatus.COMPLETED.value, {
            "status": WorkflowTaskStatus.COMPLETED.value,
            "mode": "sequential",
            "result": norm_result,
        }

    async def _run_dag(self, state: WorkflowRuntimeState) -> tuple[str, dict[str, Any]]:
        specs_by_id = {spec.task_id: spec for spec in state.contract.task_specs}
        pending = set(specs_by_id.keys())
        running: dict[str, asyncio.Task[TaskExecutionOutcome]] = {}
        workflow_start_time = asyncio.get_running_loop().time()
        last_checkpoint_time = workflow_start_time
        workflow_timeout = state.contract.workflow_timeout_seconds
        checkpoint_interval = self._checkpoint_interval

        while pending or running:
            elapsed = asyncio.get_running_loop().time() - workflow_start_time
            if elapsed >= workflow_timeout:
                logger.warning("Workflow %s timed out after %.1fs", state.workflow_id, elapsed)
                await self._cancel_running(running)
                timeout_error = f"Workflow timeout after {workflow_timeout}s"
                state.last_error = timeout_error
                # Route unstarted tasks to DLQ before clearing
                await self._enqueue_pending_to_dlq(state, set(pending), DLQReason.WORKFLOW_TIMEOUT, timeout_error)
                await self._mark_tasks(state, set(pending), WorkflowTaskStatus.CANCELLED.value, timeout_error)
                pending.clear()
                break

            await self._apply_signals(state)
            if state.cancel_requested:
                await self._cancel_running(running)
                cancel_error = "Workflow cancelled"
                await self._enqueue_pending_to_dlq(state, set(pending), DLQReason.WORKFLOW_CANCELLED, cancel_error)
                await self._mark_tasks(state, set(pending), WorkflowTaskStatus.CANCELLED.value, cancel_error)
                pending.clear()
                break
            if state.fail_fast_triggered:
                await self._cancel_running(running)
                fail_fast_error = "Fail-fast triggered"
                # fail_fast tasks are intentionally skipped, not dead-lettered
                await self._mark_tasks(state, set(pending), WorkflowTaskStatus.SKIPPED.value, fail_fast_error)
                pending.clear()
                break

            if not state.paused:
                blocked = self._blocked_ids(state, pending, specs_by_id)
                if blocked:
                    await self._mark_tasks(
                        state, blocked, WorkflowTaskStatus.BLOCKED.value, "Blocked by dependency failure"
                    )
                    pending.difference_update(blocked)
                ready = self._ready_specs(state, pending, specs_by_id)
                while ready and len(running) < max(1, state.contract.max_concurrency):
                    spec = ready.pop(0)
                    if spec is None:
                        continue
                    pending.discard(spec.task_id)
                    running[spec.task_id] = create_task_with_context(self._execute_spec(state, spec))

            if running:
                done, _ = await asyncio.wait(list(running.values()), timeout=0.1, return_when=asyncio.FIRST_COMPLETED)
                if not done:
                    # Periodic checkpoint even when no tasks completed this cycle
                    if checkpoint_interval > 0:
                        time_since_checkpoint = asyncio.get_running_loop().time() - last_checkpoint_time
                        if time_since_checkpoint >= checkpoint_interval:
                            await self._create_periodic_snapshot(state)
                            last_checkpoint_time = asyncio.get_running_loop().time()
                    continue
                for fut in done:
                    task_id = next((tid for tid, task in running.items() if task is fut), "")
                    if task_id:
                        running.pop(task_id, None)
                    outcome = await self._unwrap_outcome(fut, task_id)
                    await self._apply_outcome(state, specs_by_id, outcome)
                # Periodic checkpoint after task completion cycle
                if checkpoint_interval > 0:
                    time_since_checkpoint = asyncio.get_running_loop().time() - last_checkpoint_time
                    if time_since_checkpoint >= checkpoint_interval:
                        await self._create_periodic_snapshot(state)
                        last_checkpoint_time = asyncio.get_running_loop().time()
            elif pending:
                if state.paused:
                    await state.pause_event.wait()
                    continue
                blocked_reasons: dict[str, str] = {}
                for task_id in pending:
                    pending_spec: TaskSpec | None = specs_by_id.get(task_id)
                    if pending_spec is None:
                        blocked_reasons[task_id] = "task_spec_not_found"
                        continue
                    deps_not_completed = []
                    for dep in spec.depends_on:
                        dep_state = state.task_states.get(dep)
                        if dep_state is None:
                            deps_not_completed.append(f"{dep}(missing)")
                        elif dep_state.status != WorkflowTaskStatus.COMPLETED.value:
                            deps_not_completed.append(f"{dep}({dep_state.status})")
                    if deps_not_completed:
                        blocked_reasons[task_id] = f"waiting_on: {', '.join(deps_not_completed)}"
                    else:
                        blocked_reasons[task_id] = "ready_but_no_slots"
                blocked_detail = "; ".join(f"{k}: {v}" for k, v in blocked_reasons.items())
                await self._mark_tasks(
                    state,
                    set(pending),
                    WorkflowTaskStatus.BLOCKED.value,
                    f"Dependency graph cannot progress: {blocked_detail}",
                )
                pending.clear()

        status = self._resolve_status(state)
        return status, self._build_result(state, status=status)

    async def _execute_spec(self, state: WorkflowRuntimeState, spec: TaskSpec) -> TaskExecutionOutcome:
        task_state = state.task_states[spec.task_id]
        task_state.status = WorkflowTaskStatus.RUNNING.value
        task_state.started_at = self._now()
        await self._persist_task_state(state.workflow_id, task_state)
        input_payload = self._resolve_input(state, spec)
        for attempt in range(1, spec.retry_policy.max_attempts + 1):
            task_state.attempt = attempt
            await self._persist_task_state(state.workflow_id, task_state)
            await self._store.append_event(
                state.workflow_id,
                "task_attempt_started",
                {"task_id": spec.task_id, "attempt": attempt, "max_attempts": spec.retry_policy.max_attempts},
            )
            try:
                raw = await self._dispatch(spec, state, input_payload)
                return TaskExecutionOutcome(
                    task_id=spec.task_id,
                    status=WorkflowTaskStatus.COMPLETED.value,
                    attempt=attempt,
                    started_at=task_state.started_at or self._now(),
                    ended_at=self._now(),
                    result=self._norm(raw),
                )
            except asyncio.CancelledError:
                task_state.status = WorkflowTaskStatus.CANCELLED.value
                task_state.error = "Task cancelled"
                await self._persist_task_state(state.workflow_id, task_state)
                return TaskExecutionOutcome(
                    spec.task_id,
                    WorkflowTaskStatus.CANCELLED.value,
                    attempt,
                    task_state.started_at or self._now(),
                    self._now(),
                    error="Task cancelled",
                )
            except (RuntimeError, ValueError) as exc:
                if attempt >= spec.retry_policy.max_attempts:
                    error_msg = str(exc).strip() or "task_failed"
                    # Route to DLQ on retry exhaustion
                    if self._dead_letter_queue is not None:
                        dlq_item = DeadLetterItem(
                            task_id=spec.task_id,
                            workflow_id=state.workflow_id,
                            handler_name=spec.handler_name,
                            input_payload=input_payload,
                            error=error_msg,
                            failed_at=task_state.started_at or self._now(),
                            dlq_at=self._now(),
                            attempt=attempt,
                            max_attempts=spec.retry_policy.max_attempts,
                            dlq_reason=DLQReason.RETRY_EXHAUSTED,
                            metadata={
                                "task_type": spec.task_type,
                                "last_error": error_msg,
                                "workflow_name": state.workflow_name,
                            },
                        )
                        await self._dead_letter_queue.enqueue(dlq_item)
                        await append_dlq_event(self._store, state.workflow_id, dlq_item)
                    return TaskExecutionOutcome(
                        spec.task_id,
                        WorkflowTaskStatus.FAILED.value,
                        attempt,
                        task_state.started_at or self._now(),
                        self._now(),
                        error=error_msg,
                    )
                task_state.status = WorkflowTaskStatus.RETRYING.value
                await self._persist_task_state(state.workflow_id, task_state)
                delay = self._retry_delay(spec, attempt)
                await self._store.append_event(
                    state.workflow_id,
                    "task_retry_scheduled",
                    {
                        "task_id": spec.task_id,
                        "attempt": attempt,
                        "next_attempt": attempt + 1,
                        "delay_seconds": delay,
                        "error": str(exc),
                    },
                )
                await asyncio.sleep(delay)
        return TaskExecutionOutcome(
            spec.task_id,
            WorkflowTaskStatus.FAILED.value,
            task_state.attempt,
            task_state.started_at or self._now(),
            self._now(),
            error="retry_exhausted",
        )

    async def _dispatch(self, spec: TaskSpec, state: WorkflowRuntimeState, input_payload: dict[str, Any]) -> Any:
        timeout_seconds = max(0.01, float(spec.timeout_seconds))
        if spec.task_type == "noop":
            return {"input": input_payload}
        if spec.task_type == "activity":
            return await self._activity_runner.execute(
                spec.handler_name, input_payload, timeout_seconds=timeout_seconds
            )
        if spec.task_type == "workflow":
            handler = self._workflow_handlers.get(spec.handler_name)
            if handler is None:
                raise RuntimeError(f"No workflow task handler `{spec.handler_name}`")
            return await self._invoke_handler(
                handler,
                workflow_id=state.workflow_id,
                payload=input_payload,
                timeout_seconds=timeout_seconds,
                runtime_engine=self,
            )
        raise RuntimeError(f"Unsupported task type `{spec.task_type}`")

    async def _invoke_handler(
        self,
        handler: Callable[..., Any],
        *,
        workflow_id: str,
        payload: dict[str, Any],
        timeout_seconds: float,
        runtime_engine: Any | None = None,
    ) -> Any:
        """Invoke handler with flexible signature support (delegated to shared utility)."""
        return await invoke_handler(
            handler,
            workflow_id=workflow_id,
            payload=payload,
            timeout_seconds=timeout_seconds,
            runtime_engine=runtime_engine,
        )

    def _resolve_input(self, state: WorkflowRuntimeState, spec: TaskSpec) -> dict[str, Any]:
        resolved = {str(key): value for key, value in spec.input_payload.items()}
        for key, ref in spec.input_from.items():
            source_task, path = ref.split(".", 1)
            source = state.task_outputs.get(source_task)
            if source is None:
                raise RuntimeError(f"Reference source `{source_task}` unavailable for task `{spec.task_id}`")
            try:
                resolved[key] = self._extract(source, path)
            except KeyError as e:
                raise RuntimeError(
                    f"Failed to resolve input `{key}` from `{ref}` for task `{spec.task_id}`: {e}"
                ) from e
        resolved.setdefault("workflow_id", state.workflow_id)
        resolved.setdefault("task_id", spec.task_id)
        return resolved

    def _extract(self, payload: Any, path: str) -> Any:
        current: Any = payload
        for token in [part.strip() for part in str(path).split(".") if part.strip()]:
            try:
                if isinstance(current, dict):
                    current = current[token]
                elif isinstance(current, list):
                    current = current[int(token)]
                else:
                    raise KeyError(f"Cannot traverse token `{token}` on non-dict/list value")
            except (KeyError, IndexError, ValueError) as exc:
                raise KeyError(f"Cannot extract path `{path}` at token `{token}`: {exc}") from exc
        return current

    def _retry_delay(self, spec: TaskSpec, attempt: int) -> float:
        """Calculate retry delay with exponential backoff and jitter (delegated to shared utility)."""
        return calculate_retry_delay(spec, attempt)

    def _ready_specs(
        self,
        state: WorkflowRuntimeState,
        pending: set[str],
        specs_by_id: dict[str, TaskSpec],
    ) -> list[TaskSpec]:
        ready: list[TaskSpec] = []
        completed_statuses = {WorkflowTaskStatus.COMPLETED.value, WorkflowTaskStatus.SKIPPED.value}
        for task_id in pending:
            spec = specs_by_id[task_id]
            deps = [state.task_states[dep].status for dep in spec.depends_on if dep in state.task_states]
            if not deps or all(item in completed_statuses for item in deps):
                ready.append(spec)
        ready.sort(key=lambda s: (len(s.depends_on), s.task_id))
        return ready

    def _blocked_ids(
        self,
        state: WorkflowRuntimeState,
        pending: set[str],
        specs_by_id: dict[str, TaskSpec],
    ) -> set[str]:
        blocked: set[str] = set()
        for task_id in pending:
            spec = specs_by_id[task_id]
            deps = [state.task_states[dep].status for dep in spec.depends_on if dep in state.task_states]
            if deps and any(
                item
                in {
                    WorkflowTaskStatus.FAILED.value,
                    WorkflowTaskStatus.BLOCKED.value,
                    WorkflowTaskStatus.CANCELLED.value,
                    WorkflowTaskStatus.SKIPPED.value,
                }
                for item in deps
            ):
                blocked.add(task_id)
        return blocked

    async def _unwrap_outcome(self, fut: asyncio.Task[TaskExecutionOutcome], task_id: str) -> TaskExecutionOutcome:
        """Unwrap task outcome, handling cancellation gracefully (delegated to shared utility)."""
        return await unwrap_task_outcome(fut, task_id, now_func=self._now)

    async def _apply_outcome(
        self,
        state: WorkflowRuntimeState,
        specs_by_id: dict[str, TaskSpec],
        outcome: TaskExecutionOutcome,
    ) -> None:
        task_state = state.task_states.get(outcome.task_id)
        if task_state is None:
            return
        task_state.status = outcome.status
        task_state.attempt = int(outcome.attempt)
        task_state.started_at = outcome.started_at
        task_state.ended_at = outcome.ended_at
        task_state.result = self._norm(outcome.result)
        task_state.error = str(outcome.error or "").strip()
        await self._persist_task_state(state.workflow_id, task_state)
        await self._store.append_event(
            state.workflow_id,
            "task_finished",
            {
                "task_id": task_state.task_id,
                "status": task_state.status,
                "attempt": task_state.attempt,
                "error": task_state.error,
            },
        )
        if task_state.status == WorkflowTaskStatus.COMPLETED.value:
            state.task_outputs[task_state.task_id] = task_state.result or {}
            return
        fail_fast = not state.contract.continue_on_error
        spec = specs_by_id.get(task_state.task_id)
        if spec is not None and spec.continue_on_error:
            fail_fast = False
        if fail_fast and task_state.status in (WorkflowTaskStatus.FAILED.value, WorkflowTaskStatus.CANCELLED.value):
            state.fail_fast_triggered = True
            await self._store.append_event(
                state.workflow_id,
                "workflow_fail_fast_triggered",
                {"task_id": task_state.task_id, "status": task_state.status, "error": task_state.error},
            )

    async def _cancel_running(self, running: dict[str, asyncio.Task[TaskExecutionOutcome]]) -> None:
        """Cancel running tasks and wait for them to settle (delegated to shared utility)."""
        await cancel_running_tasks(running, timeout=5.0)

    async def _mark_tasks(
        self,
        state: WorkflowRuntimeState,
        task_ids: set[str],
        status: str,
        error: str,
    ) -> None:
        for task_id in sorted(task_ids):
            task_state = state.task_states.get(task_id)
            if task_state is None or task_state.status in TERMINAL_STATUSES:
                continue
            task_state.status = status
            task_state.error = error
            task_state.ended_at = self._now()
            await self._persist_task_state(state.workflow_id, task_state)
            await self._store.append_event(
                state.workflow_id,
                "task_marked_terminal",
                {"task_id": task_id, "status": status, "error": error},
            )

    async def _enqueue_pending_to_dlq(
        self,
        state: WorkflowRuntimeState,
        pending: set[str],
        dlq_reason: DLQReason,
        error: str,
    ) -> None:
        """Route pending tasks to the dead letter queue.

        Called when a workflow terminates abnormally (timeout/cancel/fail-fast)
        to capture unstarted tasks so they can be retried or investigated later.

        Args:
            state: Current workflow runtime state.
            pending: Set of task IDs that were pending (never started).
            dlq_reason: Reason for DLQ routing.
            error: Error message to associate with each DLQ item.
        """
        if self._dead_letter_queue is None:
            return
        specs_by_id = {spec.task_id: spec for spec in state.contract.task_specs}
        for task_id in pending:
            spec = specs_by_id.get(task_id)
            task_state = state.task_states.get(task_id)
            if spec is None or task_state is None:
                continue
            # Only enqueue tasks that never ran (attempt=0) or were waiting
            item = DeadLetterItem(
                task_id=task_id,
                workflow_id=state.workflow_id,
                handler_name=spec.handler_name,
                input_payload=self._resolve_input(state, spec),
                error=error,
                failed_at=self._now(),
                dlq_at=self._now(),
                attempt=task_state.attempt,
                max_attempts=spec.retry_policy.max_attempts,
                dlq_reason=dlq_reason,
                metadata={
                    "task_type": spec.task_type,
                    "pending_reason": error,
                    "workflow_name": state.workflow_name,
                },
            )
            await self._dead_letter_queue.enqueue(item)
            await append_dlq_event(self._store, state.workflow_id, item)

    async def _persist_task_state(self, workflow_id: str, task_state: TaskRuntimeState) -> None:
        await self._store.upsert_task_state(
            workflow_id=workflow_id,
            task_id=task_state.task_id,
            task_type=task_state.task_type,
            handler_name=task_state.handler_name,
            status=task_state.status,
            attempt=task_state.attempt,
            max_attempts=task_state.max_attempts,
            started_at=task_state.started_at,
            ended_at=task_state.ended_at,
            result=task_state.result,
            error=task_state.error,
            metadata=task_state.metadata,
        )

    async def _apply_signals(self, state: WorkflowRuntimeState) -> None:
        async with self._lock:
            signals = list(state.pending_signals)
            state.pending_signals.clear()
        for signal in signals:
            name = str(signal.get("signal_name") or "").strip().lower()
            raw_args = signal.get("args")
            args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
            if name == "pause":
                state.paused = True
                state.pause_event.clear()
                await self._store.append_event(state.workflow_id, "workflow_paused", {"signal": "pause"})
            elif name == "resume":
                state.paused = False
                state.pause_event.set()
                await self._store.append_event(state.workflow_id, "workflow_resumed", {"signal": "resume"})
            elif name == "cancel":
                state.cancel_requested = True
                await self._store.append_event(state.workflow_id, "workflow_cancel_requested", {"signal": "cancel"})
            elif name == "set_concurrency":
                value = self._coerce_int(args.get("value"), default=state.contract.max_concurrency)
                state.contract = replace(
                    state.contract,
                    max_concurrency=value,
                )
                await self._store.append_event(
                    state.workflow_id, "workflow_concurrency_updated", {"max_concurrency": value}
                )
            else:
                await self._store.append_event(
                    state.workflow_id, "workflow_custom_signal_recorded", {"signal": name, "args": args}
                )

    def _resolve_status(self, state: WorkflowRuntimeState) -> str:
        statuses = [task.status for task in state.task_states.values()]
        if not statuses:
            return WorkflowTaskStatus.COMPLETED.value
        if any(item == WorkflowTaskStatus.RUNNING.value for item in statuses):
            return WorkflowTaskStatus.RUNNING.value
        if any(item == WorkflowTaskStatus.FAILED.value for item in statuses):
            return WorkflowTaskStatus.FAILED.value
        if any(item == WorkflowTaskStatus.BLOCKED.value for item in statuses):
            return WorkflowTaskStatus.FAILED.value
        if any(item == WorkflowTaskStatus.CANCELLED.value for item in statuses):
            return WorkflowTaskStatus.CANCELLED.value
        skipped_count = sum(1 for item in statuses if item == WorkflowTaskStatus.SKIPPED.value)
        if skipped_count == len(statuses):
            return WorkflowTaskStatus.COMPLETED.value
        if all(item == WorkflowTaskStatus.COMPLETED.value for item in statuses):
            return WorkflowTaskStatus.COMPLETED.value
        return WorkflowTaskStatus.RUNNING.value

    def _build_result(self, state: WorkflowRuntimeState, *, status: str, error: str = "") -> dict[str, Any]:
        tasks = {
            task_id: {
                "status": task.status,
                "attempt": task.attempt,
                "max_attempts": task.max_attempts,
                "error": task.error,
                "result": task.result,
                "started_at": task.started_at,
                "ended_at": task.ended_at,
            }
            for task_id, task in state.task_states.items()
        }
        return {
            "status": status,
            "workflow_id": state.workflow_id,
            "workflow_name": state.workflow_name,
            "mode": state.contract.mode,
            "start_time": state.start_time,
            "close_time": self._now(),
            "tasks": tasks,
            "outputs": dict(state.task_outputs),
            "paused": state.paused,
            "cancel_requested": state.cancel_requested,
            "error": str(error or state.last_error or "").strip(),
        }

    async def describe_workflow(self, workflow_id: str) -> WorkflowSnapshot:
        return await self._store.create_snapshot(workflow_id)

    async def query_workflow(self, workflow_id: str, query_name: str, *args: Any) -> dict[str, Any]:
        q = str(query_name or "").strip().lower()
        runtime_context = self._workflow_contexts.get(workflow_id)
        if runtime_context is not None:
            raw_queries = getattr(runtime_context, "queries", None)
            query_handlers: dict[str, Any] = raw_queries if isinstance(raw_queries, dict) else {}
            direct_handler = query_handlers.get(query_name)
            normalized_handler = query_handlers.get(q)
            handler = direct_handler if callable(direct_handler) else normalized_handler
            if callable(handler):
                try:
                    value = handler(*args)
                    if inspect.isawaitable(value):
                        value = await value
                    if isinstance(value, dict):
                        result = {str(key): item for key, item in value.items()}
                        # Cache query result so post-completion queries can still
                        # return the last known custom workflow state.
                        cached = self._workflow_snapshot_cache.get(workflow_id) or {}
                        cached.update(result)
                        self._workflow_snapshot_cache[workflow_id] = cached
                        return result
                    if isinstance(value, list):
                        return {"items": value}
                    return {"value": value}
                except (RuntimeError, ValueError) as exc:
                    return {"error": f"Query `{query_name}` failed: {exc}"}

        if q == "get_runtime_snapshot":
            cached_snapshot = self._workflow_snapshot_cache.get(workflow_id)
            if isinstance(cached_snapshot, dict):
                return {str(key): value for key, value in cached_snapshot.items()}
        if q == "get_task_status" and args:
            task_id = str(args[0] or "").strip()
            cached_snapshot = self._workflow_snapshot_cache.get(workflow_id) or {}
            cached_tasks = cached_snapshot.get("tasks")
            if isinstance(cached_tasks, dict) and task_id:
                task_payload = cached_tasks.get(task_id)
                if isinstance(task_payload, dict):
                    return {str(key): value for key, value in task_payload.items()}
        if q == "get_execution_history":
            cached_snapshot = self._workflow_snapshot_cache.get(workflow_id) or {}
            history = cached_snapshot.get("history")
            if isinstance(history, list):
                return {"items": list(history)}
        if q == "status":
            snapshot = await self._store.create_snapshot(workflow_id)
            return {"workflow_id": workflow_id, "status": snapshot.status}
        if q == "tasks":
            task_states = await self._store.list_task_states(workflow_id)
            return {
                "workflow_id": workflow_id,
                "tasks": {
                    item.task_id: {
                        "task_id": item.task_id,
                        "state": item.status,
                        "attempt": item.attempt,
                        "max_attempts": item.max_attempts,
                        "error": item.error,
                        "result": item.result,
                        "metadata": item.metadata,
                        "updated_at": item.updated_at,
                    }
                    for item in task_states
                },
            }
        if q == "events":
            limit = self._coerce_int(args[0], default=100) if args else 100
            events = await self._store.get_events(workflow_id, limit=limit)
            return {
                "workflow_id": workflow_id,
                "events": [
                    {
                        "seq": event.seq,
                        "type": event.event_type,
                        "payload": event.payload,
                        "created_at": event.created_at,
                    }
                    for event in events
                ],
            }
        if q == "state":
            state = self._workflow_state.get(workflow_id)
            if state is None:
                snapshot = await self._store.create_snapshot(workflow_id)
                return {"workflow_id": workflow_id, "status": snapshot.status, "mode": "persisted"}
            return {
                "workflow_id": workflow_id,
                "mode": state.contract.mode,
                "paused": state.paused,
                "cancel_requested": state.cancel_requested,
                "tasks": {
                    task_id: {
                        "state": task.status,
                        "attempt": task.attempt,
                        "max_attempts": task.max_attempts,
                        "error": task.error,
                        "result": task.result,
                    }
                    for task_id, task in state.task_states.items()
                },
            }
        return {"error": f"Unknown query: {q}"}

    async def cancel_workflow(self, workflow_id: str, reason: str = "") -> dict[str, Any]:
        async with self._lock:
            state = self._workflow_state.get(workflow_id)
            if state is not None:
                state.pending_signals.append({"signal_name": "cancel", "args": {"reason": str(reason or "").strip()}})
            running_task = self._workflow_tasks.get(workflow_id)
        await self._store.append_event(workflow_id, "workflow_cancel_requested", {"reason": str(reason or "").strip()})
        await self._timer_wheel.cancel_workflow_timers(workflow_id)
        if running_task is None:
            try:
                events = await self._store.get_events(workflow_id)
                if not events:
                    return {
                        "cancelled": False,
                        "workflow_id": workflow_id,
                        "error": "Workflow not found or not started",
                    }
                finished_events = [
                    e
                    for e in events
                    if getattr(e, "event_type", None) in ("workflow_execution_finished", "workflow_contract_loaded")
                ]
                if finished_events:
                    return {"cancelled": False, "workflow_id": workflow_id, "error": "Workflow already finished"}
            except (RuntimeError, ValueError) as exc:
                logger.warning("[cancel_workflow] Failed to check workflow status: %s", exc)
        return {"cancelled": True, "workflow_id": workflow_id}

    async def signal_workflow(
        self,
        workflow_id: str,
        signal_name: str,
        signal_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "signal_name": str(signal_name or "").strip().lower(),
            "args": signal_args if isinstance(signal_args, dict) else {},
        }
        async with self._lock:
            state = self._workflow_state.get(workflow_id)
            if state is not None:
                state.pending_signals.append(payload)
                if payload["signal_name"] == "resume":
                    state.pause_event.set()
        await self._store.append_event(workflow_id, "signal_received", payload)
        return {"signalled": True, "workflow_id": workflow_id, "signal": payload["signal_name"]}

    async def submit_activity(
        self,
        activity_name: str,
        workflow_id: str,
        activity_id: str,
        input: dict[str, Any],
    ) -> None:
        await self._activity_runner.submit_activity(
            activity_id=activity_id,
            activity_name=activity_name,
            workflow_id=workflow_id,
            input=input if isinstance(input, dict) else {},
        )

    async def schedule_timer(self, workflow_id: str, timer_id: str, delay_seconds: float) -> None:
        async def _callback() -> None:
            await self._store.append_event(workflow_id, "timer_fired", {"timer_id": timer_id})

        await self._timer_wheel.schedule_timer(
            timer_id=timer_id,
            workflow_id=workflow_id,
            delay_seconds=max(0.0, float(delay_seconds)),
            callback=_callback,
        )

    @staticmethod
    def _norm(value: Any) -> dict[str, Any]:
        """Normalize a handler result to a dict with string keys (delegated to shared utility)."""
        return norm_result(value)

    @staticmethod
    def _coerce_int(value: Any, *, default: int) -> int:
        try:
            parsed = int(str(value).strip())
        except (TypeError, ValueError):
            parsed = default
        return max(1, parsed)

    @staticmethod
    def _coerce_float(value: Any, *, default: float) -> float:
        try:
            parsed = float(str(value).strip())
        except (TypeError, ValueError):
            parsed = default
        return max(0.01, parsed)
