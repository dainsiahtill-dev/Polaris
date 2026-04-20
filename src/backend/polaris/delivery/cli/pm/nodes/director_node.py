"""Director Node implementation (工部侍郎).

This module implements the Director role node for executing tasks
using PolarisEngine.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from polaris.delivery.cli.pm.polaris_engine import EngineRuntimeConfig

from polaris.delivery.cli.pm.nodes.base import BaseRoleNode
from polaris.delivery.cli.pm.nodes.protocols import RoleContext, RoleResult


class DirectorNode(BaseRoleNode):
    """Director Node - 工部侍郎 (Director of Works).

    Responsible for:
    - Dispatching tasks to PolarisEngine
    - Managing task execution
    - Collecting results
    """

    @property
    def role_name(self) -> str:
        return "Director"

    def get_dependencies(self) -> list[str]:
        """Director depends on PM (and optionally CE)."""
        return ["PM"]

    def get_trigger_conditions(self) -> list[str]:
        """Director runs after PM/CE completes."""
        return ["pm_complete", "ce_complete", "manual"]

    def can_handle(self, context: RoleContext) -> bool:
        """Can handle if we have tasks to execute."""
        tasks = context.get_tasks()
        return len(tasks) > 0

    def _execute_impl(self, context: RoleContext) -> RoleResult:
        """Execute Director logic to dispatch and run tasks."""
        import argparse

        from polaris.delivery.cli.pm.polaris_engine import PolarisEngine
        from polaris.delivery.cli.pm.orchestration_core import (
            get_shangshuling_ready_tasks,
            record_shangshuling_task_completion,
            sync_tasks_to_shangshuling,
        )
        from polaris.delivery.cli.pm.tasks import apply_task_status_updates, normalize_task_status

        workspace = context.workspace_full
        iteration = context.pm_iteration
        run_id = context.run_id
        args: argparse.Namespace = context.args if context.args else argparse.Namespace()

        # Get tasks
        tasks = context.get_tasks()
        if not tasks:
            return self._create_success_result(
                tasks=[],
                next_role="QA",
                continue_reason="No tasks to execute",
            )

        # Resolve config
        config = self._build_engine_config(args)

        # Build engine
        engine = PolarisEngine(config)

        # Prepare pm payload
        pm_result = context.pm_result or {}
        pm_payload = dict(pm_result)
        pm_payload["run_id"] = run_id
        pm_payload["pm_iteration"] = iteration

        # Resolve paths
        run_dir = context.run_dir
        if not run_dir and args:
            run_dir = getattr(args, "run_dir", "")
        if not run_dir:
            run_dir = str(context.metadata.get("run_dir") or "").strip()

        metadata = context.metadata if isinstance(context.metadata, dict) else {}
        runtime_status_path: str = str(metadata.get("runtime_engine_status") or "").strip()
        plan_path: str = str(metadata.get("runtime_plan_full") or "").strip()
        pm_tasks_paths = self._collect_path_list(metadata.get("pm_tasks_paths"))
        progress_payload_paths = self._collect_path_list(metadata.get("progress_payload_paths"))

        # 尚书令PM 深度集成：同步并优先使用就绪任务子集
        shang_meta: dict[str, Any] = {"sync_count": 0, "ready_count": 0, "selected_count": len(tasks)}
        dispatch_tasks = list(tasks)
        try:
            sync_count = int(sync_tasks_to_shangshuling(workspace, tasks) or 0)
            ready_tasks = get_shangshuling_ready_tasks(
                workspace,
                limit=max(6, len(tasks) * 2),
            )
            shang_meta["sync_count"] = sync_count
            shang_meta["ready_count"] = len(ready_tasks)
            selected = self._select_ready_tasks(tasks, ready_tasks)
            if selected:
                dispatch_tasks = selected
            shang_meta["selected_count"] = len(dispatch_tasks)
        except (RuntimeError, ValueError) as exc:
            shang_meta["warning"] = f"shangshuling_sync_failed: {exc}"

        pm_payload["tasks"] = dispatch_tasks

        # Dispatch
        start_time = time.time()

        try:
            dispatch_result = engine.dispatch_director_tasks(
                args=args,
                workspace_full=workspace,
                run_dir=run_dir,
                pm_payload=pm_payload,
                events_path=context.events_path,
                dialogue_path=context.dialogue_path,
                plan_path=plan_path or "",  # type: ignore[arg-type]
                pm_tasks_paths=pm_tasks_paths or None,
                runtime_status_path=runtime_status_path or "",  # type: ignore[arg-type]
                progress_payload_paths=progress_payload_paths or None,
            )
        except (RuntimeError, ValueError) as e:
            return self._create_error_result(
                error=f"Director dispatch failed: {e}",
                error_code="DIRECTOR_DISPATCH_ERROR",
            )

        # Extract results
        summary = dispatch_result.get("summary", {})
        records = dispatch_result.get("records", [])
        status_updates = dispatch_result.get("status_updates", {})
        failure_info_raw = dispatch_result.get("failure_info")
        failure_info: dict[str, Any] = failure_info_raw if isinstance(failure_info_raw, dict) else {}

        # Update task statuses on full PM task set
        apply_task_status_updates(tasks, status_updates, failure_info=failure_info)
        updated_tasks = tasks

        # Determine next steps
        has_failures = summary.get("failures", 0) > 0
        has_blocked = summary.get("blocked", 0) > 0
        if bool(dispatch_result.get("hard_failure")):
            has_failures = True

        next_role = "QA"
        if has_failures or has_blocked:
            next_role = ""

        # 回写尚书令任务完成状态（支持 legacy_id）
        shang_recorded = 0
        for task_id, raw_status in status_updates.items():
            normalized_status = normalize_task_status(raw_status)
            if normalized_status not in {"done", "failed", "blocked"}:
                continue
            success = normalized_status == "done"
            _failure_raw = failure_info.get(task_id)
            task_failure: dict[str, Any] = cast(
                "dict[str, Any]", _failure_raw if isinstance(_failure_raw, dict) else {}
            )
            payload: dict[str, Any] = {
                "summary": str(task_failure.get("summary") or "") if isinstance(task_failure, dict) else "",
                "error": str(task_failure.get("error") or "") if isinstance(task_failure, dict) else "",
                "details": task_failure,
                "retry_allowed": normalized_status != "blocked",
                "verification_method": "auto_check",
                "evidence": "Synced from Director dispatch status",
                "artifacts": [],
            }
            try:
                if record_shangshuling_task_completion(
                    workspace,
                    str(task_id),
                    "Director",
                    success,
                    payload,
                ):
                    shang_recorded += 1
            except (RuntimeError, ValueError):
                continue

        director_result = (
            dispatch_result.get("director_result") if isinstance(dispatch_result.get("director_result"), dict) else {}
        )
        if director_result:
            director_result = dict(director_result)
            director_result.setdefault("run_id", run_id)
            director_result["successes"] = int(summary.get("successes") or 0)
            director_result["total"] = int(summary.get("total") or 0)

        duration_ms = int((time.time() - start_time) * 1000)

        return RoleResult(
            success=not (has_failures or has_blocked),
            exit_code=1 if (has_failures or has_blocked) else 0,
            tasks=updated_tasks,
            status_updates=status_updates,
            next_role=next_role,
            continue_reason=f"Dispatched {summary.get('total', 0)} tasks, {summary.get('successes', 0)} succeeded",
            metadata={
                "summary": summary,
                "record_count": len(records),
                "dispatch_result": dispatch_result,
                "director_result": director_result,
                "shangshuling": {
                    **shang_meta,
                    "recorded_terminal_updates": shang_recorded,
                },
            },
            duration_ms=duration_ms,
        )

    def _build_engine_config(self, args: Any) -> EngineRuntimeConfig:
        """Build engine config from args."""
        from polaris.delivery.cli.pm.polaris_engine import EngineRuntimeConfig

        if args and hasattr(args, "director_execution_mode"):
            return EngineRuntimeConfig.from_sources(args, None)

        return EngineRuntimeConfig()

    def _apply_status_updates(
        self,
        tasks: list[dict[str, Any]],
        status_updates: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Apply status updates to tasks."""
        if not status_updates:
            return tasks

        updated = []
        for task in tasks:
            task_id = task.get("id", "")
            if not task_id:
                updated.append(task)
                continue

            new_status = status_updates.get(task_id)
            if new_status:
                task["status"] = new_status

            updated.append(task)

        return updated

    def _collect_path_list(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    def _select_ready_tasks(
        self,
        tasks: list[dict[str, Any]],
        ready_tasks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not isinstance(tasks, list) or not isinstance(ready_tasks, list):
            return []
        if not ready_tasks:
            return []

        ready_legacy_ids = set()
        for item in ready_tasks:
            if not isinstance(item, dict):
                continue
            legacy_id = str(item.get("id") or "").strip()
            if legacy_id:
                ready_legacy_ids.add(legacy_id)

        if not ready_legacy_ids:
            return []

        selected: list[dict[str, Any]] = []
        for task in tasks:
            if not isinstance(task, dict):
                continue
            task_id = str(task.get("id") or "").strip()
            if task_id and task_id in ready_legacy_ids:
                selected.append(task)

        return selected


__all__ = ["DirectorNode"]
