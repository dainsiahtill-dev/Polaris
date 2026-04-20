"""PM Node implementation (尚书令).

This module implements the PM role node for generating task contracts
from requirements.
"""

from __future__ import annotations

import json
import time
from typing import Any

from polaris.delivery.cli.pm.nodes.base import BaseRoleNode
from polaris.delivery.cli.pm.nodes.protocols import RoleContext, RoleResult


class PMNode(BaseRoleNode):
    """PM Node - 尚书令 (Prime Minister).

    Responsible for:
    - Parsing requirements and generating task contracts
    - Managing task decomposition
    - Outputting pm_tasks.contract.json
    """

    @property
    def role_name(self) -> str:
        return "PM"

    def get_dependencies(self) -> list[str]:
        """PM has no dependencies - it's the first role."""
        return []

    def get_trigger_conditions(self) -> list[str]:
        """PM is triggered by iteration start or manual invocation."""
        return ["init", "iteration", "manual"]

    def _execute_impl(self, context: RoleContext) -> RoleResult:
        """Execute PM logic to generate task contract."""
        from polaris.delivery.cli.pm.backend import build_pm_prompt, invoke_pm_backend
        from polaris.delivery.cli.pm.config import PmRoleState
        from polaris.delivery.cli.pm.orchestration_core import sync_tasks_to_shangshuling
        from polaris.delivery.cli.pm.tasks import (
            _migrate_tasks_in_place,
            collect_schema_warnings,
            normalize_pm_payload,
        )
        from polaris.infrastructure.compat.io_utils import emit_event

        workspace = context.workspace_full
        iteration = context.pm_iteration
        run_id = context.run_id
        args = context.args

        if not args:
            return self._create_error_result(
                error="No args provided",
                error_code="PM_NO_ARGS",
            )

        # Build role state
        pm_llm_events_full = getattr(args, "pm_llm_events_path", "")
        pm_last_full = getattr(args, "pm_last_message_path", "")

        role_state = PmRoleState(
            workspace_full=workspace,
            cache_root_full=context.cache_root_full,
            model=getattr(args, "model", ""),
            show_output=bool(getattr(args, "pm_show_output", False)),
            timeout=getattr(args, "timeout", 0),
            prompt_profile=str(getattr(args, "prompt_profile", "") or ""),
            output_path=pm_last_full,
            events_path=context.events_path,
            log_path=getattr(args, "pm_report", ""),
            llm_events_path=pm_llm_events_full,
        )

        # Resolve backend
        from polaris.delivery.cli.pm.backend import ensure_pm_backend_available, resolve_pm_backend_kind

        requested_backend = str(getattr(args, "pm_backend", "auto") or "auto").strip().lower()
        backend, _backend_llm_cfg = resolve_pm_backend_kind(requested_backend, role_state)
        ensure_pm_backend_available(backend)

        # Build prompt
        prompt = build_pm_prompt(
            context.requirements,
            context.plan_text,
            context.gap_report,
            context.last_qa,
            context.last_tasks,
            context.pm_state.get("last_director_result"),
            context.pm_state,
            iteration=iteration,
            run_id=run_id,
            events_path=context.events_path,
            workspace_root=workspace,
        )

        # Invoke LLM
        start_time = time.time()
        try:
            output = invoke_pm_backend(
                role_state,
                prompt,
                backend,
                args,
                context.usage_ctx,  # type: ignore[arg-type]  # Add missing usage_ctx parameter
            )
        except (RuntimeError, ValueError) as e:
            return self._create_error_result(
                error=f"PM backend invoke failed: {e}",
                error_code="PM_INVOKE_ERROR",
            )

        # Parse output
        try:
            payload = json.loads(output)
        except (RuntimeError, ValueError):
            # Try extraction
            from polaris.delivery.cli.pm.backend import _extract_json_from_llm_output

            payload = _extract_json_from_llm_output(output)
            if payload is None:
                payload = {"focus": "parse_failed", "tasks": []}

        # Normalize payload
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        normalized = normalize_pm_payload(payload, iteration, timestamp)

        # Migrate tasks
        _migrate_tasks_in_place(normalized)

        # Collect warnings
        warnings = collect_schema_warnings(normalized, workspace)

        # Validate tasks
        tasks = normalized.get("tasks", [])
        if not tasks and context.requirements.strip():
            # Generate fallback tasks from requirements
            from polaris.delivery.cli.pm.tasks_utils import build_requirements_fallback_payload

            fallback = build_requirements_fallback_payload(
                requirements=context.requirements,
                iteration=iteration,
                timestamp=timestamp,
                plan_text=context.plan_text,
            )
            if fallback:
                normalized = fallback
                tasks = normalized.get("tasks", [])

        # Persist contract
        pm_out_full = getattr(args, "pm_out", "")
        run_pm_tasks = getattr(args, "pm_task_path", "")

        from polaris.infrastructure.compat.io_utils import write_json_atomic

        for path in (pm_out_full, run_pm_tasks):
            if path:
                try:
                    write_json_atomic(path, normalized)
                except (RuntimeError, ValueError) as e:
                    warnings.append(f"Failed to persist to {path}: {e}")

        # Emit events
        if context.events_path:
            emit_event(
                context.events_path,
                kind="status",
                actor="PM",
                name="pm_tasks_generated",
                refs={"run_id": run_id, "phase": "planning"},
                summary="PM generated task contract",
                ok=True,
                output={
                    "task_count": len(tasks),
                    "warnings": warnings,
                },
            )

        # Determine next role
        next_role = "ChiefEngineer"
        if not self._should_run_chief_engineer(normalized):
            next_role = "Director"

        shangshuling_synced = 0
        try:
            shangshuling_synced = int(sync_tasks_to_shangshuling(workspace, tasks) or 0)
        except (RuntimeError, ValueError):
            shangshuling_synced = 0

        duration_ms = int((time.time() - start_time) * 1000)

        return RoleResult(
            success=True,
            exit_code=0,
            tasks=tasks,
            contract=normalized,
            warnings=warnings,
            next_role=next_role,
            continue_reason=f"Generated {len(tasks)} tasks",
            metadata={
                "backend": backend,
                "iteration": iteration,
                "shangshuling_synced": shangshuling_synced,
            },
            duration_ms=duration_ms,
        )

    def _should_run_chief_engineer(self, normalized: dict[str, Any]) -> bool:
        """Determine if ChiefEngineer should run."""
        tasks = normalized.get("tasks", [])
        if not tasks:
            return False

        # Check task complexity
        complex_tasks = 0
        for task in tasks:
            target_files = task.get("target_files", [])
            acceptance = task.get("acceptance_criteria", [])
            if len(target_files) >= 3 or len(acceptance) >= 4:
                complex_tasks += 1

        # Run CE if >30% tasks are complex
        return complex_tasks > len(tasks) * 0.3 if tasks else False


__all__ = ["PMNode"]
