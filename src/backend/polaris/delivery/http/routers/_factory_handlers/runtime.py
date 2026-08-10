# ruff: noqa: E402, F403, F405
"""Factory HTTP router helpers — payload builders, cores, bench session utils.

Extracted from factory.py so route registration stays thin. External callers that
historically imported private helpers from factory.py continue to re-export them
from polaris.delivery.http.routers.factory.
"""

from __future__ import annotations

import copy
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from polaris.cells.factory.pipeline.internal.bench_service import (
    FactoryBenchService,
)
from polaris.cells.factory.pipeline.internal.factory_store import FileLockTimeoutError
from polaris.cells.factory.pipeline.public import (
    TERMINAL_RUN_STATUSES,
    FactoryConfig,
    FactoryPipelineError,
    FactoryRun,
    FactoryRunService,
    FactoryRunStatus as ServiceRunStatus,
    RecoverStaleFactoryWorkspaceOwnerCommandV1,
    RecoverStaleFactoryWorkspaceOwnerResultV1,
    recover_stale_factory_workspace_owner,
)
from polaris.cells.factory.pipeline.public.types import (
    FactoryControlRequest,
    FactoryRunList,
    FactoryRunStatus as FactoryRunStatusContract,
    FactoryStartRequest,
)
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.cells.storage.layout.public.service import (
    save_persisted_settings,
    sync_process_settings_environment,
)
from polaris.delivery.http.routers._shared import (
    StructuredHTTPException,
)
from polaris.delivery.http.routers.jetstream_utils import (
    publish_to_jetstream,
)
from polaris.delivery.http.schemas import (
    FactoryRunArtifactsResponse,
    FactoryRunAuditBundleResponse,
    FactoryRunEventsResponse,
)
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from polaris.cells.runtime.state_owner.public.service import AppState

logger = logging.getLogger("polaris.delivery.http.routers.factory")

from .mapping import *
from .stage_ops import *


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
        if await service.get_run(run_id) is None:
            return
        timestamp = datetime.now(timezone.utc).isoformat()

        def apply_quality_rework(current_run: FactoryRun) -> bool:
            if current_run.status in {ServiceRunStatus.COMPLETED, ServiceRunStatus.CANCELLED}:
                return False
            history_raw = current_run.metadata.get("quality_rework_history")
            history: list[Any] = list(history_raw) if isinstance(history_raw, list) else []
            failure_raw = current_run.metadata.get("failure")
            intermediate_failure = dict(failure_raw) if isinstance(failure_raw, dict) else {}
            entry = {
                "cycle": cycle,
                "timestamp": timestamp,
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
            return True

        current_run = await service.apply_automatic_router_mutation(
            run_id,
            operation="quality_rework",
            mutation=apply_quality_rework,
            event={
                "type": "quality_rework_requested",
                "cycle": cycle,
                "summary": dict(summary),
            },
        )
        if current_run.status in {ServiceRunStatus.COMPLETED, ServiceRunStatus.CANCELLED}:
            return
        await service.reconcile_stage_execution_for_reentry(
            run_id,
            operation="quality_rework_reentry",
        )

    async def _record_director_local_rework_request(cycle: int, summary: dict[str, Any]) -> None:
        """Keep PM/CE authority and reopen only unfinished Director tasks."""

        if await service.get_run(run_id) is None:
            return
        timestamp = datetime.now(timezone.utc).isoformat()

        def apply_director_rework(current_run: FactoryRun) -> bool:
            if current_run.status in {ServiceRunStatus.COMPLETED, ServiceRunStatus.CANCELLED}:
                return False
            history_raw = current_run.metadata.get("director_local_rework_history")
            history: list[Any] = list(history_raw) if isinstance(history_raw, list) else []
            failure_raw = current_run.metadata.get("failure")
            intermediate_failure = dict(failure_raw) if isinstance(failure_raw, dict) else {}
            entry = {
                "cycle": cycle,
                "timestamp": timestamp,
                "source": "factory.director_dispatch.local_rework",
                "summary": dict(summary),
                "intermediate_failure": intermediate_failure,
            }
            history.append(entry)
            if current_run.status == ServiceRunStatus.FAILED:
                current_run.status = ServiceRunStatus.RUNNING
                current_run.metadata.pop("failure", None)
            if str(current_run.metadata.get("last_failed_stage") or "").strip() == "director_dispatch":
                current_run.metadata.pop("last_failed_stage", None)
            current_run.stages_failed = [
                stage for stage in current_run.stages_failed if str(stage or "").strip() != "director_dispatch"
            ]
            current_run.metadata["director_local_rework_history"] = history[-20:]
            current_run.metadata["director_local_rework_last"] = entry
            current_run.metadata["director_local_rework_cycles_executed"] = cycle
            return True

        current_run = await service.apply_automatic_router_mutation(
            run_id,
            operation="director_local_rework",
            mutation=apply_director_rework,
            event={
                "type": "director_local_rework_requested",
                "cycle": cycle,
                "summary": dict(summary),
            },
        )
        if current_run.status in {ServiceRunStatus.COMPLETED, ServiceRunStatus.CANCELLED}:
            return
        await service.reconcile_stage_execution_for_reentry(
            run_id,
            operation="director_local_rework_reentry",
        )

    async def _record_chief_engineer_local_rework_request(cycle: int, summary: dict[str, Any]) -> None:
        """Preserve the PM contract and reopen only the failed CE stage."""

        if await service.get_run(run_id) is None:
            return
        timestamp = datetime.now(timezone.utc).isoformat()

        def apply_chief_engineer_rework(current_run: FactoryRun) -> bool:
            if current_run.status in {ServiceRunStatus.COMPLETED, ServiceRunStatus.CANCELLED}:
                return False
            history_raw = current_run.metadata.get("chief_engineer_local_rework_history")
            history: list[Any] = list(history_raw) if isinstance(history_raw, list) else []
            failure_raw = current_run.metadata.get("failure")
            intermediate_failure = dict(failure_raw) if isinstance(failure_raw, dict) else {}
            entry = {
                "cycle": cycle,
                "timestamp": timestamp,
                "source": "factory.chief_engineer_review.local_rework",
                "summary": dict(summary),
                "intermediate_failure": intermediate_failure,
            }
            history.append(entry)
            if current_run.status == ServiceRunStatus.FAILED:
                current_run.status = ServiceRunStatus.RUNNING
                current_run.metadata.pop("failure", None)
            if str(current_run.metadata.get("last_failed_stage") or "").strip() == "chief_engineer_review":
                current_run.metadata.pop("last_failed_stage", None)
            current_run.stages_failed = [
                stage for stage in current_run.stages_failed if str(stage or "").strip() != "chief_engineer_review"
            ]
            current_run.metadata["chief_engineer_local_rework_history"] = history[-20:]
            current_run.metadata["chief_engineer_local_rework_last"] = entry
            current_run.metadata["chief_engineer_local_rework_cycles_executed"] = cycle
            return True

        current_run = await service.apply_automatic_router_mutation(
            run_id,
            operation="chief_engineer_local_rework",
            mutation=apply_chief_engineer_rework,
            event={
                "type": "chief_engineer_local_rework_requested",
                "cycle": cycle,
                "summary": dict(summary),
            },
        )
        if current_run.status in {ServiceRunStatus.COMPLETED, ServiceRunStatus.CANCELLED}:
            return
        await service.reconcile_stage_execution_for_reentry(
            run_id,
            operation="chief_engineer_local_rework_reentry",
        )

    async def _execute_stage_sequence(
        stage_names: list[str],
        *,
        allow_chief_engineer_rework: bool = False,
        chief_engineer_rework_cycle: int = 0,
        allow_director_rework: bool = False,
        director_rework_cycle: int = 0,
        allow_quality_rework: bool = False,
        quality_rework_cycle: int = 0,
    ) -> StageSequenceStatus:
        nonlocal active_stage
        for stage_name in stage_names:
            active_stage = str(stage_name or "").strip()
            current = await service.get_run(run_id)
            if current is None:
                return "cancelled"
            current = await _guard_automatic_router_mutation(
                service=service,
                run_id=run_id,
                current_run=current,
                operation="stage_sequence",
            )
            if current.status in TERMINAL_RUN_STATUSES:
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
                if (
                    allow_chief_engineer_rework
                    and active_stage == "chief_engineer_review"
                    and _stage_local_rework_is_authorized(
                        result,
                        expected_reason="chief_engineer_local_rework_decision_pending",
                    )
                ):
                    rework_summary = {
                        "schema_version": "factory.chief_engineer_local_rework.v1",
                        "cycle": chief_engineer_rework_cycle,
                        "stage_status": str(result.status or ""),
                        "stage_output": str(result.output or "")[:4000],
                        "preserved_pm_contract": True,
                    }
                    payload.metadata["chief_engineer_local_rework_evidence"] = dict(rework_summary)
                    await _record_chief_engineer_local_rework_request(
                        chief_engineer_rework_cycle,
                        rework_summary,
                    )
                    return "chief_engineer_rework_requested"
                if (
                    allow_director_rework
                    and active_stage == "director_dispatch"
                    and _stage_local_rework_is_authorized(
                        result,
                        expected_reason="director_local_rework_decision_pending",
                    )
                ):
                    reset_result = TaskRuntimeService(workspace).reset_task_rows_for_reexecution(
                        source="factory.director_dispatch.local_rework",
                        preserve_completed=True,
                        eligible_external_task_ids=_pm_plan_task_ids(workspace),
                    )
                    reset_files = [
                        str(item or "").strip()
                        for item in reset_result.get("changed_files", [])
                        if str(item or "").strip()
                    ]
                    if bool(reset_result.get("success")) and reset_files:
                        rework_summary = {
                            "schema_version": "factory.director_local_rework.v1",
                            "cycle": director_rework_cycle,
                            "stage_status": str(result.status or ""),
                            "stage_output": str(result.output or "")[:4000],
                            "reset_files": reset_files,
                            "preserved_files": list(reset_result.get("preserved_files", [])),
                            "excluded_files": list(reset_result.get("excluded_files", [])),
                            "eligible_external_task_ids": list(reset_result.get("eligible_external_task_ids") or []),
                        }
                        payload.metadata["director_local_rework_evidence"] = dict(rework_summary)
                        await _record_director_local_rework_request(director_rework_cycle, rework_summary)
                        return "director_rework_requested"
                if (
                    allow_quality_rework
                    and active_stage == "quality_gate"
                    and _stage_local_rework_is_authorized(
                        result,
                        expected_reason="quality_rework_decision_pending",
                    )
                ):
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
        if await service.get_run(run_id) is None:
            return

        def apply_run_configuration(current_run: FactoryRun) -> None:
            configured = [str(stage).strip() for stage in current_run.config.stages if str(stage).strip()]
            if not configured:
                raise RuntimeError("Factory run has no configured stages")
            execution = _execution_stages_for_run(current_run, configured)
            loop_requested_value = bool(payload.loop)
            current_run.metadata["loop_requested"] = loop_requested_value
            current_run.metadata["loop_enabled"] = loop_requested_value and ("pm_planning" in execution)

        run = await service.apply_automatic_router_mutation(
            run_id,
            operation="run_configuration",
            mutation=apply_run_configuration,
        )

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

        async def _execute_with_stage_local_rework(stage_names: list[str]) -> bool:
            chief_engineer_rework_cycles = 0
            director_rework_cycles = 0
            quality_rework_cycles = 0
            next_stage_names = list(stage_names)
            while True:
                next_director_cycle = director_rework_cycles + 1
                next_quality_cycle = quality_rework_cycles + 1
                sequence_status = await _execute_stage_sequence(
                    next_stage_names,
                    allow_chief_engineer_rework=(
                        chief_engineer_rework_cycles < _DEFAULT_CHIEF_ENGINEER_LOCAL_REWORK_MAX_CYCLES
                    ),
                    chief_engineer_rework_cycle=chief_engineer_rework_cycles + 1,
                    allow_director_rework=director_rework_cycles < _DEFAULT_DIRECTOR_LOCAL_REWORK_MAX_CYCLES,
                    director_rework_cycle=next_director_cycle,
                    allow_quality_rework=True,
                    quality_rework_cycle=next_quality_cycle,
                )
                if sequence_status == "completed":
                    return True
                if sequence_status == "cancelled":
                    return False
                if sequence_status == "chief_engineer_rework_requested":
                    chief_engineer_rework_cycles += 1
                    chief_engineer_index = next_stage_names.index("chief_engineer_review")
                    next_stage_names = next_stage_names[chief_engineer_index:]
                    continue
                if sequence_status == "director_rework_requested":
                    director_rework_cycles = next_director_cycle
                    director_index = next_stage_names.index("director_dispatch")
                    next_stage_names = next_stage_names[director_index:]
                    continue
                quality_rework_cycles = next_quality_cycle
                if quality_rework_cycles > quality_rework_max_cycles:
                    raise RuntimeError(
                        "Quality gate requested rework after exceeding max cycles "
                        f"({quality_rework_max_cycles}); stop to prevent infinite QA loop"
                    )
                next_stage_names = _quality_rework_stage_names()

        loop_requested = bool(payload.loop)
        loop_enabled = loop_requested and ("pm_planning" in execution_stages)

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
                run = await service.apply_automatic_router_mutation(
                    run_id,
                    operation="run_configuration",
                    mutation=lambda current_run: current_run.metadata.__setitem__("loop_enabled", False),
                )
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

                    completed = await _execute_with_stage_local_rework(iterative_stages)
                    if not completed:
                        return

                    current_run = await service.get_run(run_id)
                    if current_run is None:
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

                    def apply_delivery_loop(
                        current: FactoryRun,
                        *,
                        entry: dict[str, Any] = loop_entry,
                        cycle_value: int = cycle,
                        signature: str = plan_signature,
                        docs: dict[str, Any] = docs_state,
                        loop_decision: dict[str, Any] = decision,
                    ) -> bool:
                        if current.status in TERMINAL_RUN_STATUSES:
                            return False
                        history = current.metadata.get("loop_history")
                        loop_history = list(history) if isinstance(history, list) else []
                        loop_history.append(entry)
                        current.metadata["loop_history"] = loop_history[-100:]
                        current.metadata["loop_cycles_executed"] = cycle_value
                        current.metadata["loop_last_plan_signature"] = signature
                        current.metadata["loop_last_docs_state"] = docs
                        current.metadata["loop_last_decision"] = loop_decision.get("reason")
                        if loop_decision.get("action") in {"stop", "fail"}:
                            current.metadata["loop_stop_reason"] = loop_decision.get("reason")
                        return True

                    current_run = await service.apply_automatic_router_mutation(
                        run_id,
                        operation="delivery_loop_projection",
                        mutation=apply_delivery_loop,
                        event={
                            "type": "delivery_loop_cycle",
                            "cycle": cycle,
                            "plan_signature": plan_signature,
                            "signature_changed": signature_changed,
                            "unchanged_cycles": unchanged_cycles,
                            "docs_pipeline": docs_state,
                            "decision": decision,
                        },
                    )
                    if current_run.status in TERMINAL_RUN_STATUSES:
                        return

                    action = str(decision.get("action") or "").strip().lower()
                    if action == "continue":
                        previous_plan_signature = plan_signature
                        continue
                    if action == "fail":
                        raise RuntimeError(str(decision.get("message") or "Delivery loop failed"))
                    break

                if terminal_stages:
                    completed = await _execute_with_stage_local_rework(terminal_stages)
                    if not completed:
                        return

        if not loop_enabled:
            completed = await _execute_with_stage_local_rework(execution_stages)
            if not completed:
                return

        current_run = await service.get_run(run_id)
        if current_run is not None:
            current_run = await _guard_automatic_router_mutation(
                service=service,
                run_id=run_id,
                current_run=current_run,
                operation="success_terminalization",
            )
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
    except Exception as exc:
        # Fail-closed: provider/network exceptions (e.g. aiohttp.ClientResponseError
        # on 403 quota) must terminalize the run. Catching only RuntimeError/ValueError
        # left the Factory run stuck in RUNNING with an abandoned stage claim
        # ("Task exception was never retrieved"). CancelledError is BaseException and
        # is intentionally not swallowed here.
        is_stage_quarantine = str(getattr(exc, "code", "")).startswith("factory_stage_")
        if is_stage_quarantine:
            # Stage persistence quarantine stops automatic router mutations, but
            # lease closeout must still run. Quarantine terminalize marks the run
            # FAILED so complete_run/settle can release the workspace lease (R56/R60).
            logger.error(
                "Factory run %s quarantined at stage=%s; stopping further mutations and closing lease: %s",
                run_id,
                active_stage or "<none>",
                exc,
            )
        else:
            logger.exception(
                "Factory run %s failed at stage=%s: %s",
                run_id,
                active_stage or "<none>",
                exc,
            )
        try:
            await service.reconcile_stage_execution_for_reentry(
                run_id,
                operation="factory_failure_terminalization",
            )
        except Exception as settlement_exc:  # noqa: BLE001 — best-effort settlement; still terminalize
            logger.error(
                "Factory run %s failure remains isolated because stage settlement did not reconcile: %s",
                run_id,
                settlement_exc,
            )
            # Still attempt terminalization below; settlement isolation must not
            # leave RUNNING + open stage claim as the durable end state.
        current_run = await service.get_run(run_id)
        if current_run is not None and current_run.status != ServiceRunStatus.CANCELLED:
            failure_stage = active_stage or str(current_run.metadata.get("current_stage") or "").strip()
            tb = traceback.format_exc(limit=20)
            failure_detail = str(exc)
            failure_code = (
                str(getattr(exc, "code", "") or "").strip()
                if is_stage_quarantine
                else _classify_factory_failure_code(stage=failure_stage, detail=failure_detail)
            ) or _classify_factory_failure_code(stage=failure_stage, detail=failure_detail)
            suggested_action = _factory_failure_suggestion(failure_code)
            failure_timestamp = datetime.now(timezone.utc).isoformat()

            def apply_failure(current: FactoryRun) -> bool:
                if current.status == ServiceRunStatus.CANCELLED:
                    return False
                # Preserve quarantine terminalize failure payload when already set.
                existing_failure = current.metadata.get("failure")
                if not (
                    is_stage_quarantine
                    and isinstance(existing_failure, dict)
                    and str(existing_failure.get("code") or "") == "FACTORY_STAGE_QUARANTINED"
                ):
                    current.metadata["failure"] = {
                        "stage": failure_stage or "unknown",
                        "code": failure_code,
                        "detail": failure_detail,
                        "traceback": tb,
                        "timestamp": failure_timestamp,
                    }
                    if suggested_action:
                        current.metadata["failure"]["recoverable"] = True
                        current.metadata["failure"]["suggested_action"] = suggested_action
                if failure_stage:
                    current.metadata["last_failed_stage"] = failure_stage
                return True

            try:
                current_run = await service.apply_automatic_router_mutation(
                    run_id,
                    operation="failure_terminalization",
                    mutation=apply_failure,
                    event={
                        "type": "error",
                        "stage": failure_stage or None,
                        "message": str(exc),
                        "traceback": tb,
                    },
                )
            except Exception as mutation_exc:  # noqa: BLE001 — quarantine may block non-closeout writes
                logger.error(
                    "Factory run %s failure metadata mutation skipped: %s",
                    run_id,
                    mutation_exc,
                )
                current_run = await service.get_run(run_id)
                if current_run is None:
                    return
            if current_run.status == ServiceRunStatus.CANCELLED:
                return
            try:
                await _persist_run_summary(
                    service=service,
                    run_id=run_id,
                    payload=payload,
                    workspace=workspace,
                    status="FAIL",
                )
            except Exception as summary_exc:  # noqa: BLE001 — summary is non-authoritative
                logger.warning(
                    "Factory run %s FAIL summary persistence skipped: %s",
                    run_id,
                    summary_exc,
                )
            # Always drive terminal drain/release — including quarantine paths that
            # previously returned early and left the workspace lease active forever.
            try:
                await service.complete_run(run_id, success=False)
            except Exception as complete_exc:  # noqa: BLE001 — last-chance log; lease may need recovery
                logger.error(
                    "Factory run %s complete_run after failure did not finish: %s",
                    run_id,
                    complete_exc,
                )


def _schedule_factory_run_task(
    service: FactoryRunService,
    run_id: str,
    payload: FactoryStartRequest,
    state: AppState,
) -> Any:
    from polaris.bootstrap.factory_run_driver_runtime import get_factory_run_driver_runtime

    runtime = get_factory_run_driver_runtime()
    if runtime.service is not service:
        raise RuntimeError("factory_run_driver_service_binding_mismatch")
    return runtime.submit(run_id, payload=payload)


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


async def _load_factory_run_for_http(service: FactoryRunService, run_id: str) -> FactoryRun | None:
    """Keep snapshot contention distinct from a genuinely absent run."""

    try:
        return await service.get_run(run_id)
    except FileLockTimeoutError as exc:
        raise StructuredHTTPException(
            status_code=503,
            code="FACTORY_RUN_SNAPSHOT_BUSY",
            message=f"Run {run_id} snapshot is temporarily busy",
        ) from exc


async def _get_factory_run_status_core(
    run_id: str,
    state: AppState,
    workspace: str | None = None,
) -> FactoryRunStatusContract:
    effective_workspace = _resolve_workspace(state, workspace)
    service = _get_service(effective_workspace)
    run = await _load_factory_run_for_http(service, run_id)
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
    run = await _load_factory_run_for_http(service, run_id)
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
    run = await _load_factory_run_for_http(service, run_id)
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
    run = await _load_factory_run_for_http(service, run_id)
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


async def _recover_stale_factory_workspace_owner_core(
    run_id: str,
    payload: RecoverStaleFactoryWorkspaceOwnerCommandV1,
    state: AppState,
) -> RecoverStaleFactoryWorkspaceOwnerResultV1:
    bound_workspace = _resolve_workspace(state)
    requested_workspace = str(Path(payload.workspace).expanduser().resolve())
    if requested_workspace != bound_workspace:
        raise StructuredHTTPException(
            status_code=409,
            code="factory_workspace_binding_mismatch",
            message="Factory stale-owner recovery is bound to the backend workspace",
            details={
                "bound_workspace": bound_workspace,
                "requested_workspace": requested_workspace,
                "run_id": run_id,
            },
        )
    if payload.run_id != run_id:
        raise StructuredHTTPException(
            status_code=409,
            code="factory_run_binding_mismatch",
            message="Factory stale-owner recovery run_id does not match the route",
            details={
                "route_run_id": run_id,
                "command_run_id": payload.run_id,
                "workspace": bound_workspace,
            },
        )

    command = RecoverStaleFactoryWorkspaceOwnerCommandV1(
        workspace=bound_workspace,
        run_id=run_id,
        expected_fencing_token=payload.expected_fencing_token,
        reason=payload.reason,
    )
    try:
        return await recover_stale_factory_workspace_owner(
            command,
            service_factory=_get_service,
        )
    except FactoryPipelineError as exc:
        status_code = 500 if exc.code == "factory_workspace_run_lease_storage_error" else 409
        raise StructuredHTTPException(
            status_code=status_code,
            code=exc.code,
            message=str(exc),
            details=exc.details,
        ) from exc


async def _get_factory_run_artifacts_core(
    run_id: str,
    state: AppState,
    workspace: str | None = None,
) -> FactoryRunArtifactsResponse:
    effective_workspace = _resolve_workspace(state, workspace)
    service = _get_service(effective_workspace)
    run = await _load_factory_run_for_http(service, run_id)
    if run is None:
        raise StructuredHTTPException(status_code=404, code="RUN_NOT_FOUND", message=f"Run {run_id} not found")

    events = await service.get_run_events(run_id)
    artifacts = _merge_artifact_items(
        _list_run_artifacts(service=service, workspace=effective_workspace, run_id=run_id),
        _list_stage_artifacts(workspace=effective_workspace, events=events),
    )
    response_data = _build_artifacts_response(run=run, artifacts=artifacts)
    return FactoryRunArtifactsResponse(**response_data)


# ---- factory-bench session helpers ----

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


__all__ = [
    "FactoryBenchCompleteRequest",
    "FactoryBenchEventRequest",
    "FactoryBenchProgressRequest",
    "FactoryBenchStartRequest",
    "FactoryBenchStartResponse",
    "_bench_service",
    "_bench_session_event_meta",
    "_control_factory_run_core",
    "_execute_run_with_service",
    "_get_factory_run_artifacts_core",
    "_get_factory_run_audit_bundle_core",
    "_get_factory_run_events_core",
    "_get_factory_run_status_core",
    "_list_factory_runs_core",
    "_load_factory_run_for_http",
    "_publish_factory_bench_event_to_jetstream",
    "_recover_stale_factory_workspace_owner_core",
    "_schedule_factory_run_task",
    "_start_factory_run_core",
]
