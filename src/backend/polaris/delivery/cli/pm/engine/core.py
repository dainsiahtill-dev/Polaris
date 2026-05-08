"""Core Polaris engine for PM -> Director orchestration.

This module contains the PolarisEngine class that decouples
PM contract generation from Director execution.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from polaris.cells.docs.court_workflow.public import ensure_plan_file
from polaris.delivery.cli.pm.director_mgmt import run_director_once
from polaris.delivery.cli.pm.engine.helpers import (
    _ALLOWED_EXECUTION_MODES,
    _ALLOWED_SCHEDULING_POLICIES,
    _TERMINAL_TASK_STATUSES,
    _dedupe_paths,
    _first_existing_file,
    _is_running_status,
    _normalize_failure_detail,
    _now_timestamp,
    _safe_int,
    _task_dependency_ids,
)
from polaris.delivery.cli.pm.engine.scheduler import (
    SchedulerProtocol,
    SingleWorkerScheduler,
    _role_context_history_limit,
)
from polaris.delivery.cli.pm.task_helpers import normalize_assigned_to
from polaris.delivery.cli.pm.tasks import (
    normalize_task_status,
)
from polaris.delivery.cli.pm.utils import (
    normalize_str_list,
)
from polaris.kernelone.fs.text_ops import write_json_atomic
from polaris.kernelone.storage.io_paths import resolve_artifact_path

if TYPE_CHECKING:
    import argparse

logger = logging.getLogger(__name__)

_DEFAULT_MAX_DIRECTOR_RETRIES = 5

DirectorRunner = Callable[..., int]


@dataclass(frozen=True)
class EngineRuntimeConfig:
    """Runtime settings for Polaris engine dispatch."""

    director_execution_mode: str = "single"
    max_directors: int = 1
    scheduling_policy: str = "priority"

    @classmethod
    def from_sources(
        cls,
        args: Any,
        payload_engine: dict[str, Any] | None = None,
    ) -> EngineRuntimeConfig:
        """Build config from args and payload."""
        payload_engine = payload_engine if isinstance(payload_engine, dict) else {}

        raw_mode = payload_engine.get(
            "director_execution_mode",
            getattr(args, "director_execution_mode", "single"),
        )
        mode = str(raw_mode or "single").strip().lower()
        if mode not in _ALLOWED_EXECUTION_MODES:
            mode = "single"

        raw_workers = payload_engine.get(
            "max_directors",
            getattr(args, "max_directors", 1),
        )
        try:
            max_directors = int(raw_workers)
        except (RuntimeError, ValueError) as exc:
            logger.warning("Failed to parse max_directors from %r, using default 1: %s", raw_workers, exc)
            max_directors = 1
        if max_directors <= 0:
            max_directors = 1

        raw_policy = payload_engine.get(
            "scheduling_policy",
            getattr(args, "director_scheduling_policy", "priority"),
        )
        policy = str(raw_policy or "priority").strip().lower()
        if policy not in _ALLOWED_SCHEDULING_POLICIES:
            policy = "priority"

        if mode == "single":
            max_directors = 1

        return cls(
            director_execution_mode=mode,
            max_directors=max_directors,
            scheduling_policy=policy,
        )

    def to_payload(self) -> dict[str, Any]:
        """Convert config to payload dict."""
        return {
            "director_execution_mode": self.director_execution_mode,
            "max_directors": int(self.max_directors),
            "scheduling_policy": self.scheduling_policy,
        }


class PolarisEngine:
    """Engine that dispatches Director tasks from PM contract."""

    def __init__(
        self,
        config: EngineRuntimeConfig,
        scheduler: SchedulerProtocol | None = None,
        director_runner: DirectorRunner | None = None,
    ) -> None:
        """Initialize engine with config and optional scheduler."""
        self.config = config
        self.scheduler = scheduler or SingleWorkerScheduler()
        self._director_runner = director_runner or run_director_once
        self._status_paths: list[str] = []
        self._events_path: str = ""
        self._status: dict[str, Any] = {
            "schema_version": 1,
            "running": False,
            "phase": "idle",
            "run_id": "",
            "pm_iteration": 0,
            "config": self.config.to_payload(),
            "roles": {},
            "summary": {},
            "updated_at": _now_timestamp(),
            "error": "",
        }

    def bind_run_context(
        self,
        *,
        run_id: str,
        pm_iteration: int,
        run_dir: str,
        runtime_status_path: str = "",
        events_path: str = "",
    ) -> None:
        """Bind runtime context for a run."""
        run_status_path = os.path.join(run_dir, "engine", "status", "engine.status.json")
        self._status_paths = _dedupe_paths([run_status_path, runtime_status_path])
        self._events_path = str(events_path or "").strip()
        self._status["run_id"] = str(run_id or "").strip()
        self._status["pm_iteration"] = int(pm_iteration or 0)
        self._status["config"] = self.config.to_payload()
        self._status["updated_at"] = _now_timestamp()
        self._persist_status()

    def register_role(
        self,
        role: str,
        *,
        status: str = "idle",
        running: bool | None = None,
        task_id: str = "",
        task_title: str = "",
        detail: str = "",
        meta: dict[str, Any] | None = None,
    ) -> None:
        """Register a role in status tracking."""
        role_name = str(role or "").strip()
        if not role_name:
            return
        roles = self._status.get("roles")
        if not isinstance(roles, dict):
            roles = {}
            self._status["roles"] = roles
        if role_name in roles and isinstance(roles[role_name], dict):
            return
        role_status = str(status or "idle").strip() or "idle"
        role_running = bool(running) if running is not None else _is_running_status(role_status)
        roles[role_name] = {
            "status": role_status,
            "running": role_running,
            "task_id": str(task_id or "").strip(),
            "task_title": str(task_title or "").strip(),
            "detail": str(detail or "").strip(),
            "updated_at": _now_timestamp(),
            "meta": dict(meta) if isinstance(meta, dict) else {},
        }
        self._status["updated_at"] = _now_timestamp()
        self._persist_status()

    def update_role_status(
        self,
        role: str,
        *,
        status: str | None = None,
        running: bool | None = None,
        task_id: str | None = None,
        task_title: str | None = None,
        detail: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        """Update role status in tracking."""
        role_name = str(role or "").strip()
        if not role_name:
            return
        roles = self._status.get("roles")
        if not isinstance(roles, dict):
            roles = {}
            self._status["roles"] = roles
        payload = roles.get(role_name)
        if not isinstance(payload, dict):
            payload = {}
            roles[role_name] = payload
        if status is not None:
            normalized_status = str(status or "").strip() or "idle"
            payload["status"] = normalized_status
            if running is None:
                payload["running"] = _is_running_status(normalized_status)
        if running is not None:
            payload["running"] = bool(running)
        if task_id is not None:
            payload["task_id"] = str(task_id or "").strip()
        if task_title is not None:
            payload["task_title"] = str(task_title or "").strip()
        if detail is not None:
            payload["detail"] = str(detail or "").strip()
        if isinstance(meta, dict):
            current_meta = payload.get("meta")
            if not isinstance(current_meta, dict):
                current_meta = {}
            current_meta.update(meta)
            payload["meta"] = current_meta
        payload["updated_at"] = _now_timestamp()
        self._status["updated_at"] = _now_timestamp()
        self._persist_status()

    def _role_context_history_limit(self) -> int:
        """Get role context history limit."""
        return _role_context_history_limit()

    def _append_role_context(
        self,
        role: str,
        *,
        event: str,
        task_id: str = "",
        task_title: str = "",
        pm_status: str = "",
        error_code: str = "",
        failure_detail: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        """Append context event to role history."""
        role_name = str(role or "").strip()
        if not role_name:
            return
        roles = self._status.get("roles")
        if not isinstance(roles, dict):
            roles = {}
            self._status["roles"] = roles
        payload = roles.get(role_name)
        if not isinstance(payload, dict):
            payload = {
                "status": "idle",
                "running": False,
                "task_id": "",
                "task_title": "",
                "detail": "",
                "meta": {},
            }
            roles[role_name] = payload

        context = payload.get("context")
        if not isinstance(context, dict):
            context = {}
        history = context.get("history")
        if not isinstance(history, list):
            history = []
        counters = context.get("counters")
        if not isinstance(counters, dict):
            counters = {}

        event_token = str(event or "").strip() or "event"
        normalized_pm_status = str(pm_status or "").strip().lower()
        normalized_error = str(error_code or "").strip()
        normalized_failure = _normalize_failure_detail(failure_detail)
        entry = {
            "timestamp": _now_timestamp(),
            "event": event_token,
            "task_id": str(task_id or "").strip(),
            "task_title": str(task_title or "").strip(),
            "pm_status": normalized_pm_status,
            "error_code": normalized_error,
            "failure_detail": normalized_failure,
            "details": dict(details) if isinstance(details, dict) else {},
        }
        history.append(entry)
        history_limit = self._role_context_history_limit()
        if len(history) > history_limit:
            history = history[-history_limit:]

        counters["events"] = _safe_int(counters.get("events"), default=0) + 1
        if normalized_pm_status == "needs_continue":
            counters["needs_continue"] = _safe_int(counters.get("needs_continue"), default=0) + 1
        if normalized_pm_status in {"failed", "blocked"}:
            counters["failures"] = _safe_int(counters.get("failures"), default=0) + 1
        if normalized_pm_status == "done":
            counters["successes"] = _safe_int(counters.get("successes"), default=0) + 1

        context["history"] = history
        context["counters"] = counters
        context["last_event"] = event_token
        context["last_task_id"] = str(task_id or "").strip()
        context["last_task_title"] = str(task_title or "").strip()
        context["last_pm_status"] = normalized_pm_status
        if normalized_error:
            context["last_error_code"] = normalized_error
        if normalized_failure:
            context["last_failure_detail"] = normalized_failure
        context["updated_at"] = _now_timestamp()
        payload["context"] = context
        payload["updated_at"] = _now_timestamp()
        self._status["updated_at"] = _now_timestamp()
        self._persist_status()

    def _role_context_snapshot(self, role: str, *, history_items: int = 5) -> dict[str, Any]:
        """Get snapshot of role context."""
        role_name = str(role or "").strip()
        if not role_name:
            return {}
        roles = self._status.get("roles")
        if not isinstance(roles, dict):
            return {}
        payload = roles.get(role_name)
        if not isinstance(payload, dict):
            return {}
        context = payload.get("context")
        if not isinstance(context, dict):
            return {}
        snapshot = dict(context)
        history = context.get("history")
        if isinstance(history, list):
            keep = max(0, int(history_items))
            snapshot["history"] = history[-keep:] if keep else []
        return snapshot

    def _build_engine_role_context(self, task: dict[str, Any]) -> dict[str, Any]:
        """Build role context for engine dispatch."""
        coordination = {
            "tri_council_round_count": _safe_int(
                task.get("tri_council_round_count"),
                default=0,
            ),
            "escalation_stage": _safe_int(
                task.get("coordination_escalation_stage"),
                default=0,
            ),
            "stage_retry_count": _safe_int(
                task.get("coordination_stage_retry_count"),
                default=0,
            ),
            "last_action": str(task.get("coordination_last_action") or "").strip(),
            "last_reason": str(task.get("coordination_last_reason") or "").strip(),
            "qa_retry_count": _safe_int(task.get("qa_retry_count"), default=0),
            "qa_failed_final": bool(task.get("qa_failed_final", False)),
        }
        return {
            "director": self._role_context_snapshot("Director"),
            "chief_engineer": self._role_context_snapshot("ChiefEngineer"),
            "pm": self._role_context_snapshot("PM"),
            "architect": self._role_context_snapshot("Architect"),
            "coordination": coordination,
        }

    def set_phase(
        self,
        phase: str,
        *,
        running: bool | None = None,
        summary: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        """Set engine phase."""
        self._update_engine_status(
            phase=phase,
            running=running,
            summary=summary,
            error=error,
        )

    def plan_batches(self, tasks: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        """Plan execution batches from tasks."""
        workers = max(1, int(self.config.max_directors or 1))
        return self.scheduler.schedule(tasks, workers, self.config.scheduling_policy)

    def _update_engine_status(
        self,
        *,
        phase: str | None = None,
        running: bool | None = None,
        summary: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Update engine status."""
        if phase is not None:
            self._status["phase"] = str(phase or "idle").strip() or "idle"
        if running is not None:
            self._status["running"] = bool(running)
        if isinstance(summary, dict):
            self._status["summary"] = dict(summary)
        if error is not None:
            self._status["error"] = str(error or "").strip()
        self._status["config"] = self.config.to_payload()
        self._status["updated_at"] = _now_timestamp()
        self._persist_status()

    def _persist_status(self) -> None:
        """Persist status to file paths."""
        if not self._status_paths:
            return
        payload = dict(self._status)
        payload["updated_at"] = _now_timestamp()
        for path in self._status_paths:
            if not path:
                continue
            try:
                write_json_atomic(path, payload)
            except (OSError, RuntimeError, ValueError) as exc:
                logger.warning("Failed to write runtime status to %r: %s", path, exc)
                continue

    def dispatch_director_tasks(
        self,
        *,
        args: argparse.Namespace,
        workspace_full: str,
        run_dir: str,
        pm_payload: dict[str, Any],
        events_path: str = "",
        dialogue_path: str = "",
        plan_path: str = "",
        pm_tasks_paths: Sequence[str] | None = None,
        runtime_status_path: str = "",
        progress_payload_paths: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Dispatch director tasks from PM contract."""
        # Implementation continues in next part...
        # This is a placeholder that imports the dispatch logic
        from polaris.delivery.cli.pm.engine._dispatch import _dispatch_director_tasks_impl

        return _dispatch_director_tasks_impl(
            self,
            args=args,
            workspace_full=workspace_full,
            run_dir=run_dir,
            pm_payload=pm_payload,
            events_path=events_path,
            dialogue_path=dialogue_path,
            plan_path=plan_path,
            pm_tasks_paths=pm_tasks_paths,
            runtime_status_path=runtime_status_path,
            progress_payload_paths=progress_payload_paths,
        )


# Expose helper functions for backward compatibility
def _collect_active_director_tasks(pm_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect active director tasks from PM payload."""
    tasks = pm_payload.get("tasks") if isinstance(pm_payload, dict) else []
    if not isinstance(tasks, list):
        return []
    selected: list[dict[str, Any]] = []
    for item in tasks:
        if not isinstance(item, dict):
            continue
        assignee = normalize_assigned_to(item.get("assigned_to"))
        if assignee != "Director":
            continue
        status = normalize_task_status(item.get("status"))
        if status in _TERMINAL_TASK_STATUSES:
            continue
        selected.append(item)
    return selected


def _build_single_task_payload(
    pm_payload: dict[str, Any],
    task: dict[str, Any],
) -> dict[str, Any]:
    """Build single task payload for director."""
    original_dependencies = _task_dependency_ids(task)
    task_payload = dict(task)
    if original_dependencies:
        _metadata_raw = task_payload.get("metadata")
        metadata: dict[str, Any] = (
            dict(cast("dict[str, Any]", _metadata_raw)) if isinstance(_metadata_raw, dict) else {}
        )
        existing_deps = normalize_str_list(metadata.get("engine_dispatch_depends_on"))
        if existing_deps:
            dependency_snapshot = list(dict.fromkeys(existing_deps + original_dependencies))
        else:
            dependency_snapshot = list(original_dependencies)
        metadata["engine_dispatch_depends_on"] = dependency_snapshot
        task_payload["metadata"] = metadata
    for key in ("dependencies", "depends_on", "deps"):
        if key in task_payload:
            task_payload[key] = []

    payload: dict[str, Any] = {}
    for key, value in pm_payload.items():
        if key == "tasks":
            continue
        payload[key] = value
    payload["tasks"] = [task_payload]
    payload["engine_dispatch"] = {
        "contract_mode": "single_task",
        "task_id": str(task.get("id") or "").strip(),
        "depends_on": list(original_dependencies),
    }
    return payload


def _resolve_preflight_paths(
    *,
    args: argparse.Namespace,
    workspace_full: str,
    plan_path: str,
    pm_tasks_paths: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Resolve preflight paths for dispatch."""
    plan_candidates = _dedupe_paths(
        [
            plan_path,
            str(getattr(args, "plan_path", "") or "").strip(),
            resolve_artifact_path(workspace_full, "", "runtime/contracts/plan.md"),
        ]
    )
    pm_candidates = _dedupe_paths(
        [
            *list(pm_tasks_paths or []),
            str(getattr(args, "pm_task_path", "") or "").strip(),
            resolve_artifact_path(workspace_full, "", "runtime/contracts/pm_tasks.contract.json"),
        ]
    )
    resolved_plan = _first_existing_file(plan_candidates)
    autofixed: list[str] = []
    if not resolved_plan:
        plan_bootstrap_target = _select_plan_bootstrap_target(plan_candidates, workspace_full)
        if plan_bootstrap_target:
            try:
                ensure_plan_file(plan_bootstrap_target, auto_continue=True)
            except (RuntimeError, ValueError) as e:
                logger.debug(f"Failed to ensure plan file: {e}")
            if os.path.isfile(plan_bootstrap_target):
                resolved_plan = plan_bootstrap_target
                autofixed.append("contracts/plan.md")
                plan_candidates = _dedupe_paths([plan_bootstrap_target, *plan_candidates])
    resolved_pm_tasks = _first_existing_file(pm_candidates)
    missing: list[str] = []
    if not resolved_plan:
        missing.append("contracts/plan.md")
    if not resolved_pm_tasks:
        missing.append("contracts/pm_tasks.contract.json")
    return {
        "ok": len(missing) == 0,
        "missing": missing,
        "resolved_plan_path": resolved_plan,
        "resolved_pm_tasks_path": resolved_pm_tasks,
        "plan_candidates": plan_candidates,
        "pm_tasks_candidates": pm_candidates,
        "autofixed": autofixed,
    }


def _select_plan_bootstrap_target(candidates: Sequence[str], workspace_full: str) -> str:
    """Select plan bootstrap target from candidates."""
    for item in candidates:
        raw = str(item or "").strip()
        if not raw:
            continue
        if os.path.isabs(raw):
            return raw
        if workspace_full:
            return os.path.join(workspace_full, raw)
    return ""


__all__ = [
    "DirectorRunner",
    "EngineRuntimeConfig",
    "PolarisEngine",
    "_build_single_task_payload",
    "_collect_active_director_tasks",
    "_resolve_preflight_paths",
    "_select_plan_bootstrap_target",
]
