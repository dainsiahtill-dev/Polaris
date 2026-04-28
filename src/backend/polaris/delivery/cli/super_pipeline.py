"""SUPER Mode Pipeline Orchestrator — declarative multi-stage execution.

This module replaces the manual for-loop in _run_super_turn with a
data-driven orchestrator that uses StageConstraint for injection and
supports retry/degrade/skip failure modes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Awaitable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.delivery.cli.super_pipeline_config import (
    PipelineResult,
    PipelineStage,
    StageResult,
    SuperPipelineConfig,
)
from polaris.delivery.cli.super_mode import (
    SuperPipelineContext,
    extract_blueprint_items_from_ce_output,
    extract_task_list_from_pm_output,
    write_architect_blueprint_to_disk,
)

if TYPE_CHECKING:
    from polaris.delivery.cli.terminal_console import _TurnExecutionResult

logger = logging.getLogger(__name__)

# Type alias for the stage executor callback
StageExecutor = Callable[..., Awaitable[_TurnExecutionResult]]


class SuperPipelineOrchestrator:
    """Declarative multi-stage pipeline orchestrator for SUPER mode.

    Replaces the manual for-loop in _run_super_turn. Each stage is defined
    as a PipelineStage with constraints, retry policy, and failure handling.
    """

    def __init__(
        self,
        config: SuperPipelineConfig,
        *,
        original_request: str,
        workspace: Path,
        stage_executor: StageExecutor,
        role_sessions: dict[str, str],
        host_kind: str,
        session_title: str | None,
        json_render: str,
        debug: bool,
        dry_run: bool,
        output_format: str,
        enable_cognitive: bool | None,
    ) -> None:
        self._config = config
        self._original_request = original_request
        self._workspace = workspace
        self._execute_stage = stage_executor
        self._role_sessions = role_sessions
        self._host_kind = host_kind
        self._session_title = session_title
        self._json_render = json_render
        self._debug = debug
        self._dry_run = dry_run
        self._output_format = output_format
        self._enable_cognitive = enable_cognitive

    async def run(self) -> PipelineResult:
        """Execute the full pipeline. Returns PipelineResult with all stage outcomes."""
        t0 = time.monotonic()
        ctx = SuperPipelineContext(original_request=self._original_request)
        stage_results: list[StageResult] = []
        saw_error = False
        last_role = "director"

        for stage in self._config.stages:
            # Check total timeout
            elapsed = time.monotonic() - t0
            if elapsed > self._config.max_total_duration_seconds:
                logger.warning(
                    "PIPELINE_TIMEOUT: total_elapsed=%.0fs > max=%ds, aborting at role=%s",
                    elapsed,
                    self._config.max_total_duration_seconds,
                    stage.role,
                )
                break

            # Check skip condition
            if stage.skip_condition is not None and stage.skip_condition(ctx):
                logger.info("PIPELINE_STAGE_SKIP: role=%s reason=skip_condition", stage.role)
                stage_results.append(StageResult(role=stage.role, success=True, skipped=True))
                continue

            # Execute with retries
            result = await self._execute_with_retries(stage, ctx)
            stage_results.append(result)
            last_role = stage.role

            if result.skipped:
                continue

            if not result.success:
                saw_error = True
                if stage.on_failure == "abort":
                    logger.error("PIPELINE_ABORT: role=%s error=%s", stage.role, result.error)
                    break
                elif stage.on_failure == "skip":
                    logger.warning("PIPELINE_STAGE_FAILED_SKIP: role=%s", stage.role)
                    continue
                elif stage.on_failure == "degrade":
                    logger.warning("PIPELINE_STAGE_DEGRADED: role=%s", stage.role)
                    # Continue to next stage — it will receive degraded context
                    continue
                # on_failure == "retry" already exhausted in _execute_with_retries

            # Update context from successful stage
            ctx = self._update_context(ctx, stage, result)

        total_duration = time.monotonic() - t0
        return PipelineResult(
            stages=tuple(stage_results),
            final_role=last_role,
            total_duration_seconds=total_duration,
            saw_error=saw_error,
        )

    async def _execute_with_retries(self, stage: PipelineStage, ctx: SuperPipelineContext) -> StageResult:
        """Execute a stage with retry logic."""
        last_result: StageResult | None = None
        for attempt in range(1, stage.max_retries + 1):
            result = await self._execute_single_stage(stage, ctx, retry_count=attempt - 1)
            if result.success:
                if attempt > 1:
                    logger.info("PIPELINE_RETRY_SUCCESS: role=%s attempt=%d", stage.role, attempt)
                return result
            last_result = result
            if attempt < stage.max_retries:
                delay = min(2**attempt, 10)
                logger.warning(
                    "PIPELINE_RETRY: role=%s attempt=%d/%d error=%s, waiting %ds",
                    stage.role,
                    attempt,
                    stage.max_retries,
                    result.error or "empty_output",
                    delay,
                )
                await asyncio.sleep(delay)
        return last_result or StageResult(role=stage.role, success=False, error="no_attempts")

    async def _execute_single_stage(
        self, stage: PipelineStage, ctx: SuperPipelineContext, retry_count: int = 0
    ) -> StageResult:
        """Execute a single stage: build handoff message, call executor, collect result."""
        t0 = time.monotonic()

        # Build handoff message
        handoff_kwargs = self._build_handoff_kwargs(stage, ctx)
        constraint_text = stage.constraint.to_prompt_text()
        if constraint_text:
            handoff_kwargs["_constraint_text"] = constraint_text

        try:
            turn_message = stage.handoff_builder(**handoff_kwargs)
        except Exception as exc:
            return StageResult(
                role=stage.role,
                success=False,
                error=f"handoff_builder_failed: {exc}",
                retry_count=retry_count,
                duration_seconds=time.monotonic() - t0,
            )

        # Inject constraint into turn message
        if constraint_text:
            turn_message = self._inject_constraint(turn_message, constraint_text)

        # Resolve session
        session_id = self._role_sessions.get(stage.role)
        if not session_id:
            # Let the caller create the session
            session_id = f"super_{stage.role}_auto"

        # Execute via the stage executor callback
        try:
            exec_result = await self._execute_stage(
                role=stage.role,
                session_id=session_id,
                message=turn_message,
                json_render=self._json_render,
                debug=self._debug,
                dry_run=self._dry_run,
                output_format=self._output_format,
                enable_cognitive=self._enable_cognitive,
                tool_choice_override=stage.constraint.to_api_tool_choice(),
            )
        except Exception as exc:
            return StageResult(
                role=stage.role,
                success=False,
                error=f"executor_exception: {exc}",
                retry_count=retry_count,
                duration_seconds=time.monotonic() - t0,
            )

        duration = time.monotonic() - t0
        content = exec_result.final_content if exec_result else ""
        saw_error = exec_result.saw_error if exec_result else True

        if saw_error:
            return StageResult(
                role=stage.role,
                success=False,
                content=content,
                error="saw_error",
                retry_count=retry_count,
                duration_seconds=duration,
            )

        if not content.strip():
            return StageResult(
                role=stage.role,
                success=False,
                content="",
                error="empty_output",
                retry_count=retry_count,
                duration_seconds=duration,
            )

        return StageResult(
            role=stage.role,
            success=True,
            content=content,
            retry_count=retry_count,
            duration_seconds=duration,
        )

    def _build_handoff_kwargs(self, stage: PipelineStage, ctx: SuperPipelineContext) -> dict[str, Any]:
        """Build kwargs for the stage's handoff_builder from context."""
        kw: dict[str, Any] = {"original_request": ctx.original_request}

        if stage.role == "architect":
            pass  # only needs original_request
        elif stage.role == "pm":
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

        # Merge fixed kwargs
        kw.update(stage.handoff_kwargs)
        return kw

    def _inject_constraint(self, message: str, constraint_text: str) -> str:
        """Inject constraint text into the handoff message."""
        if not constraint_text:
            return message
        # Insert constraint before the closing tag
        for tag in (
            "[/SUPER_MODE_HANDOFF]",
            "[/SUPER_MODE_PM_HANDOFF]",
            "[/SUPER_MODE_CE_HANDOFF]",
            "[/SUPER_MODE_DIRECTOR_TASK_HANDOFF]",
            "[/SUPER_MODE_READONLY_STAGE]",
        ):
            if tag in message:
                return message.replace(tag, f"{constraint_text}\n{tag}")
        # Fallback: append
        return f"{message}\n\n{constraint_text}"

    def _update_context(
        self, ctx: SuperPipelineContext, stage: PipelineStage, result: StageResult
    ) -> SuperPipelineContext:
        """Update pipeline context after a successful stage execution."""
        from polaris.kernelone.traceability.session_source import SessionSource

        if stage.role == "architect":
            blueprint_path = ""
            if self._config.persist_blueprints:
                blueprint_path = write_architect_blueprint_to_disk(
                    workspace=str(self._workspace),
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
        elif stage.role == "chief_engineer":
            blueprint_items = extract_blueprint_items_from_ce_output(result.content, claimed_tasks=list(ctx.ce_claims))
            return replace(
                ctx,
                blueprint_items=tuple(blueprint_items),
                source_chain=ctx.source_chain.append(SessionSource.CHIEF_ENGINEER_ANALYZED),
            )
        elif stage.role == "director":
            return replace(
                ctx,
                source_chain=ctx.source_chain.append(SessionSource.DIRECTOR_EXECUTED),
            )
        return ctx


__all__ = ["SuperPipelineOrchestrator"]
