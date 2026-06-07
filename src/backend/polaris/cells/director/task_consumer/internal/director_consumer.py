"""Director consumer for PENDING_EXEC tasks with Safe Parallel support."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import warnings
from pathlib import Path
from typing import Any, Callable, Coroutine

from polaris.cells.runtime.task_market.public.contracts import (
    AcknowledgeTaskStageCommandV1,
    ClaimTaskWorkItemCommandV1,
    FailTaskStageCommandV1,
    QueryTaskMarketStatusV1,
    RenewTaskLeaseCommandV1,
)
from polaris.cells.runtime.task_market.public.service import get_task_market_service

logger = logging.getLogger(__name__)

_ROUTE_DIRECT_TO_DIRECTOR = "direct_to_director"
_ROUTE_CHIEF_BLUEPRINT_REQUIRED = "chief_blueprint_required"


def _normalize_task_market_route(payload: dict[str, Any]) -> str:
    metadata_raw = payload.get("metadata")
    metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    for container in (payload, metadata):
        for key in ("task_market_route", "route", "routing", "dispatch_route", "execution_route"):
            token = str(container.get(key) or "").strip().lower().replace("-", "_").replace(" ", "_")
            if token in {
                _ROUTE_DIRECT_TO_DIRECTOR,
                "direct",
                "director",
                "director_direct",
                "direct_director",
                "pending_exec",
                "exec",
                "execution",
            }:
                return _ROUTE_DIRECT_TO_DIRECTOR
            if token in {
                _ROUTE_CHIEF_BLUEPRINT_REQUIRED,
                "chief",
                "chief_engineer",
                "chiefengineer",
                "blueprint",
                "blueprint_required",
                "requires_blueprint",
                "pending_design",
                "design",
            }:
                return _ROUTE_CHIEF_BLUEPRINT_REQUIRED
        for key in ("blueprint_required", "requires_blueprint", "chief_engineer_required"):
            value = container.get(key)
            if isinstance(value, bool):
                return _ROUTE_CHIEF_BLUEPRINT_REQUIRED if value else _ROUTE_DIRECT_TO_DIRECTOR
            if isinstance(value, str):
                bool_token = value.strip().lower()
                if bool_token in {"1", "true", "yes", "y", "on"}:
                    return _ROUTE_CHIEF_BLUEPRINT_REQUIRED
                if bool_token in {"0", "false", "no", "n", "off"}:
                    return _ROUTE_DIRECT_TO_DIRECTOR
    return _ROUTE_CHIEF_BLUEPRINT_REQUIRED


_NO_CHANGE_FLAGS = frozenset(
    {
        "allow_no_changes",
        "no_changes_expected",
        "allow_empty_changed_files",
        "director_noop_allowed",
    }
)
_NO_CHANGE_MODES = frozenset(
    {
        "noop",
        "no_op",
        "no-op",
        "read_only",
        "read-only",
        "inspection",
        "inspection_only",
        "analysis_only",
    }
)


class UnrecoverableExecutionError(RuntimeError):
    """Execution failure that should be dead-lettered and compensated."""


DirectorTaskExecutor = Callable[[str, dict[str, Any], str], dict[str, Any]]


def _run_coroutine_sync(coro: Coroutine[Any, Any, dict[str, Any]]) -> dict[str, Any]:
    """Run an async Director adapter call from the synchronous consumer loop."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result_box: dict[str, Any] = {}

    def _runner() -> None:
        try:
            result_box["result"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001
            result_box["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    error = result_box.get("error")
    if isinstance(error, BaseException):
        raise error
    result = result_box.get("result")
    if isinstance(result, dict):
        return result
    return {}


def _normalize_string_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (str, os.PathLike)):
        raw_values: list[Any] = [raw]
    elif isinstance(raw, (list, tuple, set)):
        raw_values = list(raw)
    else:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        if not isinstance(item, (str, os.PathLike)):
            continue
        token = str(item).strip()
        if not token:
            continue
        key = token.replace("\\", "/").lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(token)
    return normalized


def _truthy_payload_flag(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False


def _allows_no_execution_evidence(payload: dict[str, Any]) -> bool:
    for key in _NO_CHANGE_FLAGS:
        if _truthy_payload_flag(payload, key):
            return True

    for key in ("execution_mode", "task_mode", "mode", "change_mode"):
        mode = str(payload.get(key) or "").strip().lower()
        if mode in _NO_CHANGE_MODES:
            return True
    return False


def _append_normalized_paths(paths: list[str], raw: Any) -> None:
    for value in _normalize_string_list(raw):
        normalized = value.replace("\\", "/")
        if normalized not in paths:
            paths.append(normalized)


def _extract_changed_files_from_mapping(paths: list[str], mapping: dict[str, Any]) -> None:
    for key in (
        "changed_files",
        "affected_files",
        "all_affected_files",
        "new_files",
        "modified_files",
        "files",
    ):
        _append_normalized_paths(paths, mapping.get(key))

    for key in ("file", "path", "target", "relative_path", "target_path"):
        value = mapping.get(key)
        if isinstance(value, (str, os.PathLike)):
            _append_normalized_paths(paths, value)

    effect_raw = mapping.get("effect_receipt")
    if isinstance(effect_raw, dict):
        _extract_changed_files_from_mapping(paths, effect_raw)


def _extract_director_changed_files(adapter_result: dict[str, Any]) -> list[str]:
    changed_files: list[str] = []
    _extract_changed_files_from_mapping(changed_files, adapter_result)

    for key in ("tool_results", "results", "actions"):
        raw_rows = adapter_result.get(key)
        if not isinstance(raw_rows, list):
            continue
        for row in raw_rows:
            if isinstance(row, dict):
                _extract_changed_files_from_mapping(changed_files, row)

    adapter_nested = adapter_result.get("adapter_result")
    if isinstance(adapter_nested, dict):
        _extract_changed_files_from_mapping(changed_files, adapter_nested)

    return changed_files


def _extract_director_side_effects(adapter_result: dict[str, Any]) -> list[dict[str, Any]]:
    raw_side_effects = adapter_result.get("side_effects")
    if not isinstance(raw_side_effects, list):
        return []
    return [dict(row) for row in raw_side_effects if isinstance(row, dict)]


def _compact_director_adapter_summary(adapter_result: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "success": bool(adapter_result.get("success")),
        "task_id": str(adapter_result.get("task_id") or "").strip(),
        "tools_executed": adapter_result.get("tools_executed", 0),
        "materialization_mode": str(adapter_result.get("materialization_mode") or "").strip(),
    }
    for key in ("error", "error_code", "failure_stage", "root_cause_hint"):
        value = adapter_result.get(key)
        if value:
            summary[key] = str(value)
    return summary


def _adapter_failure_message(adapter_result: dict[str, Any]) -> str:
    for key in ("error", "error_code", "root_cause_hint", "failure_stage"):
        value = str(adapter_result.get(key) or "").strip()
        if value:
            return value
    return "director_adapter_execution_failed"


def _build_director_adapter_input(task_id: str, payload: dict[str, Any], lease_token: str) -> dict[str, Any]:
    metadata_raw = payload.get("metadata")
    metadata: dict[str, Any] = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
    source_pm_task_id = str(payload.get("source_pm_task_id") or metadata.get("source_pm_task_id") or "").strip()
    pm_task_id = str(payload.get("pm_task_id") or metadata.get("pm_task_id") or source_pm_task_id or task_id).strip()
    title = str(payload.get("title") or payload.get("subject") or task_id).strip()
    goal = str(payload.get("goal") or metadata.get("goal") or title).strip()

    metadata.update(
        {
            "task_market_task_id": task_id,
            "task_market_lease_token": lease_token,
            "source_pm_task_id": source_pm_task_id or pm_task_id,
            "pm_task_id": pm_task_id,
            "source": "runtime.task_market.pending_exec",
        }
    )

    adapter_input = dict(payload)
    adapter_input.update(
        {
            "task_id": task_id,
            "pm_task_id": pm_task_id,
            "subject": title,
            "description": str(payload.get("description") or goal).strip(),
            "input": goal,
            "directive": goal,
            "metadata": metadata,
        }
    )
    return adapter_input


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
        Will be removed after 2026-06-30.
    """

    def __init__(
        self,
        workspace: str,
        worker_id: str = "director_worker",
        visibility_timeout_seconds: int = 1800,
        poll_interval: float = 5.0,
        enable_safe_parallel: bool = False,
        lease_renew_interval_seconds: float | None = None,
        task_executor: DirectorTaskExecutor | None = None,
    ) -> None:
        warnings.warn(
            "DirectorExecutionConsumer is deprecated. Use DirectorPool instead. Will be removed after 2026-06-30.",
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
        self._task_executor = task_executor

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
        route = _normalize_task_market_route(payload)

        # Blueprint-mediated work must carry ChiefEngineer evidence. Direct PM
        # execution work uses the PM task contract as the execution authority.
        blueprint_id = payload.get("blueprint_id")
        if not blueprint_id and route != _ROUTE_DIRECT_TO_DIRECTOR:
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
        if not blueprint_id:
            blueprint_id = f"pm-direct::{task_id}"

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
            changed_files = _normalize_string_list(exec_result.get("changed_files"))
            if not changed_files and not _allows_no_execution_evidence(payload):
                return self._missing_execution_evidence_result(
                    task_id=task_id,
                    lease_token=lease_token,
                    blueprint_id=blueprint_id,
                    payload=payload,
                )
            registered_actions = self._register_compensation_actions(
                task_id=task_id,
                lease_token=lease_token,
                exec_result=exec_result,
            )
            adapter_summary_raw = exec_result.get("director_adapter_result")
            adapter_summary = adapter_summary_raw if isinstance(adapter_summary_raw, dict) else {}

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
                        "route": route,
                        "task_market_route": route,
                        "blueprint_required": route != _ROUTE_DIRECT_TO_DIRECTOR,
                        "director_execution_authority": (
                            "pm_task_contract" if route == _ROUTE_DIRECT_TO_DIRECTOR else "chief_engineer_blueprint"
                        ),
                        "changed_files": changed_files,
                        "director_evidence_status": (
                            "changed_files_reported" if changed_files else "explicit_no_changes"
                        ),
                        "director_files_changed_count": len(changed_files),
                        "exec_duration_seconds": exec_result.get("duration", 0),
                        "director_adapter": adapter_summary,
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

    def _missing_execution_evidence_result(
        self,
        *,
        task_id: str,
        lease_token: str,
        blueprint_id: Any,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._svc.fail_task_stage(
            FailTaskStageCommandV1(
                workspace=self._workspace,
                task_id=task_id,
                lease_token=lease_token,
                error_code="EXEC_NO_EVIDENCE",
                error_message="Director execution produced no changed_files evidence",
                requeue_stage="pending_exec",
                metadata={
                    "blueprint_id": str(blueprint_id or ""),
                    "target_files": _normalize_string_list(payload.get("target_files")),
                    "scope_paths": _normalize_string_list(payload.get("scope_paths")),
                    "reason": "director_no_changed_files_evidence",
                },
            )
        )
        return {"task_id": task_id, "ok": False, "reason": "missing_execution_evidence"}

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
        """Execute task through the real Director adapter and normalize evidence."""

        if self._task_executor is not None:
            return self._task_executor(task_id, payload, lease_token)

        workspace_path = Path(self._workspace)
        if not workspace_path.exists():
            logger.warning(
                "Director consumer workspace does not exist; returning no-evidence result: workspace=%s task_id=%s",
                self._workspace,
                task_id,
            )
            return {"changed_files": [], "duration": 0, "side_effects": []}

        from polaris.cells.roles.adapters.public.service import create_role_adapter

        started_at = time.monotonic()
        adapter = create_role_adapter("director", str(workspace_path))
        adapter_input = _build_director_adapter_input(task_id, payload, lease_token)
        context = {
            "run_id": str(payload.get("run_id") or f"task-market-director-{task_id}"),
            "metadata": {
                "task_market_task_id": task_id,
                "task_market_stage": "pending_exec",
                "task_market_worker_id": self._worker_id,
                "blueprint_id": str(payload.get("blueprint_id") or ""),
                "route": _normalize_task_market_route(payload),
            },
        }
        adapter_result = _run_coroutine_sync(
            adapter.execute(task_id=task_id, input_data=adapter_input, context=context)
        )
        duration = time.monotonic() - started_at

        if adapter_result.get("success") is not True:
            raise RuntimeError(_adapter_failure_message(adapter_result))

        changed_files = _extract_director_changed_files(adapter_result)
        return {
            "changed_files": changed_files,
            "duration": duration,
            "side_effects": _extract_director_side_effects(adapter_result),
            "director_adapter_result": _compact_director_adapter_summary(adapter_result),
        }


__all__ = ["DirectorExecutionConsumer", "UnrecoverableExecutionError"]
__deprecated__ = {"DirectorExecutionConsumer": "Use DirectorPool instead. Will be removed after 2026-06-30."}
