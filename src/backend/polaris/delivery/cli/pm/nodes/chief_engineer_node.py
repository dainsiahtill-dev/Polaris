"""ChiefEngineer Node implementation (Chief Engineer).

This module implements the ChiefEngineer role node for generating
construction blueprints from tasks.
"""

from __future__ import annotations

import time
from typing import Any

from polaris.delivery.cli.pm.nodes.base import BaseRoleNode
from polaris.delivery.cli.pm.nodes.protocols import RoleContext, RoleResult


class ChiefEngineerNode(BaseRoleNode):
    """ChiefEngineer Node - Chief Engineer (Minister of Works).

    Responsible for:
    - Analyzing tasks and generating construction blueprints
    - Technical stack detection
    - Architecture analysis
    - Dependency planning
    """

    @property
    def role_name(self) -> str:
        return "ChiefEngineer"

    def get_dependencies(self) -> list[str]:
        """ChiefEngineer depends on PM completing."""
        return ["PM"]

    def get_trigger_conditions(self) -> list[str]:
        """CE runs after PM completes."""
        return ["pm_complete", "manual"]

    def can_handle(self, context: RoleContext) -> bool:
        """Can handle if PM result is available."""
        return context.pm_result is not None or len(context.get_tasks()) > 0

    def _execute_impl(self, context: RoleContext) -> RoleResult:
        """Execute ChiefEngineer logic to generate blueprints."""
        from polaris.cells.chief_engineer.blueprint.public import run_pre_dispatch_chief_engineer
        from polaris.delivery.cli.pm.chief_engineer import run_chief_engineer_analysis

        workspace = context.workspace_full
        iteration = context.pm_iteration
        run_id = context.run_id
        args = context.args

        # Get tasks from PM result
        tasks = context.get_tasks()
        if not tasks:
            return self._create_success_result(
                tasks=[],
                next_role="Director",
                continue_reason="No tasks to process",
            )

        # Resolve paths
        metadata = context.metadata if isinstance(context.metadata, dict) else {}
        run_dir = getattr(args, "run_dir", "") if args else ""
        if not run_dir:
            run_dir = context.run_dir
        if not run_dir:
            run_dir = str(metadata.get("run_dir") or "").strip()

        run_events = context.events_path
        dialogue_full = context.dialogue_path

        # Run CE stage via legacy-compatible implementation.
        # analysis_runner is explicitly injected from delivery layer so that
        # cells/ never needs to import from delivery/ (ACGA 2.0 layer rule).
        start_time = time.time()
        try:
            result = run_pre_dispatch_chief_engineer(
                args=args,
                workspace_full=workspace,
                cache_root_full=context.cache_root_full,
                run_dir=run_dir,
                run_id=run_id,
                pm_iteration=iteration,
                tasks=tasks,
                run_events=run_events,
                dialogue_full=dialogue_full,
                analysis_runner=run_chief_engineer_analysis,
            )
        except (RuntimeError, ValueError) as e:
            return self._create_error_result(
                error=f"ChiefEngineer stage failed: {e}",
                error_code="CE_ANALYSIS_ERROR",
            )

        updated_tasks = tasks
        hard_failure = bool(result.get("hard_failure"))
        ran = bool(result.get("ran"))
        reason = str(result.get("reason") or "").strip()
        summary = str(result.get("summary") or "").strip()

        duration_ms = int((time.time() - start_time) * 1000)

        return RoleResult(
            success=not hard_failure,
            exit_code=1 if hard_failure else 0,
            tasks=updated_tasks,
            blueprint={
                "blueprint_path": str(result.get("blueprint_path") or "").strip(),
                "runtime_blueprint_path": str(result.get("runtime_blueprint_path") or "").strip(),
            },
            next_role="Director",
            continue_reason=summary or reason or "ChiefEngineer stage completed",
            metadata={
                "ran": ran,
                "reason": reason,
                "task_update_count": int(result.get("task_update_count") or 0),
                "chief_engineer_result": result,
            },
            duration_ms=duration_ms,
        )

    def _should_run_chief_engineer(self, tasks: list[dict[str, Any]]) -> bool:
        """Determine if CE should run based on task complexity."""
        if not tasks:
            return False

        complex_count = 0
        for task in tasks:
            # Check complexity signals
            target_files = task.get("target_files", [])
            acceptance = task.get("acceptance_criteria", [])
            phase = str(task.get("phase", "")).lower()

            # Bootstrap tasks always need CE
            if phase == "bootstrap":
                complex_count += 2

            # Multi-file tasks with rich acceptance are complex
            if len(target_files) >= 3:
                complex_count += 1
            if len(acceptance) >= 4:
                complex_count += 1

        # Run CE if complexity score is high
        return complex_count >= len(tasks)

    def _apply_blueprint_updates(
        self,
        tasks: list[dict[str, Any]],
        task_update_map: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Apply blueprint updates to tasks."""
        if not task_update_map:
            return tasks

        updated = []
        for task in tasks:
            task_id = task.get("id", "")
            if not task_id:
                updated.append(task)
                continue

            update = task_update_map.get(task_id, {})
            if not update:
                updated.append(task)
                continue

            # Merge update into task
            merged = dict(task)

            # Add construction plan
            if "construction_plan" in update:
                merged["construction_plan"] = update["construction_plan"]

            # Add chief_engineer metadata
            merged["chief_engineer"] = {
                "scope_for_apply": update.get("scope_for_apply", []),
                "missing_targets": update.get("missing_targets", []),
                "blueprint_scope": update.get("blueprint_scope", {}),
            }

            # Add constraints
            ce_constraints = update.get("constraints", [])
            if ce_constraints:
                existing_constraints = merged.get("constraints", [])
                if isinstance(existing_constraints, list):
                    merged["constraints"] = existing_constraints + ce_constraints
                else:
                    merged["constraints"] = ce_constraints

            # Update scope paths
            ce_scope = update.get("scope_for_apply", [])
            if ce_scope:
                existing_scope = merged.get("scope_paths", [])
                if isinstance(existing_scope, list):
                    merged["scope_paths"] = list(dict.fromkeys(existing_scope + ce_scope))
                else:
                    merged["scope_paths"] = ce_scope

            updated.append(merged)

        return updated


__all__ = ["ChiefEngineerNode"]
