"""PM-related Workflow activities."""

from __future__ import annotations

from typing import Any, cast

from polaris.cells.orchestration.pm_planning.public.service import evaluate_pm_task_quality
from polaris.cells.orchestration.workflow_runtime.internal.models import PMWorkflowInput, TaskContract
from polaris.cells.orchestration.workflow_runtime.internal.workflow_client import get_activity_api

from .base import ActivityExecutionResult, register_activity

activity = get_activity_api()


def _validate_tasks(tasks: list[TaskContract]) -> list[str]:
    issues: list[str] = []
    if not tasks:
        issues.append("No PM tasks were provided to the Workflow workflow")
        return issues
    for task in tasks:
        if not task.task_id:
            issues.append("Task is missing id")
        if not task.title:
            issues.append(f"Task {task.task_id or '<unknown>'} is missing title")
        acceptance = task.payload.get("acceptance_criteria")
        if not isinstance(acceptance, list) or not [str(item).strip() for item in acceptance if str(item).strip()]:
            issues.append(f"Task {task.task_id or '<unknown>'} is missing acceptance_criteria")
    return issues


@register_activity("generate_pm_tasks")
@activity.defn(name="generate_pm_tasks")
async def generate_pm_tasks(workflow_input: PMWorkflowInput) -> dict[str, Any]:
    """Bridge a precomputed PM payload into the Workflow workflow."""
    tasks = [task.to_dict() for task in workflow_input.payload_tasks()]
    if not tasks:
        return ActivityExecutionResult(
            success=False,
            summary=("Workflow PM workflow requires a registered PM generator or a precomputed payload"),
            errors=["no_precomputed_pm_payload"],
        ).to_dict()
    return ActivityExecutionResult(
        success=True,
        summary="Using precomputed PM payload from legacy orchestrator",
        payload={"tasks": tasks, "task_count": len(tasks)},
    ).to_dict()


@register_activity("validate_task_contract")
@activity.defn(name="validate_task_contract")
async def validate_task_contract(
    payload: dict[str, Any] | list[TaskContract] | list[dict[str, Any]],
) -> dict[str, Any]:
    """Run the existing PM quality gate before Director execution."""
    docs_stage: dict[str, Any] = {}
    if isinstance(payload, dict):
        tasks = payload.get("tasks") if isinstance(payload.get("tasks"), list) else []
        docs_stage = cast(
            "dict[str, Any]",
            payload.get("docs_stage") if isinstance(payload.get("docs_stage"), dict) else {},
        )
    else:
        tasks = payload
    normalized_tasks: list[TaskContract] = []
    for item in tasks or []:
        normalized_tasks.append(item if isinstance(item, TaskContract) else TaskContract.from_mapping(item))
    issues = _validate_tasks(normalized_tasks)
    if not issues:
        try:
            report = evaluate_pm_task_quality(
                {"tasks": [task.to_dict() for task in normalized_tasks]},
                docs_stage=docs_stage,
            )
            if not bool(report.get("ok")):
                issues.extend([str(item).strip() for item in report.get("critical_issues") or [] if str(item).strip()])
        except (RuntimeError, ValueError) as exc:
            issues.append(f"pm_quality_gate_runtime_error: {exc}")
    return ActivityExecutionResult(
        success=not issues,
        summary="PM task contract validated" if not issues else "PM task contract rejected",
        payload={"task_count": len(normalized_tasks), "docs_stage": docs_stage},
        errors=issues,
    ).to_dict()
