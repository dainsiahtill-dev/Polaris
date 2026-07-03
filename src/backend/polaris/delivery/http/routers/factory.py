"""Factory Router - unattended factory HTTP + Nats-JetStream adapter."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
import inspect
import json
import logging
import os
import re
import shutil
import traceback
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal, TypeAlias, cast

from fastapi import APIRouter, Depends
from polaris.cells.control_plane.run_ledger.public.contracts import ReadRunLedgerProjectionQueryV1
from polaris.cells.control_plane.run_ledger.public.service import read_run_ledger_projection
from polaris.cells.factory.pipeline.internal.bench_service import (
    FactoryBenchService,
)
from polaris.cells.factory.pipeline.public import (
    TERMINAL_RUN_STATUSES,
    FactoryConfig,
    FactoryRun,
    FactoryRunService,
    FactoryRunStatus as ServiceRunStatus,
)
from polaris.cells.factory.pipeline.public.types import (
    FactoryControlRequest,
    FactoryRunList,
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
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.cells.storage.layout.public.service import (
    save_persisted_settings,
    sync_process_settings_environment,
)
from polaris.delivery.http.routers._shared import (
    StructuredHTTPException,
    ensure_required_roles_ready,
    require_internal_bench_surface,
)
from polaris.delivery.http.routers.jetstream_utils import (
    publish_to_jetstream,
)
from polaris.delivery.http.schemas import (
    FactoryRunArtifactsResponse,
    FactoryRunAuditBundleResponse,
    FactoryRunEventsResponse,
)
from polaris.kernelone.constants import DEFAULT_DIRECTOR_MAX_PARALLELISM
from polaris.kernelone.fs.text_ops import write_text_atomic
from polaris.kernelone.storage import resolve_logical_path, resolve_runtime_path, resolve_storage_roots
from polaris.kernelone.trace import create_task_with_context
from pydantic import BaseModel, Field

from ._shared import get_state, require_auth

if TYPE_CHECKING:
    from polaris.cells.runtime.state_owner.public.service import AppState

logger = logging.getLogger(__name__)

router = APIRouter(tags=["factory"], dependencies=[Depends(require_auth)])

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
_DEFAULT_DIRECTOR_DISPATCH_TIMEOUT_SECONDS = 1800
_DIRECTOR_DISPATCH_TIMEOUT_ENV_KEYS = (
    "KERNELONE_FACTORY_DIRECTOR_DISPATCH_TIMEOUT_SECONDS",
    "KERNELONE_DIRECTOR_LLM_TIMEOUT_SECONDS",
    "KERNELONE_DIRECTOR_LLM_CALL_TIMEOUT_SECONDS",
    "KERNELONE_DIRECTOR_LLM_TIMEOUT_MAX_SECONDS",
)
_FACTORY_RUN_DEADLINE_METADATA_KEYS = (
    "factory_run_deadline_epoch_seconds",
    "factory_deadline_epoch_seconds",
    "deadline_epoch_seconds",
)
_RETRY_START_POLICY_AFTER_CHECKPOINT = "after_checkpoint"
FactoryStartFrom: TypeAlias = Literal["auto", "architect", "pm", "director_resume"]
StageSequenceStatus: TypeAlias = Literal["completed", "cancelled", "quality_rework_requested"]
_TASK_BOUNDARY_REWORK_REASON = "task_boundary_interface_discrepancy_required"
_TASK_BOUNDARY_OWNER_REWORK_REASON = "task_boundary_owner_task_retry_required"
_PLAN_PROBE_UNPLANNABLE_STATUS = "coverage_matched_but_unplannable"


def _get_service(workspace: str) -> FactoryRunService:
    """Get a service instance bound to the current workspace."""
    return FactoryRunService(workspace=Path(workspace))


def _resolve_director_dispatch_timeout_seconds() -> int:
    candidates = [_DEFAULT_DIRECTOR_DISPATCH_TIMEOUT_SECONDS]
    for env_key in _DIRECTOR_DISPATCH_TIMEOUT_ENV_KEYS:
        raw = os.getenv(env_key)
        if raw is None:
            continue
        try:
            value = int(float(str(raw).strip()))
        except (TypeError, ValueError):
            continue
        if value > 0:
            candidates.append(value)
    return max(candidates)


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

    directive_value = _coerce_optional_string(start_payload.get("directive"))
    if directive_value is None:
        directive_value = _coerce_optional_string(run.config.description)

    return FactoryStartRequest(
        workspace=str(start_payload.get("workspace") or workspace),
        start_from=cast(FactoryStartFrom, start_from),
        directive=directive_value,
        run_director=bool(start_payload.get("run_director", "director_dispatch" in configured_stages)),
        director_iterations=_coerce_director_iterations(start_payload.get("director_iterations", 0)),
        loop=bool(start_payload.get("loop", run.metadata.get("loop_requested", False))),
        input_source=_coerce_optional_string(start_payload.get("input_source")),
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

    recoverable = bool(raw_failure.get("recoverable"))
    failure_type = FailureType.TRANSIENT if recoverable else FailureType.DETERMINISTIC
    timestamp = raw_failure.get("timestamp")
    detail = str(raw_failure.get("detail") or "").strip() or "Factory run failed"

    return FailureInfo(
        failure_type=failure_type,
        code=str(raw_failure.get("code") or "FACTORY_FAILED"),
        detail=detail,
        phase=phase,
        timestamp=_parse_datetime(str(timestamp)) or datetime.now(timezone.utc),
        recoverable=recoverable,
        suggested_action=str(raw_failure.get("suggested_action") or "").strip() or None,
        hops=[],
    )


def _json_safe_metadata(value: Any) -> dict[str, Any]:
    try:
        payload = json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}

    payload.pop("summary_md", None)
    payload.pop("summary_json", None)
    failure = payload.get("failure")
    if isinstance(failure, dict):
        failure.pop("traceback", None)
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


def _check_docs_ready(workspace: str) -> bool:
    """Check whether required docs are already present."""
    workspace_path = Path(workspace)
    docs_to_check = [
        workspace_path / "SPEC.md",
        workspace_path / "requirements.md",
        workspace_path / "docs" / "SPEC.md",
        workspace_path / "docs" / "requirements.md",
    ]
    return any(doc.exists() for doc in docs_to_check)


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _pm_plan_task_count(workspace: str) -> int:
    payload = _load_json_object(Path(resolve_runtime_path(workspace, "runtime/tasks/plan.json")))
    tasks = payload.get("tasks")
    return len(tasks) if isinstance(tasks, list) else 0


def _director_resume_task_files(task_dir: Path) -> list[Path]:
    try:
        return sorted(
            path for path in task_dir.glob("task_*.json") if path.is_file() and not path.name.endswith(".session.json")
        )
    except OSError:
        return []


def _taskboard_record_count(workspace: str) -> int:
    task_dir = Path(resolve_runtime_path(workspace, "runtime/tasks"))
    return len(_director_resume_task_files(task_dir))


def _director_resume_workspace_slug(workspace_key: str) -> str:
    match = re.match(r"^(?P<slug>.+)-[0-9a-f]{12}$", workspace_key)
    return str(match.group("slug")) if match else workspace_key


def _director_resume_source_task_dirs(workspace: str) -> list[Path]:
    roots = resolve_storage_roots(workspace)
    current_task_dir = Path(resolve_runtime_path(workspace, "runtime/tasks")).resolve()
    slug = _director_resume_workspace_slug(str(roots.workspace_key))
    runtime_project_bases = [
        Path(roots.runtime_projects_root),
        Path(os.path.expanduser("~/.cache/polaris")) / ".polaris" / "projects",
        Path(os.path.expanduser("~/.cache/kernelone")) / ".polaris" / "projects",
    ]
    candidates: list[Path] = []
    with contextlib.suppress(OSError):
        for runtime_projects_root in dict.fromkeys(runtime_project_bases):
            if not runtime_projects_root.exists():
                continue
            for project_root in runtime_projects_root.glob(f"{slug}-*"):
                task_dir = project_root / "runtime" / "tasks"
                if task_dir.resolve() == current_task_dir:
                    continue
                if (task_dir / "plan.json").is_file() and _director_resume_task_files(task_dir):
                    candidates.append(task_dir)
    return sorted(candidates, key=lambda path: path.stat().st_mtime if path.exists() else 0.0, reverse=True)


def _director_resume_taskboard_score(task_dir: Path) -> tuple[int, int, float]:
    task_files = _director_resume_task_files(task_dir)
    plan = _load_json_object(task_dir / "plan.json")
    tasks = plan.get("tasks")
    planned_count = len(tasks) if isinstance(tasks, list) else 0
    blueprint_dir = task_dir.parent / "blueprints"
    blueprint_count = 0
    with contextlib.suppress(OSError):
        blueprint_count = len([path for path in blueprint_dir.glob("ce_*.json") if path.is_file()])
    mtime = max((path.stat().st_mtime for path in [task_dir / "plan.json", *task_files] if path.exists()), default=0.0)
    return (blueprint_count, min(planned_count, len(task_files)), mtime)


def _director_resume_reset_task_payload(payload: dict[str, Any]) -> dict[str, Any]:
    reset = dict(payload)
    blocked_by = reset.get("blocked_by")
    if not isinstance(blocked_by, list):
        blocked_by = reset.get("blockedBy") if isinstance(reset.get("blockedBy"), list) else []
    reset["status"] = "blocked" if blocked_by else "pending"
    reset["claimed_by"] = None
    reset["assignee"] = ""
    reset["started_at"] = None
    reset["completed_at"] = None
    reset["claimed_at"] = None
    reset["result_summary"] = ""
    reset["error_message"] = None
    metadata = reset.get("metadata")
    if isinstance(metadata, dict):
        cleaned_metadata = dict(metadata)
        for key in (
            "adapter_phase",
            "claim_attempt",
            "claimed_at",
            "claimed_by",
            "director_claimable_task_ids",
            "factory_stage",
            "last_claimed_by",
            "last_context_summary",
            "last_execution_error",
            "last_execution_summary",
            "resume_available",
            "resume_count",
            "resume_state",
            "runtime_execution",
            "workflow_run_id",
        ):
            cleaned_metadata.pop(key, None)
        reset["metadata"] = cleaned_metadata
    return reset


def _rehydrate_director_resume_taskboard(workspace: str) -> str:
    target_dir = Path(resolve_runtime_path(workspace, "runtime/tasks"))
    if _pm_plan_task_count(workspace) > 0 and _taskboard_record_count(workspace) > 0:
        _reset_current_director_resume_taskboard(workspace, target_dir=target_dir)
        return ""
    candidates = sorted(
        _director_resume_source_task_dirs(workspace),
        key=_director_resume_taskboard_score,
        reverse=True,
    )
    for source_dir in candidates:
        plan_payload = _load_json_object(source_dir / "plan.json")
        if not isinstance(plan_payload.get("tasks"), list) or not _director_resume_task_files(source_dir):
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_dir / "plan.json", target_dir / "plan.json")
        copied: list[str] = ["plan.json"]
        for task_file in _director_resume_task_files(source_dir):
            payload = _load_json_object(task_file)
            if not payload:
                continue
            normalized_payload = _director_resume_reset_task_payload(payload)
            _write_json_text_atomic(target_dir / task_file.name, normalized_payload, trailing_newline=False)
            copied.append(task_file.name)
        max_id = source_dir / ".max_id"
        if max_id.is_file():
            shutil.copy2(max_id, target_dir / ".max_id")
            copied.append(".max_id")
        evidence = {
            "schema_version": "factory.director_resume_taskboard_rehydration.v1",
            "source": "factory_http",
            "source_task_dir": str(source_dir),
            "target_task_dir": str(target_dir),
            "copied_files": copied,
            "reset_statuses": "all_task_records",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_json_text_atomic(target_dir / "director_resume_rehydration.json", evidence, trailing_newline=False)
        return str(source_dir)
    return ""


def _reset_current_director_resume_taskboard(
    workspace: str,
    *,
    target_dir: Path | None = None,
) -> dict[str, Any]:
    """Reset existing Director task rows to a clean pre-Director claimable state."""
    task_dir = target_dir or Path(resolve_runtime_path(workspace, "runtime/tasks"))
    task_files = _director_resume_task_files(task_dir)
    if not task_files:
        return {}

    reset_files: list[str] = []
    skipped_files: list[str] = []
    deleted_session_files: list[str] = []
    for task_file in task_files:
        payload = _load_json_object(task_file)
        if not payload:
            skipped_files.append(task_file.name)
            continue
        normalized_payload = _director_resume_reset_task_payload(payload)
        _write_json_text_atomic(task_file, normalized_payload)
        reset_files.append(task_file.name)

    with contextlib.suppress(OSError):
        for session_file in sorted(task_dir.glob("task_*.session.json")):
            if not session_file.is_file():
                continue
            session_file.unlink()
            deleted_session_files.append(session_file.name)

    evidence = {
        "schema_version": "factory.director_resume_taskboard_reset.v1",
        "source": "factory_http",
        "workspace": workspace,
        "target_task_dir": str(task_dir),
        "reset_files": reset_files,
        "skipped_files": skipped_files,
        "deleted_session_files": deleted_session_files,
        "reset_statuses": "all_task_records",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    task_dir.mkdir(parents=True, exist_ok=True)
    _write_json_text_atomic(task_dir / "director_resume_reset.json", evidence)
    return evidence


def _chief_engineer_blueprint_count(workspace: str) -> int:
    workspace_path = Path(workspace)
    candidates = [
        workspace_path / ".polaris" / "blueprints" / "latest.review.json",
        Path(resolve_logical_path(workspace, "workspace/.polaris/blueprints/latest.review.json")),
    ]
    state_dir = Path(resolve_runtime_path(workspace, "runtime/state/blueprints"))
    with contextlib.suppress(OSError):
        candidates.extend(path for path in state_dir.glob("*.review.json") if path.is_file())
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        payload = _load_json_object(resolved)
        raw_count = payload.get("generated_blueprints")
        try:
            count = int(str(raw_count or 0))
        except (TypeError, ValueError):
            count = 0
        blueprints = payload.get("blueprints")
        if count > 0 or (isinstance(blueprints, list) and bool(blueprints)):
            return max(count, len(blueprints) if isinstance(blueprints, list) else 0)
    return 0


def _pre_director_snapshot_ready(workspace: str) -> bool:
    manifest_path = Path(workspace) / ".polaris" / "factory_snapshots" / "pre_director" / "manifest.json"
    if not manifest_path.is_file():
        return False
    payload = _load_json_object(manifest_path)
    return str(payload.get("snapshot_kind") or "") == "pre_director_workspace"


def _ensure_director_resume_evidence_ready(workspace: str) -> None:
    if _chief_engineer_blueprint_count(workspace) > 0:
        _rehydrate_director_resume_taskboard(workspace)
    missing: list[str] = []
    if _pm_plan_task_count(workspace) <= 0:
        missing.append("runtime/tasks/plan.json")
    if _taskboard_record_count(workspace) <= 0:
        missing.append("runtime/tasks/task_*.json")
    if _chief_engineer_blueprint_count(workspace) <= 0:
        missing.append(".polaris/blueprints/latest.review.json")
    if not _pre_director_snapshot_ready(workspace):
        missing.append(".polaris/factory_snapshots/pre_director/manifest.json")
    if missing:
        raise StructuredHTTPException(
            status_code=409,
            code="DIRECTOR_RESUME_EVIDENCE_MISSING",
            message="Director-only Factory run requires trusted PM, Chief Engineer, TaskBoard, and pre-Director snapshot evidence",
            details={
                "workspace": workspace,
                "missing_evidence": missing,
                "required_evidence": [
                    "runtime/tasks/plan.json",
                    "runtime/tasks/task_*.json",
                    ".polaris/blueprints/latest.review.json",
                    ".polaris/factory_snapshots/pre_director/manifest.json",
                ],
            },
        )


def _normalize_start_from(start_from: str, workspace: str) -> str:
    normalized = str(start_from or "auto").strip().lower()
    if normalized in {"resume_director", "director-only", "director_only"}:
        normalized = "director_resume"
    if normalized not in {"auto", "architect", "pm", "director_resume"}:
        normalized = "auto"
    if normalized != "auto":
        return normalized
    return "architect" if not _check_docs_ready(workspace) else "pm"


def _build_stage_list(start_from: str, run_director: bool) -> list[str]:
    del run_director
    normalized = str(start_from or "auto").strip().lower()
    if normalized == "architect":
        return [
            "docs_generation",
            "pm_planning",
            "chief_engineer_review",
            "director_dispatch",
            "quality_gate",
        ]
    if normalized == "pm":
        return [
            "pm_planning",
            "chief_engineer_review",
            "director_dispatch",
            "quality_gate",
        ]
    if normalized == "director_resume":
        return [
            "director_dispatch",
            "quality_gate",
        ]
    # auto is normalized before this point; fail closed to the canonical chain.
    return [
        "pm_planning",
        "chief_engineer_review",
        "director_dispatch",
        "quality_gate",
    ]


def _required_ready_roles_for_stages(stages: list[str], *, qa_enabled: bool) -> list[str]:
    roles: list[str] = []
    for stage in stages:
        role = STAGE_TO_ROLE.get(str(stage or "").strip())
        if not role:
            continue
        # Factory CE review uses the local chief_engineer.blueprint service; it
        # must not be blocked by role-chat LLM readiness.
        if role == "chief_engineer":
            continue
        if role == "qa" and not qa_enabled:
            continue
        if role not in roles:
            roles.append(role)
    return roles


def _settings_qa_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "qa_enabled", True))


def _ensure_factory_runtime_ready(state: AppState, stages: list[str]) -> None:
    roles = _required_ready_roles_for_stages(stages, qa_enabled=_settings_qa_enabled(state.settings))
    if not roles:
        return
    live_check = os.environ.get("KERNELONE_FACTORY_LIVE_LLM_PREFLIGHT", "0").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
        "disabled",
    }
    ensure_required_roles_ready(
        state,
        default_roles=roles,
        force_roles=roles,
        live_check=live_check,
    )


def _build_stage_context(
    stage: str,
    payload: FactoryStartRequest,
    state: AppState,
    *,
    run_id: str = "",
) -> dict[str, Any]:
    metadata = dict(payload.metadata or {})
    metadata["factory_start_from"] = str(payload.start_from or "").strip().lower()
    context: dict[str, Any] = {
        "settings": getattr(state, "settings", None),
        "factory_run_id": str(run_id or "").strip(),
        "factory_start_from": metadata["factory_start_from"],
        "metadata": metadata,
    }
    _append_factory_deadline_context(context, metadata)
    if stage in {"docs_generation", "pm_planning"}:
        context["directive"] = payload.directive
    if stage == "chief_engineer_review":
        context["directive"] = payload.directive
    if stage == "director_dispatch":
        requested_execution_mode = str(payload.director_workflow_execution_mode or "").strip().lower()
        context["execution_mode"] = (
            requested_execution_mode
            if requested_execution_mode in {"serial", "parallel"}
            else getattr(state.settings, "director_execution_mode", "parallel")
        )
        context["max_workers"] = getattr(
            state.settings, "director_max_parallel_tasks", DEFAULT_DIRECTOR_MAX_PARALLELISM
        )
        context["director_dispatch_driver"] = "task-market"
        context["dispatch_mode"] = "mainline-full"
        if int(payload.director_iterations) > 0:
            context["director_max_rounds"] = int(payload.director_iterations)
        director_dispatch_timeout = _resolve_director_dispatch_timeout_seconds()
        context["timeout"] = director_dispatch_timeout
        context["director_dispatch_timeout_seconds"] = director_dispatch_timeout
        context["llm_call_timeout_seconds"] = director_dispatch_timeout
        context["director_llm_timeout_seconds"] = director_dispatch_timeout
    if stage == "quality_gate":
        context["qa_target"] = payload.directive or "Quality gate"
    return context


def _json_payload(data: Any) -> str:
    payload = data.model_dump(mode="json") if hasattr(data, "model_dump") else data
    return json.dumps(payload, ensure_ascii=False)


def _write_json_text_atomic(path: Path, payload: Any, *, trailing_newline: bool = True) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if trailing_newline:
        text += "\n"
    write_text_atomic(str(path), text)


def _resolve_runtime_path(workspace: str, relative_path: str) -> Path:
    rel = str(relative_path or "").replace("\\", "/").strip().lstrip("/")
    if rel == "docs" or rel.startswith("docs/"):
        rel = f"workspace/{rel}"
    elif rel.startswith(("tasks/", "dispatch/")):
        rel = f"runtime/{rel}"
    resolved = resolve_logical_path(str(workspace), rel)
    return Path(resolved).resolve()


def _read_json_artifact(workspace: str, relative_path: str) -> dict[str, Any]:
    target = _resolve_runtime_path(workspace, relative_path)
    if not target.exists() or not target.is_file():
        return {}
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
    except (RuntimeError, ValueError):
        logger.debug("Failed to read JSON artifact: workspace=%s path=%s", workspace, relative_path)
        return {}
    if isinstance(loaded, dict):
        return loaded
    return {}


def _workspace_validation_requests_task_boundary_rework(payload: dict[str, Any]) -> bool:
    repair_raw = payload.get("repair")
    repair: dict[str, Any] = repair_raw if isinstance(repair_raw, dict) else {}
    if bool(repair.get("task_boundary_triage_required")):
        return True
    if str(repair.get("success_reason") or "").strip() == _TASK_BOUNDARY_REWORK_REASON:
        return True
    if _ownership_handoff_requests_from_repair_payload(repair):
        return True
    warnings = payload.get("warnings")
    return isinstance(warnings, list) and _TASK_BOUNDARY_REWORK_REASON in {str(item).strip() for item in warnings}


def _read_task_boundary_workspace_validation(workspace: str) -> tuple[dict[str, Any], str]:
    for relative_path in (
        "workspace/qa/latest.workspace-validation.json",
        "runtime/qa/workspace-validation.json",
    ):
        payload = _read_json_artifact(workspace, relative_path)
        if payload and _workspace_validation_requests_task_boundary_rework(payload):
            return payload, relative_path
    return {}, ""


def _task_record_needs_task_boundary_rework(record: dict[str, Any]) -> bool:
    status = str(record.get("status") or "").strip().lower()
    if status not in {"failed", "error"}:
        return False

    metadata_raw = record.get("metadata")
    metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    adapter_result_raw = metadata.get("adapter_result")
    adapter_result: dict[str, Any] = adapter_result_raw if isinstance(adapter_result_raw, dict) else {}
    quality_repair_raw = adapter_result.get("quality_repair") or metadata.get("quality_repair")
    quality_repair: dict[str, Any] = quality_repair_raw if isinstance(quality_repair_raw, dict) else {}
    interface_evidence_raw = (
        adapter_result.get("interface_discrepancy_evidence")
        or quality_repair.get("interface_discrepancy_evidence")
        or metadata.get("interface_discrepancy_evidence")
    )
    interface_evidence: dict[str, Any] = interface_evidence_raw if isinstance(interface_evidence_raw, dict) else {}
    plan_probe_raw = quality_repair.get("plan_probe_preaudit") or adapter_result.get("plan_probe_preaudit")
    plan_probe: dict[str, Any] = plan_probe_raw if isinstance(plan_probe_raw, dict) else {}

    markers = {
        str(metadata.get("last_execution_error") or "").strip(),
        str(adapter_result.get("success_reason") or "").strip(),
        str(quality_repair.get("success_reason") or "").strip(),
        str(quality_repair.get("stage") or "").strip(),
        str(interface_evidence.get("reason") or "").strip(),
        str(interface_evidence.get("plan_probe_status") or "").strip(),
        str(plan_probe.get("status") or "").strip(),
    }
    return bool(
        {
            "director_materialization_quality_failed",
            "runtime_plan_probe_unplannable",
            _TASK_BOUNDARY_REWORK_REASON,
            _PLAN_PROBE_UNPLANNABLE_STATUS,
        }
        & markers
    )


def _ownership_handoff_requests_from_repair_payload(repair: dict[str, Any]) -> list[dict[str, Any]]:
    scope_filter_raw = repair.get("task_boundary_scope_filter")
    scope_filter: dict[str, Any] = scope_filter_raw if isinstance(scope_filter_raw, dict) else {}
    candidates = (
        scope_filter.get("ownership_handoff_requests"),
        (scope_filter.get("scope_authority") or {}).get("ownership_handoff_requests")
        if isinstance(scope_filter.get("scope_authority"), dict)
        else None,
        repair.get("ownership_handoff_requests"),
        (repair.get("scope_authority") or {}).get("ownership_handoff_requests")
        if isinstance(repair.get("scope_authority"), dict)
        else None,
    )
    for requests_raw in candidates:
        if isinstance(requests_raw, list):
            return [dict(item) for item in requests_raw if isinstance(item, dict) and item]
    return []


def _owned_handoff_requests_from_repair_payload(repair: dict[str, Any]) -> list[dict[str, Any]]:
    requests_raw = _ownership_handoff_requests_from_repair_payload(repair)
    requests: list[dict[str, Any]] = []
    for request in requests_raw:
        if not bool(request.get("owner_found")):
            continue
        if str(request.get("recommended_route") or "").strip() != "owner_task_retry":
            continue
        requests.append(request)
    return requests


def _task_record_external_tokens(record: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for value in (
        record.get("id"),
        record.get("task_id"),
        record.get("external_task_id"),
        record.get("pm_task_id"),
        record.get("source_task_id"),
    ):
        token = str(value or "").strip()
        if token:
            tokens.add(token)
    metadata_raw = record.get("metadata")
    metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    for key in ("external_task_id", "pm_task_id", "source_task_id", "task_id"):
        token = str(metadata.get(key) or "").strip()
        if token:
            tokens.add(token)
    return tokens


def _matching_owner_handoff_request(
    record: dict[str, Any],
    handoff_requests: list[dict[str, Any]],
) -> dict[str, Any]:
    if not handoff_requests:
        return {}
    tokens = _task_record_external_tokens(record)
    if not tokens:
        return {}
    for request in handoff_requests:
        owner_tokens = {
            str(request.get("owner_step_id") or "").strip(),
            str(request.get("owner_parent") or "").strip(),
        }
        if tokens & {token for token in owner_tokens if token}:
            return request
    return {}


def _safe_rework_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (RuntimeError, TypeError, ValueError):
        return int(default)


def _task_boundary_rework_evidence(payload: dict[str, Any], *, artifact: str) -> dict[str, Any]:
    repair_raw = payload.get("repair")
    repair: dict[str, Any] = repair_raw if isinstance(repair_raw, dict) else {}
    warnings_raw = payload.get("warnings")
    warnings = [str(item).strip() for item in warnings_raw] if isinstance(warnings_raw, list) else []
    evidence: dict[str, Any] = {
        "artifact": artifact,
        "reason": _TASK_BOUNDARY_REWORK_REASON,
        "warnings": [item for item in warnings if item],
    }
    for key in (
        "success_reason",
        "plan_probe_preaudit",
        "interface_discrepancy_evidence",
        "task_boundary_scope_filter",
        "residual_error_count",
        "residual_errors",
    ):
        value = repair.get(key)
        if value not in (None, "", [], {}):
            evidence[key] = value
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        evidence["errors"] = errors[:20]
    return evidence


def _apply_quality_gate_task_boundary_rework_requests(workspace: str) -> dict[str, Any]:
    payload, artifact = _read_task_boundary_workspace_validation(workspace)
    summary: dict[str, Any] = {
        "requested": False,
        "evaluated_count": 0,
        "reopened_count": 0,
        "exhausted_count": 0,
        "skipped_count": 0,
        "unmatched_owner_handoff_count": 0,
        "unmatched_owner_handoff_requests": [],
        "unknown_owner_handoff_count": 0,
        "unknown_owner_handoff_requests": [],
        "tasks": [],
        "reason": _TASK_BOUNDARY_REWORK_REASON,
        "artifact": artifact,
    }
    if not payload:
        return summary

    try:
        task_board = TaskRuntimeService(str(workspace))
        entries = task_board.list_all()
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        return summary

    max_retries = _resolve_quality_rework_max_cycles()
    now_iso = datetime.now(timezone.utc).isoformat()
    evidence = _task_boundary_rework_evidence(payload, artifact=artifact)
    repair_raw = payload.get("repair")
    repair: dict[str, Any] = repair_raw if isinstance(repair_raw, dict) else {}
    all_handoff_requests = _ownership_handoff_requests_from_repair_payload(repair)
    owner_handoff_requests = _owned_handoff_requests_from_repair_payload(repair)
    unknown_owner_handoff_requests = [
        dict(request)
        for request in all_handoff_requests
        if not bool(request.get("owner_found"))
        or str(request.get("recommended_route") or "").strip() == "scope_authority_resolution"
    ]
    matched_owner_handoff_ids: set[int] = set()
    for entry in entries:
        record = entry.to_dict() if hasattr(entry, "to_dict") else entry
        if not isinstance(record, dict):
            continue
        owner_handoff_request = _matching_owner_handoff_request(record, owner_handoff_requests)
        if all_handoff_requests:
            if not owner_handoff_request:
                continue
            rework_reason = _TASK_BOUNDARY_OWNER_REWORK_REASON
            task_evidence = {
                **evidence,
                "reason": rework_reason,
                "ownership_handoff_request": owner_handoff_request,
            }
            matched_owner_handoff_ids.add(id(owner_handoff_request))
        elif _task_record_needs_task_boundary_rework(record):
            rework_reason = _TASK_BOUNDARY_REWORK_REASON
            task_evidence = evidence
        else:
            continue

        task_id = _safe_rework_int(record.get("id") or record.get("task_id"), default=0)
        if task_id <= 0:
            summary["skipped_count"] += 1
            continue

        metadata_raw = record.get("metadata")
        metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
        adapter_result_raw = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = adapter_result_raw if isinstance(adapter_result_raw, dict) else {}
        retry_count = _safe_rework_int(
            metadata.get("qa_rework_retry_count", adapter_result.get("qa_rework_retry_count")),
            default=0,
        )
        next_retry_count = retry_count + 1
        exhausted = next_retry_count >= max_retries

        merged_adapter_result: dict[str, Any] = dict(adapter_result)
        merged_adapter_result.update(
            {
                "task_boundary_rework_requested": not exhausted,
                "task_boundary_rework_reason": rework_reason,
                "qa_rework_retry_count": next_retry_count,
                "qa_rework_max_retries": max_retries,
                "qa_rework_reason": rework_reason,
                "qa_rework_exhausted": exhausted,
                "qa_rework_evidence": task_evidence,
            }
        )
        metadata_update = {
            "adapter_result": merged_adapter_result,
            "task_boundary_rework_requested": not exhausted,
            "task_boundary_rework_reason": rework_reason,
            "task_boundary_rework_evidence": task_evidence,
            "qa_rework_requested": not exhausted,
            "qa_rework_exhausted": exhausted,
            "qa_rework_retry_count": next_retry_count,
            "qa_rework_max_retries": max_retries,
            "qa_rework_reason": rework_reason,
            "qa_rework_evidence": task_evidence,
            "qa_last_reviewed_at": now_iso,
            "qa_last_verdict": "FAIL",
        }
        summary["evaluated_count"] += 1
        task_summary = {
            "task_id": str(task_id),
            "external_task_id": str(metadata.get("external_task_id") or metadata.get("pm_task_id") or "").strip(),
            "retry_count": next_retry_count,
            "max_retries": max_retries,
            "exhausted": exhausted,
            "reason": rework_reason,
        }
        try:
            if exhausted:
                task_board.update(task_id, metadata=metadata_update)
                summary["exhausted_count"] += 1
            else:
                task_board.reopen(
                    task_id,
                    reason=rework_reason,
                    metadata=metadata_update,
                )
                summary["reopened_count"] += 1
                summary["requested"] = True
            summary["tasks"].append(task_summary)
        except (RuntimeError, ValueError):
            summary["skipped_count"] += 1

    unmatched_owner_handoff_requests = [
        dict(request)
        for request in owner_handoff_requests
        if id(request) not in matched_owner_handoff_ids
    ]
    if unmatched_owner_handoff_requests:
        summary["skipped_count"] += len(unmatched_owner_handoff_requests)
        summary["unmatched_owner_handoff_count"] = len(unmatched_owner_handoff_requests)
        summary["unmatched_owner_handoff_requests"] = unmatched_owner_handoff_requests

    if unknown_owner_handoff_requests:
        summary["skipped_count"] += len(unknown_owner_handoff_requests)
        summary["unknown_owner_handoff_count"] = len(unknown_owner_handoff_requests)
        summary["unknown_owner_handoff_requests"] = unknown_owner_handoff_requests

    return summary


def _read_pm_plan_signature(workspace: str) -> str:
    plan_payload = _read_json_artifact(workspace, "tasks/plan.json")
    tasks_payload = plan_payload.get("tasks")
    if not isinstance(tasks_payload, list) or not tasks_payload:
        return ""
    canonical = json.dumps(plan_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _read_docs_pipeline_state(workspace: str) -> dict[str, Any]:
    pipeline_payload = _read_json_artifact(workspace, "runtime/contracts/architect.docs_pipeline.json")
    progress_payload = _read_json_artifact(workspace, "runtime/state/pm.docs_progress.json")

    raw_stages = pipeline_payload.get("stages")
    stage_count = len(raw_stages) if isinstance(raw_stages, list) else 0
    enabled = stage_count > 0
    active_index_raw = progress_payload.get("active_stage_index", 0)
    try:
        active_index = int(active_index_raw)
    except (RuntimeError, ValueError):
        active_index = 0
    active_index = 0 if stage_count <= 0 else max(0, min(active_index, stage_count - 1))

    advance_reason = str(progress_payload.get("advance_reason") or "").strip()
    completed = enabled and advance_reason == "pipeline_complete"
    return {
        "enabled": enabled,
        "stage_count": stage_count,
        "active_stage_index": active_index,
        "active_stage_id": str(progress_payload.get("active_stage_id") or "").strip(),
        "advance_reason": advance_reason,
        "completed": completed,
    }


def _resolve_loop_max_cycles() -> int:
    raw = os.getenv("KERNELONE_FACTORY_LOOP_MAX_CYCLES", str(_DEFAULT_LOOP_MAX_CYCLES))
    try:
        value = int(raw)
    except (RuntimeError, ValueError):
        value = _DEFAULT_LOOP_MAX_CYCLES
    return max(1, min(value, 200))


def _resolve_loop_stall_threshold() -> int:
    raw = os.getenv("KERNELONE_FACTORY_LOOP_STALL_THRESHOLD", str(_DEFAULT_LOOP_STALL_THRESHOLD))
    try:
        value = int(raw)
    except (RuntimeError, ValueError):
        value = _DEFAULT_LOOP_STALL_THRESHOLD
    return max(1, min(value, 20))


def _resolve_quality_rework_max_cycles() -> int:
    raw = os.getenv("KERNELONE_FACTORY_QUALITY_REWORK_MAX_CYCLES", str(_DEFAULT_QUALITY_REWORK_MAX_CYCLES))
    try:
        value = int(raw)
    except (RuntimeError, ValueError):
        value = _DEFAULT_QUALITY_REWORK_MAX_CYCLES
    return max(1, min(value, 20))


def _read_quality_gate_rework_summary(workspace: str) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "requested": False,
        "requested_count": 0,
        "exhausted_count": 0,
        "ready_count": 0,
        "tasks": [],
    }
    try:
        task_board = TaskRuntimeService(str(workspace))
        entries = task_board.list_all()
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        return summary

    tasks: list[dict[str, Any]] = []
    requested_count = 0
    exhausted_count = 0
    ready_count = 0
    for entry in entries:
        record = entry.to_dict() if hasattr(entry, "to_dict") else entry
        if not isinstance(record, dict):
            continue
        metadata_raw = record.get("metadata")
        metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
        requested = bool(metadata.get("qa_rework_requested"))
        exhausted = bool(metadata.get("qa_rework_exhausted"))
        if not requested and not exhausted:
            continue
        status = str(record.get("status") or "").strip().lower()
        if exhausted:
            exhausted_count += 1
        elif requested:
            requested_count += 1
        if status in {"pending", "ready"}:
            ready_count += 1
        tasks.append(
            {
                "task_id": str(record.get("id") or record.get("task_id") or "").strip(),
                "external_task_id": str(metadata.get("external_task_id") or metadata.get("pm_task_id") or "").strip(),
                "status": status,
                "reason": str(metadata.get("qa_rework_reason") or "").strip(),
                "retry_count": metadata.get("qa_rework_retry_count"),
                "max_retries": metadata.get("qa_rework_max_retries"),
                "exhausted": exhausted,
            }
        )

    summary.update(
        {
            "requested": requested_count > 0,
            "requested_count": requested_count,
            "exhausted_count": exhausted_count,
            "ready_count": ready_count,
            "tasks": tasks,
        }
    )
    return summary


def _decide_delivery_loop_action(
    *,
    plan_signature: str,
    previous_plan_signature: str,
    unchanged_cycles: int,
    docs_state: dict[str, Any],
    max_stalled_cycles: int,
) -> dict[str, str]:
    signature_changed = bool(plan_signature) and (plan_signature != previous_plan_signature)
    docs_enabled = bool(docs_state.get("enabled"))
    docs_completed = bool(docs_state.get("completed"))

    if not plan_signature:
        return {
            "action": "fail",
            "reason": "pm_plan_signature_missing",
            "message": "PM loop cannot continue: tasks/plan.json missing or empty",
        }

    if docs_enabled and not docs_completed:
        if not signature_changed and unchanged_cycles >= max_stalled_cycles:
            return {
                "action": "fail",
                "reason": "docs_pipeline_stalled",
                "message": (
                    "Architect docs pipeline still incomplete but PM plan signature stopped changing "
                    f"(unchanged_cycles={unchanged_cycles}, stall_threshold={max_stalled_cycles})"
                ),
            }
        return {
            "action": "continue",
            "reason": "docs_pipeline_incomplete",
            "message": "Architect docs pipeline incomplete; continue PM→Chief Engineer→Director loop",
        }

    if signature_changed:
        return {
            "action": "continue",
            "reason": "plan_signature_changed",
            "message": "PM produced new task contract; continue PM→Chief Engineer→Director loop",
        }

    return {
        "action": "stop",
        "reason": "plan_signature_stable",
        "message": "PM task contract stabilized; stop delivery loop",
    }


def _build_summary_json(
    *,
    run: FactoryRun,
    payload: FactoryStartRequest,
    status: str,
    workspace: str,
) -> dict[str, Any]:
    metadata = run.metadata if isinstance(run.metadata, dict) else {}
    history = metadata.get("loop_history")
    loop_history = history if isinstance(history, list) else []
    docs_state = metadata.get("loop_last_docs_state")
    if not isinstance(docs_state, dict):
        docs_state = {}
    failure = metadata.get("failure")
    if not isinstance(failure, dict):
        failure = {}
    return {
        "run_id": run.id,
        "status": status,
        "workspace": workspace,
        "start_from": payload.start_from,
        "run_director": bool(payload.run_director),
        "loop_enabled": bool(payload.loop),
        "stages_configured": list(run.config.stages or []),
        "stages_completed": list(run.stages_completed or []),
        "stages_failed": list(run.stages_failed or []),
        "loop_cycles_executed": int(metadata.get("loop_cycles_executed") or 0),
        "loop_stop_reason": str(metadata.get("loop_stop_reason") or "").strip() or None,
        "docs_pipeline": docs_state,
        "loop_history": loop_history,
        "failure": failure or None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _build_summary_markdown(summary_json: dict[str, Any]) -> str:
    status = str(summary_json.get("status") or "FAIL").strip().upper()
    run_id = str(summary_json.get("run_id") or "").strip()
    loop_enabled = bool(summary_json.get("loop_enabled"))
    loop_cycles = int(summary_json.get("loop_cycles_executed") or 0)
    stop_reason = str(summary_json.get("loop_stop_reason") or "").strip() or "n/a"
    completed = summary_json.get("stages_completed")
    failed = summary_json.get("stages_failed")
    completed_text = ", ".join(completed) if isinstance(completed, list) and completed else "none"
    failed_text = ", ".join(failed) if isinstance(failed, list) and failed else "none"

    lines = [
        "# Factory Run Summary",
        "",
        f"- Run ID: `{run_id}`",
        f"- Status: `{status}`",
        f"- Workspace: `{summary_json.get('workspace')}`",
        f"- Start From: `{summary_json.get('start_from')}`",
        f"- Loop Enabled: `{loop_enabled}`",
        f"- Loop Cycles Executed: `{loop_cycles}`",
        f"- Loop Stop Reason: `{stop_reason}`",
        f"- Stages Completed: `{completed_text}`",
        f"- Stages Failed: `{failed_text}`",
    ]

    failure = summary_json.get("failure")
    if isinstance(failure, dict) and failure:
        lines.extend(
            [
                "",
                "## Failure",
                "",
                f"- Stage: `{failure.get('stage')}`",
                f"- Code: `{failure.get('code')}`",
                f"- Detail: {failure.get('detail')}",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _model_dump_json_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json")
    elif hasattr(value, "dict"):
        payload = value.dict()
    else:
        payload = value
    if isinstance(payload, dict):
        return payload
    return {}


def _artifact_response_path(artifact_path: Path, workspace: str) -> str:
    try:
        return str(artifact_path.relative_to(Path(workspace)))
    except ValueError:
        return str(artifact_path)


def _list_run_artifacts(
    *,
    service: FactoryRunService,
    workspace: str,
    run_id: str,
) -> list[dict[str, Any]]:
    run_dir = service.store.get_run_dir(run_id)
    artifacts_dir = run_dir / "artifacts"
    artifacts: list[dict[str, Any]] = []

    if not artifacts_dir.exists():
        return artifacts

    for artifact_path in sorted(artifacts_dir.iterdir(), key=lambda item: item.name):
        if not artifact_path.is_file():
            continue
        artifacts.append(_artifact_item_from_path(artifact_path, _artifact_response_path(artifact_path, workspace)))

    return artifacts


def _extract_task_id_from_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for source in (
        payload,
        payload.get("raw") if isinstance(payload.get("raw"), dict) else None,
        payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None,
    ):
        if not isinstance(source, dict):
            continue
        for key in ("task_id", "pm_task_id", "taskId"):
            value = str(source.get(key) or "").strip()
            if value:
                return value
    return ""


def _task_id_from_artifact_name(name: str) -> str:
    stem = Path(str(name or "").replace("\\", "/")).stem.strip()
    if not stem:
        return ""
    lowered = stem.lower()
    for prefix in ("ce_", "ce-", "blueprint_", "blueprint-", "chief_engineer_", "chief-engineer-"):
        if lowered.startswith(prefix):
            return stem[len(prefix) :].strip()
    return ""


def _task_id_from_artifact_file(artifact_path: Path) -> str:
    if artifact_path.suffix.lower() == ".json":
        try:
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            payload = None
        task_id = _extract_task_id_from_payload(payload)
        if task_id:
            return task_id
    return _task_id_from_artifact_name(artifact_path.name)


def _artifact_item_from_path(artifact_path: Path, response_path: str) -> dict[str, Any]:
    item: dict[str, Any] = {
        "name": artifact_path.name,
        "path": response_path,
        "size": artifact_path.stat().st_size,
    }
    task_id = _task_id_from_artifact_file(artifact_path)
    if task_id:
        item["task_id"] = task_id
    return item


def _artifact_item_from_stage_ref(workspace: str, relative_path: str) -> dict[str, Any] | None:
    rel = str(relative_path or "").replace("\\", "/").strip().lstrip("/")
    if not rel:
        return None
    try:
        artifact_path = _resolve_runtime_path(workspace, rel)
    except (OSError, RuntimeError, ValueError):
        return None
    if not artifact_path.exists() or not artifact_path.is_file():
        return None
    return _artifact_item_from_path(artifact_path, rel)


def _list_stage_artifacts(
    *,
    workspace: str,
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        if str(event.get("type") or "").strip() != "stage_completed":
            continue
        result = event.get("result")
        if not isinstance(result, dict):
            continue
        raw_artifacts = result.get("artifacts")
        if not isinstance(raw_artifacts, list):
            continue
        for raw_path in raw_artifacts:
            rel = str(raw_path or "").replace("\\", "/").strip().lstrip("/")
            if not rel or rel in seen:
                continue
            item = _artifact_item_from_stage_ref(workspace, rel)
            if item is None:
                continue
            seen.add(rel)
            artifacts.append(item)
    return artifacts


def _merge_artifact_items(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            path = str(item.get("path") or "").strip()
            name = str(item.get("name") or "").strip()
            key = path or name
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


def _build_artifacts_response(
    *,
    run: FactoryRun,
    artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    summary_json = run.metadata.get("summary_json")
    return {
        "run_id": run.id,
        "artifacts": artifacts,
        "summary_md": str(run.metadata.get("summary_md") or "").strip() or None,
        "summary_json": summary_json if isinstance(summary_json, dict) else None,
    }


def _safe_events_tail_limit(limit: int) -> int:
    return max(0, min(int(limit), 1000))


def _count_events_by_type(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        event_type = str(event.get("type") or "unknown").strip() or "unknown"
        counts[event_type] = counts.get(event_type, 0) + 1
    return counts


def _extract_taskboard_snapshots(
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Extract initial and final taskboard snapshots from stage events."""
    initial: dict[str, Any] = {}
    final: dict[str, Any] = {}
    for event in events:
        taskboard = event.get("taskboard")
        if not isinstance(taskboard, dict):
            continue
        if not initial:
            initial = {
                "total": taskboard.get("total"),
                "claimed": taskboard.get("claimed"),
                "completed": taskboard.get("completed"),
                "failed": taskboard.get("failed"),
                "blocked": taskboard.get("blocked"),
            }
        final = {
            "total": taskboard.get("total"),
            "claimed": taskboard.get("claimed"),
            "completed": taskboard.get("completed"),
            "failed": taskboard.get("failed"),
            "blocked": taskboard.get("blocked"),
        }
    return {"initial": initial, "final": final}


def _extract_per_binding_task_status(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract per-task claim/terminal status from director events."""
    tasks: dict[str, dict[str, Any]] = {}
    for event in events:
        task_id = str(event.get("task_id") or event.get("pm_task_id") or "").strip()
        if not task_id:
            payload = event.get("result") if isinstance(event.get("result"), dict) else None
            if isinstance(payload, dict):
                task_id = str(payload.get("task_id") or payload.get("pm_task_id") or "").strip()
        if not task_id:
            continue
        event_type = str(event.get("type") or "").strip()
        entry = tasks.setdefault(task_id, {"task_id": task_id, "status": "unknown", "events": []})
        entry["events"].append(event_type)
        if event_type in ("task_completed", "task_success"):
            entry["status"] = "completed"
        elif event_type in ("task_failed", "task_error"):
            entry["status"] = "failed"
        elif event_type in ("task_blocked",):
            entry["status"] = "blocked"
        elif event_type in ("task_claimed", "task_started") and entry["status"] == "unknown":
            entry["status"] = "claimed"
    return list(tasks.values())


def _extract_missing_delivery_targets(
    *,
    run: FactoryRun,
    status_payload: dict[str, Any],
) -> list[str]:
    """Return declared stages that were never reached or completed."""
    configured_stages = list(run.config.stages) if hasattr(run.config, "stages") else []
    completed = set(run.stages_completed or [])
    failed = set(run.stages_failed or [])
    reached = completed | failed
    return [s for s in configured_stages if s not in reached]


def _build_director_convergence(
    *,
    run: FactoryRun,
    events: list[dict[str, Any]],
    status_payload: dict[str, Any],
    summary_json: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Build director convergence diagnostics when QA did not run.

    Returns None when QA ran successfully (convergence not relevant).
    """
    qa_gate = next(
        (
            g
            for g in (status_payload.get("gates") or [])
            if isinstance(g, dict) and g.get("gate_name") == "quality_gate"
        ),
        None,
    )
    qa_ran = bool(qa_gate and qa_gate.get("passed") is not None)
    status = str(status_payload.get("status") or "").lower()
    if qa_ran and status == "completed":
        return None

    blocking_phase = str(status_payload.get("current_stage") or status_payload.get("phase") or "").strip()
    taskboard = _extract_taskboard_snapshots(events)
    per_binding = _extract_per_binding_task_status(events)
    missing_targets = _extract_missing_delivery_targets(run=run, status_payload=status_payload)

    director_summary = (summary_json or {}).get("director") if isinstance(summary_json, dict) else None

    return {
        "qa_ran": qa_ran,
        "blocking_phase": blocking_phase,
        "taskboard_initial": taskboard["initial"],
        "taskboard_final": taskboard["final"],
        "missing_delivery_targets": missing_targets,
        "per_binding_task_status": per_binding,
        "director_summary": director_summary if isinstance(director_summary, dict) else None,
    }


def _build_factory_audit_bundle(
    *,
    run: FactoryRun,
    events: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    events_tail_limit: int = 100,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    status_payload = _model_dump_json_dict(_map_service_run_to_contract(run))
    summary_json = run.metadata.get("summary_json")
    tail_limit = _safe_events_tail_limit(events_tail_limit)
    events_tail = events[-tail_limit:] if tail_limit > 0 else []
    gates = status_payload.get("gates")
    failure = status_payload.get("failure")

    convergence = _build_director_convergence(
        run=run,
        events=events,
        status_payload=status_payload,
        summary_json=summary_json if isinstance(summary_json, dict) else None,
    )

    result: dict[str, Any] = {
        "run_id": status_payload.get("run_id") or run.id,
        "status": status_payload.get("status"),
        "phase": status_payload.get("phase"),
        "progress": status_payload.get("progress"),
        "current_stage": status_payload.get("current_stage"),
        "last_successful_stage": status_payload.get("last_successful_stage"),
        "gates": gates if isinstance(gates, list) else [],
        "failure": failure if isinstance(failure, dict) else None,
        "events_tail": events_tail,
        "artifacts": artifacts,
        "summary_md": str(run.metadata.get("summary_md") or "").strip() or None,
        "summary_json": summary_json if isinstance(summary_json, dict) else None,
        "generated_at": (generated_at or datetime.now(timezone.utc)).isoformat(),
        "evidence_counts": {
            "events_total": len(events),
            "events_tail": len(events_tail),
            "artifacts": len(artifacts),
            "gates": len(gates) if isinstance(gates, list) else 0,
            "failures": 1 if isinstance(failure, dict) else 0,
            "summary_md": 1 if str(run.metadata.get("summary_md") or "").strip() else 0,
            "summary_json": 1 if isinstance(summary_json, dict) else 0,
            "event_types": _count_events_by_type(events),
        },
    }
    if convergence is not None:
        result["director_convergence"] = convergence
    return result


def _factory_run_identity(*, run: FactoryRun, workspace: str) -> dict[str, Any]:
    start_request = run.metadata.get("factory_start_request")
    start_request_map = start_request if isinstance(start_request, dict) else {}
    start_metadata = start_request_map.get("metadata")
    start_metadata_map = start_metadata if isinstance(start_metadata, dict) else {}
    return {
        "schema_version": "factory.run_identity.v1",
        "run_id": run.id,
        "factory_run_id": run.id,
        "workspace": str(workspace),
        "requested_project_id": str(
            start_metadata_map.get("requested_project_id")
            or start_metadata_map.get("factory_bench_requested_project_id")
            or start_metadata_map.get("factory_bench_project_id")
            or ""
        ),
        "canonical_project_id": str(
            start_metadata_map.get("canonical_project_id")
            or start_metadata_map.get("factory_bench_canonical_project_id")
            or start_metadata_map.get("factory_bench_project_id")
            or ""
        ),
        "instance_id": str(
            start_metadata_map.get("instance_id") or start_metadata_map.get("launcher_instance_id") or ""
        ),
        "backend_port": start_metadata_map.get("backend_port"),
        "frontend_port": start_metadata_map.get("frontend_port"),
    }


def _attach_control_plane_projection(
    *,
    bundle: dict[str, Any],
    run: FactoryRun,
    workspace: str,
) -> None:
    identity = _factory_run_identity(run=run, workspace=workspace)
    bundle["factory_run_id"] = run.id
    bundle["workspace"] = str(workspace)
    bundle["run_identity"] = identity
    try:
        projection = read_run_ledger_projection(
            ReadRunLedgerProjectionQueryV1(workspace=str(workspace), run_id=run.id)
        ).projection
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        bundle["control_plane_projection_error"] = {
            "schema_version": "factory.control_plane_projection_error.v1",
            "code": "CONTROL_PLANE_PROJECTION_UNAVAILABLE",
            "message": str(exc)[:300],
            "exception_type": type(exc).__name__,
        }
        return
    bundle["control_plane_projection"] = projection
    bundle["run_ledger_projection"] = projection


async def _persist_run_summary(
    *,
    service: FactoryRunService,
    run_id: str,
    payload: FactoryStartRequest,
    workspace: str,
    status: str,
) -> None:
    run = await service.get_run(run_id)
    if run is None:
        return
    summary_json = _build_summary_json(run=run, payload=payload, status=status, workspace=workspace)
    run.metadata["summary_json"] = summary_json
    run.metadata["summary_md"] = _build_summary_markdown(summary_json)
    await service.store.save_run(run)


def _classify_factory_failure_code(*, stage: str, detail: str) -> str:
    normalized_detail = str(detail or "").lower()
    if "qa_llm_judgement_unavailable" in normalized_detail:
        return "QA_LLM_JUDGEMENT_UNAVAILABLE"
    if str(stage or "").strip():
        return "FACTORY_STAGE_FAILED"
    return "FACTORY_RUN_EXCEPTION"


def _factory_failure_suggestion(code: str) -> str:
    if code == "QA_LLM_JUDGEMENT_UNAVAILABLE":
        return "Fix QA LLM connectivity or explicitly disable qa_require_llm_judgement for non-audited dry runs."
    return ""


async def _execute_run_with_service(
    service: FactoryRunService,
    run_id: str,
    payload: FactoryStartRequest,
    state: AppState,
) -> None:
    """Execute the configured PM→Chief Engineer→Director factory stages."""
    active_stage = ""
    workspace = str(service.workspace)

    async def _record_quality_rework_request(cycle: int, summary: dict[str, Any]) -> None:
        current_run = await service.get_run(run_id)
        if current_run is None or current_run.status in {ServiceRunStatus.COMPLETED, ServiceRunStatus.CANCELLED}:
            return
        history_raw = current_run.metadata.get("quality_rework_history")
        history: list[Any] = list(history_raw) if isinstance(history_raw, list) else []
        intermediate_failure = (
            dict(current_run.metadata.get("failure")) if isinstance(current_run.metadata.get("failure"), dict) else {}
        )
        entry = {
            "cycle": cycle,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "factory.quality_gate.taskboard_rework",
            "summary": dict(summary),
            "intermediate_failure": intermediate_failure,
        }
        history.append(entry)
        if current_run.status == ServiceRunStatus.FAILED:
            current_run.status = ServiceRunStatus.RUNNING
            current_run.metadata.pop("failure", None)
            if str(current_run.metadata.get("last_failed_stage") or "").strip() == "quality_gate":
                current_run.metadata.pop("last_failed_stage", None)
            current_run.stages_failed = [
                stage for stage in current_run.stages_failed if str(stage or "").strip() != "quality_gate"
            ]
        current_run.metadata["quality_rework_history"] = history[-50:]
        current_run.metadata["quality_rework_last"] = entry
        current_run.metadata["quality_rework_cycles_executed"] = cycle
        await service.store.save_run(current_run)
        await service._append_event(
            run_id,
            {
                "type": "quality_rework_requested",
                "cycle": cycle,
                "summary": dict(summary),
            },
        )

    async def _execute_stage_sequence(
        stage_names: list[str],
        *,
        allow_quality_rework: bool = False,
        quality_rework_cycle: int = 0,
    ) -> StageSequenceStatus:
        nonlocal active_stage
        for stage_name in stage_names:
            active_stage = str(stage_name or "").strip()
            current = await service.get_run(run_id)
            if current is None or current.status in TERMINAL_RUN_STATUSES:
                return "cancelled"

            result = await service.execute_stage(
                run_id,
                active_stage,
                _build_stage_context(active_stage, payload, state, run_id=run_id),
            )
            status_normalized = str(result.status or "").strip().lower()
            if status_normalized == "cancelled":
                logger.info(
                    "Factory run %s cancelled during stage=%s",
                    run_id,
                    active_stage,
                )
                return "cancelled"
            if result.status != "success":
                if allow_quality_rework and active_stage == "quality_gate":
                    task_boundary_rework = _apply_quality_gate_task_boundary_rework_requests(workspace)
                    rework_summary = _read_quality_gate_rework_summary(workspace)
                    if bool(task_boundary_rework.get("evaluated_count")) or bool(task_boundary_rework.get("error")):
                        rework_summary["task_boundary_rework_bridge"] = task_boundary_rework
                    if bool(rework_summary.get("requested")):
                        await _record_quality_rework_request(quality_rework_cycle, rework_summary)
                        return "quality_rework_requested"
                raise RuntimeError(result.output or f"Stage {stage_name} failed")
        return "completed"

    try:
        run = await service.get_run(run_id)
        if run is None:
            return

        configured_stages = [str(stage).strip() for stage in run.config.stages if str(stage).strip()]
        if not configured_stages:
            raise RuntimeError("Factory run has no configured stages")

        execution_stages = _execution_stages_for_run(run, configured_stages)
        quality_rework_max_cycles = _resolve_quality_rework_max_cycles()

        def _quality_rework_stage_names() -> list[str]:
            if "director_dispatch" not in execution_stages or "quality_gate" not in execution_stages:
                raise RuntimeError(
                    "Quality gate requested Director rework, but this run does not include both "
                    "director_dispatch and quality_gate stages"
                )
            return ["director_dispatch", "quality_gate"]

        async def _execute_with_quality_rework(stage_names: list[str]) -> bool:
            quality_rework_cycles = 0
            next_stage_names = list(stage_names)
            while True:
                next_cycle = quality_rework_cycles + 1
                sequence_status = await _execute_stage_sequence(
                    next_stage_names,
                    allow_quality_rework=True,
                    quality_rework_cycle=next_cycle,
                )
                if sequence_status == "completed":
                    return True
                if sequence_status == "cancelled":
                    return False
                quality_rework_cycles = next_cycle
                if quality_rework_cycles > quality_rework_max_cycles:
                    raise RuntimeError(
                        "Quality gate requested rework after exceeding max cycles "
                        f"({quality_rework_max_cycles}); stop to prevent infinite QA loop"
                    )
                next_stage_names = _quality_rework_stage_names()

        loop_requested = bool(payload.loop)
        loop_enabled = loop_requested and ("pm_planning" in execution_stages)
        run.metadata["loop_requested"] = loop_requested
        run.metadata["loop_enabled"] = loop_enabled
        await service.store.save_run(run)

        if loop_enabled:
            pm_index = execution_stages.index("pm_planning")
            prefix_stages = execution_stages[:pm_index]
            iterative_stages: list[str] = []
            terminal_stages: list[str] = []
            for stage_name in execution_stages[pm_index:]:
                if stage_name == "quality_gate":
                    terminal_stages.append(stage_name)
                else:
                    iterative_stages.append(stage_name)

            if not iterative_stages:
                loop_enabled = False
                run.metadata["loop_enabled"] = False
                await service.store.save_run(run)
            else:
                if prefix_stages:
                    sequence_status = await _execute_stage_sequence(prefix_stages)
                    if sequence_status != "completed":
                        return

                max_cycles = _resolve_loop_max_cycles()
                max_stalled_cycles = _resolve_loop_stall_threshold()
                previous_plan_signature = ""
                unchanged_cycles = 0
                cycle = 0

                while True:
                    cycle += 1
                    if cycle > max_cycles:
                        raise RuntimeError(
                            f"Delivery loop exceeded max cycles ({max_cycles}); stop to prevent infinite loop"
                        )

                    sequence_status = await _execute_stage_sequence(iterative_stages)
                    if sequence_status != "completed":
                        return

                    current_run = await service.get_run(run_id)
                    if current_run is None or current_run.status in TERMINAL_RUN_STATUSES:
                        return

                    plan_signature = _read_pm_plan_signature(workspace)
                    docs_state = _read_docs_pipeline_state(workspace)
                    signature_changed = bool(plan_signature) and plan_signature != previous_plan_signature
                    if signature_changed:
                        unchanged_cycles = 0
                    else:
                        unchanged_cycles += 1

                    decision = _decide_delivery_loop_action(
                        plan_signature=plan_signature,
                        previous_plan_signature=previous_plan_signature,
                        unchanged_cycles=unchanged_cycles,
                        docs_state=docs_state,
                        max_stalled_cycles=max_stalled_cycles,
                    )
                    loop_entry = {
                        "cycle": cycle,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "plan_signature": plan_signature,
                        "signature_changed": signature_changed,
                        "unchanged_cycles": unchanged_cycles,
                        "docs_pipeline": docs_state,
                        "decision": decision.get("action"),
                        "decision_reason": decision.get("reason"),
                        "decision_message": decision.get("message"),
                    }
                    history = current_run.metadata.get("loop_history")
                    loop_history = history if isinstance(history, list) else []
                    loop_history.append(loop_entry)

                    current_run.metadata["loop_history"] = loop_history[-100:]
                    current_run.metadata["loop_cycles_executed"] = cycle
                    current_run.metadata["loop_last_plan_signature"] = plan_signature
                    current_run.metadata["loop_last_docs_state"] = docs_state
                    current_run.metadata["loop_last_decision"] = decision.get("reason")
                    if decision.get("action") == "stop":
                        current_run.metadata["loop_stop_reason"] = decision.get("reason")
                    await service.store.save_run(current_run)
                    await service._append_event(
                        run_id,
                        {
                            "type": "delivery_loop_cycle",
                            "cycle": cycle,
                            "plan_signature": plan_signature,
                            "signature_changed": signature_changed,
                            "unchanged_cycles": unchanged_cycles,
                            "docs_pipeline": docs_state,
                            "decision": decision,
                        },
                    )

                    action = str(decision.get("action") or "").strip().lower()
                    if action == "continue":
                        previous_plan_signature = plan_signature
                        continue
                    if action == "fail":
                        current_run.metadata["loop_stop_reason"] = decision.get("reason")
                        await service.store.save_run(current_run)
                        raise RuntimeError(str(decision.get("message") or "Delivery loop failed"))
                    break

                if terminal_stages:
                    completed = await _execute_with_quality_rework(terminal_stages)
                    if not completed:
                        return

        if not loop_enabled:
            completed = await _execute_with_quality_rework(execution_stages)
            if not completed:
                return

        current_run = await service.get_run(run_id)
        if current_run is not None and current_run.status in {ServiceRunStatus.RUNNING, ServiceRunStatus.RECOVERING}:
            await _persist_run_summary(
                service=service,
                run_id=run_id,
                payload=payload,
                workspace=workspace,
                status="PASS",
            )
            await service.complete_run(run_id, success=True)
            logger.info("Factory run %s completed successfully", run_id)
    except (RuntimeError, ValueError) as exc:
        logger.exception(
            "Factory run %s failed at stage=%s: %s",
            run_id,
            active_stage or "<none>",
            exc,
        )
        current_run = await service.get_run(run_id)
        if current_run is not None and current_run.status != ServiceRunStatus.CANCELLED:
            failure_stage = active_stage or str(current_run.metadata.get("current_stage") or "").strip()
            tb = traceback.format_exc(limit=20)
            failure_detail = str(exc)
            failure_code = _classify_factory_failure_code(stage=failure_stage, detail=failure_detail)
            suggested_action = _factory_failure_suggestion(failure_code)
            current_run.metadata["failure"] = {
                "stage": failure_stage or "unknown",
                "code": failure_code,
                "detail": failure_detail,
                "traceback": tb,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            if suggested_action:
                current_run.metadata["failure"]["recoverable"] = True
                current_run.metadata["failure"]["suggested_action"] = suggested_action
            if failure_stage:
                current_run.metadata["last_failed_stage"] = failure_stage
            await service.store.save_run(current_run)
            await _persist_run_summary(
                service=service,
                run_id=run_id,
                payload=payload,
                workspace=workspace,
                status="FAIL",
            )
            await service._append_event(
                run_id,
                {
                    "type": "error",
                    "stage": failure_stage or None,
                    "message": str(exc),
                    "traceback": tb,
                },
            )
            await service.complete_run(run_id, success=False)


def _schedule_factory_run_task(
    service: FactoryRunService,
    run_id: str,
    payload: FactoryStartRequest,
    state: AppState,
) -> Any:
    coro = _execute_run_with_service(service, run_id, payload, state)
    try:
        task: Any = create_task_with_context(coro, name=f"factory-run:{run_id}")
    except BaseException:
        coro.close()
        raise
    if not isinstance(task, asyncio.Task):
        coro.close()
    return task


# ---- Core implementations ----


async def _list_factory_runs_core(
    limit: int,
    offset: int,
    workspace: str | None,
    state: AppState,
) -> FactoryRunList:
    effective_workspace = _resolve_workspace(state, workspace)
    service = _get_service(effective_workspace)
    runs_data = await service.list_runs()
    runs_data.sort(key=lambda item: item.get("created_at", ""), reverse=True)

    items: list[FactoryRunStatusContract] = []
    for run_data in runs_data[offset : offset + limit]:
        run = await service.get_run(run_data["id"])
        if run is not None:
            items.append(_map_service_run_to_contract(run))

    return FactoryRunList(
        runs=items,
        total=len(runs_data),
        page=offset // limit + 1 if limit > 0 else 1,
        page_size=limit,
    )


async def _start_factory_run_core(
    payload: FactoryStartRequest,
    state: AppState,
) -> FactoryRunStatusContract:
    workspace = _resolve_workspace(state, payload.workspace)
    run_state: Any = state
    if payload.persist_workspace:
        state.settings.workspace = Path(workspace)
        try:
            if hasattr(state.settings, "workspace_path"):
                state.settings.workspace_path = workspace
        except (AttributeError, ValueError):
            logger.debug("Factory settings object does not accept workspace_path assignment")
        sync_process_settings_environment(state.settings)
        save_persisted_settings(state.settings)
    else:
        transient_settings = copy.copy(state.settings)
        transient_settings.workspace = Path(workspace)
        try:
            if hasattr(transient_settings, "workspace_path"):
                transient_settings.workspace_path = workspace
        except (AttributeError, ValueError):
            logger.debug("Factory transient settings object does not accept workspace_path assignment")
        run_state = SimpleNamespace(settings=transient_settings)
        logger.info(
            "Factory run using transient workspace without mutating global settings: workspace=%s",
            workspace,
        )
    service = _get_service(workspace)

    start_from = _normalize_start_from(payload.start_from, workspace)
    stages = _build_stage_list(start_from, payload.run_director)
    if start_from == "director_resume":
        _ensure_director_resume_evidence_ready(workspace)
    _ensure_factory_runtime_ready(state, stages)

    config = FactoryConfig(
        name=f"Factory Run - {start_from}",
        description=payload.directive,
        stages=stages,
        auto_dispatch=True,
    )

    run = await service.create_run(config)
    run = await service.start_run(run.id)
    _store_start_request_metadata(run, payload, start_from)
    await _save_service_run(service, run)
    _schedule_factory_run_task(service, run.id, payload, run_state)
    return _map_service_run_to_contract(run)


async def _get_factory_run_status_core(
    run_id: str,
    state: AppState,
    workspace: str | None = None,
) -> FactoryRunStatusContract:
    effective_workspace = _resolve_workspace(state, workspace)
    service = _get_service(effective_workspace)
    run = await service.get_run(run_id)
    if run is None:
        raise StructuredHTTPException(status_code=404, code="RUN_NOT_FOUND", message=f"Run {run_id} not found")
    return _map_service_run_to_contract(run)


async def _get_factory_run_events_core(
    run_id: str,
    limit: int,
    state: AppState,
    workspace: str | None = None,
) -> FactoryRunEventsResponse:
    service = _get_service(_resolve_workspace(state, workspace))
    run = await service.get_run(run_id)
    if run is None:
        raise StructuredHTTPException(status_code=404, code="RUN_NOT_FOUND", message=f"Run {run_id} not found")

    events = await service.get_run_events(run_id)
    return FactoryRunEventsResponse(events=events[-limit:])


async def _get_factory_run_audit_bundle_core(
    run_id: str,
    limit: int,
    state: AppState,
    workspace: str | None = None,
) -> FactoryRunAuditBundleResponse:
    effective_workspace = _resolve_workspace(state, workspace)
    service = _get_service(effective_workspace)
    run = await service.get_run(run_id)
    if run is None:
        raise StructuredHTTPException(status_code=404, code="RUN_NOT_FOUND", message=f"Run {run_id} not found")

    events = await service.get_run_events(run_id)
    artifacts = _merge_artifact_items(
        _list_run_artifacts(service=service, workspace=effective_workspace, run_id=run_id),
        _list_stage_artifacts(workspace=effective_workspace, events=events),
    )
    bundle = _build_factory_audit_bundle(
        run=run,
        events=events,
        artifacts=artifacts,
        events_tail_limit=limit,
    )
    _attach_control_plane_projection(bundle=bundle, run=run, workspace=effective_workspace)
    return FactoryRunAuditBundleResponse(**bundle)


async def _control_factory_run_core(
    run_id: str,
    payload: FactoryControlRequest,
    state: AppState,
    workspace: str | None = None,
) -> FactoryRunStatusContract:
    effective_workspace = _resolve_workspace(state, workspace)
    service = _get_service(effective_workspace)
    run = await service.get_run(run_id)
    if run is None:
        raise StructuredHTTPException(status_code=404, code="RUN_NOT_FOUND", message=f"Run {run_id} not found")

    if payload.action == "cancel":
        return _map_service_run_to_contract(await service.cancel_run(run_id, payload.reason))
    if payload.action == "pause":
        return _map_service_run_to_contract(await service.execute_pause(run_id))
    if payload.action == "resume":
        return _map_service_run_to_contract(await service.execute_resume(run_id))
    if payload.action == "retry_from_checkpoint":
        try:
            recovered = await service.retry_run_from_stage(run_id, None, payload.reason)
        except ValueError as exc:
            raise StructuredHTTPException(status_code=400, code="INVALID_REQUEST", message=str(exc)) from exc
        if recovered.status == ServiceRunStatus.RECOVERING:
            _schedule_factory_run_task(
                service, recovered.id, _build_retry_start_request(recovered, effective_workspace), state
            )
        return _map_service_run_to_contract(recovered)
    if payload.action == "retry_phase":
        retry_stage = _resolve_retry_stage(run, payload.target_phase)
        try:
            recovered = await service.retry_run_from_stage(run_id, retry_stage, payload.reason)
        except ValueError as exc:
            raise StructuredHTTPException(status_code=400, code="INVALID_REQUEST", message=str(exc)) from exc
        if recovered.status == ServiceRunStatus.RECOVERING:
            _schedule_factory_run_task(
                service, recovered.id, _build_retry_start_request(recovered, effective_workspace), state
            )
        return _map_service_run_to_contract(recovered)

    raise StructuredHTTPException(
        status_code=501,
        code="INVALID_REQUEST",
        message=f"Factory action '{payload.action}' is not implemented in this phase",
        details={"supported_actions": ["cancel", "pause", "resume", "retry_from_checkpoint", "retry_phase"]},
    )


async def _get_factory_run_artifacts_core(
    run_id: str,
    state: AppState,
    workspace: str | None = None,
) -> FactoryRunArtifactsResponse:
    effective_workspace = _resolve_workspace(state, workspace)
    service = _get_service(effective_workspace)
    run = await service.get_run(run_id)
    if run is None:
        raise StructuredHTTPException(status_code=404, code="RUN_NOT_FOUND", message=f"Run {run_id} not found")

    events = await service.get_run_events(run_id)
    artifacts = _merge_artifact_items(
        _list_run_artifacts(service=service, workspace=effective_workspace, run_id=run_id),
        _list_stage_artifacts(workspace=effective_workspace, events=events),
    )
    response_data = _build_artifacts_response(run=run, artifacts=artifacts)
    return FactoryRunArtifactsResponse(**response_data)


# ---- v2 routes (canonical) ----


@router.get("/v2/factory/runs", response_model=FactoryRunList)
async def list_factory_runs_v2(
    limit: int = 50,
    offset: int = 0,
    workspace: str | None = None,
    state: AppState = Depends(get_state),
) -> FactoryRunList:
    """List factory runs for the current workspace."""
    return await _list_factory_runs_core(limit=limit, offset=offset, workspace=workspace, state=state)


@router.post("/v2/factory/runs", response_model=FactoryRunStatusContract)
async def start_factory_run_v2(
    payload: FactoryStartRequest,
    state: AppState = Depends(get_state),
) -> FactoryRunStatusContract:
    """Create and start an unattended factory run."""
    return await _start_factory_run_core(payload=payload, state=state)


@router.get("/v2/factory/runs/{run_id}", response_model=FactoryRunStatusContract)
async def get_factory_run_status_v2(
    run_id: str,
    workspace: str | None = None,
    state: AppState = Depends(get_state),
) -> FactoryRunStatusContract:
    """Query run status."""
    return await _get_factory_run_status_core(run_id=run_id, workspace=workspace, state=state)


@router.get("/v2/factory/runs/{run_id}/events", response_model=FactoryRunEventsResponse)
async def get_factory_run_events_v2(
    run_id: str,
    limit: int = 100,
    workspace: str | None = None,
    state: AppState = Depends(get_state),
) -> FactoryRunEventsResponse:
    """Get append-only audit events for a run."""
    return await _get_factory_run_events_core(run_id=run_id, limit=limit, workspace=workspace, state=state)


@router.get("/v2/factory/runs/{run_id}/audit-bundle", response_model=FactoryRunAuditBundleResponse)
async def get_factory_run_audit_bundle_v2(
    run_id: str,
    limit: int = 100,
    workspace: str | None = None,
    state: AppState = Depends(get_state),
) -> FactoryRunAuditBundleResponse:
    """Get a machine-readable audit bundle for a factory run."""
    return await _get_factory_run_audit_bundle_core(run_id=run_id, limit=limit, workspace=workspace, state=state)


@router.post("/v2/factory/runs/{run_id}/control", response_model=FactoryRunStatusContract)
async def control_factory_run_v2(
    run_id: str,
    payload: FactoryControlRequest,
    workspace: str | None = None,
    state: AppState = Depends(get_state),
) -> FactoryRunStatusContract:
    """Control a run. This phase only supports cancel."""
    return await _control_factory_run_core(run_id=run_id, payload=payload, workspace=workspace, state=state)


@router.get("/v2/factory/runs/{run_id}/artifacts", response_model=FactoryRunArtifactsResponse)
async def get_factory_run_artifacts_v2(
    run_id: str,
    workspace: str | None = None,
    state: AppState = Depends(get_state),
) -> FactoryRunArtifactsResponse:
    """List artifact files for a run."""
    return await _get_factory_run_artifacts_core(run_id=run_id, workspace=workspace, state=state)


# =============================================================================
# Factory-bench session endpoints (workspace-agnostic).
#
# These endpoints expose the ``FactoryBenchService`` so the
# ``scripts/factory_bench/run_factory_bench.py`` subprocess (which runs in
# a terminal, not in the backend process) can publish its lifecycle to the
# Factory front-end panel in real time. The bench subprocess posts over HTTP
# (urllib in the bench, FastAPI here) and the front-end subscribes via the
# unified Nats-JetStream WebSocket runtime transport. Failures on either side
# are soft: missing session dir / dropped events are logged, never raised into
# the HTTP response, so a misconfigured bench can never crash the panel.
# =============================================================================


class FactoryBenchStartRequest(BaseModel):
    work_dir: str = Field(..., description="factory-bench work dir (per-project subdirs)")
    project_ids: list[str] = Field(default_factory=list)
    total: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = Field(default=None, description="optional explicit id")


class FactoryBenchStartResponse(BaseModel):
    session_id: str
    status: str


class FactoryBenchEventRequest(BaseModel):
    type: str = Field(..., description="event semantic type, e.g. project.started")
    name: str | None = None
    actor: str | None = None
    summary: str | None = None
    ok: bool | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class FactoryBenchCompleteRequest(BaseModel):
    success: bool = True
    summary: dict[str, Any] = Field(default_factory=dict)


class FactoryBenchProgressRequest(BaseModel):
    completed: int | None = None
    failed: int | None = None


_bench_service = FactoryBenchService()


def _bench_session_event_meta(session_id: str, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the bench session fields every realtime bench event should carry."""
    data = snapshot if snapshot is not None else (_bench_service.get_session(session_id) or {})
    meta = {
        "session_id": data.get("session_id") or session_id,
        "work_dir": data.get("work_dir"),
        "project_ids": data.get("project_ids"),
        "total": data.get("total"),
        "completed": data.get("completed"),
        "failed": data.get("failed"),
        "status": data.get("status"),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "completed_at": data.get("completed_at"),
        "metadata": data.get("metadata"),
    }
    return {key: value for key, value in meta.items() if value is not None}


async def _publish_factory_bench_event_to_jetstream(session_id: str, event: dict[str, Any]) -> bool:
    """Fan out a persisted factory-bench event on the unified runtime stream."""
    try:
        from polaris.infrastructure.messaging.nats.nats_types import (
            create_runtime_event,
        )

        envelope = create_runtime_event(
            workspace_key="bench",
            run_id=session_id,
            # The front-end subscribes to the wildcard ``event.bench`` channel,
            # which the runtime.v2 subject builder maps to
            # ``hp.runtime.bench.>``. Keeping the envelope channel per-session
            # lets consumers still filter or pin a single bench run.
            channel=f"event.bench:{session_id}",
            kind=str(event.get("type") or "bench.event"),
            payload=event,
            meta={"source": "factory_bench_subprocess"},
        )
        # ``publish_to_jetstream`` -> ``client.publish_js`` JSON-serializes
        # the payload (nats-py has no dataclass hook). Hand it a dict.
        return await publish_to_jetstream(
            subject=f"hp.runtime.bench.{session_id}",
            payload=envelope.to_dict(),
        )
    except (RuntimeError, TimeoutError, ValueError, TypeError) as exc:
        # JetStream fanout is best-effort; JSONL/session write already succeeded.
        logger.debug("bench JetStream fanout failed for %s: %s", session_id, exc)
        return False


@router.post("/v2/factory/bench/sessions", response_model=FactoryBenchStartResponse)
async def start_factory_bench_session_v2(
    payload: FactoryBenchStartRequest,
) -> FactoryBenchStartResponse:
    """Register a new bench session (typically called by the bench subprocess)."""
    require_internal_bench_surface()
    sid = _bench_service.register_session(
        work_dir=payload.work_dir,
        project_ids=payload.project_ids,
        total=payload.total or len(payload.project_ids),
        metadata=payload.metadata,
        session_id=payload.session_id,
    )
    snapshot = _bench_service.get_session(sid) or {}
    event = {
        "type": "factory_bench.session.started",
        "actor": "factory-bench",
        "summary": f"Factory bench session started: {sid}",
        "ok": True,
        "meta": _bench_session_event_meta(sid, snapshot),
    }
    if _bench_service.append_event(sid, event):
        await _publish_factory_bench_event_to_jetstream(sid, event)
    return FactoryBenchStartResponse(session_id=sid, status="running")


@router.get("/v2/factory/bench/sessions")
async def list_factory_bench_sessions_v2(
    limit: int = 50,
) -> dict[str, Any]:
    """List recent bench sessions for the Factory panel UI."""
    require_internal_bench_surface()
    sessions = _bench_service.list_sessions(limit=limit)
    return {"total": len(sessions), "sessions": sessions}


@router.get("/v2/factory/bench/sessions/{session_id}")
async def get_factory_bench_session_v2(session_id: str) -> dict[str, Any]:
    """Read a bench session's status + a tail of its recent events."""
    require_internal_bench_surface()
    snapshot = _bench_service.get_session(session_id)
    if snapshot is None:
        raise StructuredHTTPException(
            status_code=404,
            code="BENCH_SESSION_NOT_FOUND",
            message=f"bench session {session_id!r} not found",
        )
    return snapshot


@router.post("/v2/factory/bench/sessions/{session_id}/events")
async def append_factory_bench_event_v2(
    session_id: str,
    payload: FactoryBenchEventRequest,
) -> dict[str, Any]:
    """Append an event to a bench session's event log + fanout via NAT JetStream.

    Two-write path mirroring the platform's runtime event subsystem:
      1. **JSONL** (durable on disk) via ``FactoryBenchService.append_event``,
         so the audit trail survives JetStream outages and the front-end can
         replay the full event history via the standard get-session
         endpoint (``GET /v2/factory/bench/sessions/{id}``).
      2. **NAT JetStream** (best-effort fanout) via ``publish_to_jetstream``,
         with the canonical subject ``hp.runtime.bench.<session_id>``. This
         is the **only** real-time push path — the platform's existing
         ``JetStreamConsumerManager`` / WebSocket pipeline subscribes to
         ``event.bench`` and forwards every envelope to the
         client, the same way it already carries ``log.llm`` /
         ``log.process`` / etc.
    """
    require_internal_bench_surface()
    event: dict[str, Any] = {
        "type": payload.type,
        "name": payload.name,
        "actor": payload.actor,
        "summary": payload.summary,
        "ok": payload.ok,
        "meta": dict(payload.meta),
    }
    # Drop None fields so the JSONL stays compact.
    event = {k: v for k, v in event.items() if v is not None}
    ok = _bench_service.append_event(session_id, event)
    if not ok:
        # The bench may POST events before register, or against a stale id.
        return {"session_id": session_id, "appended": False, "published": False}

    # Best-effort JetStream fanout. The platform's RuntimeEventEnvelope is
    # what every existing consumer already knows how to filter on (channel,
    # kind, workspace_key, run_id); wrapping the bench event in that shape
    # means a front-end subscribing to ``event.bench`` gets the same shape
    # it already gets for ``log.llm`` etc.
    published = await _publish_factory_bench_event_to_jetstream(session_id, event)
    return {"session_id": session_id, "appended": True, "published": published}


@router.post("/v2/factory/bench/sessions/{session_id}/complete")
async def complete_factory_bench_session_v2(
    session_id: str,
    payload: FactoryBenchCompleteRequest,
) -> dict[str, Any]:
    """Mark a bench session complete (or failed)."""
    require_internal_bench_surface()
    ok = _bench_service.complete_session(
        session_id,
        success=payload.success,
        summary=payload.summary,
    )
    published = False
    if ok:
        snapshot = _bench_service.get_session(session_id) or {}
        status = str(snapshot.get("status") or ("completed" if payload.success else "failed"))
        meta: dict[str, Any] = {
            **_bench_session_event_meta(session_id, snapshot),
            "status": status,
            "total": snapshot.get("total"),
            "completed": snapshot.get("completed"),
            "failed": snapshot.get("failed"),
            "completed_at": snapshot.get("completed_at"),
            **dict(payload.summary),
        }
        event = {
            "type": f"factory_bench.run.{status}",
            "actor": "factory-bench",
            "summary": "Factory bench run completed" if payload.success else "Factory bench run failed",
            "ok": payload.success,
            "meta": {k: v for k, v in meta.items() if v is not None},
        }
        _bench_service.append_event(session_id, event)
        published = await _publish_factory_bench_event_to_jetstream(session_id, event)
    return {"session_id": session_id, "updated": ok, "published": published}


@router.post("/v2/factory/bench/sessions/{session_id}/progress")
async def update_factory_bench_progress_v2(
    session_id: str,
    payload: FactoryBenchProgressRequest,
) -> dict[str, Any]:
    """Update per-project counters so the front-end sees live ``X/Y 通过``."""
    require_internal_bench_surface()
    ok = _bench_service.update_progress(
        session_id,
        completed=payload.completed,
        failed=payload.failed,
    )
    published = False
    if ok:
        snapshot = _bench_service.get_session(session_id) or {}
        event = {
            "type": "factory_bench.progress.updated",
            "actor": "factory-bench",
            "summary": "Factory bench progress updated",
            "ok": True,
            "meta": _bench_session_event_meta(session_id, snapshot),
        }
        _bench_service.append_event(session_id, event)
        published = await _publish_factory_bench_event_to_jetstream(session_id, event)
    return {"session_id": session_id, "updated": ok, "published": published}
