"""Factory HTTP router helpers — payload builders, cores, bench session utils.

Extracted from factory.py so route registration stays thin. External callers that
historically imported private helpers from factory.py continue to re-export them
from polaris.delivery.http.routers.factory.
"""

from __future__ import annotations

import inspect
import json
import logging
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeAlias, cast

from polaris.cells.factory.pipeline.public import (
    FactoryRun,
    FactoryRunService,
    FactoryRunStatus as ServiceRunStatus,
)
from polaris.cells.factory.pipeline.public.types import (
    FactoryRunStatus as FactoryRunStatusContract,
    FactoryStartRequest,
    FailureInfo,
    FailureType,
    GateResult,
    GateStatus,
    RoleStatus,
    RunLifecycleStatus,
    RunPhase,
)
from polaris.delivery.http.routers._shared import (
    StructuredHTTPException,
)

if TYPE_CHECKING:
    from polaris.cells.runtime.state_owner.public.service import AppState

logger = logging.getLogger("polaris.delivery.http.routers.factory")

_STATUS_METADATA_VALUE_MAX_BYTES = 64 * 1024
_STATUS_METADATA_TOTAL_MAX_BYTES = 256 * 1024

_TASK_IDENTIFIER_KEYS = (
    "task_id",
    "pm_task_id",
    "external_task_id",
    "source_task_id",
    "taskId",
    "id",
)

STAGE_TO_PHASE: dict[str, RunPhase] = {
    "docs_generation": RunPhase.ARCHITECT,
    "pm_planning": RunPhase.PLANNING,
    "chief_engineer_review": RunPhase.PLANNING,
    "director_dispatch": RunPhase.IMPLEMENTATION,
    "quality_gate": RunPhase.QA_GATE,
}

STAGE_TO_ROLE: dict[str, str] = {
    "docs_generation": "architect",
    "pm_planning": "pm",
    "chief_engineer_review": "chief_engineer",
    "director_dispatch": "director",
    "quality_gate": "qa",
}

PHASE_TO_RETRY_STAGES: dict[RunPhase, tuple[str, ...]] = {
    RunPhase.ARCHITECT: ("docs_generation",),
    RunPhase.PLANNING: ("pm_planning", "chief_engineer_review"),
    RunPhase.IMPLEMENTATION: ("director_dispatch",),
    RunPhase.QA_GATE: ("quality_gate",),
}

SERVICE_STATUS_TO_CONTRACT: dict[ServiceRunStatus, RunLifecycleStatus] = {
    ServiceRunStatus.PENDING: RunLifecycleStatus.PENDING,
    ServiceRunStatus.RUNNING: RunLifecycleStatus.RUNNING,
    ServiceRunStatus.PAUSED: RunLifecycleStatus.PAUSED,
    ServiceRunStatus.COMPLETED: RunLifecycleStatus.COMPLETED,
    ServiceRunStatus.FAILED: RunLifecycleStatus.FAILED,
    ServiceRunStatus.RECOVERING: RunLifecycleStatus.RECOVERING,
    ServiceRunStatus.CANCELLED: RunLifecycleStatus.CANCELLED,
}

_DEFAULT_LOOP_MAX_CYCLES = 12
_DEFAULT_LOOP_STALL_THRESHOLD = 2
_DEFAULT_QUALITY_REWORK_MAX_CYCLES = 3
_DEFAULT_CHIEF_ENGINEER_LOCAL_REWORK_MAX_CYCLES = 2
_DEFAULT_DIRECTOR_LOCAL_REWORK_MAX_CYCLES = 2
_FACTORY_RUN_DEADLINE_METADATA_KEYS = (
    "factory_run_deadline_epoch_seconds",
    "factory_deadline_epoch_seconds",
    "deadline_epoch_seconds",
)
_RETRY_START_POLICY_AFTER_CHECKPOINT = "after_checkpoint"
FactoryStartFrom: TypeAlias = Literal["auto", "architect", "pm", "director_resume"]
StageSequenceStatus: TypeAlias = Literal[
    "completed",
    "cancelled",
    "chief_engineer_rework_requested",
    "director_rework_requested",
    "quality_rework_requested",
]
_TASK_BOUNDARY_REWORK_REASON = "task_boundary_interface_discrepancy_required"
_TASK_BOUNDARY_OWNER_REWORK_REASON = "task_boundary_owner_task_retry_required"
_PLAN_PROBE_UNPLANNABLE_STATUS = "coverage_matched_but_unplannable"


def _stage_local_rework_is_authorized(result: Any, *, expected_reason: str) -> bool:
    """Accept only a durable workflow action committed into TaskRuntime.

    A router flag or a failed stage is not recovery authority.  FactoryRunService
    emits this projection only after it validates the canonical TaskMarket receipt
    and the owner task's binding to the active Factory run.  Without it, the
    terminal stage has already drained its lease and must not be replayed.
    """

    metadata = getattr(result, "metadata", None)
    metadata_map = metadata if isinstance(metadata, Mapping) else {}
    deferred = metadata_map.get("factory_terminal_drain_deferred")
    deferred_map = deferred if isinstance(deferred, Mapping) else {}
    action_id = str(deferred_map.get("action_id") or "").strip()
    workflow_authorized = (
        str(deferred_map.get("schema_version") or "").strip() == "factory.terminal-drain-deferred.v2"
        and str(deferred_map.get("decision_owner") or "").strip() == "orchestration.workflow_orchestration"
        and re.fullmatch(r"[0-9a-f]{64}", action_id) is not None
        and bool(str(deferred_map.get("diagnostic_id") or "").strip())
    )
    return str(deferred_map.get("reason") or "").strip() == expected_reason and workflow_authorized


def _get_service(workspace: str) -> FactoryRunService:
    """Return the lifespan-owned Factory service for the bound workspace.

    Physical-attempt coordinators are deliberately process-local.  Creating a
    second ``FactoryRunService`` in the HTTP request path therefore creates a
    second, empty coordinator registry and cannot be submitted to the
    lifespan-owned driver.  Reuse the bound instance when its canonical
    workspace matches; unbound/test/other-workspace reads retain the detached
    construction path.
    """

    resolved_workspace = Path(workspace).resolve()
    try:
        from polaris.bootstrap.factory_run_driver_runtime import get_factory_run_driver_runtime

        runtime = get_factory_run_driver_runtime()
    except RuntimeError:
        runtime = None
    if runtime is not None:
        bound_service = runtime.service
        bound_workspace = Path(str(getattr(bound_service, "workspace", ""))).resolve()
        if bound_workspace == resolved_workspace:
            if not isinstance(bound_service, FactoryRunService):
                raise RuntimeError("factory_run_driver_service_binding_invalid")
            return bound_service
    return FactoryRunService(workspace=resolved_workspace)


def _metadata_float(metadata: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        raw = metadata.get(key)
        if raw is None:
            continue
        try:
            value = float(str(raw).strip())
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _append_factory_deadline_context(context: dict[str, Any], metadata: dict[str, Any]) -> None:
    deadline_epoch = _metadata_float(metadata, *_FACTORY_RUN_DEADLINE_METADATA_KEYS)
    if deadline_epoch is None:
        return
    now_epoch = datetime.now(timezone.utc).timestamp()
    remaining_seconds = max(0.0, deadline_epoch - now_epoch)
    context["factory_run_deadline_epoch_seconds"] = deadline_epoch
    context["factory_run_deadline_remaining_seconds"] = remaining_seconds
    timeout_seconds = _metadata_float(metadata, "factory_run_timeout_seconds", "factory_timeout_seconds")
    if timeout_seconds is not None:
        context["factory_run_timeout_seconds"] = timeout_seconds
    source = str(metadata.get("factory_run_deadline_source") or "").strip()
    if source:
        context["factory_run_deadline_source"] = source


def _resolve_workspace(state: AppState, workspace: str | None = None) -> str:
    requested = str(workspace or getattr(state.settings, "workspace", "") or "").strip()
    if not requested:
        raise StructuredHTTPException(
            status_code=400, code="WORKSPACE_NOT_CONFIGURED", message="workspace not configured"
        )
    return str(Path(requested).resolve())


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _calculate_progress(run: FactoryRun) -> float:
    total_stages = len(run.config.stages) if run.config.stages else 1
    completed_stages = len(run.stages_completed)
    if run.status == ServiceRunStatus.COMPLETED:
        return 100.0
    if total_stages <= 0:
        return 0.0
    return round((completed_stages / total_stages) * 100, 2)


def _resolve_phase(run: FactoryRun) -> RunPhase:
    if run.status == ServiceRunStatus.COMPLETED:
        return RunPhase.COMPLETED
    if run.status == ServiceRunStatus.FAILED:
        return RunPhase.FAILED
    if run.status == ServiceRunStatus.CANCELLED:
        return RunPhase.CANCELLED

    current_stage = str(run.metadata.get("current_stage") or "").strip()
    if current_stage:
        return STAGE_TO_PHASE.get(current_stage, RunPhase.PENDING)

    last_successful_stage = str(run.metadata.get("last_successful_stage") or run.recovery_point or "").strip()
    if last_successful_stage:
        return STAGE_TO_PHASE.get(last_successful_stage, RunPhase.PENDING)

    return RunPhase.PENDING


def _resolve_retry_stage(run: FactoryRun, target_phase: RunPhase | None) -> str:
    if target_phase is None:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_REQUEST",
            message="target_phase is required for retry_phase",
        )

    configured_stages = [str(stage).strip() for stage in run.config.stages if str(stage).strip()]
    # A public phase can contain multiple executable stages.  Planning owns
    # both PM and Chief Engineer, so blindly choosing the first configured
    # stage restarts PM after a CE-only failure and discards a valid PM
    # contract.  Prefer the concrete failed/current stage when it belongs to
    # the requested phase; retain the historical phase default only when the
    # run has no stage-local failure evidence.
    failed_stage_candidates = (
        run.metadata.get("last_failed_stage"),
        run.metadata.get("current_stage"),
        *(reversed(run.stages_failed)),
    )
    for raw_stage in failed_stage_candidates:
        failed_stage = str(raw_stage or "").strip()
        if (
            failed_stage in configured_stages
            and STAGE_TO_PHASE.get(failed_stage) == target_phase
        ):
            return failed_stage
    for candidate in PHASE_TO_RETRY_STAGES.get(target_phase, ()):
        if candidate in configured_stages:
            return candidate

    raise StructuredHTTPException(
        status_code=400,
        code="INVALID_REQUEST",
        message=f"Factory phase '{target_phase.value}' cannot be retried for this run",
        details={
            "target_phase": target_phase.value,
            "configured_stages": configured_stages,
            "supported_phases": [phase.value for phase in PHASE_TO_RETRY_STAGES],
        },
    )


async def _save_service_run(service: FactoryRunService, run: FactoryRun) -> None:
    save_run = getattr(getattr(service, "store", None), "save_run", None)
    if not callable(save_run):
        return
    result = save_run(run)
    if inspect.isawaitable(result):
        await result


def _infer_start_from_stages(stages: list[str]) -> str:
    first_stage = next((stage for stage in stages if stage), "")
    if first_stage == "docs_generation":
        return "architect"
    if first_stage == "director_dispatch":
        return "director_resume"
    return "pm"


def _coerce_director_iterations(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(parsed, 10))


def _coerce_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return None


def _store_start_request_metadata(run: FactoryRun, payload: FactoryStartRequest, start_from: str) -> None:
    start_payload = payload.model_dump(mode="json")
    start_payload["start_from"] = start_from
    run.metadata["factory_start_request"] = start_payload


def _build_retry_start_request(run: FactoryRun, workspace: str) -> FactoryStartRequest:
    configured_stages = [str(stage).strip() for stage in run.config.stages if str(stage).strip()]
    raw_start_payload = run.metadata.get("factory_start_request")
    start_payload = dict(raw_start_payload) if isinstance(raw_start_payload, dict) else {}

    stored_start_from = str(start_payload.get("start_from") or "").strip().lower()
    start_from = stored_start_from if stored_start_from in {"auto", "architect", "pm", "director_resume"} else ""
    if not start_from:
        start_from = _infer_start_from_stages(configured_stages)

    # A same-run retry at Director is a Director resume even when the original
    # run started at PM. This controls workspace semantics, not event-chain
    # identity: restore the committed pre-Director snapshot instead of taking a
    # new snapshot over partially failed output. PM/CE events stay immutable in
    # the same Factory run and are not re-executed.
    retry_execution_stage = str(run.metadata.get("retry_execution_stage") or "").strip()
    if retry_execution_stage == "director_dispatch":
        start_from = "director_resume"

    directive_value = _coerce_optional_string(start_payload.get("directive"))
    if directive_value is None:
        directive_value = _coerce_optional_string(run.config.description)

    return FactoryStartRequest(
        workspace=str(start_payload.get("workspace") or workspace),
        start_from=cast(FactoryStartFrom, start_from),
        directive=directive_value,
        run_director=bool(start_payload.get("run_director", "director_dispatch" in configured_stages)),
        director_iterations=_coerce_director_iterations(start_payload.get("director_iterations", 0)),
        director_workflow_execution_mode=start_payload.get("director_workflow_execution_mode"),
        director_dispatch_driver="task-market",
        loop=bool(start_payload.get("loop", run.metadata.get("loop_requested", False))),
        input_source=_coerce_optional_string(start_payload.get("input_source")),
        persist_workspace=False,
    )


def _execution_stages_for_run(run: FactoryRun, configured_stages: list[str]) -> list[str]:
    stages = [str(stage).strip() for stage in configured_stages if str(stage).strip()]
    if run.status != ServiceRunStatus.RECOVERING:
        return stages

    policy = str(run.metadata.get("retry_start_policy") or "").strip()
    if policy == _RETRY_START_POLICY_AFTER_CHECKPOINT:
        recovery_stage = (
            str(run.recovery_point or "").strip() or str(run.metadata.get("last_successful_stage") or "").strip()
        )
    else:
        recovery_stage = (
            str(run.metadata.get("retry_execution_stage") or "").strip()
            or str(run.recovery_point or "").strip()
            or str(run.metadata.get("current_stage") or "").strip()
        )
    if not recovery_stage or recovery_stage not in stages:
        return stages

    start_index = stages.index(recovery_stage)
    if policy == _RETRY_START_POLICY_AFTER_CHECKPOINT:
        start_index += 1
    return stages[start_index:]


def _build_roles(run: FactoryRun, phase: RunPhase) -> dict[str, RoleStatus]:
    current_stage = str(run.metadata.get("current_stage") or "").strip()
    current_role = STAGE_TO_ROLE.get(current_stage)
    failed_stage = str(run.metadata.get("last_failed_stage") or "").strip()
    failed_role = STAGE_TO_ROLE.get(failed_stage)
    completed_roles = {STAGE_TO_ROLE[stage] for stage in run.stages_completed if stage in STAGE_TO_ROLE}

    roles: dict[str, RoleStatus] = {}
    for role_name in ("pm", "architect", "chief_engineer", "director", "qa"):
        status = "idle"
        progress = 0.0
        detail: str | None = None

        if role_name in completed_roles:
            status = "completed"
            progress = 100.0
        if current_role == role_name and run.status in {ServiceRunStatus.RUNNING, ServiceRunStatus.RECOVERING}:
            status = "running"
            progress = 50.0
        if failed_role == role_name and run.status == ServiceRunStatus.FAILED:
            status = "failed"
            progress = 100.0
            detail = str(((run.metadata.get("failure") or {}) or {}).get("detail") or "").strip() or None
        if run.status == ServiceRunStatus.CANCELLED and current_role == role_name:
            status = "blocked"
            detail = str(run.metadata.get("cancel_reason") or "Run cancelled").strip()

        roles[role_name] = RoleStatus(
            role=role_name,
            status=status,
            detail=detail,
            current_task=current_stage if current_role == role_name else None,
            progress=progress,
        )

    return roles


def _quality_gate_result_from_metadata(run: FactoryRun) -> dict[str, Any]:
    stage_results = run.metadata.get("stage_results")
    if not isinstance(stage_results, dict):
        return {}
    quality_gate = stage_results.get("quality_gate")
    return quality_gate if isinstance(quality_gate, dict) else {}


def _quality_gate_score_from_output(output: Any) -> float | None:
    match = re.search(r"\bqa_score=(\d+(?:\.\d+)?)\b", str(output or ""))
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _build_gates(run: FactoryRun, phase: RunPhase) -> list[GateResult]:
    if "quality_gate" in run.stages_completed:
        quality_result = _quality_gate_result_from_metadata(run)
        score = _quality_gate_score_from_output(quality_result.get("output"))
        raw_artifacts = quality_result.get("artifacts")
        artifacts = raw_artifacts if isinstance(raw_artifacts, list) else ["runtime/qa/report.json"]
        return [
            GateResult(
                gate_name="quality_gate",
                status=GateStatus.PASSED,
                score=score if score is not None else 100.0,
                passed=True,
                message=str(quality_result.get("output") or "Quality gate passed"),
                details={"stage_result": quality_result} if quality_result else {},
                artifacts=[str(item) for item in artifacts],
            )
        ]
    if (
        run.status == ServiceRunStatus.FAILED
        and str(run.metadata.get("last_failed_stage") or "").strip() == "quality_gate"
    ):
        failure_detail = ((run.metadata.get("failure") or {}) or {}).get("detail") or "Quality gate failed"
        return [
            GateResult(
                gate_name="quality_gate",
                status=GateStatus.FAILED,
                score=0.0,
                passed=False,
                message=str(failure_detail),
                details={},
                artifacts=[],
            )
        ]
    if phase in {RunPhase.QA_GATE, RunPhase.HANDOVER, RunPhase.COMPLETED}:
        return [
            GateResult(
                gate_name="quality_gate",
                status=GateStatus.PENDING,
                score=None,
                passed=False,
                message="Quality gate pending",
                details={},
                artifacts=[],
            )
        ]
    return []


def _build_failure(run: FactoryRun, phase: RunPhase) -> FailureInfo | None:
    raw_failure = run.metadata.get("failure")
    if not isinstance(raw_failure, dict):
        return None

    stage = str(raw_failure.get("stage") or run.metadata.get("last_failed_stage") or "").strip()
    stage_results = run.metadata.get("stage_results")
    stage_result = stage_results.get(stage) if isinstance(stage_results, dict) and stage else None
    stage_result_metadata = stage_result.get("metadata") if isinstance(stage_result, dict) else None
    structured = stage_result_metadata if isinstance(stage_result_metadata, dict) else {}
    structured_recoverable = structured.get("recoverable")
    recoverable = (
        structured_recoverable if isinstance(structured_recoverable, bool) else bool(raw_failure.get("recoverable"))
    )
    failure_type = FailureType.TRANSIENT if recoverable else FailureType.DETERMINISTIC
    timestamp = raw_failure.get("timestamp")
    detail = str(raw_failure.get("detail") or "").strip() or "Factory run failed"

    def _stable_field(name: str) -> str | None:
        value = str(raw_failure.get(name) or structured.get(name) or "").strip()
        return value or None

    return FailureInfo(
        failure_type=failure_type,
        code=str(raw_failure.get("code") or "FACTORY_FAILED"),
        detail=detail,
        phase=phase,
        timestamp=_parse_datetime(str(timestamp)) or datetime.now(timezone.utc),
        recoverable=recoverable,
        suggested_action=str(raw_failure.get("suggested_action") or "").strip() or None,
        hops=[],
        stage=stage or None,
        error_code=_stable_field("error_code"),
        failure_class=_stable_field("failure_class"),
        responsible_layer=_stable_field("responsible_layer"),
        root_cause_hint=_stable_field("root_cause_hint"),
    )


def _json_safe_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}

    payload: dict[str, Any] = {}
    projected_bytes = 2
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        if key in {"summary_md", "summary_json"}:
            continue
        try:
            safe_value = json.loads(json.dumps(raw_value, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            continue
        if key == "failure" and isinstance(safe_value, dict):
            safe_value.pop("traceback", None)
        encoded = json.dumps(safe_value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        value_bytes = len(encoded)
        if (
            value_bytes > _STATUS_METADATA_VALUE_MAX_BYTES
            or projected_bytes + value_bytes > _STATUS_METADATA_TOTAL_MAX_BYTES
        ):
            safe_value = {
                "elided": True,
                "json_bytes": value_bytes,
                "reason": "factory_status_metadata_size_limit",
                "durable_evidence": "factory_run_audit_bundle",
            }
            encoded = json.dumps(safe_value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        payload[key] = safe_value
        projected_bytes += len(key.encode("utf-8")) + len(encoded) + 4
    return payload


def _map_service_run_to_contract(run: FactoryRun) -> FactoryRunStatusContract:
    phase = _resolve_phase(run)
    current_stage = str(run.metadata.get("current_stage") or "").strip() or None
    last_successful_stage = str(run.metadata.get("last_successful_stage") or run.recovery_point or "").strip() or None

    return FactoryRunStatusContract(
        run_id=run.id,
        phase=phase,
        status=SERVICE_STATUS_TO_CONTRACT.get(run.status, RunLifecycleStatus.PENDING),
        current_stage=current_stage,
        last_successful_stage=last_successful_stage,
        progress=_calculate_progress(run),
        roles=_build_roles(run, phase),
        gates=_build_gates(run, phase),
        failure=_build_failure(run, phase),
        created_at=_parse_datetime(run.created_at) or datetime.now(timezone.utc),
        started_at=_parse_datetime(run.started_at),
        updated_at=_parse_datetime(run.updated_at),
        completed_at=_parse_datetime(run.completed_at),
        summary_md=str(run.metadata.get("summary_md") or "").strip() or None,
        metadata=_json_safe_metadata(run.metadata),
    )


__all__ = [
    "PHASE_TO_RETRY_STAGES",
    "SERVICE_STATUS_TO_CONTRACT",
    "STAGE_TO_PHASE",
    "STAGE_TO_ROLE",
    "_DEFAULT_CHIEF_ENGINEER_LOCAL_REWORK_MAX_CYCLES",
    "_DEFAULT_DIRECTOR_LOCAL_REWORK_MAX_CYCLES",
    "_DEFAULT_LOOP_MAX_CYCLES",
    "_DEFAULT_LOOP_STALL_THRESHOLD",
    "_DEFAULT_QUALITY_REWORK_MAX_CYCLES",
    "_FACTORY_RUN_DEADLINE_METADATA_KEYS",
    "_PLAN_PROBE_UNPLANNABLE_STATUS",
    "_RETRY_START_POLICY_AFTER_CHECKPOINT",
    "_TASK_BOUNDARY_OWNER_REWORK_REASON",
    "_TASK_BOUNDARY_REWORK_REASON",
    "_TASK_IDENTIFIER_KEYS",
    "FactoryStartFrom",
    "StageSequenceStatus",
    "_append_factory_deadline_context",
    "_build_failure",
    "_build_gates",
    "_build_retry_start_request",
    "_build_roles",
    "_calculate_progress",
    "_coerce_director_iterations",
    "_coerce_optional_string",
    "_execution_stages_for_run",
    "_get_service",
    "_infer_start_from_stages",
    "_json_safe_metadata",
    "_map_service_run_to_contract",
    "_metadata_float",
    "_parse_datetime",
    "_quality_gate_result_from_metadata",
    "_quality_gate_score_from_output",
    "_resolve_phase",
    "_resolve_retry_stage",
    "_resolve_workspace",
    "_save_service_run",
    "_stage_local_rework_is_authorized",
    "_store_start_request_metadata",
]
