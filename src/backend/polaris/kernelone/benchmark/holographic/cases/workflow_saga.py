"""Workflow / saga-engine benchmark executors (TC-CHR-001..003).

Saga compensation chains, workflow resume, and human-in-the-loop
suspend/resume, backed by an in-memory benchmark workflow store.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from polaris.kernelone.benchmark.holographic.stats import _now_iso, _perf_ms
from polaris.kernelone.benchmark.holographic_models import HolographicCase
from polaris.kernelone.benchmark.holographic_stats import summarize_samples
from polaris.kernelone.workflow.activity_runner import ActivityRunner
from polaris.kernelone.workflow.base import WorkflowSnapshot
from polaris.kernelone.workflow.engine import WorkflowEngine
from polaris.kernelone.workflow.saga_engine import SagaWorkflowEngine
from polaris.kernelone.workflow.saga_events import (
    _EVENT_COMPENSATION_TASK_COMPLETED,
    _EVENT_COMPENSATION_TASK_STARTED,
)
from polaris.kernelone.workflow.task_queue import TaskQueueManager
from polaris.kernelone.workflow.task_status import WorkflowTaskStatus
from polaris.kernelone.workflow.timer_wheel import TimerWheel


@dataclass
class _StoreExecution:
    workflow_id: str
    workflow_name: str
    status: str
    payload: dict[str, Any]
    created_at: str
    result: dict[str, Any] | None = None
    close_time: str | None = None


@dataclass
class _StoreEvent:
    id: int
    workflow_id: str
    seq: int
    event_type: str
    payload: dict[str, Any]
    created_at: str


@dataclass
class _StoreTaskState:
    workflow_id: str
    task_id: str
    task_type: str
    handler_name: str
    status: str
    attempt: int
    max_attempts: int
    started_at: str | None
    ended_at: str | None
    result: dict[str, Any] | None
    error: str
    metadata: dict[str, Any]


class _InMemoryWorkflowStore:
    """In-memory runtime store for benchmark-only workflow executions."""

    def __init__(self) -> None:
        self._executions: dict[str, _StoreExecution] = {}
        self._events: dict[str, list[_StoreEvent]] = {}
        self._task_states: dict[str, dict[str, _StoreTaskState]] = {}
        self._seqs: dict[str, int] = {}

    def init_schema(self) -> None:
        return

    async def get_execution(self, workflow_id: str) -> _StoreExecution | None:
        return self._executions.get(workflow_id)

    async def create_execution(self, workflow_id: str, workflow_name: str, payload: dict[str, Any]) -> None:
        self._executions[workflow_id] = _StoreExecution(
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            status=WorkflowTaskStatus.RUNNING.value,
            payload=dict(payload),
            created_at=_now_iso(),
        )
        self._events[workflow_id] = []
        self._task_states[workflow_id] = {}
        self._seqs[workflow_id] = 1

    async def append_event(self, workflow_id: str, event_type: str, payload: dict[str, Any]) -> None:
        seq = self._seqs.get(workflow_id, 1)
        self._seqs[workflow_id] = seq + 1
        self._events.setdefault(workflow_id, []).append(
            _StoreEvent(
                id=len(self._events.get(workflow_id, [])) + 1,
                workflow_id=workflow_id,
                seq=seq,
                event_type=event_type,
                payload=dict(payload),
                created_at=_now_iso(),
            )
        )

    async def update_execution(
        self,
        workflow_id: str,
        *,
        status: str,
        result: dict[str, Any],
        close_time: str,
    ) -> None:
        execution = self._executions.get(workflow_id)
        if execution is None:
            return
        execution.status = status
        execution.result = dict(result)
        execution.close_time = close_time

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
    ) -> None:
        self._task_states.setdefault(workflow_id, {})[task_id] = _StoreTaskState(
            workflow_id=workflow_id,
            task_id=task_id,
            task_type=task_type,
            handler_name=handler_name,
            status=status,
            attempt=attempt,
            max_attempts=max_attempts,
            started_at=started_at,
            ended_at=ended_at,
            result=dict(result) if isinstance(result, dict) else result,
            error=error,
            metadata=dict(metadata),
        )

    async def create_snapshot(self, workflow_id: str) -> WorkflowSnapshot:
        execution = self._executions.get(workflow_id)
        if execution is None:
            return WorkflowSnapshot(
                workflow_id=workflow_id,
                workflow_name="",
                status="not_found",
                run_id=workflow_id,
                start_time="",
            )
        return WorkflowSnapshot(
            workflow_id=workflow_id,
            workflow_name=execution.workflow_name,
            status=execution.status,
            run_id=workflow_id,
            start_time=execution.created_at,
            close_time=execution.close_time,
            result=dict(execution.result) if isinstance(execution.result, dict) else execution.result,
            pending_actions=[],
        )

    async def list_task_states(self, workflow_id: str) -> list[_StoreTaskState]:
        return list(self._task_states.get(workflow_id, {}).values())

    async def get_events(self, workflow_id: str, *, limit: int = 100) -> list[_StoreEvent]:
        events = self._events.get(workflow_id, [])
        return events[-limit:]


async def _wait_execution_terminal(
    store: _InMemoryWorkflowStore,
    workflow_id: str,
    *,
    timeout_s: float = 10.0,
) -> _StoreExecution | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        execution = await store.get_execution(workflow_id)
        if execution is not None and execution.status not in {
            WorkflowTaskStatus.RUNNING.value,
            "",
        }:
            return execution
        await asyncio.sleep(0.005)
    return await store.get_execution(workflow_id)


async def _wait_task_status(
    store: _InMemoryWorkflowStore,
    workflow_id: str,
    task_id: str,
    *,
    target_status: str | None = None,
    timeout_s: float = 10.0,
) -> _StoreTaskState | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        states = await store.list_task_states(workflow_id)
        state = next((item for item in states if item.task_id == task_id), None)
        if state is None:
            await asyncio.sleep(0.005)
            continue
        if target_status is None or state.status == target_status:
            return state
        await asyncio.sleep(0.005)
    states = await store.list_task_states(workflow_id)
    return next((item for item in states if item.task_id == task_id), None)


def _saga_compensation_payload() -> dict[str, Any]:
    return {
        "orchestration": {
            "mode": "dag",
            "max_concurrency": 1,
            "continue_on_error": False,
            "tasks": [
                {
                    "id": "TaskA",
                    "type": "activity",
                    "handler": "task_a",
                    "retry": {"max_attempts": 1},
                    "compensation_handler": "undo_task_a",
                },
                {
                    "id": "TaskB",
                    "type": "activity",
                    "handler": "task_b",
                    "depends_on": ["TaskA"],
                    "retry": {"max_attempts": 1},
                    "compensation_handler": "undo_task_b",
                },
                {
                    "id": "TaskC",
                    "type": "activity",
                    "handler": "task_c_fail",
                    "depends_on": ["TaskB"],
                    "retry": {"max_attempts": 1},
                },
                {
                    "id": "TaskD",
                    "type": "activity",
                    "handler": "task_d",
                    "depends_on": ["TaskC"],
                    "retry": {"max_attempts": 1},
                },
                {
                    "id": "TaskE",
                    "type": "activity",
                    "handler": "task_e",
                    "depends_on": ["TaskD"],
                    "retry": {"max_attempts": 1},
                },
            ],
        }
    }


async def _exec_tc_chr_001(case: HolographicCase) -> dict[str, float]:
    store = _InMemoryWorkflowStore()
    timer_wheel = TimerWheel(tick_interval=0.005)
    queue_manager = TaskQueueManager()
    activity_runner = ActivityRunner(max_concurrent=8)
    engine = SagaWorkflowEngine(
        store=store,
        timer_wheel=timer_wheel,
        task_queue_manager=queue_manager,
        activity_runner=activity_runner,
        checkpoint_interval_seconds=0.0,
        human_review_timeout_seconds=30.0,
    )
    await engine.start()

    async def _task_a(payload: Any) -> dict[str, Any]:
        return {"ok": "a", "payload": payload}

    async def _task_b(payload: Any) -> dict[str, Any]:
        return {"ok": "b", "payload": payload}

    async def _task_c_fail(payload: Any) -> dict[str, Any]:
        _ = payload
        raise RuntimeError("task_c_failed")

    async def _task_d(payload: Any) -> dict[str, Any]:
        return {"ok": "d", "payload": payload}

    async def _task_e(payload: Any) -> dict[str, Any]:
        return {"ok": "e", "payload": payload}

    async def _undo_task_a(payload: Any) -> dict[str, Any]:
        return {"undo": "a", "payload": payload}

    async def _undo_task_b(payload: Any) -> dict[str, Any]:
        return {"undo": "b", "payload": payload}

    engine.register_activity("task_a", _task_a)
    engine.register_activity("task_b", _task_b)
    engine.register_activity("task_c_fail", _task_c_fail)
    engine.register_activity("task_d", _task_d)
    engine.register_activity("task_e", _task_e)
    engine.register_activity("undo_task_a", _undo_task_a)
    engine.register_activity("undo_task_b", _undo_task_b)

    iterations = max(50, min(case.min_samples, 200))
    chain_latencies_ms: list[float] = []
    compensation_op_ms: list[float] = []
    consistency_ok = 0
    log_integrity_ok = 0

    try:
        for index in range(iterations):
            workflow_id = f"tc-chr-001-{index}"
            started = time.perf_counter_ns()
            await engine.start_workflow("saga_compensation", workflow_id, _saga_compensation_payload())
            _ = await _wait_execution_terminal(store, workflow_id, timeout_s=10.0)
            chain_latencies_ms.append(_perf_ms(started))

            events = await store.get_events(workflow_id, limit=2000)
            completed_order = [
                event.payload.get("task_id", "")
                for event in events
                if event.event_type == _EVENT_COMPENSATION_TASK_COMPLETED
            ]
            if completed_order[:2] == ["TaskB", "TaskA"]:
                consistency_ok += 1

            sequence_values = [event.seq for event in events]
            if sequence_values == sorted(sequence_values):
                log_integrity_ok += 1

            started_map: dict[str, str] = {}
            for event in events:
                task_id = str(event.payload.get("task_id", ""))
                if event.event_type == _EVENT_COMPENSATION_TASK_STARTED and task_id:
                    started_map[task_id] = event.created_at
                elif event.event_type == _EVENT_COMPENSATION_TASK_COMPLETED and task_id:
                    start_iso = started_map.get(task_id)
                    if start_iso:
                        start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
                        end_dt = datetime.fromisoformat(event.created_at.replace("Z", "+00:00"))
                        compensation_op_ms.append((end_dt - start_dt).total_seconds() * 1000.0)
    finally:
        await engine.stop()

    chain_stats = summarize_samples(chain_latencies_ms, warmup_rounds=case.warmup_rounds)
    op_stats = summarize_samples(compensation_op_ms, warmup_rounds=case.warmup_rounds)
    return {
        "compensation_chain_p50_ms": chain_stats.p50,
        "compensation_chain_p90_ms": chain_stats.p90,
        "compensation_chain_p99_ms": chain_stats.p99,
        "compensation_op_p99_ms": op_stats.p99,
        "consistency_percent": (consistency_ok / iterations) * 100.0,
        "event_log_integrity_percent": (log_integrity_ok / iterations) * 100.0,
    }


def _resume_payload(task_count: int = 10) -> dict[str, Any]:
    tasks = [
        {
            "id": f"Task{index}",
            "type": "noop",
            "retry": {"max_attempts": 1},
        }
        for index in range(task_count)
    ]
    return {"orchestration": {"mode": "dag", "max_concurrency": 2, "tasks": tasks}}


class _BlockedResumeWorkflowEngine(WorkflowEngine):
    """WorkflowEngine variant that keeps resumed workflow task running."""

    async def _run_workflow(self, workflow_id: str) -> None:
        _ = workflow_id
        await asyncio.sleep(10.0)


async def _exec_tc_chr_002(case: HolographicCase) -> dict[str, float]:
    iterations = max(50, min(case.min_samples, 150))
    resume_samples_ms: list[float] = []
    skip_checks = 0
    skip_ok = 0
    result_consistency_ok = 0

    for index in range(iterations):
        store = _InMemoryWorkflowStore()
        workflow_id = f"tc-chr-002-{index}"
        payload = _resume_payload(task_count=10)
        await store.create_execution(workflow_id, "resume_bench", payload)
        await store.append_event(workflow_id, "workflow_contract_loaded", {"task_count": 10})
        for task_index in range(10):
            if task_index < 5:
                status = WorkflowTaskStatus.COMPLETED.value
            elif task_index < 8:
                status = WorkflowTaskStatus.PENDING.value
            else:
                status = "blocked"
            await store.upsert_task_state(
                workflow_id=workflow_id,
                task_id=f"Task{task_index}",
                task_type="noop",
                handler_name="",
                status=status,
                attempt=0,
                max_attempts=1,
                started_at=None,
                ended_at=None,
                result={"task": task_index} if status == WorkflowTaskStatus.COMPLETED.value else None,
                error="",
                metadata={},
            )

        engine = _BlockedResumeWorkflowEngine(
            store=store,
            timer_wheel=TimerWheel(tick_interval=0.01),
            task_queue_manager=TaskQueueManager(),
            activity_runner=ActivityRunner(max_concurrent=4),
        )

        started = time.perf_counter_ns()
        resume_result = await engine.resume_workflow("resume_bench", workflow_id, None)
        resume_samples_ms.append(_perf_ms(started))

        if resume_result.submitted and resume_result.status == "resumed":
            result_consistency_ok += 1

        state = engine._workflow_state.get(workflow_id)
        if state is not None:
            for completed_index in range(5):
                skip_checks += 1
                task_state = state.task_states.get(f"Task{completed_index}")
                if task_state is not None and task_state.status == WorkflowTaskStatus.COMPLETED.value:
                    skip_ok += 1

        running_task = engine._workflow_tasks.get(workflow_id)
        if running_task is not None:
            running_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await running_task

    stats = summarize_samples(resume_samples_ms, warmup_rounds=case.warmup_rounds)
    skip_accuracy = (skip_ok / skip_checks) * 100.0 if skip_checks else 0.0
    consistency = (result_consistency_ok / iterations) * 100.0
    return {
        "resume_p50_ms": stats.p50,
        "resume_p90_ms": stats.p90,
        "resume_p99_ms": stats.p99,
        "skip_accuracy_percent": skip_accuracy,
        "result_consistency_percent": consistency,
    }


def _waiting_human_payload() -> dict[str, Any]:
    return {
        "orchestration": {
            "mode": "dag",
            "max_concurrency": 2,
            "high_risk_actions": ["risky_task"],
            "tasks": [
                {
                    "id": "safe_task",
                    "type": "activity",
                    "handler": "safe_handler",
                    "retry": {"max_attempts": 1},
                },
                {
                    "id": "risky_task",
                    "type": "activity",
                    "handler": "risky_handler",
                    "depends_on": ["safe_task"],
                    "is_high_risk": True,
                    "retry": {"max_attempts": 1},
                },
                {
                    "id": "final_task",
                    "type": "activity",
                    "handler": "final_handler",
                    "depends_on": ["risky_task"],
                    "retry": {"max_attempts": 1},
                },
            ],
        }
    }


async def _exec_tc_chr_003(case: HolographicCase) -> dict[str, float]:
    store = _InMemoryWorkflowStore()
    timer_wheel = TimerWheel(tick_interval=0.005)
    queue_manager = TaskQueueManager()
    activity_runner = ActivityRunner(max_concurrent=8)
    engine = SagaWorkflowEngine(
        store=store,
        timer_wheel=timer_wheel,
        task_queue_manager=queue_manager,
        activity_runner=activity_runner,
        checkpoint_interval_seconds=0.0,
        human_review_timeout_seconds=30.0,
    )
    await engine.start()

    async def _safe_handler(payload: Any) -> dict[str, Any]:
        return {"safe": True, "payload": payload}

    async def _risky_handler(payload: Any) -> dict[str, Any]:
        return {"approved": True, "payload": payload}

    async def _final_handler(payload: Any) -> dict[str, Any]:
        return {"final": True, "payload": payload}

    engine.register_activity("safe_handler", _safe_handler)
    engine.register_activity("risky_handler", _risky_handler)
    engine.register_activity("final_handler", _final_handler)

    iterations = max(20, min(case.min_samples, 80))
    suspend_samples_ms: list[float] = []
    resume_samples_ms: list[float] = []

    try:
        for index in range(iterations):
            workflow_id = f"tc-chr-003-{index}"
            start_ns = time.perf_counter_ns()
            await engine.start_workflow("waiting_human", workflow_id, _waiting_human_payload())
            state = await _wait_task_status(
                store,
                workflow_id,
                "risky_task",
                target_status=WorkflowTaskStatus.WAITING_HUMAN.value,
                timeout_s=10.0,
            )
            if state is None:
                continue
            suspend_samples_ms.append(_perf_ms(start_ns))

            approve_start = time.perf_counter_ns()
            await engine.signal_workflow(
                workflow_id,
                "approve_task",
                {"task_id": "risky_task"},
            )
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                current = await _wait_task_status(store, workflow_id, "risky_task", timeout_s=0.01)
                if current is not None and current.status != WorkflowTaskStatus.WAITING_HUMAN.value:
                    break
                await asyncio.sleep(0.005)
            resume_samples_ms.append(_perf_ms(approve_start))

            _ = await _wait_execution_terminal(store, workflow_id, timeout_s=10.0)
    finally:
        await engine.stop()

    suspend_stats = summarize_samples(suspend_samples_ms, warmup_rounds=case.warmup_rounds)
    resume_stats = summarize_samples(resume_samples_ms, warmup_rounds=case.warmup_rounds)
    return {
        "suspend_p50_ms": suspend_stats.p50,
        "suspend_p99_ms": suspend_stats.p99,
        "resume_p50_ms": resume_stats.p50,
        "resume_p99_ms": resume_stats.p99,
    }
