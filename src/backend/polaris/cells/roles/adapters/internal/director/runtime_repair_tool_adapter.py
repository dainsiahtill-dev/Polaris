"""Pure adapter bridge from Director repair planning to deferred effects.

The adapter may discover and plan a deterministic repair, but it never owns a
physical tool executor.  Physical mutation is deferred to ``roles.kernel`` so
the exact TaskRuntime attempt, Job Token, TaskBoundary, lifecycle and receipt
facts can be validated at the single execution boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from polaris.cells.director.runtime.public import (
    PlanDirectorRepairCommandV1,
    RepairAdvisoryV1,
    plan_director_repair,
)
from polaris.cells.roles.kernel.public import (
    DeferredDirectorCommandRequestV1,
    DeferredDirectorRepairRequestV1,
    create_deferred_director_command_request,
    create_deferred_director_repair_request,
)
from polaris.cells.runtime.task_runtime.public import TaskRuntimeExecutionAttemptIdentityV1


def _failure(
    *,
    source_tool: str,
    error_code: str,
    error_message: str,
    planning: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "tool": "director_repair_kernel",
        "tool_name": "director_repair_kernel",
        "success": False,
        "result": {
            "ok": False,
            "source_tool": source_tool,
            "error_code": error_code,
            "error_message": error_message,
            "repair_applied": False,
            "repair_kernel": {
                "owner_cell": "director.runtime",
                "planning": dict(planning or {}),
                "execution_skipped": True,
                "execution_skip_reason": error_code,
                "physical_executor_owned": False,
            },
        },
    }


def _deferred_result(
    *,
    source_tool: str,
    request: DeferredDirectorRepairRequestV1,
    planning: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "tool": "deferred_director_repair",
        "tool_name": "deferred_director_repair",
        "success": True,
        "result": {
            "ok": True,
            "status": "deferred_repair_effects_pending",
            "repair_applied": False,
            "source_tool": source_tool,
            "request_id": request.request_id,
            "request_hash": request.request_hash,
            "plan_hash": request.plan.plan_hash,
            "allowed_paths": list(request.allowed_paths),
            "deferred_request": request,
            "repair_kernel": {
                "owner_cell": "director.runtime",
                "planning": dict(planning),
                "execution_deferred": True,
                "execution_authority": "roles.kernel",
                "physical_executor_owned": False,
            },
        },
    }


def defer_director_command_with_director_tools(
    *,
    workspace_path: Path,
    task_id: str,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None,
    command: str,
    timeout_seconds: int = 60,
    purpose: str = "verification",
    cwd: str = ".",
) -> dict[str, Any]:
    """Return one typed command request; never execute the command locally."""

    if type(execution_attempt) is not TaskRuntimeExecutionAttemptIdentityV1:
        return _failure(
            source_tool="director_deferred_command",
            error_code="deo_deferred_command_attempt_required",
            error_message="a canonical TaskRuntime execution attempt is required before command effects can be deferred",
        )
    typed_execution_attempt = cast(TaskRuntimeExecutionAttemptIdentityV1, execution_attempt)
    try:
        requested_task_id = str(task_id or "").strip()
        bound_external_task_id = typed_execution_attempt.external_task_id
        bound_private_task_id = str(typed_execution_attempt.task_id)
        if requested_task_id not in {bound_external_task_id, bound_private_task_id}:
            raise ValueError(
                "task_id must match the execution attempt's external task id or exact private TaskRuntime row id"
            )
        request: DeferredDirectorCommandRequestV1 = create_deferred_director_command_request(
            workspace=workspace_path.resolve().as_posix(),
            task_id=bound_external_task_id,
            execution_attempt=typed_execution_attempt,
            command=command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            purpose=purpose,
        )
    except (TypeError, ValueError) as exc:
        return _failure(
            source_tool="director_deferred_command",
            error_code="deo_deferred_command_request_invalid",
            error_message=str(exc),
        )
    return {
        "tool": "deferred_director_command",
        "tool_name": "deferred_director_command",
        "success": True,
        "result": {
            "ok": True,
            "status": "deferred_command_effect_pending",
            "request_id": request.request_id,
            "request_hash": request.request_hash,
            "purpose": request.purpose,
            "deferred_request": request,
            "execution_authority": "roles.kernel",
            "physical_executor_owned": False,
        },
    }


def run_runtime_repair_with_director_tools(
    adapter: Any,
    *,
    workspace_path: Path,
    task_id: str,
    source_tool: str,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str] = (),
    artifact_quality_issues: Sequence[Mapping[str, Any]] = (),
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisoryV1] = (),
    use_editor: bool = True,
    revalidator: Callable[[Any], Any] | None = None,
    convergence_verifier: Callable[[Any], Any] | None = None,
    max_rounds: int = 1,
) -> list[dict[str, Any]]:
    """Plan exactly one repair round and return one typed deferred request.

    ``adapter``, ``use_editor`` and ``revalidator`` remain accepted while the
    legacy callers migrate, but they grant no execution authority and are not
    invoked.  Multi-round convergence requires the first round's lifecycle,
    receipt and revalidation facts, so it is rejected at this pure boundary.
    """

    del adapter, use_editor, revalidator, convergence_verifier
    if not base_files:
        return []
    if max_rounds != 1:
        return [
            _failure(
                source_tool=source_tool,
                error_code="deo_multi_round_repair_requires_receipt_close",
                error_message="multi-round repair requires receipt-backed closure of the previous round",
            )
        ]
    if type(execution_attempt) is not TaskRuntimeExecutionAttemptIdentityV1:
        return [
            _failure(
                source_tool=source_tool,
                error_code="deo_deferred_repair_attempt_required",
                error_message="a canonical TaskRuntime execution attempt is required before repair effects can be deferred",
            )
        ]

    typed_execution_attempt = cast(TaskRuntimeExecutionAttemptIdentityV1, execution_attempt)
    requested_task_id = str(task_id or "").strip()
    bound_external_task_id = str(typed_execution_attempt.external_task_id or "").strip()
    bound_private_task_id = str(typed_execution_attempt.task_id)
    # Callers may pass board/pm/private row ids; DeferredDirectorRepairRequestV1
    # binds only to external_task_id. Never raise into Director runtime — return
    # a structured failure (R78: numeric "1" vs external form killed tools_executed=0).
    # Also accept TASK-N / task-N aliases of digit-only private or external ids.
    accepted_task_ids = {bound_external_task_id, bound_private_task_id}
    for candidate in (bound_external_task_id, bound_private_task_id):
        token = str(candidate or "").strip()
        if not token:
            continue
        accepted_task_ids.add(token)
        upper = token.upper()
        if upper.startswith("TASK-"):
            accepted_task_ids.add(token[5:])
            accepted_task_ids.add(upper)
            accepted_task_ids.add(f"TASK-{token[5:]}")
        elif token.isdigit():
            accepted_task_ids.add(f"TASK-{token}")
            accepted_task_ids.add(f"task-{token}")
    if requested_task_id not in accepted_task_ids:
        return [
            _failure(
                source_tool=source_tool,
                error_code="deo_deferred_repair_task_mismatch",
                error_message=(
                    "task_id must match the execution attempt's external task id or exact private TaskRuntime row id"
                ),
            )
        ]

    command = PlanDirectorRepairCommandV1(
        source_tool=source_tool,
        artifact_quality_errors=tuple(str(item) for item in artifact_quality_errors),
        artifact_quality_issues=tuple(dict(item) for item in artifact_quality_issues),
        base_files=dict(base_files),
        deterministic_only=True,
        advisor_notes=tuple(advisor_notes),
    )
    planning = plan_director_repair(command)
    planning_payload = planning.to_dict()
    if not planning.ok:
        if planning.error_code == "repair_not_planned" or (not planning.planned and not planning.error_code):
            return []
        return [
            _failure(
                source_tool=source_tool,
                error_code=str(planning.error_code or "director_repair_planning_failed"),
                error_message=str(planning.error_message or "Director repair planning failed"),
                planning=planning_payload,
            )
        ]
    if not planning.planned or planning.effect_plan is None:
        return []

    try:
        request = create_deferred_director_repair_request(
            workspace=workspace_path.resolve().as_posix(),
            task_id=bound_external_task_id,
            execution_attempt=typed_execution_attempt,
            planning_command=command,
            planning_result=planning,
            allowed_paths=tuple(allowed_paths or base_files.keys()),
        )
    except (TypeError, ValueError) as exc:
        return [
            _failure(
                source_tool=source_tool,
                error_code="deo_deferred_repair_request_invalid",
                error_message=str(exc),
                planning=planning_payload,
            )
        ]
    return [_deferred_result(source_tool=source_tool, request=request, planning=planning_payload)]


__all__ = ["defer_director_command_with_director_tools", "run_runtime_repair_with_director_tools"]
