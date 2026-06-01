"""Director-related Workflow activities."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from polaris.cells.orchestration.workflow_runtime.internal.models import DirectorWorkflowInput, TaskContract
from polaris.cells.orchestration.workflow_runtime.internal.workflow_client import get_activity_api
from polaris.domain.entities.policy import Policy
from polaris.domain.state_machine import PhaseContext, PhaseExecutor, PhaseResult, TaskPhase
from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name
from polaris.kernelone.fs.text_ops import write_json_atomic

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
_DIRECTOR_PROCESS_TIMEOUT_MAX_SECONDS = 3600
_DIRECTOR_TASK_TIMEOUT_MAX_SECONDS = 3570


def _normalize_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _normalize_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _coerce_timeout_seconds(value: Any, *, default: int, maximum: int) -> int:
    try:
        timeout = int(value) if value is not None else int(default)
    except (TypeError, ValueError):
        timeout = int(default)
    if timeout <= 0:
        timeout = int(default)
    return max(30, min(timeout, maximum))


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


def _resolve_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "src" / "backend" / "polaris" / "delivery" / "cli" / "loop-director.py").is_file():
            return parent
    return current.parent


def _task_artifact_paths(
    *,
    workspace: str,
    run_id: str,
    task_id: str,
    runtime_metadata: dict[str, Any],
) -> tuple[str, str]:
    cache_root = str(runtime_metadata.get("cache_root_full") or "").strip()
    if cache_root:
        task_root = os.path.join(
            cache_root,
            "workflow",
            run_id or "adhoc",
            task_id or "task",
        )
    else:
        metadata_dir = get_workspace_metadata_dir_name()
        task_root = os.path.join(
            workspace,
            metadata_dir,
            "runtime",
            "workflow",
            run_id or "adhoc",
            task_id or "task",
        )
    os.makedirs(task_root, exist_ok=True)
    return os.path.join(task_root, "director.result.json"), os.path.join(task_root, "director.log")


def _read_json_object(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _terminal_result_payload(
    existing: dict[str, Any],
    *,
    task_id: str,
    status: str,
    summary: str,
    errors: list[str],
    completed_phases: list[str],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    status_token = str(status or "").strip().lower()
    completed = status_token in {"completed", "success", "passed", "succeeded"}
    blocked = status_token in {"blocked", "dependency_blocked"}
    result_status = "success" if completed else "blocked" if blocked else "failed"
    context_raw = metadata.get("context")
    context: dict[str, Any] = context_raw if isinstance(context_raw, dict) else {}
    verification_raw = context.get("verification_result")
    if not isinstance(verification_raw, dict):
        verification_raw = metadata.get("verification_result")
    verification_result = dict(verification_raw) if isinstance(verification_raw, dict) else {}
    payload = dict(existing)
    payload.update(
        {
            "schema_version": max(int(payload.get("schema_version") or 1), 1),
            "task_id": task_id,
            "status": result_status,
            "success": completed,
            "acceptance": completed,
            "qa_verdict": "PASS" if completed else "FAIL",
            "workflow_terminal": True,
            "workflow_child_status": "completed" if completed else "blocked" if blocked else "failed",
            "workflow_completed_phases": list(completed_phases),
            "workflow_metadata": metadata,
            "verification_result": verification_result,
            "summary": summary,
            "result_summary": summary,
            "errors": list(errors),
            "error": "" if completed else (errors[0] if errors else summary or "director_task_failed"),
        }
    )
    return payload


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
    phase_context: PhaseContext,
    director_config: dict[str, Any],
    runtime_metadata: dict[str, Any],
) -> tuple[bool, str, list[str], dict[str, Any]]:
    from polaris.delivery.cli.pm.director_interface_core import DirectorTask, create_director

    project_root = _resolve_repo_root()
    result_path, log_path = _task_artifact_paths(
        workspace=workspace,
        run_id=run_id,
        task_id=contract.task_id,
        runtime_metadata=runtime_metadata,
    )

    process_timeout = _coerce_timeout_seconds(
        director_config.get("timeout"),
        default=600,
        maximum=_DIRECTOR_PROCESS_TIMEOUT_MAX_SECONDS,
    )
    task_timeout = _coerce_timeout_seconds(
        director_config.get("task_timeout") or director_config.get("task_timeout_seconds"),
        default=max(process_timeout - 30, 30),
        maximum=_DIRECTOR_TASK_TIMEOUT_MAX_SECONDS,
    )
    timeout = max(process_timeout, min(task_timeout + 30, _DIRECTOR_PROCESS_TIMEOUT_MAX_SECONDS))

    config = {
        "script": str(director_config.get("script") or "src/backend/polaris/delivery/cli/loop-director.py"),
        "timeout": timeout,
        "task_timeout": task_timeout,
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
        context={
            "workspace": workspace,
            "run_id": run_id,
            "task": contract.to_dict(),
            "phase_context": _serialize_context(phase_context),
            "previous_verification_result": dict(phase_context.verification_result),
        },
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
            phase_context=context,
            director_config=_normalize_dict((payload or {}).get("director_config")),
            runtime_metadata=_normalize_dict((payload or {}).get("runtime_metadata")),
        )
        if success:
            context.changed_files = list(changed_files)
            context.metadata["director_execution"] = dict(_metadata)
            result = PhaseResult(
                success=True,
                phase=TaskPhase.EXECUTION,
                message="Director implementation step completed",
                context_updates={
                    "changed_files": list(changed_files),
                    "metadata": dict(context.metadata),
                },
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
            **dict(context.verification_result),
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


@register_activity("record_director_task_terminal_result")
@activity.defn(name="record_director_task_terminal_result")
async def record_director_task_terminal_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist the workflow-child terminal result over the legacy task artifact."""
    contract = TaskContract.from_mapping((payload or {}).get("task"))
    workspace = str((payload or {}).get("workspace") or "").strip()
    run_id = str((payload or {}).get("run_id") or "").strip()
    runtime_metadata = _normalize_dict((payload or {}).get("runtime_metadata"))
    if not contract.task_id or not workspace:
        return ActivityExecutionResult(
            success=False,
            summary="Cannot record Director task terminal result without task_id and workspace",
            payload={"task_id": contract.task_id, "workspace": workspace},
            errors=["missing_terminal_result_identity"],
        ).to_dict()

    result_path, _log_path = _task_artifact_paths(
        workspace=workspace,
        run_id=run_id,
        task_id=contract.task_id,
        runtime_metadata=runtime_metadata,
    )
    existing = _read_json_object(result_path)
    status = str((payload or {}).get("status") or "").strip()
    summary = str((payload or {}).get("summary") or "").strip()
    errors = _normalize_list((payload or {}).get("errors"))
    completed_phases = _normalize_list((payload or {}).get("completed_phases"))
    metadata = _normalize_dict((payload or {}).get("metadata"))
    terminal_payload = _terminal_result_payload(
        existing,
        task_id=contract.task_id,
        status=status,
        summary=summary,
        errors=errors,
        completed_phases=completed_phases,
        metadata=metadata,
    )
    write_json_atomic(result_path, terminal_payload)
    return ActivityExecutionResult(
        success=True,
        summary="Director task terminal result recorded",
        payload={
            "task_id": contract.task_id,
            "result_path": result_path,
            "status": terminal_payload.get("status"),
            "workflow_child_status": terminal_payload.get("workflow_child_status"),
        },
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
