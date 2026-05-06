"""Factory Router - unattended factory HTTP/SSE adapter."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
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
from polaris.cells.storage.layout.public.service import (
    save_persisted_settings,
    sync_process_settings_environment,
)
from polaris.delivery.http.routers._shared import StructuredHTTPException
from polaris.delivery.http.routers.sse_utils import (
    create_sse_jetstream_consumer,
    sse_jetstream_generator,
)
from polaris.kernelone.constants import DEFAULT_DIRECTOR_MAX_PARALLELISM
from polaris.kernelone.storage import resolve_logical_path
from polaris.kernelone.trace import create_task_with_context

from ._shared import get_state, require_auth

if TYPE_CHECKING:
    from polaris.cells.runtime.state_owner.public.service import AppState

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2/factory", tags=["factory"], dependencies=[Depends(require_auth)])

STAGE_TO_PHASE: dict[str, RunPhase] = {
    "docs_generation": RunPhase.ARCHITECT,
    "pm_planning": RunPhase.PLANNING,
    "director_dispatch": RunPhase.IMPLEMENTATION,
    "quality_gate": RunPhase.QA_GATE,
}

STAGE_TO_ROLE: dict[str, str] = {
    "docs_generation": "architect",
    "pm_planning": "pm",
    "director_dispatch": "director",
    "quality_gate": "qa",
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


def _get_service(workspace: str) -> FactoryRunService:
    """Get a service instance bound to the current workspace."""
    return FactoryRunService(workspace=Path(workspace))


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
    return datetime.fromisoformat(value)


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


def _build_roles(run: FactoryRun, phase: RunPhase) -> dict[str, RoleStatus]:
    current_stage = str(run.metadata.get("current_stage") or "").strip()
    current_role = STAGE_TO_ROLE.get(current_stage)
    failed_stage = str(run.metadata.get("last_failed_stage") or "").strip()
    failed_role = STAGE_TO_ROLE.get(failed_stage)
    completed_roles = {STAGE_TO_ROLE[stage] for stage in run.stages_completed if stage in STAGE_TO_ROLE}

    roles: dict[str, RoleStatus] = {}
    for role_name in ("pm", "architect", "director", "qa"):
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


def _build_gates(run: FactoryRun, phase: RunPhase) -> list[GateResult]:
    if "quality_gate" in run.stages_completed:
        return [
            GateResult(
                gate_name="quality_gate",
                status=GateStatus.PASSED,
                score=100.0,
                passed=True,
                message="Quality gate passed",
                details={},
                artifacts=["runtime/qa/report.json"],
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

    return FailureInfo(
        failure_type=failure_type,
        code=str(raw_failure.get("code") or "FACTORY_FAILED"),
        detail="Factory run failed",
        phase=phase,
        timestamp=_parse_datetime(str(timestamp)) or datetime.now(timezone.utc),
        recoverable=recoverable,
        suggested_action=str(raw_failure.get("suggested_action") or "").strip() or None,
        hops=[],
    )


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


def _normalize_start_from(start_from: str, workspace: str) -> str:
    normalized = str(start_from or "auto").strip().lower()
    if normalized not in {"auto", "architect", "pm", "director"}:
        normalized = "auto"
    if normalized != "auto":
        return normalized
    return "architect" if not _check_docs_ready(workspace) else "pm"


def _build_stage_list(start_from: str, run_director: bool) -> list[str]:
    normalized = str(start_from or "auto").strip().lower()
    if normalized == "architect":
        return [
            "docs_generation",
            "pm_planning",
            *(["director_dispatch"] if run_director else []),
            "quality_gate",
        ]
    if normalized == "pm":
        return [
            "pm_planning",
            *(["director_dispatch"] if run_director else []),
            "quality_gate",
        ]
    if normalized == "director":
        return [
            *(["director_dispatch"] if run_director else []),
            "quality_gate",
        ]
    # fallback：auto 已在 _normalize_start_from 归一化，这里保守回退到 pm->qa
    return [
        "pm_planning",
        *(["director_dispatch"] if run_director else []),
        "quality_gate",
    ]


def _build_stage_context(stage: str, payload: FactoryStartRequest, state: AppState) -> dict[str, Any]:
    context: dict[str, Any] = {
        "settings": getattr(state, "settings", None),
    }
    if stage in {"docs_generation", "pm_planning"}:
        context["directive"] = payload.directive
    if stage == "director_dispatch":
        context["execution_mode"] = getattr(state.settings, "director_execution_mode", "parallel")
        context["max_workers"] = getattr(
            state.settings, "director_max_parallel_tasks", DEFAULT_DIRECTOR_MAX_PARALLELISM
        )
        context["director_max_rounds"] = int(payload.director_iterations)
    if stage == "quality_gate":
        context["qa_target"] = payload.directive or "Quality gate"
    return context


def _json_payload(data: Any) -> str:
    payload = data.model_dump(mode="json") if hasattr(data, "model_dump") else data
    return json.dumps(payload, ensure_ascii=False)


def _resolve_runtime_path(workspace: str, relative_path: str) -> Path:
    rel = str(relative_path or "").replace("\\", "/").strip().lstrip("/")
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
            "message": "Architect docs pipeline incomplete; continue PM->Director loop",
        }

    if signature_changed:
        return {
            "action": "continue",
            "reason": "plan_signature_changed",
            "message": "PM produced new task contract; continue PM->Director loop",
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
        artifacts.append(
            {
                "name": artifact_path.name,
                "path": _artifact_response_path(artifact_path, workspace),
                "size": artifact_path.stat().st_size,
            }
        )

    return artifacts


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

    return {
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


async def _execute_run_with_service(
    service: FactoryRunService,
    run_id: str,
    payload: FactoryStartRequest,
    state: AppState,
) -> None:
    """Execute the configured factory stages with optional PM->Director delivery loop."""
    active_stage = ""
    workspace = str(service.workspace)

    async def _execute_stage_sequence(stage_names: list[str]) -> bool:
        nonlocal active_stage
        for stage_name in stage_names:
            active_stage = str(stage_name or "").strip()
            current = await service.get_run(run_id)
            if current is None or current.status in TERMINAL_RUN_STATUSES:
                return False

            result = await service.execute_stage(
                run_id,
                active_stage,
                _build_stage_context(active_stage, payload, state),
            )
            status_normalized = str(result.status or "").strip().lower()
            if status_normalized == "cancelled":
                logger.info(
                    "Factory run %s cancelled during stage=%s",
                    run_id,
                    active_stage,
                )
                return False
            if result.status != "success":
                raise RuntimeError(result.output or f"Stage {stage_name} failed")
        return True

    try:
        run = await service.get_run(run_id)
        if run is None:
            return

        configured_stages = list(run.config.stages or [])
        loop_requested = bool(payload.loop)
        loop_enabled = loop_requested and ("pm_planning" in configured_stages)
        run.metadata["loop_requested"] = loop_requested
        run.metadata["loop_enabled"] = loop_enabled
        await service.store.save_run(run)

        if not configured_stages:
            raise RuntimeError("Factory run has no configured stages")

        if loop_enabled:
            pm_index = configured_stages.index("pm_planning")
            prefix_stages = configured_stages[:pm_index]
            iterative_stages: list[str] = []
            terminal_stages: list[str] = []
            for stage_name in configured_stages[pm_index:]:
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
                    completed = await _execute_stage_sequence(prefix_stages)
                    if not completed:
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

                    completed = await _execute_stage_sequence(iterative_stages)
                    if not completed:
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
                    completed = await _execute_stage_sequence(terminal_stages)
                    if not completed:
                        return

        if not loop_enabled:
            completed = await _execute_stage_sequence(configured_stages)
            if not completed:
                return

        current_run = await service.get_run(run_id)
        if current_run is not None and current_run.status == ServiceRunStatus.RUNNING:
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
            current_run.metadata["failure"] = {
                "stage": failure_stage or "unknown",
                "code": "FACTORY_RUN_EXCEPTION",
                "detail": str(exc),
                "traceback": tb,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
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


@router.get("/runs")
async def list_factory_runs(
    limit: int = 50,
    offset: int = 0,
    workspace: str | None = None,
    state: AppState = Depends(get_state),
) -> FactoryRunList:
    """List factory runs for the current workspace."""
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


@router.post("/runs")
async def start_factory_run(
    payload: FactoryStartRequest,
    state: AppState = Depends(get_state),
) -> FactoryRunStatusContract:
    """Create and start an unattended factory run."""
    workspace = _resolve_workspace(state, payload.workspace)
    state.settings.workspace = Path(workspace)
    sync_process_settings_environment(state.settings)
    save_persisted_settings(state.settings)
    service = _get_service(workspace)

    start_from = _normalize_start_from(payload.start_from, workspace)
    stages = _build_stage_list(start_from, payload.run_director)

    config = FactoryConfig(
        name=f"Factory Run - {start_from}",
        description=payload.directive,
        stages=stages,
        auto_dispatch=True,
    )

    run = await service.create_run(config)
    run = await service.start_run(run.id)
    create_task_with_context(_execute_run_with_service(service, run.id, payload, state))
    return _map_service_run_to_contract(run)


@router.get("/runs/{run_id}")
async def get_factory_run_status(
    run_id: str,
    state: AppState = Depends(get_state),
) -> FactoryRunStatusContract:
    """Query run status."""
    service = _get_service(_resolve_workspace(state))
    run = await service.get_run(run_id)
    if run is None:
        raise StructuredHTTPException(status_code=404, code="RUN_NOT_FOUND", message=f"Run {run_id} not found")
    return _map_service_run_to_contract(run)


@router.get("/runs/{run_id}/events")
async def get_factory_run_events(
    run_id: str,
    limit: int = 100,
    state: AppState = Depends(get_state),
) -> list[dict[str, Any]]:
    """Get append-only audit events for a run."""
    service = _get_service(_resolve_workspace(state))
    run = await service.get_run(run_id)
    if run is None:
        raise StructuredHTTPException(status_code=404, code="RUN_NOT_FOUND", message=f"Run {run_id} not found")

    events = await service.get_run_events(run_id)
    return events[-limit:]


@router.get("/runs/{run_id}/audit-bundle")
async def get_factory_run_audit_bundle(
    run_id: str,
    limit: int = 100,
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    """Get a machine-readable audit bundle for a factory run."""
    workspace = _resolve_workspace(state)
    service = _get_service(workspace)
    run = await service.get_run(run_id)
    if run is None:
        raise StructuredHTTPException(status_code=404, code="RUN_NOT_FOUND", message=f"Run {run_id} not found")

    events = await service.get_run_events(run_id)
    artifacts = _list_run_artifacts(service=service, workspace=workspace, run_id=run_id)
    return _build_factory_audit_bundle(
        run=run,
        events=events,
        artifacts=artifacts,
        events_tail_limit=limit,
    )


@router.get("/runs/{run_id}/stream")
async def stream_factory_run_events(
    run_id: str,
    state: AppState = Depends(get_state),
) -> StreamingResponse:
    """Stream canonical factory status and audit events via SSE."""
    workspace = _resolve_workspace(state)
    workspace_key = Path(workspace).name

    # Build JetStream subject for factory events
    subject = f"hp.runtime.{workspace_key}.event.factory.{run_id}"

    # Try JetStream consumer first
    try:
        consumer = create_sse_jetstream_consumer(
            workspace_key=workspace_key,
            subject=subject,
            last_event_id=0,
        )

        if consumer.is_connected or await consumer.connect():
            logger.info(f"Using JetStream consumer for factory stream: {subject}")
            return StreamingResponse(
                sse_jetstream_generator(consumer),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
    except (RuntimeError, ValueError) as e:
        logger.warning(f"JetStream consumer failed, falling back to direct mode: {e}")

    # Fallback to direct polling mode (original implementation)
    service = _get_service(workspace)
    run = await service.get_run(run_id)
    if run is None:
        raise StructuredHTTPException(status_code=404, code="RUN_NOT_FOUND", message=f"Run {run_id} not found")

    async def event_generator():
        last_event_count = 0
        last_status_payload = ""

        while True:
            current_run = await service.get_run(run_id)
            if current_run is None:
                yield 'event: error\ndata: {"error":"run_not_found"}\n\n'
                return

            snapshot = _map_service_run_to_contract(current_run)
            snapshot_payload = _json_payload(snapshot)
            if snapshot_payload != last_status_payload:
                last_status_payload = snapshot_payload
                yield f"event: status\ndata: {snapshot_payload}\n\n"

            events = await service.get_run_events(run_id)
            while last_event_count < len(events):
                event = events[last_event_count]
                yield f"event: event\ndata: {_json_payload(event)}\n\n"
                last_event_count += 1

            if current_run.status in TERMINAL_RUN_STATUSES:
                yield f"event: complete\ndata: {snapshot_payload}\n\n"
                return

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/runs/{run_id}/control")
async def control_factory_run(
    run_id: str,
    payload: FactoryControlRequest,
    state: AppState = Depends(get_state),
) -> FactoryRunStatusContract:
    """Control a run. This phase only supports cancel."""
    service = _get_service(_resolve_workspace(state))
    run = await service.get_run(run_id)
    if run is None:
        raise StructuredHTTPException(status_code=404, code="RUN_NOT_FOUND", message=f"Run {run_id} not found")

    if payload.action == "cancel":
        return _map_service_run_to_contract(await service.cancel_run(run_id, payload.reason))

    raise StructuredHTTPException(
        status_code=501,
        code="INVALID_REQUEST",
        message=f"Factory action '{payload.action}' is not implemented in this phase",
        details={"supported_actions": ["cancel"]},
    )


@router.get("/runs/{run_id}/artifacts")
async def get_factory_run_artifacts(
    run_id: str,
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    """List artifact files for a run."""
    workspace = _resolve_workspace(state)
    service = _get_service(workspace)
    run = await service.get_run(run_id)
    if run is None:
        raise StructuredHTTPException(status_code=404, code="RUN_NOT_FOUND", message=f"Run {run_id} not found")

    artifacts = _list_run_artifacts(service=service, workspace=workspace, run_id=run_id)
    return _build_artifacts_response(run=run, artifacts=artifacts)
