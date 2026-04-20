"""CE consumer that polls PENDING_DESIGN and generates blueprints."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import threading
from typing import Any

from polaris.cells.chief_engineer.blueprint.internal.adr_store import ADRStore
from polaris.cells.chief_engineer.blueprint.internal.chief_engineer_preflight import (
    PreflightContext,
    run_pre_dispatch_chief_engineer_ctx,
)
from polaris.cells.chief_engineer.blueprint.internal.director_pool import (
    DirectorPool,
    DirectorPoolConflictError,
)
from polaris.cells.runtime.task_market.public.contracts import (
    AcknowledgeTaskStageCommandV1,
    ClaimTaskWorkItemCommandV1,
    FailTaskStageCommandV1,
)
from polaris.cells.runtime.task_market.public.service import get_task_market_service

logger = logging.getLogger(__name__)


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
        enable_director_pool: Whether to enable DirectorPool and ADRStore integration.
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
        self._director_pool: DirectorPool | None = None
        self._adr_store: ADRStore | None = None
        self._async_thread: threading.Thread | None = None
        self._async_loop: asyncio.AbstractEventLoop | None = None
        if self._enable_director_pool:
            self._director_pool = DirectorPool(workspace=self._workspace)
            self._adr_store = ADRStore(workspace=self._workspace)
            self._start_async_loop()

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
            ack_payload: dict[str, Any] = {
                "blueprint_id": blueprint_id,
                "context_snapshot_ref": str(payload.get("context_snapshot_ref", "")),
                "guardrails": blueprint_result.get("guardrails", []),
                "no_touch_zones": blueprint_result.get("no_touch_zones", []),
                "scope_paths": blueprint_result.get("scope_paths", payload.get("scope_paths", [])),
            }

            if self._enable_director_pool and self._adr_store is not None and self._director_pool is not None:
                self._adr_store.create_blueprint(
                    blueprint_id,
                    {
                        "task_id": task_id,
                        "preflight_result": blueprint_result,
                        "scope_paths": ack_payload["scope_paths"],
                        "guardrails": ack_payload["guardrails"],
                        "no_touch_zones": ack_payload["no_touch_zones"],
                    },
                )
                self._adr_store.compile(blueprint_id)

                try:
                    self._run_async(
                        self._director_pool.assign_task(
                            _TaskStub(
                                task_id=task_id,
                                scope_paths=payload.get("scope_paths", []),
                                title=payload.get("title", task_id),
                                goal=payload.get("goal", ""),
                                payload=payload,
                                run_id=payload.get("run_id", ""),
                            ),
                            _BlueprintStub(blueprint_id),
                        )
                    )
                    ack_payload["director_pool_assigned"] = True
                except DirectorPoolConflictError:
                    self._svc.fail_task_stage(
                        FailTaskStageCommandV1(
                            workspace=self._workspace,
                            task_id=task_id,
                            lease_token=lease_token,
                            error_code="CE_director_pool_conflict",
                            error_message="Director pool conflict: no director assigned.",
                            requeue_stage="pending_design",
                        )
                    )
                    return {
                        "task_id": task_id,
                        "ok": False,
                        "reason": "director_pool_conflict",
                    }

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
        resolved_workspace = str(payload.get("workspace", os.environ.get("POLARIS_WORKSPACE", ""))).strip()
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
        self._stop_async_loop()

    def _start_async_loop(self) -> None:
        """Start a background daemon thread running an asyncio event loop."""
        self._async_loop = asyncio.new_event_loop()

        def _loop_runner() -> None:
            asyncio.set_event_loop(self._async_loop)
            self._async_loop.run_forever()

        self._async_thread = threading.Thread(target=_loop_runner, daemon=True)
        self._async_thread.start()

    def _stop_async_loop(self) -> None:
        """Stop the background event loop and join the thread."""
        if self._async_loop is not None:
            self._async_loop.call_soon_threadsafe(self._async_loop.stop)
        if self._async_thread is not None:
            self._async_thread.join(timeout=5.0)
            self._async_thread = None
        if self._async_loop is not None:
            self._async_loop.close()
            self._async_loop = None

    def _run_async(self, coro: Any) -> Any:
        if self._async_loop is not None:
            future = asyncio.run_coroutine_threadsafe(coro, self._async_loop)
            return future.result()
        try:
            return asyncio.run(coro)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(loop.run_until_complete, coro).result()


class _TaskStub:
    """Minimal task stub for DirectorPool assignment."""

    def __init__(
        self,
        task_id: str,
        scope_paths: Any,
        title: str = "",
        goal: str = "",
        payload: dict[str, Any] | None = None,
        run_id: str = "",
    ) -> None:
        self.id = task_id
        self.target_files = scope_paths if isinstance(scope_paths, list) else []
        self.title = title or task_id
        self.goal = goal
        self.payload = dict(payload) if isinstance(payload, dict) else {}
        self.run_id = run_id


class _BlueprintStub:
    """Minimal blueprint stub for DirectorPool assignment."""

    def __init__(self, blueprint_id: str) -> None:
        self.blueprint_id = blueprint_id


__all__ = ["CEConsumer"]
