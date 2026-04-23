"""Sequential Engine 集成

包含 Sequential Engine 和 Hybrid Engine 的执行逻辑。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from polaris.cells.roles.runtime.public import (
    FailureClass,
    SequentialEngine,
    SequentialMode,
    SequentialTraceLevel,
)

from .dialogue import seq_llm_caller

logger = logging.getLogger(__name__)


def build_sequential_config(
    settings: Any,
    context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Build Sequential configuration from settings and context."""
    from polaris.cells.roles.runtime.public import create_sequential_budget

    from .helpers import (
        _seq_resolve_bool,
        _seq_resolve_int,
        _seq_resolve_str,
    )

    sentinel = object()
    seq_enabled = _seq_resolve_bool(settings, sentinel, "seq_enabled", "KERNELONE_SEQ_ENABLED", False)
    if not seq_enabled:
        return None
    enabled_roles = [
        r.strip()
        for r in _seq_resolve_str(
            settings,
            sentinel,
            "seq_default_roles",
            "KERNELONE_SEQ_DEFAULT_ROLES",
            "director,adaptive",
        ).split(",")
    ]
    if "director" not in enabled_roles:
        return None

    max_steps = _seq_resolve_int(settings, sentinel, "seq_max_steps", "KERNELONE_SEQ_MAX_STEPS", 12, minimum=1)
    max_tool_calls = _seq_resolve_int(
        settings,
        sentinel,
        "seq_max_tool_calls_total",
        "KERNELONE_SEQ_MAX_TOOL_CALLS_TOTAL",
        24,
        minimum=1,
    )
    max_no_progress = _seq_resolve_int(
        settings,
        sentinel,
        "seq_max_no_progress_steps",
        "KERNELONE_SEQ_MAX_NO_PROGRESS_STEPS",
        3,
        minimum=1,
    )
    max_wall_time = _seq_resolve_int(
        settings,
        sentinel,
        "seq_max_wall_time_seconds",
        "KERNELONE_SEQ_MAX_WALL_TIME_SECONDS",
        120,
        minimum=1,
    )
    trace_level = _seq_resolve_str(settings, sentinel, "seq_trace_level", "KERNELONE_SEQ_TRACE_LEVEL", "summary")
    trace_level_enum = SequentialTraceLevel.SUMMARY
    if trace_level == "off":
        trace_level_enum = SequentialTraceLevel.OFF
    elif trace_level == "detailed":
        trace_level_enum = SequentialTraceLevel.DETAILED

    budget = create_sequential_budget(
        max_steps=max_steps,
        max_tool_calls_total=max_tool_calls,
        max_no_progress_steps=max_no_progress,
        max_wall_time_seconds=max_wall_time,
    )
    mode = resolve_sequential_mode(settings, sentinel, context)
    use_hybrid = _seq_resolve_bool(settings, sentinel, "seq_use_hybrid", "KERNELONE_SEQ_USE_HYBRID", False)
    return {
        "mode": mode,
        "budget": budget,
        "trace_level": trace_level_enum,
        "use_hybrid": use_hybrid,
    }


def resolve_sequential_mode(
    settings: Any,
    sentinel: object,
    context: dict[str, Any] | None,
) -> SequentialMode:
    """Resolve SequentialMode from settings and context."""
    from .helpers import _seq_resolve_str

    default_mode = _seq_resolve_str(settings, sentinel, "seq_default_mode", "KERNELONE_SEQ_DEFAULT_MODE", "enabled")
    default_mode_token = str(default_mode or "").strip().lower()
    mode = SequentialMode.ENABLED
    if default_mode_token == "disabled":
        mode = SequentialMode.DISABLED
    elif default_mode_token == "required":
        mode = SequentialMode.REQUIRED
    if context:
        seq_mode = context.get("sequential_mode")
        if seq_mode == "disabled":
            mode = SequentialMode.DISABLED
        elif seq_mode == "required":
            mode = SequentialMode.REQUIRED
        elif seq_mode == "enabled":
            mode = SequentialMode.ENABLED
    return mode


async def execute_sequential(
    workspace: str,
    role_id: str,
    task: dict[str, Any],
    task_id: str,
    run_id: str,
    context: dict[str, Any] | None,
    seq_config: dict[str, Any],
    call_role_llm_with_timeout: Any,
    emit_task_trace_event: Any,
    build_director_message: Any,
) -> dict[str, Any]:
    """Execute task using Sequential Engine."""
    from polaris.cells.roles.runtime.public.service import registry

    budget = seq_config["budget"]
    try:
        profile = registry.get_profile_or_raise(role_id)
    except ValueError as exc:
        return {"success": False, "error": f"Failed to load profile: {exc}"}

    engine = SequentialEngine(
        workspace=workspace,
        budget=budget,
        trace_level=seq_config["trace_level"].value,
    )
    engine.set_context(role=role_id, run_id=run_id, task_id=task_id)

    async def _wrap_llmCaller(**kwargs: Any) -> str:
        prompt = str(kwargs.get("prompt") or "").strip()
        return await seq_llm_caller(
            workspace,
            role_id,
            prompt,
            context or {},
            call_role_llm_with_timeout,
        )

    engine.set_dependencies(llm_caller=_wrap_llmCaller, tool_gateway=None)
    message = build_director_message(task)
    stats = await engine.execute(initial_message=message, profile=profile)

    await emit_task_trace_event(
        task_id=task_id,
        phase="sequential_complete",
        step_kind="sequential",
        step_title="Sequential execution completed",
        step_detail=f"Completed: reason={stats.termination_reason}, steps={stats.steps}",
        status="completed",
        run_id=run_id,
    )
    return {
        "success": stats.failure_class == FailureClass.SUCCESS,
        "task_id": task_id,
        "sequential_stats": {
            "steps": stats.steps,
            "tool_calls": stats.tool_calls,
            "termination_reason": stats.termination_reason,
        },
        "mode": "sequential",
    }


async def execute_hybrid(
    workspace: str,
    role_id: str,
    task: dict[str, Any],
    task_id: str,
    run_id: str,
    context: dict[str, Any] | None,
    seq_config: dict[str, Any],
    emit_task_trace_event: Any,
) -> dict[str, Any]:
    """Execute task using Hybrid Engine."""
    from polaris.cells.roles.engine.public.service import EngineBudget, EngineContext, HybridEngine
    from polaris.cells.roles.runtime.public.service import registry

    try:
        profile = registry.get_profile_or_raise(role_id)
    except ValueError as exc:
        return {"success": False, "error": f"Failed to load profile: {exc}"}

    budget = EngineBudget(
        max_steps=seq_config["budget"].max_steps,
        max_tool_calls_total=seq_config["budget"].max_tool_calls_total,
        max_no_progress_steps=seq_config["budget"].max_no_progress_steps,
        max_wall_time_seconds=seq_config["budget"].max_wall_time_seconds,
    )
    subject = task.get("subject") or task.get("title", "")
    description = task.get("description", "")
    message = f"任务: {subject}\n\n描述: {description}"

    await emit_task_trace_event(
        task_id=task_id,
        phase="hybrid_start",
        step_kind="hybrid",
        step_title="Hybrid execution started",
        step_detail=f"Hybrid Engine started with budget: max_steps={budget.max_steps}",
        status="running",
        run_id=run_id,
    )

    try:
        engine = HybridEngine(
            workspace=workspace,
            budget=budget,
            auto_select=True,
            enable_switching=True,
        )
        engine_context = EngineContext(
            workspace=workspace,
            role=role_id,
            task=message,
            profile=profile,
        )
        result = await engine.run(task=message, context=engine_context)

        await emit_task_trace_event(
            task_id=task_id,
            phase="hybrid_complete",
            step_kind="hybrid",
            step_title="Hybrid execution completed",
            step_detail=f"Strategy: {result.strategy.value}, Steps: {result.total_steps}",
            status="completed",
            run_id=run_id,
        )
        return {
            "success": result.success,
            "task_id": task_id,
            "hybrid_result": {
                "strategy": result.strategy.value,
                "steps": result.total_steps,
                "tool_calls": result.total_tool_calls,
            },
            "mode": "hybrid",
        }
    except (asyncio.TimeoutError, OSError, RuntimeError, TypeError, ValueError):
        await emit_task_trace_event(
            task_id=task_id,
            phase="hybrid_error",
            step_kind="hybrid",
            step_title="Hybrid execution failed",
            step_detail="internal error",
            status="failed",
            run_id=run_id,
        )
        return {"success": False, "error": "internal error", "mode": "hybrid"}
