"""Chief Engineer Preflight Module.

This module handles Chief Engineer preflight checks and decision making.
Designed to be testable as pure functions where possible.

Refactoring notes (God Object fix):
- run_pre_dispatch_chief_engineer's 10 positional params collapsed into PreflightContext
- EventEmitter extracted as injectable protocol to break import-time coupling
- All public symbols preserved for backward compatibility
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Contracts & Protocols
# ═══════════════════════════════════════════════════════════════════════


class EventEmitter(Protocol):
    """Protocol for emitting audit events. Injected to allow mocking."""

    def emit_event(
        self,
        path: str,
        *,
        kind: str,
        actor: str,
        name: str,
        refs: dict[str, Any],
        summary: str,
        ok: bool,
        output: dict[str, Any],
        error: str,
    ) -> None: ...

    def emit_dialogue(
        self,
        path: str,
        *,
        speaker: str,
        type: str,
        text: str,
        summary: str,
        run_id: str,
        pm_iteration: int,
        refs: dict[str, Any],
        meta: dict[str, Any],
    ) -> None: ...


class _NullEventEmitter:
    """No-op emitter used when kernelone.tool_execution.io_utils is unavailable."""

    def emit_event(self, path: str, **kwargs: Any) -> None:  # type: ignore[override]
        return None

    def emit_dialogue(self, path: str, **kwargs: Any) -> None:  # type: ignore[override]
        return None


def _load_kernelone_emitter() -> EventEmitter:
    """Load the real event emitter, fall back to no-op on ImportError."""
    try:
        from polaris.infrastructure.compat.io_utils import emit_dialogue, emit_event

        class _KernelOneEmitter:
            def emit_event(self, path: str, **kwargs: Any) -> None:  # type: ignore[override]
                emit_event(path, **kwargs)

            def emit_dialogue(self, path: str, **kwargs: Any) -> None:  # type: ignore[override]
                emit_dialogue(path, **kwargs)

        return _KernelOneEmitter()
    except ImportError:
        return _NullEventEmitter()


# ═══════════════════════════════════════════════════════════════════════
# Preflight Context — replaces 10-arg God function signature
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class PreflightContext:
    """All inputs needed by run_pre_dispatch_chief_engineer.

    Grouping these into a single value object:
    1. Makes call sites self-documenting (named fields vs positional args).
    2. Enables partial construction + DI overrides in tests.
    3. Isolates the parameter boundary from the implementation.
    """

    workspace_full: str
    cache_root_full: str
    run_dir: str
    run_id: str
    pm_iteration: int
    tasks: Any
    run_events: str
    dialogue_full: str
    # Injectable overrides (default None = load lazily)
    analysis_runner: Callable[..., Any] | None = field(default=None, repr=False)
    event_emitter: EventEmitter | None = field(default=None, repr=False)

    # Legacy: args namespace carried for backward compat (ignored internally)
    args: Any = field(default=None, repr=False)

    def effective_emitter(self) -> EventEmitter:
        if self.event_emitter is not None:
            return self.event_emitter
        return _load_kernelone_emitter()

    def effective_runner(self) -> Callable[..., Any]:
        if self.analysis_runner is not None:
            return self.analysis_runner
        raise RuntimeError(
            "PreflightContext.analysis_runner is required but was not injected. "
            "Callers in the delivery layer must pass analysis_runner explicitly "
            "(e.g. from polaris.delivery.cli.pm.chief_engineer import run_chief_engineer_analysis). "
            "cells/ must not import from delivery/."
        )


# ═══════════════════════════════════════════════════════════════════════
# Preflight Result Builder (pure, easily testable)
# ═══════════════════════════════════════════════════════════════════════


def _build_failure_result(
    *,
    run_blueprint_path: str,
    runtime_blueprint_path: str,
    reason: str,
    summary: str,
    tasks: Any,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "role": "ChiefEngineer",
        "ran": True,
        "hard_failure": True,
        "reason": reason,
        "summary": summary,
        "blueprint_path": run_blueprint_path,
        "runtime_blueprint_path": runtime_blueprint_path,
        "task_update_count": 0,
        "task_updates": [],
        "task_update_map": {},
        "stats": {},
    }


def _normalize_success_result(
    result: dict[str, Any],
    *,
    run_blueprint_path: str,
    runtime_blueprint_path: str,
) -> dict[str, Any]:
    result = dict(result)
    result.setdefault("schema_version", 1)
    result.setdefault("role", "ChiefEngineer")
    result.setdefault("ran", True)
    result.setdefault("hard_failure", False)
    result.setdefault("reason", "chief_engineer_updated")
    result.setdefault("summary", "ChiefEngineer preflight completed")
    result.setdefault("task_update_count", len(result.get("task_updates") or []))
    result.setdefault("task_updates", [])
    result.setdefault("task_update_map", {})
    result.setdefault("stats", {})
    result["blueprint_path"] = str(result.get("blueprint_path") or run_blueprint_path).strip()
    result["runtime_blueprint_path"] = str(result.get("runtime_blueprint_path") or runtime_blueprint_path).strip()
    return result


# ═══════════════════════════════════════════════════════════════════════
# Core Preflight Logic (extracted, uses PreflightContext)
# ═══════════════════════════════════════════════════════════════════════


def _execute_preflight(ctx: PreflightContext) -> dict[str, Any]:
    """Run Chief Engineer analysis using the context's runner.

    Separated from event emission so it can be tested in isolation.
    """
    from polaris.cells.runtime.artifact_store.public.service import resolve_artifact_path

    run_blueprint_path = os.path.join(ctx.run_dir, "contracts", "chief_engineer.blueprint.json")
    runtime_blueprint_path = resolve_artifact_path(
        ctx.workspace_full,
        ctx.cache_root_full,
        "runtime/contracts/chief_engineer.blueprint.json",
    )
    runner = ctx.effective_runner()

    try:
        result = runner(
            tasks=ctx.tasks,
            workspace_full=ctx.workspace_full,
            run_id=ctx.run_id,
            pm_iteration=int(ctx.pm_iteration or 0),
            run_blueprint_path=run_blueprint_path,
            runtime_blueprint_path=runtime_blueprint_path,
        )
    except (RuntimeError, ValueError) as exc:
        logger.error("ChiefEngineer preflight check failed: %s", exc, exc_info=True)
        return _build_failure_result(
            run_blueprint_path=run_blueprint_path,
            runtime_blueprint_path=runtime_blueprint_path,
            reason="chief_engineer_error",
            summary=f"ChiefEngineer preflight failed: {exc}",
            tasks=ctx.tasks,
        )

    if not isinstance(result, dict):
        return _build_failure_result(
            run_blueprint_path=run_blueprint_path,
            runtime_blueprint_path=runtime_blueprint_path,
            reason="chief_engineer_invalid_result",
            summary=f"ChiefEngineer preflight returned invalid result: {type(result)}",
            tasks=ctx.tasks,
        )

    return _normalize_success_result(
        result,
        run_blueprint_path=run_blueprint_path,
        runtime_blueprint_path=runtime_blueprint_path,
    )


def _emit_preflight_events(ctx: PreflightContext, result: dict[str, Any]) -> None:
    """Emit audit events after preflight completes. Side-effect only."""
    emitter = ctx.effective_emitter()
    hard_failure = bool(result.get("hard_failure"))
    is_failure = hard_failure

    # Choose event name based on outcome
    event_name = "chief_engineer_preflight_failed" if is_failure else "chief_engineer_preflight_completed"
    run_id = ctx.run_id
    pm_iteration = int(ctx.pm_iteration or 0)
    summary = str(result.get("summary") or "").strip()
    bp_path = str(result.get("blueprint_path") or "").strip()
    rt_bp_path = str(result.get("runtime_blueprint_path") or "").strip()

    emitter.emit_event(
        ctx.run_events,
        kind="status",
        actor="ChiefEngineer",
        name=event_name,
        refs={
            "run_id": run_id,
            "phase": "chief_engineer",
            "files": [bp_path, rt_bp_path] if not is_failure else [],
        },
        summary=summary,
        ok=not hard_failure,
        output={
            "task_count": len(ctx.tasks) if isinstance(ctx.tasks, list) else 0,
            "task_update_count": int(result.get("task_update_count") or 0),
            "reason": str(result.get("reason") or "").strip(),
        },
        error="" if not hard_failure else summary,
    )
    emitter.emit_dialogue(
        ctx.dialogue_full,
        speaker="ChiefEngineer",
        type="analysis",
        text=summary,
        summary="ChiefEngineer preflight completed" if not is_failure else "ChiefEngineer preflight failed",
        run_id=run_id,
        pm_iteration=pm_iteration,
        refs={
            "phase": "chief_engineer",
            "files": [bp_path, rt_bp_path],
        },
        meta={
            "hard_failure": hard_failure,
            "task_update_count": int(result.get("task_update_count") or 0),
        },
    )


# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════


def run_pre_dispatch_chief_engineer_ctx(ctx: PreflightContext) -> dict[str, Any]:
    """Execute Chief Engineer preflight from a PreflightContext.

    Preferred over the legacy 10-argument form for new call sites.
    """
    result = _execute_preflight(ctx)
    _emit_preflight_events(ctx, result)
    return result


def run_pre_dispatch_chief_engineer(
    *,
    args: Any,
    workspace_full: str,
    cache_root_full: str,
    run_dir: str,
    run_id: str,
    pm_iteration: int,
    tasks: Any,
    run_events: str,
    dialogue_full: str,
    analysis_runner: Any | None = None,
) -> dict[str, Any]:
    """Execute Chief Engineer preflight using the canonical analysis pipeline.

    Backward-compatible wrapper around run_pre_dispatch_chief_engineer_ctx.

    Args:
        args: Command line arguments (retained for backward compat, unused)
        workspace_full: Workspace path
        cache_root_full: Cache root path
        run_dir: Run directory
        run_id: Run identifier
        pm_iteration: Iteration number
        tasks: Tasks to analyze
        run_events: Events file path
        dialogue_full: Dialogue file path
        analysis_runner: Optional analysis runner override

    Returns:
        Chief Engineer result dict
    """
    ctx = PreflightContext(
        args=args,
        workspace_full=workspace_full,
        cache_root_full=cache_root_full,
        run_dir=run_dir,
        run_id=run_id,
        pm_iteration=pm_iteration,
        tasks=tasks,
        run_events=run_events,
        dialogue_full=dialogue_full,
        analysis_runner=analysis_runner,
    )
    return run_pre_dispatch_chief_engineer_ctx(ctx)


def build_task_focused_chief_engineer_payload(
    *,
    task: dict[str, Any],
    task_update: dict[str, Any],
    blueprint_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build Chief Engineer payload focused on specific task.

    Args:
        task: Original task definition
        task_update: Task update with latest info
        blueprint_data: Blueprint data from previous runs

    Returns:
        Chief Engineer payload dict
    """
    payload: dict[str, Any] = {
        "task_id": str(task_update.get("task_id") or task.get("id") or "").strip(),
        "scope_for_apply": _normalize_path_list(task_update.get("scope_for_apply") or []),
        "missing_targets": _normalize_path_list(task_update.get("missing_targets") or []),
    }

    construction_plan = task_update.get("construction_plan")
    if isinstance(construction_plan, dict):
        compact_plan: dict[str, Any] = {}
        file_plans = (
            construction_plan.get("file_plans") if isinstance(construction_plan.get("file_plans"), list) else []
        )
        if not isinstance(file_plans, list):
            file_plans = []
        compact_file_plans: list[dict[str, Any]] = []
        for file_plan in file_plans[:12]:
            normalized = _normalize_file_plan_entry(file_plan)
            if normalized is None:
                continue
            compact_file_plans.append(normalized)
        if compact_file_plans:
            compact_plan["file_plans"] = compact_file_plans
        method_catalog = _trim_str_list(construction_plan.get("method_catalog"), limit=20)
        if method_catalog:
            compact_plan["method_catalog"] = method_catalog
        verification_steps = _trim_str_list(
            construction_plan.get("verification_steps"),
            limit=15,
        )
        if verification_steps:
            compact_plan["verification_steps"] = verification_steps

        if compact_plan:
            payload["construction_plan"] = compact_plan

    task_title = str(task.get("title") or "").strip()
    task_goal = str(task.get("goal") or "").strip()
    task_description = str(task.get("description") or "").strip()

    if task_title:
        payload["task_title"] = task_title
    if task_goal:
        payload["task_goal"] = task_goal
    if task_description:
        payload["task_description"] = task_description

    return payload


def inject_chief_engineer_constraints(
    chief_payload: dict[str, Any],
    *,
    tasks: list[dict[str, Any]],
    workspace_full: str,
) -> dict[str, Any]:
    """Inject constraints into Chief Engineer payload.

    Args:
        chief_payload: Base Chief Engineer payload
        tasks: All tasks for context
        workspace_full: Workspace path

    Returns:
        Constrained payload dict
    """
    constraints: dict[str, Any] = {}

    task_ids = [str(t.get("id") or "").strip() for t in tasks if isinstance(t, dict)]
    task_ids = [tid for tid in task_ids if tid]
    if task_ids:
        constraints["task_ids"] = task_ids

    all_scope_paths: list[str] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        paths = task.get("scope_paths") or []
        all_scope_paths.extend(_normalize_path_list(paths))
    if all_scope_paths:
        constraints["all_scope_paths"] = list(set(all_scope_paths))[:50]

    unique_modules = list(set(_module_key_from_path(p) for p in all_scope_paths))
    if unique_modules:
        constraints["affected_modules"] = unique_modules[:20]

    chief_payload["constraints"] = constraints
    return chief_payload


def chief_engineer_auto_decision(director_tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Make automatic decision for Chief Engineer.

    Analyzes director tasks and determines if they can proceed or need intervention.

    Args:
        director_tasks: List of director tasks

    Returns:
        Decision dict with 'proceed' boolean and 'reason'
    """
    if not director_tasks:
        return {"proceed": False, "reason": "no_tasks", "needs_review": True}

    needs_review_count = 0
    blocked_count = 0
    total_tasks = len(director_tasks)

    for task in director_tasks:
        if not isinstance(task, dict):
            continue
        status = str(task.get("status") or "").lower()
        if status in ("blocked", "failed"):
            blocked_count += 1
        if task.get("needs_review"):
            needs_review_count += 1

    if blocked_count > 0:
        return {
            "proceed": False,
            "reason": f"{blocked_count} tasks blocked/failed",
            "blocked_count": blocked_count,
            "needs_review": True,
        }

    if needs_review_count > 0:
        return {
            "proceed": False,
            "reason": f"{needs_review_count} tasks need review",
            "needs_review_count": needs_review_count,
            "needs_review": True,
        }

    if total_tasks >= 10 and needs_review_count == 0:
        return {
            "proceed": True,
            "reason": "all tasks ready",
            "task_count": total_tasks,
        }

    return {"proceed": True, "reason": "auto_approved", "task_count": total_tasks}


# ═══════════════════════════════════════════════════════════════════════
# Internal Helpers
# ═══════════════════════════════════════════════════════════════════════


def _normalize_path_list(value: Any) -> list[str]:
    """Normalize path list from various input formats."""
    if isinstance(value, str):
        entries = [segment.strip() for segment in value.split(",") if segment.strip()]
    elif isinstance(value, list):
        entries = [str(item).strip() for item in value if item]
    else:
        return []
    return [e.replace("\\", "/").lstrip("./") for e in entries if e]


def _normalize_file_plan_entry(file_plan: Any) -> dict[str, Any] | None:
    """Normalize a file plan entry."""
    if isinstance(file_plan, dict):
        return {
            "path": str(file_plan.get("path") or "").strip(),
            "action": str(file_plan.get("action") or "modify").strip().lower(),
            "content": file_plan.get("content"),
        }
    if isinstance(file_plan, str):
        path = file_plan.strip()
        if not path:
            return None
        action = "modify"
        if path.startswith("+"):
            path = path[1:].strip()
            action = "create"
        elif path.startswith("-"):
            path = path[1:].strip()
            action = "delete"
        return {"path": path, "action": action}
    return None


def _trim_str_list(values: Any, limit: int = 20) -> list[str]:
    """Trim and limit string list."""
    if isinstance(values, str):
        values = [v.strip() for v in values.split(",") if v.strip()]
    elif isinstance(values, list):
        values = [str(v).strip() for v in values if v]
    else:
        return []
    return values[:limit]


def _module_key_from_path(path: str) -> str:
    """Extract module key from file path.

    This version is consistent with chief_engineer.py's _module_key.

    Args:
        path: File path

    Returns:
        Module key string
    """
    normalized = str(path or "").replace("\\", "/").strip()
    if not normalized:
        return "root"
    parts = [p for p in normalized.split("/") if p]
    if not parts:
        return "root"
    if parts[0] in {"src", "app", "backend", "frontend", "cmd", "internal", "lib"} and len(parts) >= 2:
        return "/".join(parts[:2])
    return parts[0]


def _tail_non_empty_lines(text: str, *, limit: int = 8) -> list[str]:
    """Get non-empty tail lines from text.

    Args:
        text: Input text
        limit: Maximum number of lines to return

    Returns:
        List of non-empty lines from the end
    """
    lines = [str(line).rstrip() for line in str(text or "").splitlines() if str(line).strip()]
    if len(lines) <= limit:
        return lines
    return lines[-limit:]


def _collect_task_scope_modules(
    task: dict[str, Any],
    task_update: dict[str, Any],
) -> list[str]:
    """Collect module keys from task scope.

    Args:
        task: Original task definition
        task_update: Task update with latest info

    Returns:
        List of unique module keys (max 12)
    """
    candidates: list[str] = []
    candidates.extend(_normalize_path_list(task.get("target_files") or []))
    candidates.extend(_normalize_path_list(task.get("scope_paths") or task.get("scope") or []))
    candidates.extend(_normalize_path_list(task_update.get("scope_for_apply") or []))
    candidates.extend(_normalize_path_list(task_update.get("missing_targets") or []))
    construction_plan = task_update.get("construction_plan")
    if isinstance(construction_plan, dict):
        raw_file_plans = construction_plan.get("file_plans")
        file_plans: list[Any] = raw_file_plans if isinstance(raw_file_plans, list) else []
        for file_plan in file_plans:
            if not isinstance(file_plan, dict):
                continue
            path = str(file_plan.get("path") or "").strip()
            if path:
                candidates.append(path)
    modules: list[str] = []
    for item in candidates:
        module_key = _module_key_from_path(item)
        if module_key and module_key not in modules:
            modules.append(module_key)
    return modules[:12]


def _slice_blueprint_for_task(
    *,
    task: dict[str, Any],
    task_update: dict[str, Any],
    blueprint_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Slice blueprint data for specific task scope.

    Args:
        task: Original task definition
        task_update: Task update with latest info
        blueprint_data: Blueprint data from previous runs

    Returns:
        Sliced blueprint data dict
    """
    if not isinstance(blueprint_data, dict):
        return {}

    scope_modules = _collect_task_scope_modules(task, task_update)
    module_order_all = _trim_str_list(blueprint_data.get("module_order"), limit=64)
    scoped_module_order = [item for item in module_order_all if item in scope_modules][:12]
    if not scoped_module_order and scope_modules:
        scoped_module_order = scope_modules[:12]

    architecture_constraints = _trim_str_list(
        blueprint_data.get("architecture_constraints"),
        limit=8,
    )

    module_architecture: dict[str, dict[str, Any]] = {}
    raw_module_arch = (
        blueprint_data.get("module_architecture") if isinstance(blueprint_data.get("module_architecture"), dict) else {}
    )
    for module_key in scoped_module_order:
        node = raw_module_arch.get(module_key) if isinstance(raw_module_arch, dict) else None
        if not isinstance(node, dict):
            continue
        normalized_node: dict[str, Any] = {}
        if "layer" in node:
            normalized_node["layer"] = node.get("layer")
        if "stability_score" in node:
            normalized_node["stability_score"] = node.get("stability_score")
        raw_deps = node.get("dependencies")
        dependencies = _trim_str_list(raw_deps, limit=8) if isinstance(raw_deps, list) else []
        if dependencies:
            normalized_node["dependencies"] = dependencies
        raw_symbols = node.get("public_symbols")
        public_symbols = _trim_str_list(raw_symbols, limit=10) if isinstance(raw_symbols, list) else []
        if public_symbols:
            normalized_node["public_symbols"] = public_symbols
        if normalized_node:
            module_architecture[module_key] = normalized_node

    api_contracts: list[dict[str, Any]] = []
    raw_api_contracts_val = blueprint_data.get("api_contracts")
    raw_api_contracts: list[Any] = raw_api_contracts_val if isinstance(raw_api_contracts_val, list) else []
    for item in raw_api_contracts:
        if not isinstance(item, dict):
            continue
        provider = str(item.get("provider") or item.get("producer") or "").strip()
        consumer = str(item.get("consumer") or "").strip()
        contract_name = str(item.get("name") or item.get("contract") or "").strip()
        if scope_modules and not any(token in scope_modules for token in (provider, consumer)):
            continue
        compact: dict[str, Any] = {
            "provider": provider,
            "consumer": consumer,
            "name": contract_name,
        }
        signature = str(item.get("signature") or "").strip()
        if signature:
            compact["signature"] = signature
        api_contracts.append(compact)
        if len(api_contracts) >= 8:
            break

    return {
        "scope_modules": scope_modules,
        "module_order": scoped_module_order,
        "api_contracts": api_contracts,
        "module_architecture": module_architecture,
        "architecture_constraints": architecture_constraints,
        "blueprint_digest": _safe_payload_digest(blueprint_data),
    }


def _safe_payload_digest(payload: Any) -> str:
    """Generate a safe digest from payload for caching/logging.

    Args:
        payload: Any JSON-serializable payload

    Returns:
        MD5 hex digest (truncated to 16 chars)
    """
    import hashlib

    try:
        normalized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(normalized.encode("utf-8")).hexdigest()[:16]
    except (RuntimeError, ValueError) as exc:
        logger.debug("Failed to compute payload hash: %s", exc)
        return "invalid"


__all__ = [
    "EventEmitter",
    # New API
    "PreflightContext",
    "_collect_task_scope_modules",
    "_module_key_from_path",
    "_slice_blueprint_for_task",
    "_tail_non_empty_lines",
    # Legacy API (backward compat)
    "build_task_focused_chief_engineer_payload",
    "chief_engineer_auto_decision",
    "inject_chief_engineer_constraints",
    "run_pre_dispatch_chief_engineer",
    "run_pre_dispatch_chief_engineer_ctx",
]
