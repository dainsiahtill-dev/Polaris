"""Director consumer for PENDING_EXEC tasks with Safe Parallel support."""

from __future__ import annotations

import logging
import threading
import warnings
from typing import Any

from polaris.cells.runtime.task_market.public.contracts import (
    AcknowledgeTaskStageCommandV1,
    ClaimTaskWorkItemCommandV1,
    FailTaskStageCommandV1,
    QueryTaskMarketStatusV1,
    RenewTaskLeaseCommandV1,
)
from polaris.cells.runtime.task_market.public.service import get_task_market_service

logger = logging.getLogger(__name__)


class UnrecoverableExecutionError(RuntimeError):
    """Execution failure that should be dead-lettered and compensated."""


class ScopeConflictDetector:
    """Detect scope path conflicts with other in-progress tasks."""

    def check_conflict(self, workspace: str, current_task_id: str, scope_paths: list[str]) -> bool:
        """Return True if any other IN_EXECUTION task shares scope paths with current task."""
        normalized_scope = self._normalize_paths(scope_paths)
        if not normalized_scope:
            return False
        svc = get_task_market_service()
        status = svc.query_status(
            QueryTaskMarketStatusV1(
                workspace=workspace,
                stage="pending_exec",
                include_payload=True,
                limit=5000,
            )
        )
        for item in status.items:
            if str(item.get("task_id") or "").strip() == str(current_task_id or "").strip():
                continue
            if str(item.get("status") or "").strip().lower() != "in_execution":
                continue
            payload = item.get("payload")
            if not isinstance(payload, dict):
                continue
            candidate_paths = self._extract_scope_paths(payload)
            if normalized_scope.intersection(candidate_paths):
                return True
        return False

    def _extract_scope_paths(self, payload: dict[str, Any]) -> set[str]:
        collected: list[str] = []
        raw_scope = payload.get("scope_paths")
        if isinstance(raw_scope, list):
            for row in raw_scope:
                if isinstance(row, str):
                    collected.append(row)
        raw_targets = payload.get("target_files")
        if isinstance(raw_targets, list):
            for row in raw_targets:
                if isinstance(row, str):
                    collected.append(row)
        return self._normalize_paths(collected)

    def _normalize_paths(self, paths: list[str]) -> set[str]:
        normalized: set[str] = set()
        for raw in paths:
            token = str(raw or "").strip()
            if not token:
                continue
            normalized.add(token.replace("\\", "/").lower())
        return normalized


class _LeaseHeartbeat:
    """Background lease renewer for long-running execution."""

    def __init__(
        self,
        *,
        svc: Any,
        workspace: str,
        task_id: str,
        lease_token: str,
        visibility_timeout_seconds: int,
        interval_seconds: float,
    ) -> None:
        self._svc = svc
        self._workspace = workspace
        self._task_id = task_id
        self._lease_token = lease_token
        self._visibility_timeout_seconds = max(1, int(visibility_timeout_seconds))
        self._interval_seconds = max(0.05, float(interval_seconds))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run_loop(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            try:
                self._svc.renew_task_lease(
                    RenewTaskLeaseCommandV1(
                        workspace=self._workspace,
                        task_id=self._task_id,
                        lease_token=self._lease_token,
                        visibility_timeout_seconds=self._visibility_timeout_seconds,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Lease heartbeat failed: task_id=%s lease_token=%s error=%s",
                    self._task_id,
                    self._lease_token,
                    exc,
                )
                return


class DirectorExecutionConsumer:
    """Director consumer with Safe Parallel support.

    .. deprecated::
        Use :class:`polaris.cells.director.pool.internal.director_pool.DirectorPool` instead.
    """

    def __init__(
        self,
        workspace: str,
        worker_id: str = "director_worker",
        visibility_timeout_seconds: int = 1800,
        poll_interval: float = 5.0,
        enable_safe_parallel: bool = False,
        lease_renew_interval_seconds: float | None = None,
    ) -> None:
        warnings.warn(
            "DirectorExecutionConsumer is deprecated. Use DirectorPool instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._workspace = workspace
        self._worker_id = worker_id
        self._visibility_timeout = visibility_timeout_seconds
        self._poll_interval = poll_interval
        self._enable_safe_parallel = enable_safe_parallel
        self._lease_renew_interval_seconds = (
            float(lease_renew_interval_seconds)
            if lease_renew_interval_seconds is not None
            else max(1.0, min(60.0, float(self._visibility_timeout) / 3.0))
        )
        self._stop_event = threading.Event()
        self._svc = get_task_market_service()
        self._conflict_detector = ScopeConflictDetector()

    def poll_once(self) -> list[dict[str, Any]]:
        """Poll once for PENDING_EXEC tasks."""
        results: list[dict[str, Any]] = []
        while not self._stop_event.is_set():
            claim = self._svc.claim_work_item(
                ClaimTaskWorkItemCommandV1(
                    workspace=self._workspace,
                    stage="pending_exec",
                    worker_id=self._worker_id,
                    worker_role="director",
                    visibility_timeout_seconds=self._visibility_timeout,
                )
            )
            if not claim.ok:
                break

            processed = self._process_claim(claim)
            results.append(processed)
        return results

    def _process_claim(self, claim: Any) -> dict[str, Any]:
        """Process a single claimed execution task."""
        task_id = claim.task_id
        lease_token = claim.lease_token
        payload = dict(claim.payload) if claim.payload else {}

        # Validate blueprint_id exists
        blueprint_id = payload.get("blueprint_id")
        if not blueprint_id:
            self._svc.fail_task_stage(
                FailTaskStageCommandV1(
                    workspace=self._workspace,
                    task_id=task_id,
                    lease_token=lease_token,
                    error_code="MISSING_BLUEPRINT",
                    error_message="Director cannot execute without blueprint_id",
                    to_dead_letter=True,
                )
            )
            return {"task_id": task_id, "ok": False, "reason": "missing_blueprint"}

        # Safe parallel conflict check
        if self._enable_safe_parallel:
            scope_paths = payload.get("scope_paths", [])
            if self._conflict_detector.check_conflict(self._workspace, task_id, scope_paths):
                # Requeue instead of dead-letter — it's a transient conflict
                self._svc.fail_task_stage(
                    FailTaskStageCommandV1(
                        workspace=self._workspace,
                        task_id=task_id,
                        lease_token=lease_token,
                        error_code="SCOPE_CONFLICT",
                        error_message="Scope conflict with other in-progress task",
                        requeue_stage="pending_exec",
                    )
                )
                return {"task_id": task_id, "ok": False, "reason": "scope_conflict"}

        heartbeat: _LeaseHeartbeat | None = None
        try:
            heartbeat = _LeaseHeartbeat(
                svc=self._svc,
                workspace=self._workspace,
                task_id=task_id,
                lease_token=lease_token,
                visibility_timeout_seconds=self._visibility_timeout,
                interval_seconds=self._lease_renew_interval_seconds,
            )
            heartbeat.start()
            # Execute (placeholder — actual execution delegated to DirectorAgent)
            exec_result = self._execute_task(task_id, payload, lease_token)
            registered_actions = self._register_compensation_actions(
                task_id=task_id,
                lease_token=lease_token,
                exec_result=exec_result,
            )

            # Acknowledge → PENDING_QA
            ack = self._svc.acknowledge_task_stage(
                AcknowledgeTaskStageCommandV1(
                    workspace=self._workspace,
                    task_id=task_id,
                    lease_token=lease_token,
                    next_stage="pending_qa",
                    summary=f"Execution complete for {task_id}",
                    metadata={
                        "blueprint_id": blueprint_id,
                        "changed_files": exec_result.get("changed_files", []),
                        "exec_duration_seconds": exec_result.get("duration", 0),
                    },
                )
            )
            if ack.ok and registered_actions > 0:
                self._svc.commit_compensation_actions(
                    workspace=self._workspace,
                    task_id=task_id,
                    lease_token=lease_token,
                )
            return {
                "task_id": task_id,
                "ok": ack.ok,
                "status": ack.status,
                "saga_actions": registered_actions,
            }

        except UnrecoverableExecutionError as exc:
            logger.exception("Unrecoverable execution failed for task %s: %s", task_id, exc)
            self._svc.compensate_task(
                workspace=self._workspace,
                task_id=task_id,
                reason=f"director_unrecoverable:{exc}",
                initiator="director_consumer",
            )
            self._svc.fail_task_stage(
                FailTaskStageCommandV1(
                    workspace=self._workspace,
                    task_id=task_id,
                    lease_token=lease_token,
                    error_code="EXEC_UNRECOVERABLE",
                    error_message=str(exc),
                    to_dead_letter=True,
                )
            )
            return {"task_id": task_id, "ok": False, "reason": str(exc), "dead_lettered": True}

        except Exception as exc:
            logger.exception("Execution failed for task %s: %s", task_id, exc)
            self._svc.fail_task_stage(
                FailTaskStageCommandV1(
                    workspace=self._workspace,
                    task_id=task_id,
                    lease_token=lease_token,
                    error_code="EXEC_FAILED",
                    error_message=str(exc),
                    requeue_stage="pending_exec",
                )
            )
            return {"task_id": task_id, "ok": False, "reason": str(exc)}
        finally:
            if heartbeat is not None:
                heartbeat.stop()

    def _register_compensation_actions(
        self,
        *,
        task_id: str,
        lease_token: str,
        exec_result: dict[str, Any],
    ) -> int:
        actions = self._normalize_compensation_actions(exec_result)
        for action in actions:
            self._svc.register_compensation_action(
                workspace=self._workspace,
                task_id=task_id,
                lease_token=lease_token,
                action=action,
            )
        return len(actions)

    def _normalize_compensation_actions(self, exec_result: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        raw_effects = exec_result.get("side_effects")
        if not isinstance(raw_effects, list):
            return ()

        actions: list[dict[str, Any]] = []
        for row in raw_effects:
            if not isinstance(row, dict):
                continue
            action_type = str(row.get("action_type") or row.get("type") or "").strip()
            target = str(row.get("target") or "").strip()
            if not action_type or not target:
                continue
            reverse_payload_raw = row.get("reverse_payload")
            if not isinstance(reverse_payload_raw, dict):
                reverse_payload_raw = row.get("reverse_data")
            reverse_payload = dict(reverse_payload_raw) if isinstance(reverse_payload_raw, dict) else {}
            actions.append(
                {
                    "action_type": action_type,
                    "target": target,
                    "reverse_payload": reverse_payload,
                }
            )
        return tuple(actions)

    def run(self) -> None:
        """Continuously poll and process PENDING_EXEC tasks until stop() is called."""
        logger.info(
            "Director consumer started: worker_id=%s workspace=%s poll_interval=%.1f",
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
                    "Director consumer poll cycle failed, retrying in %.1fs: %s",
                    self._poll_interval,
                    exc,
                )
                self._stop_event.wait(self._poll_interval)
        logger.info("Director consumer stopped: worker_id=%s", self._worker_id)

    def stop(self) -> None:
        """Signal the consumer to stop after the current poll cycle."""
        self._stop_event.set()

    def _execute_task(self, task_id: str, payload: dict[str, Any], lease_token: str) -> dict[str, Any]:
        """Execute task — delegates to DirectorAgent or placeholder."""
        # TODO: integrate DirectorAgent.execute()
        # For now, return a placeholder result.
        return {"changed_files": [], "duration": 0, "side_effects": []}


__all__ = ["DirectorExecutionConsumer", "UnrecoverableExecutionError"]
__deprecated__ = {"DirectorExecutionConsumer": "Use DirectorPool instead."}
