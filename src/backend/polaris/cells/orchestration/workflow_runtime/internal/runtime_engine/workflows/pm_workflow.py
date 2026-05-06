"""Workflow top-level workflow for PM -> Director -> QA orchestration."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from polaris.cells.orchestration.workflow_runtime.internal.models import (
    DirectorWorkflowInput,
    PMWorkflowInput,
    PMWorkflowResult,
    QAWorkflowInput,
    QAWorkflowResult,
    TaskContract,
    director_workflow_id,
    qa_workflow_id,
)
from polaris.cells.orchestration.workflow_runtime.internal.runtime_queries import WorkflowQueryState
from polaris.cells.orchestration.workflow_runtime.internal.workflow_client import get_workflow_api
from polaris.infrastructure.di.container import get_container
from polaris.kernelone.events.message_bus import MessageBus, MessageType

from .director_workflow import DirectorWorkflow
from .qa_workflow import QAWorkflow

logger = logging.getLogger(__name__)

workflow = get_workflow_api()


def _extract_tasks(payload: Any, fallback: list[TaskContract]) -> list[TaskContract]:
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


def _validation_ok(payload: Any) -> tuple[bool, list[str]]:
    if not isinstance(payload, dict):
        return False, ["invalid_validation_payload"]
    raw_errors = payload.get("errors")
    errors: list[str] = raw_errors if isinstance(raw_errors, list) else []
    return bool(payload.get("success")), [str(item).strip() for item in errors if str(item).strip()]


def _director_status(payload: Any) -> str:
    if isinstance(payload, dict):
        return str(payload.get("status") or "").strip()
    return str(getattr(payload, "status", "") or "").strip()


def _qa_outcome(payload: Any) -> tuple[bool, str]:
    """Extract QA outcome from QAWorkflowResult."""
    passed = bool(getattr(payload, "passed", False))
    status = "passed" if passed else str(getattr(payload, "reason", "qa_failed") or "qa_failed").strip()
    return passed, status


def _director_execution_mode(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in {"serial", "sequential"}:
        return "serial"
    if token == "parallel":
        return token
    return "parallel"


def _director_positive_int(value: Any, default: int) -> int:
    try:
        return max(1, int(value))
    except (RuntimeError, ValueError):
        return max(1, int(default))


def _record_resident_decision_safe(workspace: str, payload: dict[str, Any]) -> None:
    try:
        from polaris.cells.resident.autonomy.public.service import record_resident_decision

        record_resident_decision(workspace, payload)
    except (RuntimeError, ValueError):
        logger.debug("DEBUG: pm_workflow.py:{87} {exc} (swallowed)")


@workflow.defn
class PMWorkflow(WorkflowQueryState):
    """Coordinate the PM, Director, and QA Workflow workflows."""

    def __init__(self) -> None:
        super().__init__()
        self.workspace: str = ""
        self.run_id: str = ""

    async def _broadcast_task_trace(
        self,
        phase: str,
        step_kind: str,
        step_title: str,
        step_detail: str,
        status: str,
        task_id: str = "pm::global",
        **refs,
    ) -> None:
        """Broadcast PM task trace event to frontend."""
        try:
            from polaris.cells.orchestration.workflow_runtime.internal.task_trace import TaskTraceBuilder

            builder = TaskTraceBuilder(
                run_id=self.run_id or self.workspace,
                role="pm",
                task_id=task_id,
            )

            event = builder.build(
                phase=phase,
                step_kind=step_kind,
                step_title=step_title,
                step_detail=step_detail,
                status=status,
                **refs,
            )

            payload = builder.to_ws_payload(event)

            container = await get_container()
            message_bus = await container.resolve_async(MessageBus)
            if message_bus:
                await message_bus.broadcast(
                    MessageType.TASK_TRACE,
                    "pm_workflow",
                    payload,
                )
        except (RuntimeError, ValueError) as e:
            # Log trace broadcast errors at debug level for troubleshooting
            # but don't disrupt workflow execution
            import logging

            logging.getLogger(__name__).debug(f"Task trace broadcast error: {e}")

    @workflow.run
    async def run(self, workflow_input: PMWorkflowInput) -> PMWorkflowResult:
        self.workspace = workflow_input.workspace
        self.run_id = workflow_input.run_id

        # PM 启动事件
        await self._broadcast_task_trace(
            phase="planning",
            step_kind="system",
            step_title="PM started",
            step_detail="Project Manager orchestration started",
            status="started",
            task_id="pm::global",
        )

        self._record_event(
            stage="pm_started",
            message="PM workflow started",
            details={"run_id": workflow_input.run_id, "workspace": workflow_input.workspace},
        )
        _record_resident_decision_safe(
            workflow_input.workspace,
            {
                "run_id": workflow_input.run_id,
                "actor": "pm",
                "stage": "workflow_start",
                "summary": "PM workflow started",
                "strategy_tags": ["contract_first", "governed_planning"],
                "expected_outcome": {"status": "planning", "success": True},
                "actual_outcome": {"status": "planning", "success": True},
                "verdict": "success",
                "evidence_refs": ["runtime/contracts/plan.md"],
                "confidence": 0.7,
            },
        )
        tasks = workflow_input.payload_tasks()
        if not tasks:
            generated = await workflow.execute_activity(
                "generate_pm_tasks",
                workflow_input,
                start_to_close_timeout=timedelta(minutes=10),
            )
            tasks = _extract_tasks(generated, tasks)

        # 任务生成后事件
        await self._broadcast_task_trace(
            phase="planning",
            step_kind="system",
            step_title="Tasks generated",
            step_detail=f"Generated {len(tasks)} tasks from plan",
            status="completed",
            task_id="pm::global",
            related_task_ids=[t.task_id for t in tasks],
        )
        _record_resident_decision_safe(
            workflow_input.workspace,
            {
                "run_id": workflow_input.run_id,
                "actor": "pm",
                "stage": "task_generation",
                "summary": f"Generated {len(tasks)} PM task contracts",
                "strategy_tags": ["task_decomposition", "contract_generation"],
                "expected_outcome": {"status": "tasks_generated", "task_count_min": 1},
                "actual_outcome": {"status": "tasks_generated", "task_count": len(tasks)},
                "verdict": "success" if len(tasks) > 0 else "failure",
                "evidence_refs": ["runtime/contracts/plan.md", "runtime/contracts/agents.generated.md"],
                "context_refs": [task.task_id for task in tasks],
                "confidence": 0.68,
            },
        )

        validation_payload = await workflow.execute_activity(
            "validate_task_contract",
            {
                "tasks": [task.to_dict() for task in tasks],
                "docs_stage": (
                    workflow_input.metadata.get("docs_stage", {}) if isinstance(workflow_input.metadata, dict) else {}
                ),
            },
            start_to_close_timeout=timedelta(minutes=2),
        )
        valid, errors = _validation_ok(validation_payload)
        if not valid:
            reason = "; ".join(errors) if errors else "PM task contract validation failed"
            _record_resident_decision_safe(
                workflow_input.workspace,
                {
                    "run_id": workflow_input.run_id,
                    "actor": "pm",
                    "stage": "contract_validation",
                    "summary": reason,
                    "strategy_tags": ["contract_validation"],
                    "expected_outcome": {"status": "validated", "success": True},
                    "actual_outcome": {"status": "validation_failed", "success": False, "errors": errors},
                    "verdict": "failure",
                    "evidence_refs": ["runtime/contracts/plan.md"],
                    "context_refs": [task.task_id for task in tasks],
                    "confidence": 0.8,
                },
            )
            # PM 失败事件
            await self._broadcast_task_trace(
                phase="failed",
                step_kind="validation",
                step_title="Contract validation failed",
                step_detail=reason,
                status="failed",
                task_id="pm::global",
                errors=errors,
            )
            self._record_event(
                stage="pm_failed",
                message=reason,
                details={"run_id": workflow_input.run_id, "errors": errors},
            )
            raise RuntimeError(reason)

        # 合同校验通过后事件
        await self._broadcast_task_trace(
            phase="analyzing",
            step_kind="validation",
            step_title="Contract validated",
            step_detail=f"All {len(tasks)} tasks passed contract validation",
            status="completed",
            task_id="pm::global",
        )
        _record_resident_decision_safe(
            workflow_input.workspace,
            {
                "run_id": workflow_input.run_id,
                "actor": "pm",
                "stage": "contract_validation",
                "summary": f"Validated {len(tasks)} PM task contracts",
                "strategy_tags": ["contract_validation"],
                "expected_outcome": {"status": "validated", "success": True},
                "actual_outcome": {"status": "validated", "success": True, "task_count": len(tasks)},
                "verdict": "success",
                "evidence_refs": ["runtime/contracts/plan.md"],
                "context_refs": [task.task_id for task in tasks],
                "confidence": 0.82,
            },
        )

        for task in tasks:
            self._set_task_status(task.task_id, "planned", summary="PM task ready")

        director_config = (
            workflow_input.metadata.get("director_config", {})
            if isinstance(workflow_input.metadata, dict)
            and isinstance(workflow_input.metadata.get("director_config"), dict)
            else {}
        )

        # Director 启动事件
        await self._broadcast_task_trace(
            phase="executing",
            step_kind="system",
            step_title="Director started",
            step_detail=f"Launching Director with {len(tasks)} tasks",
            status="started",
            task_id="pm::global",
            director_config={
                "execution_mode": _director_execution_mode(director_config.get("execution_mode")),
                "max_parallel_tasks": _director_positive_int(
                    director_config.get("max_parallel_tasks"),
                    3,
                ),
            },
        )
        selected_mode = _director_execution_mode(director_config.get("execution_mode"))
        _record_resident_decision_safe(
            workflow_input.workspace,
            {
                "run_id": workflow_input.run_id,
                "actor": "pm",
                "stage": "director_handoff",
                "summary": f"Dispatch Director using {selected_mode} execution mode",
                "options": [
                    {
                        "label": "parallel_dispatch",
                        "rationale": "Favor throughput for independent tasks.",
                        "strategy_tags": ["parallel_dispatch"],
                        "estimated_score": 0.78,
                    },
                    {
                        "label": "serial_dispatch",
                        "rationale": "Favor predictability for tightly coupled tasks.",
                        "strategy_tags": ["serial_dispatch"],
                        "estimated_score": 0.62,
                    },
                ],
                "selected_option_id": "",
                "strategy_tags": [f"{selected_mode}_dispatch", "governed_handoff"],
                "expected_outcome": {"status": "director_running", "success": True},
                "actual_outcome": {
                    "status": "director_dispatched",
                    "success": True,
                    "execution_mode": selected_mode,
                },
                "verdict": "success",
                "evidence_refs": ["runtime/contracts/plan.md"],
                "context_refs": [task.task_id for task in tasks],
                "confidence": 0.73,
            },
        )

        director_result = await workflow.execute_child_workflow(
            DirectorWorkflow.run,
            DirectorWorkflowInput(
                workspace=workflow_input.workspace,
                run_id=workflow_input.run_id,
                tasks=tasks,
                execution_mode=_director_execution_mode(director_config.get("execution_mode")),
                max_parallel_tasks=_director_positive_int(
                    director_config.get("max_parallel_tasks"),
                    3,
                ),
                ready_timeout_seconds=_director_positive_int(
                    director_config.get("ready_timeout_seconds"),
                    30,
                ),
                task_timeout_seconds=_director_positive_int(
                    director_config.get("task_timeout_seconds"),
                    3600,
                ),
                metadata=dict(workflow_input.metadata),
            ),
            id=director_workflow_id(workflow_input.run_id),
        )

        director_status = _director_status(director_result)
        if director_status == "completed":
            qa_result = await workflow.execute_child_workflow(
                QAWorkflow.run,
                QAWorkflowInput(
                    workspace=workflow_input.workspace,
                    run_id=workflow_input.run_id,
                    director_status=director_status,
                    metadata=dict(workflow_input.metadata),
                ),
                id=qa_workflow_id(workflow_input.run_id),
            )
        else:
            qa_result = QAWorkflowResult(
                run_id=workflow_input.run_id,
                passed=False,
                reason="director_failed",
                evidence={"director_status": director_status},
            )
        qa_passed, qa_status = _qa_outcome(qa_result)

        # PM 完成事件
        final_status = "completed" if qa_passed else "failed"
        await self._broadcast_task_trace(
            phase="completed",
            step_kind="system",
            step_title="PM completed",
            step_detail="All PM phases completed successfully" if qa_passed else f"PM workflow failed: {qa_status}",
            status=final_status,
            task_id="pm::global",
            director_status=director_status,
            qa_status=qa_status,
        )

        self._record_event(
            stage="pm_completed",
            message="PM workflow completed",
            details={
                "run_id": workflow_input.run_id,
                "director_status": director_status,
                "qa_status": qa_status,
            },
        )
        _record_resident_decision_safe(
            workflow_input.workspace,
            {
                "run_id": workflow_input.run_id,
                "actor": "pm",
                "stage": "workflow_completion",
                "summary": f"PM workflow completed with director={director_status} qa={qa_status}",
                "strategy_tags": ["qa_gate", "workflow_completion"],
                "expected_outcome": {"status": "passed", "success": True},
                "actual_outcome": {
                    "status": "passed" if qa_passed else qa_status,
                    "success": qa_passed,
                    "director_status": director_status,
                },
                "verdict": "success" if qa_passed else "failure",
                "evidence_refs": [
                    "runtime/results/director.result.json",
                    "runtime/results/integration_qa.result.json",
                    "runtime/results/unit_qa.result.json",
                    "runtime/status/engine.status.json",
                ],
                "qa_gate": {
                    "passed": qa_passed,
                    "status": qa_status,
                    "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                    "path": "qa_workflow",
                },
                "context_refs": [task.task_id for task in tasks],
                "confidence": 0.86 if qa_passed else 0.74,
            },
        )
        return PMWorkflowResult(
            run_id=workflow_input.run_id,
            tasks=tasks,
            director_status=director_status,
            qa_status="passed" if qa_passed else qa_status,
            metadata={"task_count": len(tasks)},
        )
