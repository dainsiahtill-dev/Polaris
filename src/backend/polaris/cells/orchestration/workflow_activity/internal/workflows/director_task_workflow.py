"""Director task child workflow for one task's 4-phase execution.

Migrated from:
  polaris/cells/orchestration/workflow_runtime/internal/runtime_engine/workflows/director_task_workflow.py

ACGA 2.0: This module is Cell-local and must NOT be imported by other Cells
without going through the public contract.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from polaris.cells.orchestration.workflow_activity.internal.activities.base import (
    ActivityExecutionResult,
    register_activity,
)
from polaris.cells.orchestration.workflow_activity.internal.embedded_api import get_workflow_api
from polaris.cells.orchestration.workflow_activity.internal.models import DirectorTaskInput, DirectorTaskResult
from polaris.cells.orchestration.workflow_activity.internal.runtime_queries import WorkflowQueryState
from polaris.cells.orchestration.workflow_activity.internal.task_trace import TaskTraceBuilder
from polaris.cells.orchestration.workflow_activity.internal.workflow_client import get_activity_api
from polaris.infrastructure.di.container import get_container
from polaris.kernelone.events.message_bus import MessageBus, MessageType
from polaris.kernelone.traceability.internal.safety import safe_register_node
from polaris.kernelone.traceability.public.service import create_traceability_service

logger = logging.getLogger(__name__)


def _create_rollback_guard(workspace: str) -> Any:
    from polaris.cells.chief_engineer.blueprint.public.service import (
        create_rollback_guard,
    )

    return create_rollback_guard(workspace, director_pool_mode=True)


async def _snapshot_files(rollback_guard: Any, director_id: str, files: list[str]) -> None:
    try:
        await rollback_guard.snapshot_for_director(director_id, files)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Rollback snapshot failed for %s: %s", director_id, exc)


async def _rollback_director(rollback_guard: Any, director_id: str) -> None:
    try:
        await rollback_guard.rollback_director(director_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Rollback failed for %s: %s", director_id, exc)


def _discard_snapshot(rollback_guard: Any, director_id: str) -> None:
    try:
        rollback_guard.discard_snapshot(director_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Discard snapshot failed for %s: %s", director_id, exc)


workflow = get_workflow_api()
activity = get_activity_api()

_DEFAULT_PHASES = ("prepare", "validate", "implement", "verify", "report")


def _activity_result(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return {
            "success": bool(payload.get("success")),
            "summary": str(payload.get("summary") or "").strip(),
            "payload": payload.get("payload") if isinstance(payload.get("payload"), dict) else {},
            "errors": payload.get("errors") if isinstance(payload.get("errors"), list) else [],
        }
    return {"success": False, "summary": "", "payload": {}, "errors": []}


def _director_config(metadata: Any) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    value = metadata.get("director_config")
    if isinstance(value, dict):
        return value
    return {}


def _timeout_seconds(value: Any, default: int) -> int:
    try:
        return max(1, int(value))
    except (RuntimeError, ValueError):
        return max(1, int(default))


def _normalize_file_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return normalized


def _task_progress_metadata(
    *,
    phase: str,
    phase_index: int,
    phase_total: int,
    retry_count: int,
    max_retries: int,
    completed_phases: list[str],
    phase_context: dict[str, Any] | None = None,
    phase_payload: dict[str, Any] | None = None,
    retry_phase: str = "",
    status_note: str = "",
) -> dict[str, Any]:
    payload = phase_payload if isinstance(phase_payload, dict) else {}
    context = phase_context if isinstance(phase_context, dict) else {}
    changed_files = _normalize_file_list(payload.get("changed_files") or context.get("changed_files"))
    current_file = str(
        payload.get("current_file") or payload.get("current_file_path") or (changed_files[-1] if changed_files else "")
    ).strip()
    files_modified = payload.get("files_modified")
    if files_modified is None:
        files_modified_count = len(changed_files)
    else:
        try:
            files_modified_count = max(0, int(files_modified))
        except (RuntimeError, ValueError):
            files_modified_count = len(changed_files)

    metadata: dict[str, Any] = {
        "phase": str(phase or "").strip().lower(),
        "phase_index": max(1, int(phase_index)),
        "phase_total": max(1, int(phase_total)),
        "retry_count": max(0, int(retry_count)),
        "max_retries": max(0, int(max_retries)),
        "completed_phases": list(completed_phases),
        "files_modified": max(files_modified_count, len(changed_files)),
    }
    if changed_files:
        metadata["changed_files"] = changed_files
    if current_file:
        metadata["current_file"] = current_file
    if retry_phase:
        metadata["retry_phase"] = str(retry_phase).strip().lower()
    if status_note:
        metadata["status_note"] = str(status_note).strip().lower()
    return metadata


@register_activity("register_traceability_commit")
@activity.defn(name="register_traceability_commit")  # type: ignore[untyped-decorator]
async def _register_traceability_commit_activity(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Activity that registers a traceability commit node."""
    task_id = str(payload.get("task_id") or "").strip()
    changed_files = payload.get("changed_files") or []
    run_id = str(payload.get("run_id") or "").strip()
    workspace = str(payload.get("workspace") or "").strip()

    if not task_id or not run_id or not workspace:
        return ActivityExecutionResult(
            success=False,
            summary="Missing required traceability fields",
            errors=["invalid_traceability_payload"],
        ).to_dict()

    try:
        trace_service = create_traceability_service(workspace)
        content = "\n".join(str(f) for f in changed_files) if changed_files else ""
        safe_register_node(
            trace_service,
            node_kind="commit",
            role="director",
            external_id=f"{run_id}-{task_id}",
            content=content,
            metadata={
                "task_id": task_id,
                "run_id": run_id,
                "changed_files": list(changed_files),
                "workspace": workspace,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Traceability commit registration failed for %s: %s", task_id, exc)
        return ActivityExecutionResult(
            success=False,
            summary=str(exc),
            errors=["traceability_commit_failed"],
        ).to_dict()

    return ActivityExecutionResult(
        success=True,
        summary="Traceability commit node registered",
        payload={"task_id": task_id, "run_id": run_id},
    ).to_dict()


async def _broadcast_task_progress(
    task_id: str,
    phase: str,
    phase_index: int,
    phase_total: int,
    retry_count: int = 0,
    max_retries: int = 0,
    current_file: str = "",
) -> None:
    """Broadcast task progress update to frontend via message bus."""
    try:
        container = await get_container()
        message_bus = await container.resolve_async(MessageBus)
        if message_bus:
            await message_bus.broadcast(
                MessageType.TASK_PROGRESS,
                "director_task_workflow",
                {
                    "task_id": task_id,
                    "phase": phase,
                    "phase_index": phase_index,
                    "phase_total": phase_total,
                    "retry_count": retry_count,
                    "max_retries": max_retries,
                    "current_file": current_file,
                },
            )
    except (RuntimeError, ValueError) as e:
        logger.debug("Broadcast failed for task %s: %s", task_id, e)


@workflow.defn
class DirectorTaskWorkflow(WorkflowQueryState):
    """Drive the 4-phase Director execution contract for one task."""

    def __init__(self) -> None:
        super().__init__()
        self.run_id: str = ""
        self.task_id: str = ""

    async def _broadcast_task_trace(
        self,
        phase: str,
        step_kind: str,
        step_title: str,
        step_detail: str,
        status: str,
        attempt: int = 0,
        **refs: Any,
    ) -> None:
        """Broadcast task trace event to frontend."""
        try:
            builder = TaskTraceBuilder(
                run_id=self.run_id,
                role="director",
                task_id=self.task_id,
            )
            event = builder.build(
                phase=phase,
                step_kind=step_kind,
                step_title=step_title,
                step_detail=step_detail,
                status=status,
                attempt=attempt,
                **refs,
            )
            payload = builder.to_ws_payload(event)
            container = await get_container()
            message_bus = await container.resolve_async(MessageBus)
            if message_bus:
                await message_bus.broadcast(
                    MessageType.TASK_TRACE,
                    "director_task_workflow",
                    payload,
                )
        except (RuntimeError, ValueError) as e:
            logger.debug("Trace broadcast failed: %s", e)

    @workflow.run  # type: ignore[untyped-decorator]
    async def run(self, workflow_input: DirectorTaskInput) -> DirectorTaskResult:
        task = workflow_input.task
        task_id = str(task.task_id or "").strip()
        self.run_id = str(workflow_input.run_id or "").strip()
        self.task_id = task_id
        director_config = _director_config(workflow_input.metadata)
        claim_timeout_seconds = _timeout_seconds(
            director_config.get("claim_timeout_seconds"),
            30,
        )
        phase_timeout_seconds = _timeout_seconds(
            director_config.get("phase_timeout_seconds"),
            900,
        )
        complete_timeout_seconds = _timeout_seconds(
            director_config.get("complete_timeout_seconds"),
            30,
        )
        self._record_event(
            stage="director_task_started",
            message=f"Director task workflow started for {task_id}",
            details={"task_id": task_id, "title": task.title},
        )
        self._set_task_status(
            task_id,
            "queued",
            summary="Queued for execution",
            metadata={
                "phase": "queued",
                "phase_index": 0,
                "phase_total": len(_DEFAULT_PHASES),
                "retry_count": 0,
                "max_retries": 0,
                "files_modified": 0,
            },
        )

        claim_result = _activity_result(
            await workflow.execute_activity(
                "claim_task",
                task,
                start_to_close_timeout=timedelta(seconds=claim_timeout_seconds),
            )
        )
        if not claim_result["success"]:
            error = "Task claim rejected by Director activity"
            self._set_task_status(task_id, "failed", summary=error, metadata=claim_result["payload"])
            self._record_event(
                stage="director_task_failed",
                message=error,
                details={"task_id": task_id},
            )
            return DirectorTaskResult(task_id=task_id, status="failed", errors=[error])

        rollback_guard = _create_rollback_guard(str(workflow_input.workspace or ""))
        target_files = _normalize_file_list(
            task.payload.get("target_files") if isinstance(task.payload, dict) else None
        )
        director_id = task_id
        await _snapshot_files(rollback_guard, director_id, target_files)

        completed_phases: list[str] = []
        phase_context: dict[str, Any] = {}
        phases = list(workflow_input.phases or _DEFAULT_PHASES)
        phase_index = 0
        max_retries = max(
            0,
            int(
                workflow_input.metadata.get("max_verification_retries", 2)
                if isinstance(workflow_input.metadata, dict)
                else 2
            ),
        )
        retry_count = 0
        workflow_failed = False
        while phase_index < len(phases):
            phase = phases[phase_index]
            metadata = _task_progress_metadata(
                phase=phase,
                phase_index=phase_index + 1,
                phase_total=len(phases),
                retry_count=retry_count,
                max_retries=max_retries,
                completed_phases=completed_phases,
                phase_context=phase_context,
                status_note="phase_started",
            )
            self._set_task_status(
                task_id,
                "running",
                summary=f"Executing phase {phase}",
                metadata=metadata,
            )
            await _broadcast_task_progress(
                task_id=task_id,
                phase=phase,
                phase_index=phase_index + 1,
                phase_total=len(phases),
                retry_count=retry_count,
                max_retries=max_retries,
                current_file=metadata.get("current_file", ""),
            )
            await self._broadcast_task_trace(
                phase=phase,
                step_kind="phase_start",
                step_title=f"Phase {phase} started",
                step_detail=f"Executing phase {phase} ({phase_index + 1}/{len(phases)})",
                status="running",
                attempt=retry_count,
            )
            phase_result = _activity_result(
                await workflow.execute_activity(
                    "execute_task_phase",
                    {
                        "phase": phase,
                        "task_id": task_id,
                        "task": task.to_dict(),
                        "workspace": workflow_input.workspace,
                        "run_id": workflow_input.run_id,
                        "director_config": (
                            workflow_input.metadata.get("director_config", {})
                            if isinstance(workflow_input.metadata, dict)
                            else {}
                        ),
                        "runtime_metadata": (
                            workflow_input.metadata if isinstance(workflow_input.metadata, dict) else {}
                        ),
                        "context": phase_context,
                    },
                    start_to_close_timeout=timedelta(seconds=phase_timeout_seconds),
                )
            )
            phase_payload = phase_result["payload"] if isinstance(phase_result["payload"], dict) else {}
            if isinstance(phase_payload.get("context"), dict):
                phase_context_update: dict[str, Any] = phase_payload.get("context")  # type: ignore[assignment]
                phase_context = phase_context_update
            if not phase_result["success"]:
                retry_phase = str(phase_payload.get("retry_phase") or "").strip().lower()
                if retry_phase and retry_phase != phase and retry_count < max_retries:
                    retry_count += 1
                    retry_metadata = _task_progress_metadata(
                        phase=phase,
                        phase_index=phase_index + 1,
                        phase_total=len(phases),
                        retry_count=retry_count,
                        max_retries=max_retries,
                        completed_phases=completed_phases,
                        phase_context=phase_context,
                        phase_payload=phase_payload,
                        retry_phase=retry_phase,
                        status_note="retrying",
                    )
                    self._set_task_status(
                        task_id,
                        "running",
                        summary=f"Retrying phase {retry_phase} ({retry_count}/{max_retries})",
                        metadata=retry_metadata,
                    )
                    await _broadcast_task_progress(
                        task_id=task_id,
                        phase=retry_phase,
                        phase_index=phase_index + 1,
                        phase_total=len(phases),
                        retry_count=retry_count,
                        max_retries=max_retries,
                        current_file=retry_metadata.get("current_file", ""),
                    )
                    await self._broadcast_task_trace(
                        phase=phase,
                        step_kind="retry",
                        step_title=f"Retrying phase {retry_phase}",
                        step_detail=(
                            f"Retry {retry_count}/{max_retries} for phase {retry_phase} after failure in {phase}"
                        ),
                        status="retrying",
                        attempt=retry_count,
                        retry_phase=retry_phase,
                    )
                    self._record_event(
                        stage="director_task_retry",
                        message=f"Retrying task {task_id} after phase {phase}",
                        details={
                            "task_id": task_id,
                            "phase": phase,
                            "retry_phase": retry_phase,
                            "retry_count": retry_count,
                        },
                    )
                    phases = [*phases[:phase_index], retry_phase, "verify", *phases[phase_index + 1 :]]
                    continue
                elif retry_phase == phase:
                    self._record_event(
                        stage="director_task_retry_skipped",
                        message=(f"Skipping retry for task {task_id}: retry_phase equals current phase {phase}"),
                        details={"task_id": task_id, "phase": phase},
                    )
                error = str(phase_result["summary"] or f"Phase {phase} failed").strip()
                failed_metadata = _task_progress_metadata(
                    phase=phase,
                    phase_index=phase_index + 1,
                    phase_total=len(phases),
                    retry_count=retry_count,
                    max_retries=max_retries,
                    completed_phases=completed_phases,
                    phase_context=phase_context,
                    phase_payload=phase_payload,
                    status_note="failed",
                )
                failed_metadata["context"] = phase_context
                self._set_task_status(
                    task_id,
                    "failed",
                    summary=error,
                    metadata=failed_metadata,
                )
                await self._broadcast_task_trace(
                    phase=phase,
                    step_kind="failure",
                    step_title=f"Phase {phase} failed",
                    step_detail=error[:200] if error else "",
                    status="failed",
                    attempt=retry_count,
                )
                workflow_failed = True
                self._record_event(
                    stage="director_task_failed",
                    message=error,
                    details={"task_id": task_id, "phase": phase},
                )
                break
            completed_phases.append(phase)
            await self._broadcast_task_trace(
                phase=phase,
                step_kind="phase_complete",
                step_title=f"Phase {phase} completed",
                step_detail=f"Phase {phase} completed successfully",
                status="completed",
                attempt=retry_count,
            )
            self._set_task_status(
                task_id,
                "running",
                summary=f"Phase {phase} completed",
                metadata=_task_progress_metadata(
                    phase=phase,
                    phase_index=phase_index + 1,
                    phase_total=len(phases),
                    retry_count=retry_count,
                    max_retries=max_retries,
                    completed_phases=completed_phases,
                    phase_context=phase_context,
                    phase_payload=phase_payload,
                    status_note="phase_completed",
                ),
            )
            phase_index += 1

        if workflow_failed:
            await _rollback_director(rollback_guard, director_id)
            return DirectorTaskResult(
                task_id=task_id,
                status="failed",
                completed_phases=completed_phases,
                errors=[error],
                metadata=failed_metadata,
            )

        await workflow.execute_activity(
            "complete_task",
            task,
            start_to_close_timeout=timedelta(seconds=complete_timeout_seconds),
        )
        completed_metadata = _task_progress_metadata(
            phase="report",
            phase_index=len(phases),
            phase_total=len(phases),
            retry_count=retry_count,
            max_retries=max_retries,
            completed_phases=completed_phases,
            phase_context=phase_context,
            status_note="completed",
        )
        completed_metadata["context"] = phase_context
        changed_files = _normalize_file_list(
            completed_metadata.get("changed_files") or phase_context.get("changed_files")
        )
        try:
            await workflow.execute_activity(
                "register_traceability_commit",
                {
                    "task_id": task_id,
                    "changed_files": changed_files,
                    "run_id": self.run_id,
                    "workspace": workflow_input.workspace,
                },
                start_to_close_timeout=timedelta(seconds=30),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Traceability activity failed for task %s: %s", task_id, exc)
        _discard_snapshot(rollback_guard, director_id)
        self._set_task_status(
            task_id,
            "completed",
            summary="Director task completed",
            metadata=completed_metadata,
        )
        self._record_event(
            stage="director_task_completed",
            message=f"Director task completed for {task_id}",
            details={"task_id": task_id, "completed_phases": completed_phases},
        )
        await self._broadcast_task_trace(
            phase="report",
            step_kind="workflow_complete",
            step_title="Director task completed",
            step_detail=f"All phases completed: {', '.join(completed_phases)}",
            status="completed",
            attempt=retry_count,
        )
        return DirectorTaskResult(
            task_id=task_id,
            status="completed",
            completed_phases=completed_phases,
            metadata=completed_metadata,
        )
