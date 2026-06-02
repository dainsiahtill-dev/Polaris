"""CE consumer that polls PENDING_DESIGN and generates blueprints."""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from polaris.cells.chief_engineer.blueprint.internal.adr_store import ADRStore
from polaris.cells.chief_engineer.blueprint.internal.blueprint_persistence import (
    BlueprintPersistence,
)
from polaris.cells.chief_engineer.blueprint.internal.chief_engineer_preflight import (
    PreflightContext,
    run_pre_dispatch_chief_engineer_ctx,
)
from polaris.cells.runtime.task_market.public.contracts import (
    AcknowledgeTaskStageCommandV1,
    ClaimTaskWorkItemCommandV1,
    FailTaskStageCommandV1,
)
from polaris.cells.runtime.task_market.public.service import get_task_market_service

logger = logging.getLogger(__name__)


def _blueprint_runtime_path(blueprint_id: str) -> str:
    return f"runtime/blueprints/{blueprint_id}.json"


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        token = value.strip()
        return [token] if token else []
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    rows: list[str] = []
    seen: set[str] = set()
    for item in value:
        token = str(item or "").strip()
        if token and token not in seen:
            seen.add(token)
            rows.append(token)
    return rows


class CEConsumer:
    """ChiefEngineer consumer for PENDING_DESIGN tasks.

    This consumer polls the task market for tasks in the ``pending_design`` stage,
    runs the CE preflight to generate a blueprint, and acknowledges the task with
    ``pending_exec`` as the next stage.

    Args:
        workspace: Workspace path for task market operations.
        worker_id: Unique identifier for this worker instance.
        visibility_timeout_seconds: How long a claimed task is locked before it
            becomes visible to other workers again on failure.
        poll_interval: Seconds to sleep between poll cycles when no task is found.
        enable_director_pool: Legacy flag for ADRStore-backed blueprint persistence.
    """

    def __init__(
        self,
        workspace: str,
        worker_id: str = "ce_worker",
        visibility_timeout_seconds: int = 900,
        poll_interval: float = 5.0,
        enable_director_pool: bool = True,
    ) -> None:
        self._workspace = str(workspace or "").strip()
        if not self._workspace:
            raise ValueError("workspace must be a non-empty string")
        self._worker_id = str(worker_id or "").strip()
        if not self._worker_id:
            raise ValueError("worker_id must be a non-empty string")
        self._visibility_timeout = int(visibility_timeout_seconds)
        self._poll_interval = float(poll_interval)
        self._stop_event = threading.Event()
        self._svc = get_task_market_service()
        self._enable_director_pool = bool(enable_director_pool)
        self._adr_store: ADRStore | None = None
        if self._enable_director_pool:
            self._adr_store = ADRStore(workspace=self._workspace)

    def poll_once(self) -> list[dict[str, Any]]:
        """Poll once for PENDING_DESIGN tasks.

        Claims and processes all available tasks until no claimable work remains.
        Returns a list of processed task results, each containing ``task_id``,
        ``ok`` status, and (on failure) ``reason``.
        """
        results: list[dict[str, Any]] = []
        while not self._stop_event.is_set():
            processed = self._claim_and_process_one()
            if processed is None:
                break
            results.append(processed)
        return results

    def _claim_and_process_one(self) -> dict[str, Any] | None:
        """Attempt to claim one PENDING_DESIGN task and process it.

        Returns:
            Processed result dict, or None if no claimable task was found.
        """
        claim = self._svc.claim_work_item(
            ClaimTaskWorkItemCommandV1(
                workspace=self._workspace,
                stage="pending_design",
                worker_id=self._worker_id,
                worker_role="chief_engineer",
                visibility_timeout_seconds=self._visibility_timeout,
            )
        )
        if not claim.ok:
            return None

        task_id = str(claim.task_id or "").strip()
        lease_token = str(claim.lease_token or "").strip()

        try:
            payload: dict[str, Any] = dict(claim.payload) if claim.payload else {}
            blueprint_result = self._run_ce_preflight(task_id, payload)

            blueprint_id = str(blueprint_result.get("blueprint_id", f"bp-{task_id}"))
            scope_paths = _string_list(blueprint_result.get("scope_paths")) or _string_list(payload.get("scope_paths"))
            target_files = (
                _string_list(blueprint_result.get("target_files"))
                or _string_list(payload.get("target_files"))
                or list(scope_paths)
            )
            blueprint_path = _blueprint_runtime_path(blueprint_id)
            ack_payload: dict[str, Any] = {
                "blueprint_id": blueprint_id,
                "blueprint_path": blueprint_path,
                "runtime_blueprint_path": blueprint_path,
                "context_snapshot_ref": str(payload.get("context_snapshot_ref", "")),
                "guardrails": blueprint_result.get("guardrails", []),
                "no_touch_zones": blueprint_result.get("no_touch_zones", []),
                "scope_paths": scope_paths,
                "target_files": target_files,
                "route": "chief_blueprint_required",
                "task_market_route": "chief_blueprint_required",
                "blueprint_required": True,
            }

            if self._enable_director_pool and self._adr_store is not None:
                self._adr_store.create_blueprint(
                    blueprint_id,
                    {
                        "task_id": task_id,
                        "run_id": str(payload.get("run_id", "")),
                        "route": "chief_blueprint_required",
                        "preflight_result": blueprint_result,
                        "scope_paths": scope_paths,
                        "target_files": target_files,
                        "guardrails": ack_payload["guardrails"],
                        "no_touch_zones": ack_payload["no_touch_zones"],
                    },
                )
                self._adr_store.compile(blueprint_id)

                ack_payload["director_pool_assignment"] = "deferred_to_task_market"
            else:
                BlueprintPersistence(self._workspace).save(
                    blueprint_id,
                    {
                        "schema_version": "chief_engineer.blueprint.v1",
                        "blueprint_id": blueprint_id,
                        "status": "approved",
                        "task_id": task_id,
                        "run_id": str(payload.get("run_id", "")),
                        "route": "chief_blueprint_required",
                        "scope_paths": scope_paths,
                        "target_files": target_files,
                        "guardrails": ack_payload["guardrails"],
                        "no_touch_zones": ack_payload["no_touch_zones"],
                        "preflight_result": blueprint_result,
                    },
                )

            ack = self._svc.acknowledge_task_stage(
                AcknowledgeTaskStageCommandV1(
                    workspace=self._workspace,
                    task_id=task_id,
                    lease_token=lease_token,
                    next_stage="pending_exec",
                    summary=f"Blueprint {ack_payload['blueprint_id']} ready for Director",
                    metadata=ack_payload,
                )
            )
            return {
                "task_id": task_id,
                "ok": bool(ack.ok),
                "status": str(ack.status or ""),
            }

        except Exception as exc:
            logger.exception("CE consumer failed for task %s: %s", task_id, exc)
            self._svc.fail_task_stage(
                FailTaskStageCommandV1(
                    workspace=self._workspace,
                    task_id=task_id,
                    lease_token=lease_token,
                    error_code="CE_design_failed",
                    error_message=str(exc),
                    requeue_stage="pending_design",
                )
            )
            return {
                "task_id": task_id,
                "ok": False,
                "reason": str(exc),
            }

    def _run_ce_preflight(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Run CE preflight and return blueprint result dict.

        Args:
            task_id: Identifier of the task being processed.
            payload: Task payload dict from the task market.

        Returns:
            Blueprint result dict with ``blueprint_id``, ``guardrails``,
            ``no_touch_zones``, and ``scope_paths``.
        """
        # Resolve paths from payload, falling back to environment / workspace.
        resolved_workspace = str(payload.get("workspace", os.environ.get("KERNELONE_WORKSPACE", ""))).strip()
        run_dir = str(payload.get("run_dir", "")).strip()
        cache_root = str(payload.get("cache_root", "")).strip()
        run_id = str(payload.get("run_id", "")).strip()

        # Build task list from payload for PreflightContext.
        task_entry: dict[str, Any] = {
            "title": payload.get("title", task_id),
            **payload,
            "id": task_id,
        }

        # Build minimal run/events/dialogue paths.
        from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

        metadata_dir = get_workspace_metadata_dir_name()
        events_path = os.path.join(resolved_workspace, metadata_dir, "runs", run_id, "events.json")
        dialogue_path = os.path.join(resolved_workspace, metadata_dir, "runs", run_id, "dialogue.jsonl")

        ctx = PreflightContext(
            workspace_full=resolved_workspace,
            cache_root_full=cache_root,
            run_dir=run_dir,
            run_id=run_id,
            pm_iteration=0,
            tasks=[task_entry],
            run_events=events_path,
            dialogue_full=dialogue_path,
            args=None,
            analysis_runner=None,
            event_emitter=None,
        )

        result = run_pre_dispatch_chief_engineer_ctx(ctx)
        return {
            "blueprint_id": f"bp-{task_id}",
            "guardrails": result.get("blueprint_guardrails", []) if isinstance(result, dict) else [],
            "no_touch_zones": result.get("no_touch_zones", []) if isinstance(result, dict) else [],
            "scope_paths": payload.get("scope_paths", []),
            "doc_id": payload.get("doc_id", run_id or task_id),
        }

    def run(self) -> None:
        """Continuously poll and process PENDING_DESIGN tasks until stop() is called."""
        logger.info(
            "CE consumer started: worker_id=%s workspace=%s poll_interval=%.1f",
            self._worker_id,
            self._workspace,
            self._poll_interval,
        )
        while not self._stop_event.is_set():
            try:
                processed = self.poll_once()
                if not processed:
                    self._stop_event.wait(self._poll_interval)
            except Exception as exc:
                logger.exception(
                    "CE consumer poll cycle failed, retrying in %.1fs: %s",
                    self._poll_interval,
                    exc,
                )
                self._stop_event.wait(self._poll_interval)
        logger.info("CE consumer stopped: worker_id=%s", self._worker_id)

    def stop(self) -> None:
        """Signal the consumer to stop after the current poll cycle."""
        self._stop_event.set()


__all__ = ["CEConsumer"]
