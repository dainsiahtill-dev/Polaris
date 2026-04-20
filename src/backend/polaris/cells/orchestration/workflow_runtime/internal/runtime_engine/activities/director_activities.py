"""Director-related Workflow activities."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from polaris.cells.orchestration.workflow_runtime.internal.models import DirectorWorkflowInput, TaskContract
from polaris.cells.orchestration.workflow_runtime.internal.workflow_client import get_activity_api
from polaris.domain.entities.policy import Policy
from polaris.domain.state_machine import PhaseContext, PhaseExecutor, PhaseResult, TaskPhase
from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

from .base import ActivityExecutionResult, register_activity

activity = get_activity_api()

_PHASE_NAME_MAP = {
    "prepare": TaskPhase.PLANNING,
    "planning": TaskPhase.PLANNING,
    "validate": TaskPhase.VALIDATION,
    "validation": TaskPhase.VALIDATION,
    "implement": TaskPhase.EXECUTION,
    "execution": TaskPhase.EXECUTION,
    "verify": TaskPhase.VERIFICATION,
    "verification": TaskPhase.VERIFICATION,
    "report": TaskPhase.COMPLETED,
}


def _normalize_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _normalize_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _phase_from_name(name: str) -> TaskPhase | None:
    return _PHASE_NAME_MAP.get(str(name or "").strip().lower())


def _serialize_context(context: PhaseContext) -> dict[str, Any]:
    return {
        "task_id": context.task_id,
        "workspace": context.workspace,
        "plan": context.plan,
        "blueprint": dict(context.blueprint),
        "policy_check_result": dict(context.policy_check_result),
        "snapshot_path": context.snapshot_path,
        "changed_files": list(context.changed_files),
        "verification_result": dict(context.verification_result),
        "build_round": int(context.build_round),
        "max_build_rounds": int(context.max_build_rounds),
        "stall_count": int(context.stall_count),
        "previous_missing_targets": list(context.previous_missing_targets),
        "previous_unresolved_imports": list(context.previous_unresolved_imports),
        "metadata": dict(context.metadata),
    }


def _build_context(payload: dict[str, Any], contract: TaskContract) -> PhaseContext:
    current = _normalize_dict(payload.get("context"))
    metadata = _normalize_dict(current.get("metadata"))
    metadata.update(
        {
            "task_payload": contract.to_dict(),
            "target_files": _normalize_list(contract.payload.get("target_files") or metadata.get("target_files")),
            "write_scope": _normalize_list(
                contract.payload.get("scope_paths")
                or contract.payload.get("target_files")
                or metadata.get("write_scope")
            )
            or [str(payload.get("workspace") or "").strip()],
            "acceptance_criteria": _normalize_list(
                contract.payload.get("acceptance_criteria") or metadata.get("acceptance_criteria")
            ),
        }
    )
    return PhaseContext(
        task_id=contract.task_id,
        workspace=str(payload.get("workspace") or current.get("workspace") or "").strip(),
        plan=str(current.get("plan") or contract.goal or contract.title).strip(),
        blueprint=_normalize_dict(current.get("blueprint")),
        policy_check_result=_normalize_dict(current.get("policy_check_result")),
        snapshot_path=str(current.get("snapshot_path") or "").strip() or None,
        changed_files=_normalize_list(current.get("changed_files")),
        verification_result=_normalize_dict(current.get("verification_result")),
        build_round=max(0, int(current.get("build_round") or 0)),
        max_build_rounds=max(1, int(current.get("max_build_rounds") or 4)),
        stall_count=max(0, int(current.get("stall_count") or 0)),
        previous_missing_targets=_normalize_list(current.get("previous_missing_targets")),
        previous_unresolved_imports=_normalize_list(current.get("previous_unresolved_imports")),
        metadata=metadata,
    )


def _result_payload(
    result: PhaseResult,
    context: PhaseContext,
    *,
    changed_files: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "phase": result.phase.name.lower(),
        "next_phase": result.next_phase.name.lower() if result.next_phase else "",
        "context": _serialize_context(context),
        "error_code": str(result.error_code or "").strip(),
        "can_retry": bool(result.can_retry),
        "should_rollback": bool(result.should_rollback),
    }
    if changed_files:
        payload["changed_files"] = list(changed_files)
    if result.phase == TaskPhase.VERIFICATION and result.can_retry:
        payload["retry_phase"] = TaskPhase.EXECUTION.name.lower()
    return payload


def _run_director_execution(
    *,
    workspace: str,
    run_id: str,
    contract: TaskContract,
    director_config: dict[str, Any],
    runtime_metadata: dict[str, Any],
) -> tuple[bool, str, list[str], dict[str, Any]]:
    from director_interface import DirectorTask, create_director

    project_root = Path(__file__).resolve().parents[5]
    cache_root = str(runtime_metadata.get("cache_root_full") or "").strip()
    if cache_root:
        task_root = os.path.join(
            cache_root,
            "workflow",
            run_id or "adhoc",
            contract.task_id or "task",
        )
    else:
        metadata_dir = get_workspace_metadata_dir_name()
        task_root = os.path.join(
            workspace,
            metadata_dir,
            "runtime",
            "workflow",
            run_id or "adhoc",
            contract.task_id or "task",
        )
    os.makedirs(task_root, exist_ok=True)
    result_path = os.path.join(task_root, "director.result.json")
    log_path = os.path.join(task_root, "director.log")

    # Use a more reasonable default timeout (5 minutes instead of 1 hour)
    # to prevent indefinite hanging. Can be overridden via config.
    timeout = director_config.get("timeout", 300)
    # Ensure timeout is within reasonable bounds (30s - 600s)
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        timeout = 300
    timeout = max(30, min(int(timeout), 600))

    config = {
        "script": str(director_config.get("script") or "src/backend/polaris/delivery/cli/loop-director.py"),
        "timeout": timeout,
        "model": str(director_config.get("model") or "").strip(),
        "prompt_profile": str(director_config.get("prompt_profile") or "").strip(),
        "director_result_path": result_path,
        "director_log_path": log_path,
        "project_root": project_root,
    }
    director_type = str(director_config.get("type") or "auto").strip().lower() or "auto"
    director = create_director(workspace, director_type, config)
    if not director.is_available():
        return False, "Director adapter unavailable", [], {"result_path": result_path, "log_path": log_path}

    director_task = DirectorTask(
        task_id=contract.task_id,
        goal=contract.goal or contract.title,
        target_files=_normalize_list(contract.payload.get("target_files")),
        acceptance_criteria=_normalize_list(contract.payload.get("acceptance_criteria")),
        constraints=_normalize_list(contract.payload.get("constraints")),
        context={"workspace": workspace, "run_id": run_id, "task": contract.to_dict()},
        scope_paths=_normalize_list(contract.payload.get("scope_paths")),
        scope_mode=str(contract.payload.get("scope_mode") or "module").strip() or "module",
    )
    result = director.execute(director_task)
    changed_files = _normalize_list(getattr(result, "changed_files", []))
    metadata = _normalize_dict(getattr(result, "metadata", {}))
    metadata.update({"result_path": result_path, "log_path": log_path})
    return bool(result.success), str(result.error or "").strip(), changed_files, metadata


def _is_no_director_mode(payload: dict[str, Any]) -> bool:
    director_config = _normalize_dict((payload or {}).get("director_config"))
    return str(director_config.get("type") or "").strip().lower() == "none"


@register_activity("get_ready_tasks")
@activity.defn(name="get_ready_tasks")
async def get_ready_tasks(workflow_input: DirectorWorkflowInput) -> dict[str, Any]:
    """Return tasks already selected by the PM contract."""
    tasks = [task.to_dict() for task in workflow_input.tasks]
    return ActivityExecutionResult(
        success=True,
        summary="Resolved Director-ready tasks from PM workflow payload",
        payload={"tasks": tasks, "task_count": len(tasks)},
    ).to_dict()


@register_activity("claim_task")
@activity.defn(name="claim_task")
async def claim_task(task: TaskContract | dict[str, Any]) -> dict[str, Any]:
    """Perform lightweight task claim validation before execution."""
    contract = task if isinstance(task, TaskContract) else TaskContract.from_mapping(task)
    success = bool(contract.task_id and contract.title)
    return ActivityExecutionResult(
        success=success,
        summary="Task claimed" if success else "Task claim rejected",
        payload={"task_id": contract.task_id, "title": contract.title},
        errors=[] if success else ["invalid_task_contract"],
    ).to_dict()


@register_activity("execute_task_phase")
@activity.defn(name="execute_task_phase")
async def execute_task_phase(payload: dict[str, Any]) -> dict[str, Any]:
    """Execute a real 4-phase Director step using the legacy Director adapter."""
    phase = str((payload or {}).get("phase") or "").strip() or "unknown"
    task_id = str((payload or {}).get("task_id") or "").strip()
    phase_enum = _phase_from_name(phase)
    contract = TaskContract.from_mapping((payload or {}).get("task"))
    if phase_enum is None or not contract.task_id:
        return ActivityExecutionResult(
            success=False,
            summary=f"Unsupported Director phase `{phase}`",
            payload={"phase": phase, "task_id": task_id},
            errors=["unsupported_director_phase"],
        ).to_dict()

    context = _build_context(payload or {}, contract)
    executor = PhaseExecutor(
        workspace=context.workspace,
        policy=Policy(),
        snapshot_enabled=False,
    )

    if phase_enum == TaskPhase.PLANNING:
        result = PhaseResult(
            success=True,
            phase=TaskPhase.PLANNING,
            message="Planning context prepared from PM task contract",
            next_phase=TaskPhase.VALIDATION,
        )
        return ActivityExecutionResult(
            success=True,
            summary=result.message,
            payload=_result_payload(result, context),
            step_title="Phase planning completed",
            step_detail="Planning context prepared from PM task contract",
        ).to_dict()

    if phase_enum == TaskPhase.VALIDATION:
        result = executor.execute_phase(phase_enum, context)
        step_title = f"Phase validation {'completed' if result.success else 'failed'}"
        step_detail = str(result.message or "")[:200]
        return ActivityExecutionResult(
            success=bool(result.success),
            summary=result.message,
            payload=_result_payload(result, context),
            errors=[] if result.success else [str(result.error_code or "validation_failed")],
            step_title=step_title,
            step_detail=step_detail,
        ).to_dict()

    if phase_enum == TaskPhase.EXECUTION:
        success, error_text, changed_files, _metadata = _run_director_execution(
            workspace=context.workspace,
            run_id=str((payload or {}).get("run_id") or "").strip(),
            contract=contract,
            director_config=_normalize_dict((payload or {}).get("director_config")),
            runtime_metadata=_normalize_dict((payload or {}).get("runtime_metadata")),
        )
        if success:
            context.changed_files = list(changed_files)
            result = PhaseResult(
                success=True,
                phase=TaskPhase.EXECUTION,
                message="Director implementation step completed",
                context_updates={"changed_files": list(changed_files)},
                next_phase=TaskPhase.VERIFICATION,
            )
            step_title = "Phase execution completed"
            step_detail = f"Director implementation completed, {len(changed_files)} files changed"
            return ActivityExecutionResult(
                success=True,
                summary=result.message,
                payload=_result_payload(result, context, changed_files=changed_files),
                step_title=step_title,
                step_detail=step_detail,
                changed_files=changed_files,
            ).to_dict()
        result = PhaseResult(
            success=False,
            phase=TaskPhase.EXECUTION,
            message=error_text or "Director execution failed",
            error_code="DIRECTOR_EXECUTION_FAILED",
        )
        step_title = "Phase execution failed"
        step_detail = str(error_text or "")[:200]
        return ActivityExecutionResult(
            success=False,
            summary=result.message,
            payload=_result_payload(result, context, changed_files=changed_files),
            errors=[result.message],
            error_code=result.error_code,  # 传递错误码
            step_title=step_title,
            step_detail=step_detail,
            changed_files=changed_files,
        ).to_dict()

    if phase_enum == TaskPhase.VERIFICATION:
        if _is_no_director_mode(payload or {}):
            result = PhaseResult(
                success=True,
                phase=TaskPhase.VERIFICATION,
                message="Verification skipped because no-director mode does not modify files",
                next_phase=TaskPhase.COMPLETED,
            )
            verification_payload = _result_payload(
                result,
                context,
                changed_files=_normalize_list(context.changed_files),
            )
            verification_payload["verification_skipped"] = True
            verification_payload["verification_result"] = {
                "build_round": int(context.build_round),
                "stall_count": int(context.stall_count),
                "mode": "no_director",
            }
            return ActivityExecutionResult(
                success=True,
                summary=result.message,
                payload=verification_payload,
                step_title="Phase verification skipped",
                step_detail="Verification skipped because no-director mode does not modify files",
            ).to_dict()
        result = executor.execute_phase(phase_enum, context)
        verification_payload = _result_payload(
            result,
            context,
            changed_files=_normalize_list(context.changed_files),
        )
        verification_payload["verification_result"] = {
            "build_round": int(context.build_round),
            "stall_count": int(context.stall_count),
        }
        step_title = f"Phase verification {'completed' if result.success else 'failed'}"
        step_detail = str(result.message or "")[:200]
        return ActivityExecutionResult(
            success=bool(result.success),
            summary=result.message,
            payload=verification_payload,
            errors=[] if result.success else [str(result.error_code or "verification_failed")],
            step_title=step_title,
            step_detail=step_detail,
        ).to_dict()

    return ActivityExecutionResult(
        success=True,
        summary="Report phase acknowledged",
        payload={"phase": phase, "task_id": contract.task_id, "context": _serialize_context(context)},
        step_title="Phase report completed",
        step_detail="Report phase acknowledged",
    ).to_dict()


@register_activity("complete_task")
@activity.defn(name="complete_task")
async def complete_task(task: TaskContract | dict[str, Any]) -> dict[str, Any]:
    """Return a completion marker for successful Director child workflows."""
    contract = task if isinstance(task, TaskContract) else TaskContract.from_mapping(task)
    return ActivityExecutionResult(
        success=bool(contract.task_id),
        summary="Task completion recorded" if contract.task_id else "Task completion rejected",
        payload={"task_id": contract.task_id},
        errors=[] if contract.task_id else ["missing_task_id"],
    ).to_dict()
