"""Main console class and entry point for Polaris CLI."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys
import threading
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.host.public import RoleHostKind
from polaris.delivery.cli.cli_completion import load_history, readline_input, save_history
from polaris.delivery.cli.cli_prompt import create_prompt_session
from polaris.delivery.cli.super_mode import (
    SUPER_ROLE,
    SuperBlueprintItem,
    SuperClaimedTask,
    SuperModeRouter,
    SuperPipelineContext,
    SuperRouteDecision,
    SuperTaskItem,
    build_chief_engineer_handoff_message,
    build_director_handoff_message,
    build_director_task_handoff_message,
    build_pm_handoff_message,
    build_super_readonly_message,
    extract_blueprint_items_from_ce_output,
    extract_task_list_from_pm_output,
    write_architect_blueprint_to_disk,
)
from polaris.delivery.cli.terminal._base import (
    _ALLOWED_BACKENDS,
    _apply_keymode,
    _coerce_bool,
    _get_default_keymode,
    _normalize_role,
    _restore_infrastructure_logs,
    _safe_text,
    _set_current_model,
    _show_onboarding,
    _suppress_infrastructure_logs,
)
from polaris.delivery.cli.terminal.commands import (
    _console_display_role,
    _handle_command,
    _resolve_role_session,
)
from polaris.delivery.cli.terminal.events import _run_streaming_turn
from polaris.delivery.cli.terminal.layout import (
    _build_render_state,
    _ConsoleRenderState,
    _print_banner,
    _PromptRenderer,
)
from polaris.kernelone.fs.encoding import enforce_utf8
from polaris.kernelone.traceability.session_source import SessionSource, SourceChain

if TYPE_CHECKING:
    from polaris.delivery.cli.director.console_host import RoleConsoleHost

logger = logging.getLogger(__name__)

# Sentinel for unset output format (CLI case: --output-format not provided)
_UNSET = object()

# Sentinel value to detect if Director has more work to do
_DIRECTOR_CONTINUE_MARKER = (
    "待执行",
    "待处理",
    "remaining",
    "pending",
    "next step",
    "next turn",
    "next round",
    "下一步",
    "下一回合",
    "下一轮",
    "继续执行",
    "未完成",
    "incomplete",
    "执行状态",
    "将使用",
    "i will",
)
_DIRECTOR_DONE_MARKER = (
    "全部完成",
    "任务已全部完成",
    "执行完毕",
    "all tasks complete",
    "all_tasks_complete",
    "all done",
)
_MAX_DIRECTOR_LOOPS = 5
_SUPER_DIRECTOR_MULTI_TURN_REASONS = {"code_delivery", "architect_code_delivery", "architecture_design"}


def _director_output_suggests_more_work(content: str) -> bool:
    """Heuristic: does Director output suggest there is more work to do?"""
    text = str(content or "").lower()
    # If explicitly says done, trust it
    if any(marker in text for marker in _DIRECTOR_DONE_MARKER):
        return False
    # If mentions remaining/pending tasks, continue
    if any(marker in text for marker in _DIRECTOR_CONTINUE_MARKER):
        return True
    # If output is very short (just ack), likely more to do
    return len(text.strip()) < 200


def _run_director_execution_loop(
    host: RoleConsoleHost,
    *,
    session_id: str,
    original_request: str,
    pm_output: str,
    extracted_tasks: list[SuperTaskItem],
    last_result: Any,
    json_render: str,
    debug: bool,
    prompt_renderer: _PromptRenderer,
    workspace_path: Path,
    dry_run: bool,
    output_format: str,
    enable_cognitive: bool | None = None,
) -> Any:
    """Run Director in a loop until all tasks are executed or safety limit reached.

    Each iteration sends a continuation prompt that instructs the Director
    to keep executing remaining tasks without re-planning.
    """

    result = last_result
    for loop_idx in range(1, _MAX_DIRECTOR_LOOPS + 1):
        if result.saw_error:
            logger.info(
                "SUPER_MODE_DIRECTOR_LOOP_BREAK: loop=%d reason=saw_error",
                loop_idx,
            )
            break
        if not _director_output_suggests_more_work(result.final_content):
            logger.info(
                "SUPER_MODE_DIRECTOR_LOOP_BREAK: loop=%d reason=output_suggests_complete",
                loop_idx,
            )
            break

        continuation_message = (
            "[mode:materialize]\n"
            "[SUPER_MODE_DIRECTOR_CONTINUE]\n"
            "instructions:\n"
            "- The previous turn completed part of the PM plan.\n"
            "- Do NOT summarize, report, or explain what you already did.\n"
            "- Do NOT ask the user for confirmation.\n"
            "- Immediately continue executing the REMAINING tasks from the PM plan.\n"
            "- Use edit_file, write_file, or str_replace_editor to make changes.\n"
            "- Just DO the work. No preamble. No conclusion.\n"
            "- If ALL tasks are truly complete, output exactly: ALL_TASKS_COMPLETE\n\n"
            f"original_request: {original_request}\n\n"
            f"pm_plan_summary: {pm_output[:800]}...\n"
            "[/SUPER_MODE_DIRECTOR_CONTINUE]"
        )
        logger.info(
            "SUPER_MODE_DIRECTOR_LOOP: loop=%d/%d session=%s",
            loop_idx,
            _MAX_DIRECTOR_LOOPS,
            session_id,
        )
        result = _run_streaming_turn(
            host,
            role="director",
            session_id=session_id,
            message=continuation_message,
            json_render=json_render,
            debug=debug,
            spinner_label=prompt_renderer.render_spinner_label(
                role="director",
                session_id=session_id,
                workspace=workspace_path,
            ),
            dry_run=dry_run,
            output_format=output_format,
            enable_cognitive=enable_cognitive,
        )
    logger.info(
        "SUPER_MODE_DIRECTOR_LOOP_END: loops=%d final_role=%s saw_error=%s",
        loop_idx,
        result.role,
        result.saw_error,
    )
    return result


def _claim_super_tasks_from_market(
    *,
    workspace: str,
    stage: str,
    worker_role: str,
    task_ids: list[int] | list[str],
    visibility_timeout_seconds: int = 900,
) -> list[SuperClaimedTask]:
    """Claim specific SUPER-mode tasks from task_market for a role stage."""
    claims: list[SuperClaimedTask] = []
    if not task_ids:
        return claims

    try:
        from polaris.cells.runtime.task_market.public.contracts import ClaimTaskWorkItemCommandV1
        from polaris.cells.runtime.task_market.public.service import get_task_market_service

        service = get_task_market_service()
        worker_id = f"super_{worker_role}_{uuid.uuid4().hex[:8]}"
        for task_id in task_ids:
            result = service.claim_work_item(
                ClaimTaskWorkItemCommandV1(
                    workspace=workspace,
                    stage=stage,
                    worker_id=worker_id,
                    worker_role=worker_role,
                    visibility_timeout_seconds=visibility_timeout_seconds,
                    task_id=str(task_id),
                )
            )
            if not result.ok:
                logger.info(
                    "SUPER_MODE_TASK_CLAIM_MISS: role=%s stage=%s task_id=%s reason=%s",
                    worker_role,
                    stage,
                    task_id,
                    getattr(result, "reason", ""),
                )
                continue
            claims.append(
                SuperClaimedTask(
                    task_id=str(result.task_id or task_id).strip(),
                    stage=str(result.stage or stage).strip(),
                    status=str(result.status or stage).strip(),
                    trace_id=str(result.trace_id or "").strip(),
                    run_id=str(result.run_id or "").strip(),
                    lease_token=str(result.lease_token or "").strip(),
                    payload=dict(result.payload or {}),
                )
            )
    except Exception as exc:
        logger.exception(
            "SUPER_MODE_TASK_CLAIM_FAILED: role=%s stage=%s task_ids=%s error=%s",
            worker_role,
            stage,
            task_ids,
            exc,
        )
    return claims


def _acknowledge_super_claims(
    *,
    workspace: str,
    claims: list[SuperClaimedTask],
    next_stage: str,
    summary: str,
    metadata_by_task: dict[str, dict[str, Any]] | None = None,
) -> int:
    """Advance claimed SUPER-mode tasks to the next stage."""
    if not claims:
        return 0

    acked = 0
    try:
        from polaris.cells.runtime.task_market.public.contracts import AcknowledgeTaskStageCommandV1
        from polaris.cells.runtime.task_market.public.service import get_task_market_service

        service = get_task_market_service()
        for claim in claims:
            if not claim.lease_token:
                logger.info(
                    "SUPER_MODE_TASK_ACK_SKIP: task_id=%s next_stage=%s reason=missing_lease",
                    claim.task_id,
                    next_stage,
                )
                continue
            result = service.acknowledge_task_stage(
                AcknowledgeTaskStageCommandV1(
                    workspace=workspace,
                    task_id=claim.task_id,
                    lease_token=claim.lease_token,
                    next_stage=next_stage,
                    summary=summary,
                    metadata=dict((metadata_by_task or {}).get(claim.task_id, {})),
                )
            )
            if result.ok:
                acked += 1
    except Exception as exc:
        logger.exception(
            "SUPER_MODE_TASK_ACK_FAILED: next_stage=%s claims=%s error=%s",
            next_stage,
            [claim.task_id for claim in claims],
            exc,
        )
    return acked


def _run_super_turn_orchestrator(
    host: RoleConsoleHost,
    *,
    decision: SuperRouteDecision,
    role_sessions: dict[str, str],
    host_kind: str,
    session_title: str | None,
    workspace_path: Path,
    prompt_renderer: _PromptRenderer,
    message: str,
    json_render: str,
    debug: bool,
    dry_run: bool,
    output_format: str,
    enable_cognitive: bool | None = None,
) -> Any:
    """Orchestrator-driven SUPER pipeline — uses SuperPipelineConfig for declarative stages.

    This is the new path. Each stage's constraints (exploration limits, tool_choice,
    forbidden tools) are injected via StageConstraint rather than hardcoded prompts.
    PM gets 2 retries by default. CE is skipped when PM produces no output.
    Director always gets tool_choice='required' via API-level enforcement.
    """
    from polaris.delivery.cli.super_pipeline_config import DEFAULT_SUPER_PIPELINE, StageResult
    from polaris.delivery.cli.terminal.layout import _TurnExecutionResult

    config = DEFAULT_SUPER_PIPELINE
    # Filter stages to only those in the decision.roles
    active_stages = tuple(s for s in config.stages if s.role in decision.roles)
    if not active_stages:
        active_session_id = role_sessions.get(decision.fallback_role) or _resolve_role_session(
            host,
            role=decision.fallback_role,
            role_sessions=role_sessions,
            host_kind=host_kind,
            session_title=session_title,
        )
        return _TurnExecutionResult(role=decision.fallback_role, session_id=active_session_id, saw_error=True)

    ctx = SuperPipelineContext(original_request=message)
    last_result: Any | None = None
    stage_results: list[Any] = []

    for stage in active_stages:
        # Skip condition check
        if stage.skip_condition is not None and stage.skip_condition(ctx):
            logger.info("ORCH_STAGE_SKIP: role=%s reason=condition", stage.role)
            stage_results.append(StageResult(role=stage.role, success=True, skipped=True))
            continue

        # Build handoff message with constraint injection
        handoff_kwargs = _orch_build_handoff_kwargs(stage, ctx, message)
        constraint_text = stage.constraint.to_prompt_text()

        try:
            turn_message = stage.handoff_builder(**handoff_kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.error("ORCH_HANDOFF_BUILD_FAILED: role=%s error=%s", stage.role, exc)
            stage_results.append(StageResult(role=stage.role, success=False, error=str(exc)))
            if stage.on_failure == "abort":
                break
            continue

        # Inject constraint text
        if constraint_text:
            turn_message = _orch_inject_constraint(turn_message, constraint_text)

        # Resolve session
        if stage.role == "director" and "director" in role_sessions:
            del role_sessions["director"]
        session_id = role_sessions.get(stage.role) or _resolve_role_session(
            host,
            role=stage.role,
            role_sessions=role_sessions,
            host_kind=host_kind,
            session_title=session_title,
        )

        # Execute with retry
        stage_result, exec_result = _orch_execute_with_retry(
            host,
            stage=stage,
            session_id=session_id,
            turn_message=turn_message,
            prompt_renderer=prompt_renderer,
            workspace_path=workspace_path,
            json_render=json_render,
            debug=debug,
            dry_run=dry_run,
            output_format=output_format,
            enable_cognitive=enable_cognitive,
        )
        stage_results.append(stage_result)
        last_result = exec_result

        if stage_result.skipped:
            continue

        if not stage_result.success:
            if stage.on_failure == "abort":
                break
            elif stage.on_failure in ("skip", "degrade"):
                continue
            # retry already exhausted

        # Update context
        ctx = _orch_update_context(ctx, stage, stage_result, workspace_path)

    # Log completion
    logger.info(
        "ORCH_PIPELINE_COMPLETE: final_role=%s stages=%d success=%d failed=%d",
        last_result.role if last_result else "none",
        len(stage_results),
        sum(1 for s in stage_results if s.success),
        sum(1 for s in stage_results if not s.success and not s.skipped),
    )

    if last_result is None:
        active_session_id = role_sessions.get(decision.fallback_role) or _resolve_role_session(
            host,
            role=decision.fallback_role,
            role_sessions=role_sessions,
            host_kind=host_kind,
            session_title=session_title,
        )
        return _TurnExecutionResult(role=decision.fallback_role, session_id=active_session_id, saw_error=True)
    return last_result


def _orch_build_handoff_kwargs(stage: Any, ctx: SuperPipelineContext, message: str) -> dict[str, Any]:
    """Build kwargs for the stage's handoff_builder from context."""
    kw: dict[str, Any] = {"original_request": ctx.original_request or message}
    if stage.role == "pm":
        kw["architect_output"] = ctx.architect_output
        kw["blueprint_file_path"] = ctx.blueprint_file_path
    elif stage.role == "chief_engineer":
        kw["architect_output"] = ctx.architect_output
        kw["pm_output"] = ctx.pm_output
        kw["claimed_tasks"] = list(ctx.ce_claims)
    elif stage.role == "director":
        kw["architect_output"] = ctx.architect_output
        kw["pm_output"] = ctx.pm_output
        kw["claimed_tasks"] = list(ctx.director_claims)
        kw["blueprint_items"] = list(ctx.blueprint_items)
    return kw


def _orch_inject_constraint(message: str, constraint_text: str) -> str:
    """Inject constraint text before closing SUPER_MODE tag."""
    if not constraint_text:
        return message
    for tag in (
        "[/SUPER_MODE_HANDOFF]",
        "[/SUPER_MODE_PM_HANDOFF]",
        "[/SUPER_MODE_CE_HANDOFF]",
        "[/SUPER_MODE_DIRECTOR_TASK_HANDOFF]",
        "[/SUPER_MODE_READONLY_STAGE]",
    ):
        if tag in message:
            return message.replace(tag, f"{constraint_text}\n{tag}")
    return f"{message}\n\n{constraint_text}"


def _orch_execute_with_retry(
    host: RoleConsoleHost,
    *,
    stage: Any,
    session_id: str,
    turn_message: str,
    prompt_renderer: Any,
    workspace_path: Path,
    json_render: str,
    debug: bool,
    dry_run: bool,
    output_format: str,
    enable_cognitive: bool | None,
) -> tuple[Any, Any]:
    """Execute a stage with retry logic. Returns (StageResult, last _TurnExecutionResult)."""
    from polaris.delivery.cli.super_pipeline_config import StageResult

    last_exec: Any | None = None
    for attempt in range(1, stage.max_retries + 1):
        exec_result = _run_streaming_turn(
            host,
            role=stage.role,
            session_id=session_id,
            message=turn_message,
            json_render=json_render,
            debug=debug,
            spinner_label=prompt_renderer.render_spinner_label(
                role=stage.role,
                session_id=session_id,
                workspace=workspace_path,
            ),
            dry_run=dry_run,
            output_format=output_format,
            enable_cognitive=enable_cognitive,
        )
        last_exec = exec_result
        if exec_result and not exec_result.saw_error and exec_result.final_content.strip():
            if attempt > 1:
                logger.info("ORCH_RETRY_SUCCESS: role=%s attempt=%d", stage.role, attempt)
            sr = StageResult(
                role=stage.role,
                success=True,
                content=exec_result.final_content,
                retry_count=attempt - 1,
            )
            return sr, exec_result
        if attempt < stage.max_retries:
            delay = min(2**attempt, 10)
            logger.warning(
                "ORCH_RETRY: role=%s attempt=%d/%d, waiting %ds",
                stage.role,
                attempt,
                stage.max_retries,
                delay,
            )
            import time as _time

            _time.sleep(delay)
    error_msg = "saw_error" if (exec_result and exec_result.saw_error) else "empty_output"
    sr = StageResult(
        role=stage.role,
        success=False,
        error=error_msg,
        retry_count=stage.max_retries,
    )
    return sr, last_exec


def _orch_update_context(
    ctx: SuperPipelineContext,
    stage: Any,
    result: Any,
    workspace_path: Path,
) -> SuperPipelineContext:
    """Update pipeline context after a successful stage."""
    if stage.role == "architect":
        blueprint_path = write_architect_blueprint_to_disk(
            workspace=str(workspace_path),
            original_request=ctx.original_request,
            architect_output=result.content,
        )
        return SuperPipelineContext(
            original_request=ctx.original_request,
            architect_output=result.content,
            pm_output=ctx.pm_output,
            blueprint_file_path=blueprint_path,
            source_chain=ctx.source_chain.append(SessionSource.ARCHITECT_DESIGNED),
        )
    elif stage.role == "pm":
        pm_tasks = extract_task_list_from_pm_output(result.content)
        return SuperPipelineContext(
            original_request=ctx.original_request,
            architect_output=ctx.architect_output,
            pm_output=result.content,
            blueprint_file_path=ctx.blueprint_file_path,
            extracted_tasks=tuple(pm_tasks),
            source_chain=ctx.source_chain.append(SessionSource.PM_DELEGATED),
        )
    elif stage.role == "director":
        return SuperPipelineContext(
            original_request=ctx.original_request,
            architect_output=ctx.architect_output,
            pm_output=ctx.pm_output,
            blueprint_file_path=ctx.blueprint_file_path,
            extracted_tasks=ctx.extracted_tasks,
            source_chain=ctx.source_chain.append(SessionSource.DIRECTOR_EXECUTED),
        )
    return ctx


def _run_super_turn(
    host: RoleConsoleHost,
    *,
    fallback_role: str,
    role_sessions: dict[str, str],
    host_kind: str,
    session_title: str | None,
    workspace_path: Path,
    render_state: _ConsoleRenderState,
    prompt_renderer: _PromptRenderer,
    message: str,
    json_render: str,
    debug: bool,
    dry_run: bool,
    output_format: str,
    enable_cognitive: bool | None = None,
) -> Any:
    from polaris.delivery.cli.terminal.layout import _TurnExecutionResult

    decision = SuperModeRouter().decide(message, fallback_role=fallback_role)
    logger.debug(
        "super_mode decision: fallback_role=%s reason=%s roles=%s architect=%s pm=%s ce=%s director=%s",
        fallback_role,
        decision.reason,
        ",".join(decision.roles),
        decision.use_architect,
        decision.use_pm,
        decision.use_chief_engineer,
        decision.use_director,
    )

    # FIX-20260427: Opt-in orchestrator path via env var.
    # When enabled, uses SuperPipelineOrchestrator with StageConstraint-based
    # constraint injection and retry/degrade failure handling.
    if os.environ.get("KERNELONE_SUPER_USE_ORCHESTRATOR", "").strip().lower() in ("1", "true", "yes"):
        return _run_super_turn_orchestrator(
            host,
            decision=decision,
            role_sessions=role_sessions,
            host_kind=host_kind,
            session_title=session_title,
            workspace_path=workspace_path,
            prompt_renderer=prompt_renderer,
            message=message,
            json_render=json_render,
            debug=debug,
            dry_run=dry_run,
            output_format=output_format,
            enable_cognitive=enable_cognitive,
        )

    # ── Legacy path (kept for fallback) ────────────────────────────────
    last_result: Any | None = None
    ctx = SuperPipelineContext(
        original_request=message,
        source_chain=SourceChain.root(SessionSource.USER_DIRECT),
    )
    # Mutable accumulators (will be folded back into ctx at end of each stage)
    pm_tasks: list[SuperTaskItem] = []
    published_task_ids: list[int] = []
    ce_claims: list[SuperClaimedTask] = []
    director_claims: list[SuperClaimedTask] = []
    blueprint_items: list[SuperBlueprintItem] = []
    for next_role in decision.roles:
        next_session_id = role_sessions.get(next_role) or _resolve_role_session(
            host,
            role=next_role,
            role_sessions=role_sessions,
            host_kind=host_kind,
            session_title=session_title,
        )
        turn_message = message

        if next_role == "architect":
            turn_message = build_super_readonly_message(
                role="architect",
                original_request=message,
            )
        elif next_role == "pm":
            # Pass blueprint file path if architect already wrote one
            turn_message = build_pm_handoff_message(
                original_request=message,
                architect_output=ctx.architect_output,
                blueprint_file_path=ctx.blueprint_file_path,
            )
        elif next_role == "chief_engineer":
            if not ctx.pm_output.strip():
                logger.info("SUPER_MODE_CE_SKIP: missing_pm_output")
                continue
            pm_tasks = extract_task_list_from_pm_output(ctx.pm_output)
            if pm_tasks and not published_task_ids:
                logger.info(
                    "SUPER_MODE_TASK_EXTRACT: %d tasks from PM output",
                    len(pm_tasks),
                )
                published_task_ids = _persist_super_tasks_to_board(
                    workspace=str(workspace_path),
                    tasks=pm_tasks,
                    original_request=message,
                    publish_stage="pending_design",
                    architect_output=ctx.architect_output,
                    pm_output=ctx.pm_output,
                )
            ce_claims = _claim_super_tasks_from_market(
                workspace=str(workspace_path),
                stage="pending_design",
                worker_role="chief_engineer",
                task_ids=published_task_ids,
            )
            if not ce_claims:
                logger.info("SUPER_MODE_CE_SKIP: no_claimed_pending_design_tasks")
                continue
            turn_message = build_chief_engineer_handoff_message(
                original_request=message,
                architect_output=ctx.architect_output,
                pm_output=ctx.pm_output,
                claimed_tasks=ce_claims,
            )
        elif next_role == "director":
            if "director" in role_sessions:
                del role_sessions["director"]
                logger.debug("SUPER_MODE_FRESH_DIRECTOR_SESSION: cleared cached director session")
            next_session_id = _resolve_role_session(
                host,
                role=next_role,
                role_sessions=role_sessions,
                host_kind=host_kind,
                session_title=session_title,
            )
            if (
                ce_claims
                and last_result is not None
                and last_result.role == "chief_engineer"
                and not last_result.saw_error
            ):
                blueprint_items = extract_blueprint_items_from_ce_output(
                    last_result.final_content,
                    claimed_tasks=ce_claims,
                )
                metadata_by_task = {
                    item.task_id: {
                        "blueprint_id": item.blueprint_id,
                        "blueprint_summary": item.summary,
                        "scope_paths": list(item.scope_paths),
                        "guardrails": list(item.guardrails),
                        "no_touch_zones": list(item.no_touch_zones),
                    }
                    for item in blueprint_items
                }
                _acknowledge_super_claims(
                    workspace=str(workspace_path),
                    claims=ce_claims,
                    next_stage="pending_exec",
                    summary="ChiefEngineer blueprint ready for Director",
                    metadata_by_task=metadata_by_task,
                )
                director_claims = _claim_super_tasks_from_market(
                    workspace=str(workspace_path),
                    stage="pending_exec",
                    worker_role="director",
                    task_ids=[claim.task_id for claim in ce_claims],
                    visibility_timeout_seconds=1800,
                )
                if director_claims:
                    turn_message = build_director_task_handoff_message(
                        original_request=message,
                        architect_output=ctx.architect_output,
                        pm_output=ctx.pm_output,
                        claimed_tasks=director_claims,
                        blueprint_items=blueprint_items,
                    )
                else:
                    logger.info("SUPER_MODE_DIRECTOR_FALLBACK: no_claimed_pending_exec_tasks")
                    turn_message = build_director_handoff_message(
                        original_request=message,
                        pm_output=ctx.pm_output or "(ChiefEngineer stage produced no claimable pending_exec tasks)",
                        extracted_tasks=pm_tasks,
                    )
            else:
                if not ctx.pm_output.strip():
                    logger.info("SUPER_MODE_DIRECTOR_FALLBACK: missing_pm_output")
                turn_message = build_director_handoff_message(
                    original_request=message,
                    pm_output=ctx.pm_output
                    or "(PM planning stage produced no output; proceeding with original request)",
                    extracted_tasks=pm_tasks,
                )
        elif next_role == "qa":
            turn_message = build_super_readonly_message(
                role="qa",
                original_request=message,
            )

        last_result = _run_streaming_turn(
            host,
            role=next_role,
            session_id=next_session_id,
            message=turn_message,
            json_render=json_render,
            debug=debug,
            spinner_label=prompt_renderer.render_spinner_label(
                role=next_role,
                session_id=next_session_id,
                workspace=workspace_path,
            ),
            dry_run=dry_run,
            output_format=output_format,
            enable_cognitive=enable_cognitive,
        )
        # FIX-20250422-v5: For Director in SUPER mode, loop until work is complete
        # or safety limit reached. Directors often need multiple turns to execute
        # all tasks from a PM plan.
        if next_role == "director" and decision.reason in _SUPER_DIRECTOR_MULTI_TURN_REASONS:
            last_result = _run_director_execution_loop(
                host,
                session_id=next_session_id,
                original_request=message,
                pm_output=ctx.pm_output or (last_result.final_content if last_result else ""),
                extracted_tasks=pm_tasks,
                last_result=last_result,
                json_render=json_render,
                debug=debug,
                prompt_renderer=prompt_renderer,
                workspace_path=workspace_path,
                dry_run=dry_run,
                output_format=output_format,
                enable_cognitive=enable_cognitive,
            )
            if (
                director_claims
                and not last_result.saw_error
                and not _director_output_suggests_more_work(last_result.final_content)
            ):
                director_metadata = {
                    claim.task_id: {
                        "director_summary": last_result.final_content[:500],
                    }
                    for claim in director_claims
                }
                _acknowledge_super_claims(
                    workspace=str(workspace_path),
                    claims=director_claims,
                    next_stage="pending_qa",
                    summary="Director execution complete",
                    metadata_by_task=director_metadata,
                )

        if next_role == "architect" and last_result is not None and not last_result.saw_error:
            # Write architect output to blueprint file on disk
            blueprint_path = write_architect_blueprint_to_disk(
                workspace=str(workspace_path),
                original_request=message,
                architect_output=last_result.final_content,
            )
            ctx = SuperPipelineContext(
                original_request=ctx.original_request,
                architect_output=last_result.final_content,
                pm_output=ctx.pm_output,
                blueprint_file_path=blueprint_path,
                source_chain=ctx.source_chain.append(SessionSource.ARCHITECT_DESIGNED),
            )
        elif next_role == "pm" and last_result is not None and not last_result.saw_error:
            ctx = SuperPipelineContext(
                original_request=ctx.original_request,
                architect_output=ctx.architect_output,
                pm_output=last_result.final_content,
                source_chain=ctx.source_chain.append(SessionSource.PM_DELEGATED),
            )
        elif next_role == "director" and last_result is not None and not last_result.saw_error:
            ctx = SuperPipelineContext(
                original_request=ctx.original_request,
                architect_output=ctx.architect_output,
                pm_output=ctx.pm_output,
                source_chain=ctx.source_chain.append(SessionSource.DIRECTOR_EXECUTED),
            )
    if last_result is None:
        active_session_id = role_sessions.get(fallback_role) or _resolve_role_session(
            host,
            role=fallback_role,
            role_sessions=role_sessions,
            host_kind=host_kind,
            session_title=session_title,
        )
        return _TurnExecutionResult(role=fallback_role, session_id=active_session_id, saw_error=True)
    # Degraded handoff when PM output was empty but director is in the pipeline
    if last_result.role != "director" and "director" in decision.roles and not last_result.saw_error:
        logger.info("SUPER_MODE_DEGRADED_HANDOFF: pm_output_empty, sending original request to director")
        director_session_id = role_sessions.get("director") or _resolve_role_session(
            host,
            role="director",
            role_sessions=role_sessions,
            host_kind=host_kind,
            session_title=session_title,
        )
        pm_fallback = ctx.pm_output or "(PM planning stage produced no output; proceeding with original request)"
        if ctx.blueprint_file_path:
            pm_fallback += f"\n\nblueprint_file: {ctx.blueprint_file_path}"
        degraded_handoff = build_director_handoff_message(
            original_request=message,
            pm_output=pm_fallback,
            extracted_tasks=pm_tasks,
        )
        last_result = _run_streaming_turn(
            host,
            role="director",
            session_id=director_session_id,
            message=degraded_handoff,
            json_render=json_render,
            debug=debug,
            spinner_label=prompt_renderer.render_spinner_label(
                role="director",
                session_id=director_session_id,
                workspace=workspace_path,
            ),
            dry_run=dry_run,
            output_format=output_format,
            enable_cognitive=enable_cognitive,
        )
    logger.info(
        "SUPER_MODE_PIPELINE_COMPLETE: final_role=%s saw_error=%s architect=%s pm=%s ce=%s director=%s",
        last_result.role,
        last_result.saw_error,
        decision.use_architect,
        decision.use_pm,
        decision.use_chief_engineer,
        decision.use_director,
    )
    return last_result


def _persist_super_tasks_to_board(
    workspace: str,
    tasks: list[SuperTaskItem],
    original_request: str,
    *,
    publish_stage: str = "pending_exec",
    architect_output: str = "",
    pm_output: str = "",
) -> list[int]:
    """Persist extracted SUPER-mode tasks to TaskBoard and TaskMarket.

    Returns list of created task IDs.
    """
    task_ids: list[int] = []
    run_id = str(uuid.uuid4())
    logger.info(
        "SUPER_MODE_PERSIST_START: workspace=%s tasks=%d run_id=%s publish_stage=%s",
        workspace,
        len(tasks),
        run_id,
        publish_stage,
    )
    try:
        from polaris.cells.runtime.task_market.public.contracts import (
            PublishTaskWorkItemCommandV1,
        )
        from polaris.cells.runtime.task_market.public.service import get_task_market_service
        from polaris.cells.runtime.task_runtime.internal.task_board import (
            TaskBoard,
        )

        board = TaskBoard(workspace=workspace)
        market = get_task_market_service()
        logger.info("SUPER_MODE_PERSIST_INIT: TaskBoard and TaskMarket initialized")

        for idx, task in enumerate(tasks, 1):
            logger.info(
                "SUPER_MODE_PERSIST_TASK: idx=%d subject=%s",
                idx,
                task.subject,
            )
            created = board.create(
                subject=task.subject,
                description=task.description,
                priority="high",
                tags=["super_mode", "auto_generated"],
                estimated_hours=task.estimated_hours,
                metadata={
                    "source": "super_mode_cli",
                    "original_request": original_request,
                    "target_files": list(task.target_files),
                    "architect_output_excerpt": architect_output[:500],
                    "pm_output_excerpt": pm_output[:500],
                },
            )
            task_ids.append(created.id)
            logger.info(
                "SUPER_MODE_TASK_CREATED: id=%d subject=%s",
                created.id,
                created.subject,
            )

            # Publish to TaskMarket for Director pickup
            trace_id = str(uuid.uuid4())
            cmd = PublishTaskWorkItemCommandV1(
                workspace=workspace,
                trace_id=trace_id,
                run_id=run_id,
                task_id=str(created.id),
                stage=publish_stage,
                priority="high",
                payload={
                    "subject": created.subject,
                    "title": created.subject,
                    "goal": created.description or task.description or original_request,
                    "description": created.description,
                    "target_files": list(task.target_files),
                    "scope_paths": list(task.target_files),
                    "workspace": workspace,
                    "run_id": run_id,
                    "original_request": original_request,
                },
                source_role="pm",
                max_attempts=3,
                metadata={
                    "source": "super_mode_cli",
                    "architect_output_excerpt": architect_output[:500],
                    "pm_output_excerpt": pm_output[:500],
                },
            )
            market.publish_work_item(cmd)
            logger.info(
                "SUPER_MODE_TASK_PUBLISHED: id=%d stage=%s trace=%s",
                created.id,
                publish_stage,
                trace_id,
            )

    except Exception as exc:
        logger.exception(
            "SUPER_MODE_TASK_PERSIST_FAILED: %s (task_ids_so_far=%s)",
            exc,
            task_ids,
        )
    logger.info(
        "SUPER_MODE_PERSIST_END: created=%d task_ids=%s",
        len(task_ids),
        task_ids,
    )
    return task_ids


class PolarisRoleConsole:
    """Compatibility wrapper object for app-style console invocation."""

    def __init__(
        self,
        *,
        workspace: str | Path,
        role: str = "director",
        backend: str = "auto",
        session_id: str | None = None,
        session_title: str | None = None,
        prompt_style: str = "plain",
        omp_config: str | None = None,
        json_render: str = "raw",
        debug: bool = False,
        batch: bool = False,
        model: str | None = None,
        dry_run: bool = False,
        output_format: str | None = "text",
        super_mode: bool = False,
    ) -> None:
        self.workspace = str(Path(workspace).resolve())
        self.role = _normalize_role(role)
        self.backend = _safe_text(backend) or "auto"
        self.session_id = _safe_text(session_id) or None
        self.session_title = _safe_text(session_title) or None
        self.prompt_style = _safe_text(prompt_style) or "plain"
        self.omp_config = _safe_text(omp_config) or None
        self.json_render = _safe_text(json_render) or "raw"
        self.debug = bool(debug)
        self.batch = bool(batch)
        self.model = model
        self.dry_run = bool(dry_run)
        self.output_format = output_format
        self.super_mode = bool(super_mode)

    def run(self) -> int:
        return run_role_console(
            workspace=self.workspace,
            role=self.role,
            backend=self.backend,
            session_id=self.session_id,
            session_title=self.session_title,
            prompt_style=self.prompt_style,
            omp_config=self.omp_config,
            json_render=self.json_render,
            debug=self.debug,
            batch=self.batch,
            model=self.model,
            dry_run=self.dry_run,
            output_format=self.output_format,
            super_mode=self.super_mode,
        )


class PolarisLazyClaude(PolarisRoleConsole):
    """Legacy class name kept for backward compatibility."""


def _run_batch_mode(
    host: RoleConsoleHost,
    *,
    role: str,
    session_id: str,
    message: str,
    json_render: str,
    debug: bool,
    output_format: str,
    enable_cognitive: bool | None = None,
) -> int:
    """Run a single turn in batch mode: read stdin, stream output, exit on complete."""
    exit_code = 0

    def _sigint_handler(signum: int, frame: Any) -> None:
        nonlocal exit_code
        exit_code = 130

    old_handler = signal.signal(signal.SIGINT, _sigint_handler)

    try:
        _run_streaming_turn(
            host,
            role=role,
            session_id=session_id,
            message=message,
            json_render=json_render,
            debug=debug,
            spinner_label="",
            output_format=output_format,
            enable_cognitive=enable_cognitive,
        )
    except KeyboardInterrupt:
        exit_code = 130
    except (RuntimeError, ValueError):
        exit_code = 1
    finally:
        signal.signal(signal.SIGINT, old_handler)

    return exit_code


def _trigger_slm_warmup() -> None:
    """Fire-and-forget background SLM warmup so the model is resident by first user message."""

    def _warmup() -> None:
        logger.debug("[SLM warmup] 后台线程启动 (daemon=%s)", threading.current_thread().daemon)
        try:
            from polaris.cells.roles.kernel.internal.transaction.cognitive_gateway import (
                CognitiveGateway,
            )

            async def _init_and_wait() -> None:
                logger.debug("[SLM warmup] 正在初始化 CognitiveGateway...")
                gateway = await CognitiveGateway.default()
                logger.debug("[SLM warmup] CognitiveGateway 初始化完成")
                # 关键：必须显式等待后台 warmup task 完成，否则 asyncio.run()
                # 在 default() 返回后就会关闭事件循环，cancel 所有 pending task。
                if gateway._warmup_task is not None and not gateway._warmup_task.done():
                    logger.debug("[SLM warmup] 等待后台 _silent_warmup task 完成 (timeout=15s)...")
                    with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                        await asyncio.wait_for(gateway._warmup_task, timeout=15.0)
                    logger.debug("[SLM warmup] 后台 _silent_warmup task 已结束")
                else:
                    logger.debug("[SLM warmup] 无待处理的 warmup task (可能已跳过)")

            logger.debug("[SLM warmup] 启动事件循环...")
            asyncio.run(_init_and_wait())
            logger.debug("[SLM warmup] 事件循环已关闭，SLM 预热流程结束")
        except Exception as exc:  # noqa: BLE001
            logger.debug("[SLM warmup] 预热线程异常 (静默忽略): %s", exc, exc_info=True)

    threading.Thread(target=_warmup, daemon=True, name="slm-warmup").start()
    logger.debug("[SLM warmup] 已提交 daemon 线程 '%s'", "slm-warmup")


def run_role_console(
    *,
    workspace: str | Path = ".",
    role: str = "director",
    backend: str = "auto",
    session_id: str | None = None,
    session_title: str | None = None,
    prompt_style: str | None = None,
    omp_config: str | None = None,
    json_render: str | None = None,
    debug: bool | None = None,
    batch: bool = False,
    model: str | None = None,
    dry_run: bool = False,
    output_format: str | None = None,
    enable_cognitive: bool | None = None,
    super_mode: bool = False,
) -> int:
    # Enforce UTF-8 encoding for Chinese characters and other Unicode output
    enforce_utf8()
    # Apply initial model from CLI flag if provided
    if model:
        _set_current_model(model)
    workspace_path = Path(workspace).resolve()
    role_token = _normalize_role(role)
    backend_token = _safe_text(backend).lower() or "auto"
    if backend_token not in _ALLOWED_BACKENDS:
        print(
            f"[console] backend={backend_token!r} is deprecated; using plain terminal output.",
            file=sys.stderr,
        )
    render_state = _build_render_state(
        prompt_style=prompt_style,
        omp_config=omp_config,
        json_render=json_render,
        output_format=output_format,
    )
    debug_enabled = _coerce_bool(debug if debug is not None else os.environ.get("KERNELONE_CLI_DEBUG"))
    if debug_enabled:
        # --debug 标志降低日志级别并确保 handler 输出 DEBUG
        polaris_logger = logging.getLogger("polaris")
        polaris_logger.setLevel(logging.DEBUG)
        logger.setLevel(logging.DEBUG)
        # 避免重复添加 handler：检查层级中是否已有 StreamHandler
        _has_stream = False
        _check: logging.Logger | None = polaris_logger
        while _check is not None:
            for h in _check.handlers:
                if isinstance(h, logging.StreamHandler):
                    _has_stream = True
                    h.setLevel(logging.DEBUG)
            _check.setLevel(logging.DEBUG)
            if not _check.propagate:
                break
            _check = _check.parent
        if not _has_stream:
            _handler = logging.StreamHandler(sys.stderr)
            _handler.setLevel(logging.DEBUG)
            _handler.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
            polaris_logger.addHandler(_handler)
    prompt_renderer = _PromptRenderer(render_state)

    from polaris.delivery.cli.director.console_host import RoleConsoleHost

    host = RoleConsoleHost(str(workspace_path), role=role_token)
    host_kind = _safe_text(getattr(host.config, "host_kind", RoleHostKind.CLI.value)) or RoleHostKind.CLI.value
    allowed_roles = frozenset(
        str(item).strip().lower() for item in getattr(host, "_ALLOWED_ROLES", ()) if str(item).strip()
    ) or frozenset({"director", "pm", "architect", "chief_engineer", "qa"})
    role_sessions: dict[str, str] = {}
    active_session_id = _resolve_role_session(
        host,
        role=role_token,
        role_sessions=role_sessions,
        host_kind=host_kind,
        session_id=session_id,
        session_title=session_title,
    )

    # Suppress infrastructure logs and Instructor warning around banner display
    import warnings

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*Instructor.*")
        warnings.filterwarnings("ignore", message=".*instructor.*")
        previous_log_levels = _suppress_infrastructure_logs()
        _print_banner(
            workspace=workspace_path,
            role=_console_display_role(role=role_token, super_mode=super_mode),
            session_id=active_session_id,
            allowed_roles=allowed_roles | (frozenset({SUPER_ROLE}) if super_mode else frozenset()),
            render_state=render_state,
        )
        _restore_infrastructure_logs(previous_log_levels)

    _show_onboarding()
    _trigger_slm_warmup()

    # Initialize keyboard mode
    current_keymode = _get_default_keymode()
    _apply_keymode(current_keymode)

    # Load command history
    load_history()

    # Batch mode: read stdin and run single turn, then exit
    if batch:
        batch_message = sys.stdin.read().strip()
        if not batch_message:
            return 0
        if super_mode:
            result = _run_super_turn(
                host,
                fallback_role=role_token,
                role_sessions=role_sessions,
                host_kind=host_kind,
                session_title=session_title,
                workspace_path=workspace_path,
                render_state=render_state,
                prompt_renderer=prompt_renderer,
                message=batch_message,
                json_render=render_state.json_render,
                debug=debug_enabled,
                dry_run=dry_run,
                output_format=render_state.output_format,
                enable_cognitive=enable_cognitive,
            )
            return 1 if result.saw_error else 0
        return _run_batch_mode(
            host,
            role=role_token,
            session_id=active_session_id,
            message=batch_message,
            json_render=render_state.json_render,
            debug=debug_enabled,
            output_format=render_state.output_format,
            enable_cognitive=enable_cognitive,
        )

    current_role = role_token
    current_dry_run = dry_run

    # Create prompt session for TTY mode (prompt-toolkit with integrated status)
    prompt_session = None
    if sys.stdout.isatty():
        prompt_session = create_prompt_session(
            role=_console_display_role(role=current_role, super_mode=super_mode),
            session_id=active_session_id,
            workspace=str(workspace_path),
            omp_config=render_state.omp_config,
            omp_executable=render_state.omp_executable,
        )

    while True:
        # Update session role if changed
        if prompt_session is not None:
            prompt_session.set_role(_console_display_role(role=current_role, super_mode=super_mode))

        try:
            if prompt_session is not None:
                # Use prompt-toolkit session with bottom toolbar
                raw = prompt_session.prompt()
            else:
                # Non-TTY or fallback: use readline_input
                raw = readline_input(
                    prompt_renderer.render(
                        role=_console_display_role(role=current_role, super_mode=super_mode),
                        session_id=active_session_id,
                        workspace=workspace_path,
                    ),
                    role=current_role,
                    session_id=active_session_id,
                )
        except EOFError:
            print()
            save_history()
            return 0
        except KeyboardInterrupt:
            print()
            save_history()
            return 130

        message = _safe_text(raw)
        if not message:
            continue

        handled, exit_code, current_role, active_session_id, current_keymode, current_dry_run = _handle_command(
            message,
            host=host,
            current_role=current_role,
            active_session_id=active_session_id,
            render_state=render_state,
            prompt_renderer=prompt_renderer,
            current_keymode=current_keymode,
            current_dry_run=current_dry_run,
            allowed_roles=allowed_roles,
            role_sessions=role_sessions,
            host_kind=host_kind,
            super_mode=super_mode,
            super_role=SUPER_ROLE,
        )
        if handled:
            if exit_code >= 0:
                return exit_code
            continue

        try:
            if super_mode:
                result = _run_super_turn(
                    host,
                    fallback_role=current_role,
                    role_sessions=role_sessions,
                    host_kind=host_kind,
                    session_title=session_title,
                    workspace_path=workspace_path,
                    render_state=render_state,
                    prompt_renderer=prompt_renderer,
                    message=raw,
                    json_render=render_state.json_render,
                    debug=debug_enabled,
                    dry_run=current_dry_run,
                    output_format=render_state.output_format,
                    enable_cognitive=enable_cognitive,
                )
                active_session_id = result.session_id
            else:
                _run_streaming_turn(
                    host,
                    role=current_role,
                    session_id=active_session_id,
                    message=raw,
                    json_render=render_state.json_render,
                    debug=debug_enabled,
                    spinner_label=prompt_renderer.render_spinner_label(
                        role=current_role,
                        session_id=active_session_id,
                        workspace=workspace_path,
                    ),
                    dry_run=current_dry_run,
                    output_format=render_state.output_format,
                    enable_cognitive=enable_cognitive,
                )
        except KeyboardInterrupt:
            print()
            print("[console] interrupted current turn", file=sys.stderr)
        except (RuntimeError, ValueError) as exc:
            print(f"[error] {exc}", file=sys.stderr)


def run_director_console(
    *,
    workspace: str | Path = ".",
    role: str = "director",
    backend: str = "auto",
    session_id: str | None = None,
    session_title: str | None = None,
    prompt_style: str | None = None,
    omp_config: str | None = None,
    json_render: str | None = None,
    debug: bool | None = None,
    batch: bool = False,
    model: str | None = None,
    dry_run: bool = False,
    enable_cognitive: bool | None = None,
    super_mode: bool = False,
) -> int:
    """Legacy alias retained for compatibility with Director entry points."""
    return run_role_console(
        workspace=workspace,
        role=role or "director",
        backend=backend,
        session_id=session_id,
        session_title=session_title,
        prompt_style=prompt_style,
        omp_config=omp_config,
        json_render=json_render,
        debug=debug,
        batch=batch,
        model=model,
        dry_run=dry_run,
        enable_cognitive=enable_cognitive,
        super_mode=super_mode,
    )


__all__ = [
    "PolarisLazyClaude",
    "PolarisRoleConsole",
    "_acknowledge_super_claims",
    "_claim_super_tasks_from_market",
    "_director_output_suggests_more_work",
    "_persist_super_tasks_to_board",
    "_run_batch_mode",
    "_run_director_execution_loop",
    "_run_super_turn",
    "_trigger_slm_warmup",
    "run_director_console",
    "run_role_console",
]
