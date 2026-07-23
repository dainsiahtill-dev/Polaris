"""Public construction boundary for deferred Director repair requests."""

from __future__ import annotations

import json
from collections.abc import Sequence

from polaris.cells.director.runtime.public import (
    DirectorRepairPlanningResultV1,
    PlanDirectorRepairCommandV1,
)
from polaris.cells.director.runtime.public.directed_effect_contracts import hash_directed_effect_arguments
from polaris.cells.runtime.task_runtime.public import TaskRuntimeExecutionAttemptIdentityV1

from .directed_effect_contracts import DeferredDirectorCommandRequestV1, DeferredDirectorRepairRequestV1


def create_deferred_director_command_request(
    *,
    workspace: str,
    task_id: str,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
    command: str,
    cwd: str = ".",
    timeout_seconds: int = 60,
    purpose: str = "verification",
) -> DeferredDirectorCommandRequestV1:
    """Bind one adapter-discovered command to an exact TaskRuntime attempt."""

    request_seed = hash_directed_effect_arguments(
        (
            (
                "attempt_record_json",
                json.dumps(
                    execution_attempt.to_record(),
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            ),
            ("command", str(command or "").strip()),
            ("cwd", str(cwd or "").strip().replace("\\", "/")),
            ("domain", "roles_kernel_deferred_director_command_request_id_v1"),
            ("purpose", str(purpose or "").strip()),
            ("task_id", task_id),
            ("timeout_seconds", timeout_seconds),
            ("workspace", workspace),
        )
    )
    return DeferredDirectorCommandRequestV1(
        request_id=f"deferred-command-{request_seed[:24]}",
        workspace=workspace,
        task_id=task_id,
        execution_attempt=execution_attempt,
        command=command,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        purpose=purpose,
    )


def create_deferred_director_repair_request(
    *,
    workspace: str,
    task_id: str,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
    planning_command: PlanDirectorRepairCommandV1,
    planning_result: DirectorRepairPlanningResultV1,
    allowed_paths: Sequence[str],
) -> DeferredDirectorRepairRequestV1:
    """Bind one public Director plan to the exact TaskRuntime attempt.

    This constructor is pure. It does not synthesize tool calls, execute an
    effect, or own a mutation port.
    """

    if type(planning_command) is not PlanDirectorRepairCommandV1:
        raise TypeError("planning_command must be exactly PlanDirectorRepairCommandV1")
    if type(planning_result) is not DirectorRepairPlanningResultV1:
        raise TypeError("planning_result must be exactly DirectorRepairPlanningResultV1")
    if planning_result.effect_plan is None:
        raise ValueError("planning_result must contain an effect_plan")
    from polaris.cells.roles.kernel.internal.deferred_repair_effects import (
        build_deferred_repair_planning_payload,
    )

    planning_payload_json = build_deferred_repair_planning_payload(planning_command)
    canonical_allowed_paths = tuple(sorted({str(path or "").strip().replace("\\", "/") for path in allowed_paths}))
    request_seed = hash_directed_effect_arguments(
        (
            ("allowed_paths", canonical_allowed_paths),
            (
                "attempt_record_json",
                json.dumps(
                    execution_attempt.to_record(),
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            ),
            ("domain", "roles_kernel_deferred_director_repair_request_id_v1"),
            ("plan_hash", planning_result.effect_plan.plan_hash),
            ("planning_payload_json", planning_payload_json),
            ("task_id", task_id),
            ("workspace", workspace),
        )
    )
    return DeferredDirectorRepairRequestV1(
        request_id=f"deferred-repair-{request_seed[:24]}",
        workspace=workspace,
        task_id=task_id,
        execution_attempt=execution_attempt,
        plan=planning_result.effect_plan,
        planning_payload_json=planning_payload_json,
        allowed_paths=canonical_allowed_paths,
    )


__all__ = ["create_deferred_director_command_request", "create_deferred_director_repair_request"]
