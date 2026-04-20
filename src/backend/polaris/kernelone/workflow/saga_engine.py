"""SagaWorkflowEngine - Chronos Hourglass Long-Running Task State Machine.

This module implements the Saga compensation pattern and Human-in-the-loop
checkpointing for long-running workflows. It extends the base WorkflowEngine
with:

1. **Saga Compensation**: When a task fails after retries are exhausted, the engine
   executes compensating actions in reverse dependency order to rollback the
   effects of previously completed tasks.

2. **Human-in-the-Loop**: High-risk tasks can be configured to suspend execution
   and wait for human approval before proceeding. The workflow state is persisted
   to the store, allowing safe restart after service crashes.

3. **Stateless Engine**: All state is stored in WorkflowRuntimeStore. The engine
   itself holds no mutable state between method calls. This enables transparent
   restart/recovery.

Design principles (Chronos Hourglass):
- Use Python 3.12+ match-case for complex state transitions
- All state persisted to store before action (fail-closed)
- Compensate in reverse dependency order (Saga pattern)
- High-risk tasks suspend to WAITING_HUMAN state
- Checkpoint interval-driven periodic snapshots

References:
- Base Engine: kernelone/workflow/engine.py
- Timer Wheel: kernelone/workflow/timer_wheel.py
- DLQ: kernelone/workflow/dlq.py
- Shared Utilities: kernelone/workflow/_engine_utils.py
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from polaris.kernelone.constants import MAX_WORKFLOW_TIMEOUT_SECONDS
from polaris.kernelone.trace import create_task_with_context

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
from .contracts import TaskSpec, WorkflowContract
from .dlq import DeadLetterItem, DeadLetterQueuePort, DLQReason, append_dlq_event
from .engine import TaskExecutionOutcome, TaskRuntimeState, WorkflowEngine, WorkflowRuntimeStorePort
from .saga_events import (
    _EVENT_COMPENSATION_COMPLETED,
    _EVENT_COMPENSATION_FAILED,
    _EVENT_COMPENSATION_STARTED,
    _EVENT_COMPENSATION_TASK_COMPLETED,
    _EVENT_COMPENSATION_TASK_FAILED,
    _EVENT_COMPENSATION_TASK_STARTED,
    _EVENT_HUMAN_APPROVED,
    _EVENT_HUMAN_REJECTED,
    _EVENT_TASK_SUSPENDED_HUMAN_REVIEW,
    _EVENT_WORKFLOW_CHECKPOINT,
    _EVENT_WORKFLOW_PAUSED,
    _EVENT_WORKFLOW_RESUMED,
    _EVENT_WORKFLOW_SIGNAL_RECEIVED,
)
from .task_status import WorkflowTaskStatus

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from .activity_runner import ActivityRunner
    from .task_queue import TaskQueueManager
    from .timer_wheel import TimerWheel

logger = logging.getLogger(__name__)


@dataclass
class SagaExecutionState:
    """Tracks Saga compensation execution state for a workflow."""

    workflow_id: str
    compensation_tasks: list[str] = field(default_factory=list)
    completed_compensations: list[str] = field(default_factory=list)
    failed_compensations: list[str] = field(default_factory=list)
    is_compensating: bool = False


class SagaWorkflowEngine(WorkflowEngine):
    """Saga-based workflow engine with compensation and human-in-the-loop support.

    This engine extends the base WorkflowEngine with:
    - Saga compensation chain execution on task failure
    - Human-in-the-loop checkpointing for high-risk tasks
    - Stateless operation (all state in WorkflowRuntimeStore)

    The engine is designed to be restartable: after a crash, calling
    resume_workflow() restores the full state from the store.
    """

    def __init__(
        self,
        store: WorkflowRuntimeStorePort,
        timer_wheel: TimerWheel,
        task_queue_manager: TaskQueueManager,
        activity_runner: ActivityRunner,
        dead_letter_queue: DeadLetterQueuePort | None = None,
        *,
        max_concurrent_workflows: int = 100,
        default_task_timeout_seconds: float = 60.0,
        checkpoint_interval_seconds: float = 60.0,
        human_review_timeout_seconds: float = MAX_WORKFLOW_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(
            store=store,
            timer_wheel=timer_wheel,
            task_queue_manager=task_queue_manager,
            activity_runner=activity_runner,
            dead_letter_queue=dead_letter_queue,
            max_concurrent_workflows=max_concurrent_workflows,
            default_task_timeout_seconds=default_task_timeout_seconds,
            checkpoint_interval_seconds=checkpoint_interval_seconds,
        )
        # Saga-specific configuration
        self._human_review_timeout = max(1.0, float(human_review_timeout_seconds))
        # Saga state per workflow
        self._saga_states: dict[str, SagaExecutionState] = {}
        # Signal tracking per workflow (in-memory for active workflows)
        self._pending_signals: dict[str, list[dict[str, Any]]] = {}

    async def start(self) -> None:
        if self._running:
            return
        self._store.init_schema()
        await self._timer_wheel.start()
        await self._activity_runner.start()
        self._running = True
        logger.info("SagaWorkflowEngine started")

    async def stop(self) -> None:
        if not self._running:
            return
        async with self._lock:
            tasks = list(self._workflow_tasks.values())
            for task in tasks:
                task.cancel()
            self._workflow_tasks.clear()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._timer_wheel.stop()
        await self._activity_runner.stop()
        self._running = False
        logger.info("SagaWorkflowEngine stopped")

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

    # -------------------------------------------------------------------------
    # Workflow Lifecycle
    # -------------------------------------------------------------------------

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
            from .base import RuntimeSubmissionResult

            return RuntimeSubmissionResult(
                submitted=False,
                status="invalid_request",
                workflow_id=wid,
                error="workflow_name and workflow_id are required",
            )

        from .contracts import WorkflowContractError

        try:
            contract = WorkflowContract.from_payload(
                normalized_payload,
                default_timeout_seconds=self._default_task_timeout_seconds,
            )
        except WorkflowContractError as exc:
            from .base import RuntimeSubmissionResult

            errors: list[str] = []
            if hasattr(exc, "errors"):
                errors = list(exc.errors)
            return RuntimeSubmissionResult(
                submitted=False,
                status="invalid_contract",
                workflow_id=wid,
                error=str(exc),
                details={"errors": errors},
            )

        async with self._lock:
            if wid in self._workflow_tasks:
                from .base import RuntimeSubmissionResult

                return RuntimeSubmissionResult(
                    submitted=False,
                    status="already_running",
                    workflow_id=wid,
                    error=f"Workflow {wid} is already running",
                )
            if await self._store.get_execution(wid):
                from .base import RuntimeSubmissionResult

                return RuntimeSubmissionResult(
                    submitted=False,
                    status="already_exists",
                    workflow_id=wid,
                    error=f"Workflow {wid} already exists",
                )
            await self._store.create_execution(wid, wname, normalized_payload)
            now = self._now()

            # Initialize Saga state
            self._saga_states[wid] = SagaExecutionState(workflow_id=wid)
            self._pending_signals[wid] = []

            # Persist initial task states with saga-specific metadata
            for spec in contract.task_specs:
                await self._store.upsert_task_state(
                    workflow_id=wid,
                    task_id=spec.task_id,
                    task_type=spec.task_type,
                    handler_name=spec.handler_name,
                    status=WorkflowTaskStatus.PENDING.value,
                    attempt=0,
                    max_attempts=spec.retry_policy.max_attempts,
                    started_at=None,
                    ended_at=None,
                    result=None,
                    error="",
                    metadata={
                        "is_high_risk": spec.is_high_risk,
                        "compensation_handler": spec.compensation_handler,
                    },
                )

        await self._store.append_event(
            wid,
            "workflow_contract_loaded",
            {
                "mode": contract.mode,
                "task_count": contract.task_count,
                "max_concurrency": contract.max_concurrency,
                "continue_on_error": contract.continue_on_error,
                "high_risk_actions": list(contract.high_risk_actions),
            },
        )

        # Start workflow execution in background
        task = create_task_with_context(self._run_workflow(wid, wname, contract, normalized_payload, now))
        async with self._lock:
            self._workflow_tasks[wid] = task

        from .base import RuntimeSubmissionResult

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
        wid = str(workflow_id or "").strip()
        wname = str(workflow_name or "").strip()
        if not wid or not wname:
            from .base import RuntimeSubmissionResult

            return RuntimeSubmissionResult(
                submitted=False,
                status="invalid_request",
                workflow_id=wid,
                error="workflow_name and workflow_id are required",
            )

        async with self._lock:
            if wid in self._workflow_tasks:
                from .base import RuntimeSubmissionResult

                return RuntimeSubmissionResult(
                    submitted=False,
                    status="already_running",
                    workflow_id=wid,
                    error=f"Workflow {wid} is already running",
                )

            existing = await self._store.get_execution(wid)
            if existing is None:
                from .base import RuntimeSubmissionResult

                return RuntimeSubmissionResult(
                    submitted=False,
                    status="not_found",
                    workflow_id=wid,
                    error=f"Workflow {wid} not found - cannot resume",
                )

            from .contracts import WorkflowContractError

            # If new payload provided, use it; otherwise preserve original payload from existing execution
            existing_payload = extract_existing_payload(existing)
            normalized_payload = normalize_resume_payload(payload, existing_payload)
            try:
                contract = WorkflowContract.from_payload(
                    normalized_payload,
                    default_timeout_seconds=self._default_task_timeout_seconds,
                )
            except WorkflowContractError as exc:
                from .base import RuntimeSubmissionResult

                return RuntimeSubmissionResult(
                    submitted=False,
                    status="invalid_contract",
                    workflow_id=wid,
                    error=str(exc),
                )

            # Load task states from store
            persisted_by_id = await load_persisted_task_states(self._store, wid)

            # Initialize Saga state
            self._saga_states[wid] = SagaExecutionState(workflow_id=wid)
            self._pending_signals[wid] = []

            # Rebuild task states from persisted data
            task_states: dict[str, TaskRuntimeState] = {}
            for spec in contract.task_specs:
                pstate = persisted_by_id.get(spec.task_id)
                if pstate is not None:
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

        # Re-register high-risk task timers with timer wheel (outside lock)
        for task_id, ts in task_states.items():
            if ts.status == WorkflowTaskStatus.WAITING_HUMAN.value:
                await self._schedule_human_review_timer(wid, task_id)

        await self._store.append_event(
            wid,
            "workflow_resumed",
            {
                "mode": contract.mode,
                "task_count": contract.task_count,
                "resumed_at": self._now(),
            },
        )

        # Start workflow execution in background
        task = create_task_with_context(
            self._run_workflow(wid, wname, contract, normalized_payload, self._now()),
        )
        async with self._lock:
            self._workflow_tasks[wid] = task

        from .base import RuntimeSubmissionResult

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

    async def signal_workflow(
        self,
        workflow_id: str,
        signal_name: str,
        signal_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        wid = str(workflow_id or "").strip()
        if not wid:
            return {"signalled": False, "error": "workflow_id is required"}

        signal_name_lower = str(signal_name or "").strip().lower()
        args_dict: dict[str, Any] = signal_args if isinstance(signal_args, dict) else {}

        # Persist signal to store for event sourcing
        await self._store.append_event(
            wid,
            _EVENT_WORKFLOW_SIGNAL_RECEIVED,
            {"signal_name": signal_name_lower, "args": args_dict},
        )

        # Add to in-memory queue for active workflows
        async with self._lock:
            if wid in self._pending_signals:
                self._pending_signals[wid].append(
                    {
                        "signal_name": signal_name_lower,
                        "args": args_dict,
                    }
                )

        # Handle human approval/rejection signals immediately
        match signal_name_lower:
            case "approve_task":
                task_id = str(args_dict.get("task_id", "")).strip()
                if task_id:
                    await self._handle_human_approval(wid, task_id, approved=True)
                    return {"signalled": True, "workflow_id": wid, "signal": signal_name_lower, "task_id": task_id}
            case "reject_task":
                task_id = str(args_dict.get("task_id", "")).strip()
                if task_id:
                    await self._handle_human_approval(wid, task_id, approved=False)
                    return {"signalled": True, "workflow_id": wid, "signal": signal_name_lower, "task_id": task_id}

        return {"signalled": True, "workflow_id": wid, "signal": signal_name_lower}

    async def describe_workflow(self, workflow_id: str) -> WorkflowSnapshot:
        return await self._store.create_snapshot(workflow_id)

    # -------------------------------------------------------------------------
    # Core Workflow Execution
    # -------------------------------------------------------------------------

    async def _run_workflow(  # type: ignore[override]
        self,
        workflow_id: str,
        workflow_name: str,
        contract: WorkflowContract,
        payload: dict[str, Any],
        start_time: str,
    ) -> None:
        async with self._workflow_semaphore:
            status = WorkflowTaskStatus.FAILED.value
            result: dict[str, Any] = {"status": WorkflowTaskStatus.FAILED.value}

            try:
                await self._store.append_event(workflow_id, "workflow_execution_started", {"mode": contract.mode})

                if contract.is_dag:
                    status, result = await self._run_dag_saga(workflow_id, workflow_name, contract, payload, start_time)
                else:
                    status, result = await self._run_sequential_saga(workflow_id, workflow_name, contract, payload)

            except (RuntimeError, ValueError) as exc:
                logger.exception("Workflow %s failed", workflow_id)
                status = WorkflowTaskStatus.FAILED.value
                result = {
                    "status": WorkflowTaskStatus.FAILED.value,
                    "workflow_id": workflow_id,
                    "error": str(exc),
                }

            finally:
                await self._store.update_execution(
                    workflow_id,
                    status=status,
                    result=result,
                    close_time=self._now(),
                )
                await self._store.append_event(workflow_id, "workflow_execution_finished", {"status": status})
                await self._timer_wheel.cancel_workflow_timers(workflow_id)
                # Clean up
                async with self._lock:
                    self._workflow_tasks.pop(workflow_id, None)
                    self._saga_states.pop(workflow_id, None)
                    self._pending_signals.pop(workflow_id, None)

    async def _run_dag_saga(
        self,
        workflow_id: str,
        workflow_name: str,
        contract: WorkflowContract,
        payload: dict[str, Any],
        start_time: str,
    ) -> tuple[str, dict[str, Any]]:
        specs_by_id = {spec.task_id: spec for spec in contract.task_specs}
        pending: set[str] = set(specs_by_id.keys())
        running: dict[str, asyncio.Task[TaskExecutionOutcome]] = {}
        task_outputs: dict[str, dict[str, Any]] = {}
        workflow_start_time = asyncio.get_running_loop().time()
        last_checkpoint_time = workflow_start_time
        workflow_timeout = contract.workflow_timeout_seconds
        checkpoint_interval = self._checkpoint_interval

        # Track completed tasks for Saga compensation (in execution order)
        completed_tasks: list[str] = []
        failed_task_id: str | None = None

        # Fix: Track suspended tasks separately (they need to be re-added on approval)
        suspended: dict[str, TaskSpec] = {}

        # Fix: Load persisted waiting_human tasks and add them back to pending
        persisted_states = await self._store.list_task_states(workflow_id)
        for ts in persisted_states:
            tid = getattr(ts, "task_id", None)
            if tid and getattr(ts, "status", None) == WorkflowTaskStatus.WAITING_HUMAN.value:
                spec = specs_by_id.get(tid)
                if spec:
                    pending.add(tid)
                    suspended[tid] = spec
                    logger.info("Restored waiting_human task %s to pending for workflow %s", tid, workflow_id)

        while pending or running or suspended:
            elapsed = asyncio.get_running_loop().time() - workflow_start_time
            if elapsed >= workflow_timeout:
                logger.warning("Workflow %s timed out after %.1fs", workflow_id, elapsed)
                await self._cancel_running(running)
                timeout_error = f"Workflow timeout after {workflow_timeout}s"
                await self._enqueue_pending_to_dlq(
                    workflow_id, contract, pending, DLQReason.WORKFLOW_TIMEOUT, timeout_error
                )
                await self._mark_tasks(
                    workflow_id, specs_by_id, pending, WorkflowTaskStatus.CANCELLED.value, timeout_error
                )
                pending.clear()
                suspended.clear()
                break

            # Process signals (pause/resume/cancel) - fix: use stored signals
            signals = self._consume_pending_signals(workflow_id)
            cancel_requested = False
            pause_requested = False

            for signal in signals:
                match signal.get("signal_name", "").strip().lower():
                    case "cancel":
                        cancel_requested = True
                    case "pause":
                        pause_requested = True
                    case _:
                        pass

            if cancel_requested:
                await self._cancel_running(running)
                await self._enqueue_pending_to_dlq(
                    workflow_id, contract, pending, DLQReason.WORKFLOW_CANCELLED, "Cancelled"
                )
                await self._mark_tasks(
                    workflow_id, specs_by_id, pending, WorkflowTaskStatus.CANCELLED.value, "Cancelled"
                )
                pending.clear()
                suspended.clear()
                break

            if pause_requested:
                await self._store.append_event(workflow_id, _EVENT_WORKFLOW_PAUSED, {"signal": "pause"})
                # Wait for resume signal - fix: properly handle pause with suspended tasks
                while True:
                    await asyncio.sleep(0.1)
                    signals = self._consume_pending_signals(workflow_id)
                    if any(s.get("signal_name", "").strip().lower() == "resume" for s in signals):
                        await self._store.append_event(workflow_id, _EVENT_WORKFLOW_RESUMED, {"signal": "resume"})
                        break

            # Re-check suspended tasks for approval (fix: re-add approved tasks to pending)
            for task_id in list(suspended.keys()):
                task_states = await self._store.list_task_states(workflow_id)
                task_state = next(
                    (ts for ts in task_states if getattr(ts, "task_id", None) == task_id),
                    None,
                )
                if task_state:
                    status = getattr(task_state, "status", None)
                    if status == WorkflowTaskStatus.PENDING.value:
                        # Task was approved - re-add to pending
                        pending.add(task_id)
                        spec = suspended.pop(task_id, None)
                        if spec and task_id not in specs_by_id:
                            specs_by_id[task_id] = spec
                        logger.info("Task %s approved, re-added to pending", task_id)
                    elif status == WorkflowTaskStatus.FAILED.value:
                        # Task was rejected - trigger failure handling
                        suspended.pop(task_id, None)
                        failed_task_id = task_id
                        break

            if not pending and not running and not suspended:
                break

            # Find ready tasks (dependencies satisfied, not running, not suspended)
            if pending:
                ready: list[TaskSpec] = []
                for task_id in list(pending):
                    spec = specs_by_id.get(task_id)
                    if spec is None:
                        pending.discard(task_id)
                        continue
                    # Check dependencies
                    deps = [specs_by_id[dep].task_id for dep in spec.depends_on if dep in specs_by_id]
                    if all(dep in completed_tasks for dep in deps):
                        ready.append(spec)

                # Execute ready tasks within concurrency limit
                while ready and len(running) < max(1, contract.max_concurrency):
                    spec = ready.pop(0)
                    pending.discard(spec.task_id)

                    # Check high-risk flag
                    if spec.is_high_risk or spec.task_id in contract.high_risk_actions:
                        # Suspend for human review
                        await self._suspend_for_human_review(workflow_id, spec)
                        suspended[spec.task_id] = spec
                        continue

                    running[spec.task_id] = create_task_with_context(
                        self._execute_spec_with_compensation(workflow_id, spec, contract)
                    )

            # Wait for task completion
            if running:
                done, _ = await asyncio.wait(list(running.values()), timeout=0.1, return_when=asyncio.FIRST_COMPLETED)
                if not done:
                    # Periodic checkpoint
                    if checkpoint_interval > 0:
                        time_since_checkpoint = asyncio.get_running_loop().time() - last_checkpoint_time
                        if time_since_checkpoint >= checkpoint_interval:
                            await self._create_checkpoint(workflow_id, contract, task_outputs)
                            last_checkpoint_time = asyncio.get_running_loop().time()
                    continue

                for fut in done:
                    task_id = next((tid for tid, task in running.items() if task is fut), "")
                    if task_id:
                        running.pop(task_id, None)

                    outcome = await self._unwrap_outcome(fut, task_id)
                    await self._apply_outcome_saga(
                        workflow_id,
                        contract,
                        specs_by_id,
                        task_outputs,
                        outcome,
                        completed_tasks,
                    )

                    # Check if we need to trigger compensation
                    if outcome.status == WorkflowTaskStatus.FAILED.value:
                        failed_task_id = outcome.task_id
                        await self._cancel_running(running)
                        pending.clear()
                        suspended.clear()
                        break

                # Periodic checkpoint after task completion
                if checkpoint_interval > 0:
                    time_since_checkpoint = asyncio.get_running_loop().time() - last_checkpoint_time
                    if time_since_checkpoint >= checkpoint_interval:
                        await self._create_checkpoint(workflow_id, contract, task_outputs)
                        last_checkpoint_time = asyncio.get_running_loop().time()

        # Determine final status
        if failed_task_id:
            saga_state = self._saga_states.get(workflow_id)
            if saga_state and completed_tasks:
                compensation_success = await self._execute_compensation_chain(
                    workflow_id, contract, specs_by_id, completed_tasks
                )
                status = WorkflowTaskStatus.FAILED.value if not compensation_success else "completed_with_compensation"
            else:
                status = WorkflowTaskStatus.FAILED.value
        else:
            status = WorkflowTaskStatus.COMPLETED.value

        result = {
            "status": status,
            "workflow_id": workflow_id,
            "workflow_name": workflow_name,
            "mode": contract.mode,
            "start_time": start_time,
            "close_time": self._now(),
            "tasks": {
                spec.task_id: {
                    "status": WorkflowTaskStatus.COMPLETED.value
                    if spec.task_id in completed_tasks
                    else WorkflowTaskStatus.SKIPPED.value,
                }
                for spec in contract.task_specs
            },
            "outputs": task_outputs,
        }
        return status, result

    async def _run_sequential_saga(
        self,
        workflow_id: str,
        workflow_name: str,
        contract: WorkflowContract,
        payload: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        handler = self._workflow_handlers.get(workflow_name)
        if handler is None:
            raise RuntimeError(f"No handler for workflow `{workflow_name}`")

        try:
            result = await self._invoke_handler(
                handler,
                workflow_id=workflow_id,
                payload=payload,
                timeout_seconds=self._default_task_timeout_seconds,
            )
            return WorkflowTaskStatus.COMPLETED.value, {
                "status": WorkflowTaskStatus.COMPLETED.value,
                "mode": "sequential",
                "result": self._norm(result),
            }
        except (RuntimeError, ValueError) as exc:
            return WorkflowTaskStatus.FAILED.value, {"status": WorkflowTaskStatus.FAILED.value, "error": str(exc)}

    # -------------------------------------------------------------------------
    # Signal Handling (Fix: proper in-memory signal queue)
    # -------------------------------------------------------------------------

    def _consume_pending_signals(self, workflow_id: str) -> list[dict[str, Any]]:
        """Consume and return all pending signals for a workflow.

        This is called from the workflow execution loop to process signals.
        Signals are consumed atomically (cleared after consumption).
        """
        signals = self._pending_signals.get(workflow_id, [])
        self._pending_signals[workflow_id] = []
        return signals

    # -------------------------------------------------------------------------
    # Saga Compensation
    # -------------------------------------------------------------------------

    async def _execute_compensation_chain(
        self,
        workflow_id: str,
        contract: WorkflowContract,
        specs_by_id: dict[str, TaskSpec],
        completed_tasks: list[str],
    ) -> bool:
        saga_state = self._saga_states.get(workflow_id)
        if saga_state is None:
            saga_state = SagaExecutionState(workflow_id=workflow_id)
            self._saga_states[workflow_id] = saga_state

        saga_state.is_compensating = True

        await self._store.append_event(
            workflow_id,
            _EVENT_COMPENSATION_STARTED,
            {"task_count": len(completed_tasks), "tasks": completed_tasks},
        )

        all_success = True

        # Reverse order: compensate most recent first
        for task_id in reversed(completed_tasks):
            spec = specs_by_id.get(task_id)
            if spec is None:
                continue

            if not spec.compensation_handler:
                logger.debug("Task %s has no compensation_handler, skipping", task_id)
                saga_state.completed_compensations.append(task_id)
                continue

            await self._store.append_event(
                workflow_id,
                _EVENT_COMPENSATION_TASK_STARTED,
                {
                    "task_id": task_id,
                    "compensation_handler": spec.compensation_handler,
                    "compensation_input": spec.compensation_input,
                },
            )

            try:
                result = await self._activity_runner.execute(
                    spec.compensation_handler,
                    {
                        **spec.compensation_input,
                        "workflow_id": workflow_id,
                        "task_id": task_id,
                        "original_input": spec.input_payload,
                    },
                    timeout_seconds=spec.timeout_seconds,
                )
                await self._store.append_event(
                    workflow_id,
                    _EVENT_COMPENSATION_TASK_COMPLETED,
                    {"task_id": task_id, "result": result},
                )
                saga_state.completed_compensations.append(task_id)

            except (RuntimeError, ValueError) as exc:
                logger.exception("Compensation task %s failed: %s", task_id, exc)
                await self._store.append_event(
                    workflow_id,
                    _EVENT_COMPENSATION_TASK_FAILED,
                    {"task_id": task_id, "error": str(exc)},
                )
                saga_state.failed_compensations.append(task_id)
                all_success = False

        await self._store.append_event(
            workflow_id,
            _EVENT_COMPENSATION_COMPLETED if all_success else _EVENT_COMPENSATION_FAILED,
            {
                "completed": saga_state.completed_compensations,
                "failed": saga_state.failed_compensations,
            },
        )

        saga_state.is_compensating = False
        return all_success

    async def _execute_spec_with_compensation(
        self,
        workflow_id: str,
        spec: TaskSpec,
        contract: WorkflowContract,
    ) -> TaskExecutionOutcome:
        task_state = TaskRuntimeState(
            task_id=spec.task_id,
            task_type=spec.task_type,
            handler_name=spec.handler_name,
            status=WorkflowTaskStatus.RUNNING.value,
            attempt=0,
            max_attempts=spec.retry_policy.max_attempts,
        )
        task_state.started_at = self._now()
        await self._persist_task_state(workflow_id, task_state)

        input_payload = dict(spec.input_payload)
        input_payload.setdefault("workflow_id", workflow_id)
        input_payload.setdefault("task_id", spec.task_id)

        for attempt in range(1, spec.retry_policy.max_attempts + 1):
            task_state.attempt = attempt
            await self._persist_task_state(workflow_id, task_state)
            await self._store.append_event(
                workflow_id,
                "task_attempt_started",
                {
                    "task_id": spec.task_id,
                    "attempt": attempt,
                    "max_attempts": spec.retry_policy.max_attempts,
                },
            )
            try:
                raw = await self._dispatch(spec, input_payload)
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
                await self._persist_task_state(workflow_id, task_state)
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
                    if self._dead_letter_queue is not None:
                        dlq_item = DeadLetterItem(
                            task_id=spec.task_id,
                            workflow_id=workflow_id,
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
                            },
                        )
                        await self._dead_letter_queue.enqueue(dlq_item)
                        await append_dlq_event(self._store, workflow_id, dlq_item)
                    return TaskExecutionOutcome(
                        spec.task_id,
                        WorkflowTaskStatus.FAILED.value,
                        attempt,
                        task_state.started_at or self._now(),
                        self._now(),
                        error=error_msg,
                    )
                task_state.status = WorkflowTaskStatus.RETRYING.value
                await self._persist_task_state(workflow_id, task_state)
                delay = self._retry_delay(spec, attempt)
                await self._store.append_event(
                    workflow_id,
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

    # -------------------------------------------------------------------------
    # Human-in-the-Loop
    # -------------------------------------------------------------------------

    async def _suspend_for_human_review(
        self,
        workflow_id: str,
        spec: TaskSpec,
    ) -> None:
        """Suspend a task waiting for human review."""
        await self._store.append_event(
            workflow_id,
            _EVENT_TASK_SUSPENDED_HUMAN_REVIEW,
            {
                "task_id": spec.task_id,
                "reason": "high_risk_action",
                "handler": spec.handler_name,
            },
        )

        await self._store.upsert_task_state(
            workflow_id=workflow_id,
            task_id=spec.task_id,
            task_type=spec.task_type,
            handler_name=spec.handler_name,
            status=WorkflowTaskStatus.WAITING_HUMAN.value,
            attempt=0,
            max_attempts=spec.retry_policy.max_attempts,
            started_at=None,
            ended_at=None,
            result=None,
            error="",
            metadata={
                "is_high_risk": spec.is_high_risk,
                "suspended_at": self._now(),
            },
        )

        await self._schedule_human_review_timer(workflow_id, spec.task_id)

    async def _schedule_human_review_timer(
        self,
        workflow_id: str,
        task_id: str,
    ) -> None:
        """Schedule a timer for human review timeout."""

        async def _timeout_callback() -> None:
            # Check if still waiting human
            task_states = await self._store.list_task_states(workflow_id)
            task_state = next(
                (ts for ts in task_states if getattr(ts, "task_id", None) == task_id),
                None,
            )
            if task_state and getattr(task_state, "status", None) == WorkflowTaskStatus.WAITING_HUMAN.value:
                logger.warning(
                    "Human review timeout for task %s in workflow %s",
                    task_id,
                    workflow_id,
                )
                await self._handle_human_approval(workflow_id, task_id, approved=False)

        timer_id = f"human_review_{workflow_id}_{task_id}"
        await self._timer_wheel.schedule_timer(
            timer_id=timer_id,
            workflow_id=workflow_id,
            delay_seconds=self._human_review_timeout,
            callback=_timeout_callback,
        )

    async def _handle_human_approval(
        self,
        workflow_id: str,
        task_id: str,
        approved: bool,
    ) -> None:
        """Handle human approval or rejection for a WAITING_HUMAN task.

        Fix: Cancel the timer when approved to prevent race condition with timeout.
        """
        # Fix: Cancel the timer first to prevent timeout callback from firing after approval
        await self._timer_wheel.cancel_timer(f"human_review_{workflow_id}_{task_id}")

        task_states = await self._store.list_task_states(workflow_id)
        task_state = next(
            (ts for ts in task_states if getattr(ts, "task_id", None) == task_id),
            None,
        )

        if task_state is None:
            logger.warning(
                "Task %s not found in store for human approval in workflow %s",
                task_id,
                workflow_id,
            )
            return

        current_status = getattr(task_state, "status", None)
        if current_status != WorkflowTaskStatus.WAITING_HUMAN.value:
            logger.info(
                "Task %s in workflow %s is no longer waiting_human (status=%s), skipping approval handling",
                task_id,
                workflow_id,
                current_status,
            )
            return

        if approved:
            await self._store.append_event(
                workflow_id,
                _EVENT_HUMAN_APPROVED,
                {"task_id": task_id},
            )
            await self._store.upsert_task_state(
                workflow_id=workflow_id,
                task_id=task_id,
                task_type=getattr(task_state, "task_type", ""),
                handler_name=getattr(task_state, "handler_name", ""),
                status=WorkflowTaskStatus.PENDING.value,  # Fix: set to pending so it gets re-queued
                attempt=0,
                max_attempts=int(getattr(task_state, "max_attempts", 1)),
                started_at=None,
                ended_at=None,
                result=None,
                error="",
                metadata=getattr(task_state, "metadata", {}) or {},
            )
            logger.info("Task %s approved, status set to pending", task_id)
        else:
            await self._store.append_event(
                workflow_id,
                _EVENT_HUMAN_REJECTED,
                {"task_id": task_id},
            )
            await self._store.upsert_task_state(
                workflow_id=workflow_id,
                task_id=task_id,
                task_type=getattr(task_state, "task_type", ""),
                handler_name=getattr(task_state, "handler_name", ""),
                status=WorkflowTaskStatus.FAILED.value,
                attempt=0,
                max_attempts=int(getattr(task_state, "max_attempts", 1)),
                started_at=None,
                ended_at=self._now(),
                result=None,
                error="Human review rejected",
                metadata=getattr(task_state, "metadata", {}) or {},
            )
            logger.info("Task %s rejected, status set to failed", task_id)

    # -------------------------------------------------------------------------
    # Helper Methods
    # -------------------------------------------------------------------------

    async def _create_checkpoint(
        self,
        workflow_id: str,
        contract: WorkflowContract,
        task_outputs: dict[str, dict[str, Any]],
    ) -> None:
        try:
            await self._store.append_event(
                workflow_id,
                _EVENT_WORKFLOW_CHECKPOINT,
                {
                    "task_outputs": task_outputs,
                    "checkpoint_at": self._now(),
                },
            )
        except (RuntimeError, ValueError):
            logger.exception("Failed to create checkpoint for workflow %s", workflow_id)

    async def _apply_outcome_saga(
        self,
        workflow_id: str,
        contract: WorkflowContract,
        specs_by_id: dict[str, TaskSpec],
        task_outputs: dict[str, dict[str, Any]],
        outcome: TaskExecutionOutcome,
        completed_tasks: list[str],
    ) -> None:
        task_state = TaskRuntimeState(
            task_id=outcome.task_id,
            task_type=specs_by_id.get(outcome.task_id).__dict__.get("task_type", "")
            if specs_by_id.get(outcome.task_id)
            else "",
            handler_name=specs_by_id.get(outcome.task_id).__dict__.get("handler_name", "")
            if specs_by_id.get(outcome.task_id)
            else "",
            status=outcome.status,
            attempt=int(outcome.attempt),
            started_at=outcome.started_at,
            ended_at=outcome.ended_at,
            result=outcome.result,
            error=str(outcome.error or "").strip(),
        )
        await self._persist_task_state(workflow_id, task_state)
        await self._store.append_event(
            workflow_id,
            "task_finished",
            {
                "task_id": task_state.task_id,
                "status": task_state.status,
                "attempt": task_state.attempt,
                "error": task_state.error,
            },
        )
        if task_state.status == WorkflowTaskStatus.COMPLETED.value:
            task_outputs[task_state.task_id] = task_state.result or {}
            completed_tasks.append(task_state.task_id)

    async def _cancel_running(
        self,
        running: dict[str, asyncio.Task[TaskExecutionOutcome]],
    ) -> None:
        """Cancel running tasks and wait for them to settle (delegated to shared utility)."""
        await cancel_running_tasks(running, timeout=5.0)

    async def _mark_tasks(  # type: ignore[override]
        self,
        workflow_id: str,
        specs_by_id: dict[str, TaskSpec],
        task_ids: set[str],
        status: str,
        error: str,
    ) -> None:
        for task_id in sorted(task_ids):
            spec = specs_by_id.get(task_id)
            if spec is None:
                continue
            await self._store.upsert_task_state(
                workflow_id=workflow_id,
                task_id=task_id,
                task_type=spec.task_type,
                handler_name=spec.handler_name,
                status=status,
                attempt=0,
                max_attempts=spec.retry_policy.max_attempts,
                started_at=None,
                ended_at=self._now(),
                result=None,
                error=error,
                metadata={},
            )

    async def _enqueue_pending_to_dlq(  # type: ignore[override]
        self,
        workflow_id: str,
        contract: WorkflowContract,
        pending: set[str],
        dlq_reason: DLQReason,
        error: str,
    ) -> None:
        if self._dead_letter_queue is None:
            return
        specs_by_id = {spec.task_id: spec for spec in contract.task_specs}
        for task_id in pending:
            spec = specs_by_id.get(task_id)
            if spec is None:
                continue
            dlq_item = DeadLetterItem(
                task_id=task_id,
                workflow_id=workflow_id,
                handler_name=spec.handler_name,
                input_payload=spec.input_payload,
                error=error,
                failed_at=self._now(),
                dlq_at=self._now(),
                attempt=0,
                max_attempts=spec.retry_policy.max_attempts,
                dlq_reason=dlq_reason,
                metadata={"task_type": spec.task_type},
            )
            await self._dead_letter_queue.enqueue(dlq_item)
            await append_dlq_event(self._store, workflow_id, dlq_item)

    async def _persist_task_state(
        self,
        workflow_id: str,
        task_state: TaskRuntimeState,
    ) -> None:
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

    async def _dispatch(  # type: ignore[override]
        self,
        spec: TaskSpec,
        input_payload: dict[str, Any],
    ) -> Any:
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
                workflow_id=input_payload.get("workflow_id", ""),
                payload=input_payload,
                timeout_seconds=timeout_seconds,
            )
        raise RuntimeError(f"Unsupported task type `{spec.task_type}`")

    async def _invoke_handler(  # type: ignore[override]
        self,
        handler: Callable[..., Any],
        *,
        workflow_id: str,
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> Any:
        """Invoke handler with flexible signature support (delegated to shared utility)."""
        return await invoke_handler(
            handler,
            workflow_id=workflow_id,
            payload=payload,
            timeout_seconds=timeout_seconds,
            runtime_engine=None,
        )

    def _retry_delay(self, spec: TaskSpec, attempt: int) -> float:
        """Calculate retry delay with exponential backoff and jitter (delegated to shared utility)."""
        return calculate_retry_delay(spec, attempt)

    async def _unwrap_outcome(
        self,
        fut: asyncio.Task[TaskExecutionOutcome],
        task_id: str,
    ) -> Any:
        """Unwrap task outcome, handling cancellation gracefully (delegated to shared utility)."""
        return await unwrap_task_outcome(fut, task_id, now_func=self._now)

    @staticmethod
    def _norm(value: Any) -> dict[str, Any]:
        """Normalize a handler result to a dict with string keys (delegated to shared utility)."""
        return norm_result(value)
