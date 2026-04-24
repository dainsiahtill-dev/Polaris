"""Workflow workflow for Director task fan-out and convergence."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from polaris.cells.orchestration.shared_types import ErrorCategory, ErrorClassifier
from polaris.cells.orchestration.workflow_runtime.internal.models import (
    DirectorTaskInput,
    DirectorTaskResult,
    DirectorWorkflowInput,
    DirectorWorkflowResult,
    TaskContract,
    TaskFailureRecord,
)
from polaris.cells.orchestration.workflow_runtime.internal.runtime_queries import WorkflowQueryState
from polaris.cells.orchestration.workflow_runtime.internal.workflow_client import get_workflow_api

from .director_task_workflow import DirectorTaskWorkflow

logger = logging.getLogger(__name__)

workflow = get_workflow_api()


def _extract_ready_tasks(
    payload: Any,
    fallback: list[TaskContract],
) -> list[TaskContract]:
    if not isinstance(payload, dict):
        return list(fallback)
    activity_payload = payload.get("payload")
    if not isinstance(activity_payload, dict):
        return list(fallback)
    raw_tasks_val = activity_payload.get("tasks")
    raw_tasks: list[Any] = raw_tasks_val if isinstance(raw_tasks_val, list) else []
    tasks: list[TaskContract] = []
    for item in raw_tasks:
        contract = TaskContract.from_mapping(item)
        if contract.task_id:
            tasks.append(contract)
    return tasks or list(fallback)


def _coerce_task_result(payload: Any) -> DirectorTaskResult | None:
    if isinstance(payload, DirectorTaskResult):
        return payload
    if not isinstance(payload, dict):
        return None
    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        return None
    raw_completed_phases = payload.get("completed_phases")
    completed_phases: list[str] = (
        [str(item).strip() for item in raw_completed_phases if str(item).strip()]
        if isinstance(raw_completed_phases, list)
        else []
    )
    raw_errors = payload.get("errors")
    errors: list[str] = (
        [str(item).strip() for item in raw_errors if str(item).strip()] if isinstance(raw_errors, list) else []
    )
    raw_metadata = payload.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    return DirectorTaskResult(
        task_id=task_id,
        status=str(payload.get("status") or "").strip(),
        completed_phases=completed_phases,
        errors=errors,
        metadata={str(key): value for key, value in metadata.items()},
    )


def _task_dependencies(task: TaskContract) -> set[str]:
    payload = task.payload if isinstance(task.payload, dict) else {}
    raw_dependencies: list[Any] = []
    if isinstance(payload.get("depends_on"), list):
        raw_dependencies.extend(payload.get("depends_on") or [])
    if isinstance(payload.get("blocked_by"), list):
        raw_dependencies.extend(payload.get("blocked_by") or [])
    return {str(item).strip() for item in raw_dependencies if str(item).strip()}


def _positive_int(value: Any, default: int) -> int:
    try:
        return max(1, int(value))
    except (RuntimeError, ValueError):
        return max(1, int(default))


def _execution_mode(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in {"serial", "parallel"}:
        return token
    return "parallel"


def _record_resident_decision_safe(workspace: str, payload: dict[str, Any]) -> None:
    try:
        from polaris.cells.resident.autonomy.public.service import record_resident_decision

        record_resident_decision(workspace, payload)
    except (RuntimeError, ValueError):
        logger.warning(
            "director_workflow: failed to record resident decision: run_id=%s stage=%s",
            payload.get("run_id"),
            payload.get("stage"),
            exc_info=True,
        )


@workflow.defn
class DirectorWorkflow(WorkflowQueryState):
    """Execute Director tasks as Workflow child workflows."""

    def __init__(self) -> None:
        super().__init__()

    @workflow.run
    async def run(self, workflow_input: DirectorWorkflowInput) -> DirectorWorkflowResult:
        mode = _execution_mode(workflow_input.execution_mode)
        parallel_limit = 1 if mode == "serial" else _positive_int(workflow_input.max_parallel_tasks, 3)
        ready_timeout_seconds = _positive_int(workflow_input.ready_timeout_seconds, 30)
        task_timeout_seconds = _positive_int(workflow_input.task_timeout_seconds, 3600)
        self._record_event(
            stage="director_started",
            message="Director workflow started",
            details={
                "run_id": workflow_input.run_id,
                "task_count": len(workflow_input.tasks),
                "execution_mode": mode,
                "max_parallel_tasks": parallel_limit,
            },
        )
        _record_resident_decision_safe(
            workflow_input.workspace,
            {
                "run_id": workflow_input.run_id,
                "actor": "director",
                "stage": "workflow_start",
                "summary": f"Director workflow started in {mode} mode",
                "strategy_tags": [f"{mode}_dispatch", "workflow_fanout"],
                "expected_outcome": {"status": "tasks_queued", "success": True},
                "actual_outcome": {
                    "status": "tasks_queued",
                    "success": True,
                    "task_count": len(workflow_input.tasks),
                    "max_parallel_tasks": parallel_limit,
                },
                "verdict": "success",
                "evidence_refs": ["runtime/contracts/plan.md"],
                "context_refs": [task.task_id for task in workflow_input.tasks],
                "confidence": 0.75,
            },
        )
        ready_payload = await workflow.execute_activity(
            "get_ready_tasks",
            workflow_input,
            start_to_close_timeout=timedelta(seconds=ready_timeout_seconds),
        )
        all_tasks = _extract_ready_tasks(ready_payload, workflow_input.tasks)
        for task in all_tasks:
            self._set_task_status(task.task_id, "queued", summary="Queued for Director")

        completed = 0
        failed = 0
        completed_ids: set[str] = set()
        failed_ids: set[str] = set()
        pending: dict[str, TaskContract] = {task.task_id: task for task in all_tasks}
        dispatch_cycle = 0

        while pending:
            dispatch_cycle += 1
            self._record_event(
                stage="director_dispatch_cycle",
                message="Director evaluating TaskBoard-ready tasks",
                details={
                    "run_id": workflow_input.run_id,
                    "cycle": dispatch_cycle,
                    "pending_count": len(pending),
                    "completed_count": len(completed_ids),
                    "failed_count": len(failed_ids),
                    "pending_task_ids": sorted(pending.keys())[:20],
                },
            )
            blocked_by_failed = [task for task in pending.values() if _task_dependencies(task).intersection(failed_ids)]
            for task in blocked_by_failed:
                pending.pop(task.task_id, None)
                failed += 1
                failed_ids.add(task.task_id)
                self._set_task_status(
                    task.task_id,
                    "blocked",
                    summary="Blocked by failed dependency",
                    metadata={"dependencies": sorted(_task_dependencies(task))},
                )
                _record_resident_decision_safe(
                    workflow_input.workspace,
                    {
                        "run_id": workflow_input.run_id,
                        "actor": "director",
                        "stage": "dependency_block",
                        "task_id": task.task_id,
                        "summary": "Task blocked by failed dependency",
                        "strategy_tags": ["dependency_guard", f"{mode}_dispatch"],
                        "expected_outcome": {"status": "ready", "success": True},
                        "actual_outcome": {
                            "status": "blocked",
                            "success": False,
                            "dependencies": sorted(_task_dependencies(task)),
                        },
                        "verdict": "blocked",
                        "evidence_refs": ["runtime/contracts/plan.md"],
                        "context_refs": sorted(_task_dependencies(task)),
                        "confidence": 0.84,
                    },
                )

            if not pending:
                break

            ready_batch = [task for task in pending.values() if _task_dependencies(task).issubset(completed_ids)]
            if not ready_batch:
                self._record_event(
                    stage="director_waiting_ready_tasks",
                    message="Director has no ready tasks in current cycle",
                    details={
                        "run_id": workflow_input.run_id,
                        "cycle": dispatch_cycle,
                        "pending_count": len(pending),
                        "pending_task_ids": sorted(task.task_id for task in pending.values())[:20],
                        "completed_task_ids": sorted(completed_ids)[:20],
                        "failed_task_ids": sorted(failed_ids)[:20],
                    },
                )
                for task in list(pending.values()):
                    pending.pop(task.task_id, None)
                    failed += 1
                    failed_ids.add(task.task_id)
                    self._set_task_status(
                        task.task_id,
                        "failed",
                        summary="Dependency graph cannot converge",
                        metadata={"dependencies": sorted(_task_dependencies(task))},
                    )
                self._record_event(
                    stage="director_deadlock",
                    message="Director dependency graph cannot converge",
                    details={"pending_task_ids": sorted(pending.keys())},
                )
                _record_resident_decision_safe(
                    workflow_input.workspace,
                    {
                        "run_id": workflow_input.run_id,
                        "actor": "director",
                        "stage": "dependency_deadlock",
                        "summary": "Director dependency graph cannot converge",
                        "strategy_tags": ["dependency_graph", "deadlock_detection"],
                        "expected_outcome": {"status": "ready_batch", "success": True},
                        "actual_outcome": {
                            "status": "deadlock",
                            "success": False,
                            "pending_task_ids": sorted(task.task_id for task in pending.values()),
                        },
                        "verdict": "failure",
                        "evidence_refs": ["runtime/contracts/plan.md"],
                        "context_refs": sorted(task.task_id for task in pending.values()),
                        "confidence": 0.9,
                    },
                )
                break

            batch = ready_batch[:parallel_limit]
            self._record_event(
                stage="director_batch_selected",
                message="Director selected ready dispatch batch",
                details={
                    "run_id": workflow_input.run_id,
                    "cycle": dispatch_cycle,
                    "ready_count": len(ready_batch),
                    "dispatch_count": len(batch),
                    "ready_task_ids": [task.task_id for task in ready_batch[:20]],
                    "batch_task_ids": [task.task_id for task in batch],
                },
            )
            results = await asyncio.gather(
                *[
                    workflow.execute_child_workflow(
                        DirectorTaskWorkflow.run,
                        DirectorTaskInput(
                            workspace=workflow_input.workspace,
                            run_id=workflow_input.run_id,
                            task=task,
                            metadata=dict(workflow_input.metadata),
                        ),
                        run_timeout=timedelta(seconds=task_timeout_seconds),
                    )
                    for task in batch
                ],
                return_exceptions=True,  # 捕获异常，防止单个任务失败导致整个批处理中断
            )

            for task, item in zip(batch, results, strict=False):
                pending.pop(task.task_id, None)
                # 处理可能的异常
                if isinstance(item, Exception):
                    # 分类错误以确定恢复策略
                    category, recommendation = ErrorClassifier.analyze(item)
                    failed += 1
                    failed_ids.add(task.task_id)

                    failure_record = TaskFailureRecord(
                        task_id=task.task_id,
                        error_message=str(item),
                        error_category=category.value,
                        retryable=recommendation.can_retry,
                        max_retries=recommendation.max_retries,
                        recovery_strategy=recommendation.strategy,
                    )

                    # 对于可重试的瞬时错误，可以在这里实现重试逻辑
                    # 目前先记录失败，后续可以实现自动重试批次
                    self._set_task_status(
                        task.task_id,
                        "failed",
                        summary=f"Director child workflow raised exception: {item}",
                        metadata={
                            "error_category": category.value,
                            "retryable": recommendation.can_retry,
                            "recovery_strategy": recommendation.strategy,
                            "failure_record": failure_record.to_dict(),
                        },
                    )
                    _record_resident_decision_safe(
                        workflow_input.workspace,
                        {
                            "run_id": workflow_input.run_id,
                            "actor": "director",
                            "stage": "task_execution",
                            "task_id": task.task_id,
                            "summary": f"Director child workflow raised exception: {item}",
                            "strategy_tags": [f"{mode}_dispatch", "exception_path"],
                            "expected_outcome": {"status": "completed", "success": True},
                            "actual_outcome": {
                                "status": "failed",
                                "success": False,
                                "error_category": category.value,
                                "retryable": recommendation.can_retry,
                            },
                            "verdict": "failure",
                            "evidence_refs": ["runtime/results/director.result.json"],
                            "confidence": 0.88,
                        },
                    )
                    continue
                result = _coerce_task_result(item)
                if result is None:
                    failed += 1
                    failed_ids.add(task.task_id)
                    self._set_task_status(
                        task.task_id,
                        "failed",
                        summary="Director child workflow returned invalid payload",
                        metadata={"error_category": ErrorCategory.PERMANENT_VALIDATION.value},
                    )
                    _record_resident_decision_safe(
                        workflow_input.workspace,
                        {
                            "run_id": workflow_input.run_id,
                            "actor": "director",
                            "stage": "task_execution",
                            "task_id": task.task_id,
                            "summary": "Director child workflow returned invalid payload",
                            "strategy_tags": [f"{mode}_dispatch", "payload_validation"],
                            "expected_outcome": {"status": "completed", "success": True},
                            "actual_outcome": {
                                "status": "invalid_payload",
                                "success": False,
                            },
                            "verdict": "failure",
                            "evidence_refs": ["runtime/results/director.result.json"],
                            "confidence": 0.86,
                        },
                    )
                    continue
                if result.status == "completed":
                    completed += 1
                    completed_ids.add(result.task_id)
                    self._set_task_status(
                        result.task_id,
                        "completed",
                        summary="Director child workflow completed",
                    )
                    _record_resident_decision_safe(
                        workflow_input.workspace,
                        {
                            "run_id": workflow_input.run_id,
                            "actor": "director",
                            "stage": "task_execution",
                            "task_id": result.task_id,
                            "summary": "Director child workflow completed",
                            "strategy_tags": [f"{mode}_dispatch", "task_execution"],
                            "expected_outcome": {"status": "completed", "success": True},
                            "actual_outcome": {
                                "status": "completed",
                                "success": True,
                                "completed_phases": list(result.completed_phases),
                            },
                            "verdict": "success",
                            "evidence_refs": ["runtime/results/director.result.json"],
                            "confidence": 0.82,
                        },
                    )
                else:
                    failed += 1
                    failed_ids.add(result.task_id)

                    # 分析错误以提供更好的元数据
                    error_metadata: dict[str, Any] = {"errors": list(result.errors)}
                    if result.errors:
                        # 分析第一个错误进行分类
                        category, recommendation = ErrorClassifier.classify_from_message(result.errors[0])
                        error_metadata["error_category"] = category.value
                        error_metadata["retryable"] = recommendation.can_retry
                        error_metadata["recovery_strategy"] = recommendation.strategy

                    self._set_task_status(
                        result.task_id,
                        "failed",
                        summary="Director child workflow failed",
                        metadata=error_metadata,
                    )
                    _record_resident_decision_safe(
                        workflow_input.workspace,
                        {
                            "run_id": workflow_input.run_id,
                            "actor": "director",
                            "stage": "task_execution",
                            "task_id": result.task_id,
                            "summary": "Director child workflow failed",
                            "strategy_tags": [f"{mode}_dispatch", "task_execution"],
                            "expected_outcome": {"status": "completed", "success": True},
                            "actual_outcome": {
                                "status": "failed",
                                "success": False,
                                "errors": list(result.errors),
                                **error_metadata,
                            },
                            "verdict": "failure",
                            "evidence_refs": ["runtime/results/director.result.json"],
                            "confidence": 0.83,
                        },
                    )

            self._record_event(
                stage="director_batch_completed",
                message="Director finished dispatch cycle batch",
                details={
                    "run_id": workflow_input.run_id,
                    "cycle": dispatch_cycle,
                    "remaining_pending_count": len(pending),
                    "completed_count": completed,
                    "failed_count": failed,
                    "completed_task_ids": sorted(completed_ids)[:20],
                    "failed_task_ids": sorted(failed_ids)[:20],
                },
            )

        status = "completed" if failed == 0 else "failed"
        self._record_event(
            stage="director_completed",
            message=f"Director workflow {status}",
            details={
                "run_id": workflow_input.run_id,
                "completed_tasks": completed,
                "failed_tasks": failed,
            },
        )
        _record_resident_decision_safe(
            workflow_input.workspace,
            {
                "run_id": workflow_input.run_id,
                "actor": "director",
                "stage": "workflow_completion",
                "summary": f"Director workflow {status}",
                "strategy_tags": [f"{mode}_dispatch", "workflow_completion"],
                "expected_outcome": {"status": "completed", "success": True},
                "actual_outcome": {
                    "status": status,
                    "success": failed == 0,
                    "completed_tasks": completed,
                    "failed_tasks": failed,
                },
                "verdict": "success" if failed == 0 else "failure",
                "evidence_refs": ["runtime/results/director.result.json", "runtime/status/engine.status.json"],
                "context_refs": [task.task_id for task in all_tasks],
                "confidence": 0.87 if failed == 0 else 0.79,
            },
        )
        return DirectorWorkflowResult(
            run_id=workflow_input.run_id,
            status=status,
            completed_tasks=completed,
            failed_tasks=failed,
            metadata={"task_count": len(all_tasks)},
        )
