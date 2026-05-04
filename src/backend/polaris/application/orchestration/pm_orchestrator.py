"""Application-layer orchestrator for the PM (Project Manager) domain.

This module provides a high-level facade that encapsulates the PM iteration
workflow.  It coordinates planning, dispatch, blocked-policy handling, and
iteration finalization without exposing Cell internals to the delivery layer.

Call chain::

    delivery -> PmOrchestrator -> cells.orchestration.*.public
                               -> cells.runtime.*.public
                               -> kernelone.*

Architecture constraints (AGENTS.md):
    - Imports ONLY from Cell ``public/`` boundaries and ``kernelone`` contracts.
    - NEVER imports from ``internal/`` at module level.
    - All text I/O uses explicit UTF-8.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import argparse

logger = logging.getLogger(__name__)

__all__ = [
    "PmIterationContext",
    "PmIterationResult",
    "PmOrchestrator",
    "PmOrchestratorError",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PmOrchestratorError(RuntimeError):
    """Application-layer error for PM orchestration operations.

    Wraps lower-level Cell or KernelOne errors so delivery never catches
    infrastructure-specific exception types.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "pm_orchestrator_error",
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.cause = cause


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PmIterationResult:
    """Immutable snapshot of a single PM iteration outcome.

    This is the primary return type for ``PmOrchestrator.run_iteration``.
    Delivery layers should map this to HTTP / CLI responses without
    inspecting Cell-internal payloads.
    """

    exit_code: int
    run_id: str
    iteration: int
    task_count: int
    status: str  # "completed" | "failed" | "blocked"
    chief_engineer_result: dict[str, Any] | None = None
    engine_dispatch: dict[str, Any] | None = None
    integration_qa_result: dict[str, Any] | None = None
    director_result: dict[str, Any] | None = None
    blocked_policy_result: dict[str, Any] | None = None
    schema_warnings: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True, slots=True)
class PmIterationContext:
    """Lightweight context required to drive a PM iteration.

    Delivery layers construct this from CLI args or HTTP request bodies
    and pass it to ``PmOrchestrator.run_iteration``.
    """

    workspace: str
    iteration: int = 1
    run_id: str = ""
    args: argparse.Namespace | None = None
    planning_context: dict[str, Any] = field(default_factory=dict)
    dispatch_enabled: bool = True
    trace_service: Any | None = None


# ---------------------------------------------------------------------------
# PmOrchestrator
# ---------------------------------------------------------------------------


class PmOrchestrator:
    """High-level facade for PM iteration lifecycle.

    Responsibilities:
        1. Planning – invoke ``pm_planning`` Cell to generate tasks.
        2. Dispatch – invoke ``pm_dispatch`` Cell to hand tasks to Director.
        3. Blocked policy – evaluate blocked-task strategies.
        4. Finalization – persist state, emit telemetry, archive history.

    The orchestrator is stateless and cheap to construct.  All mutable
    state (PM state dict, engine handles, etc.) is passed through the
    ``context`` parameter or returned in ``PmIterationResult``.
    """

    # -- planning -----------------------------------------------------------

    @staticmethod
    def run_planning(
        *,
        args: argparse.Namespace,
        workspace_full: str,
        iteration: int,
        state: Any,
        context: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        """Run the PM planning iteration via the ``pm_planning`` Cell.

        Args:
            args: CLI arguments namespace (or equivalent delivery payload).
            workspace_full: Absolute workspace path.
            iteration: Current iteration number.
            state: PM role state object (``PmRoleState`` equivalent).
            context: Planning context dict built by the caller.

        Returns:
            Tuple of (exit_code, normalized_payload).

        Raises:
            PmOrchestratorError: if the planning Cell raises an unexpected
                exception.
        """
        try:
            from polaris.cells.orchestration.pm_planning.public.pipeline import (
                run_pm_planning_iteration,
            )

            return run_pm_planning_iteration(
                args=args,
                workspace_full=workspace_full,
                iteration=iteration,
                state=state,
                context=context,
            )
        except (ImportError, RuntimeError, TypeError, ValueError) as exc:
            raise PmOrchestratorError(
                f"PM planning iteration failed: {exc}",
                code="pm_planning_failed",
                cause=exc,
            ) from exc

    # -- dispatch -----------------------------------------------------------

    @staticmethod
    def run_dispatch(
        *,
        workspace_full: str,
        cache_root_full: str,
        run_dir: str,
        run_id: str,
        iteration: int,
        normalized: dict[str, Any],
        run_events: str,
        dialogue_full: str,
        runtime_pm_tasks_full: str,
        pm_out_full: str,
        run_pm_tasks: str,
        run_director_result: str,
        docs_stage: dict[str, Any] | None = None,
        callbacks: Any | None = None,
    ) -> dict[str, Any]:
        """Run the dispatch pipeline via the ``pm_dispatch`` Cell.

        This encapsulates the full dispatch flow: Chief Engineer preflight,
        engine dispatch, and integration QA.

        Args:
            workspace_full: Absolute workspace path.
            cache_root_full: Absolute cache-root path.
            run_dir: Run-specific directory.
            run_id: Run identifier.
            iteration: Current iteration number.
            normalized: Normalized PM payload (tasks, focus, etc.).
            run_events: Path to runtime events JSONL file.
            dialogue_full: Path to dialogue JSONL file.
            runtime_pm_tasks_full: Path to runtime PM tasks contract.
            pm_out_full: Path to PM output file.
            run_pm_tasks: Path to run-local PM tasks contract.
            run_director_result: Path to Director result file.
            docs_stage: Optional docs-stage configuration.
            callbacks: Optional ``DispatchCallbacks`` for host-layer
                side-effects (e.g. role-status updates).

        Returns:
            Pipeline outcome dict with keys:
            ``used``, ``exit_code``, ``chief_engineer_result``,
            ``engine_dispatch``, ``integration_qa_result``,
            ``director_result``, ``error``.

        Raises:
            PmOrchestratorError: if the dispatch Cell raises an unexpected
                exception.
        """
        try:
            from polaris.cells.orchestration.pm_dispatch.public import run_dispatch_pipeline

            return run_dispatch_pipeline(
                callbacks=callbacks,
                workspace_full=workspace_full,
                cache_root_full=cache_root_full,
                run_dir=run_dir,
                run_id=run_id,
                iteration=iteration,
                normalized=normalized,
                run_events=run_events,
                dialogue_full=dialogue_full,
                runtime_pm_tasks_full=runtime_pm_tasks_full,
                pm_out_full=pm_out_full,
                run_pm_tasks=run_pm_tasks,
                run_director_result=run_director_result,
                docs_stage=docs_stage,
            )
        except (ImportError, RuntimeError, TypeError, ValueError) as exc:
            raise PmOrchestratorError(
                f"PM dispatch pipeline failed: {exc}",
                code="pm_dispatch_failed",
                cause=exc,
            ) from exc

    # -- blocked policy -----------------------------------------------------

    @staticmethod
    def evaluate_blocked_policy(
        *,
        strategy: str,
        task: dict[str, Any],
        director_result: dict[str, Any],
        pm_state: dict[str, Any],
        retry_count: int,
        max_retries: int,
        degrade_retry_budget: int = 1,
    ) -> dict[str, Any]:
        """Evaluate blocked-task policy via the ``pm_dispatch`` Cell.

        Args:
            strategy: Blocked handling strategy (``auto``, ``skip``,
                ``manual``, ``degrade_retry``).
            task: The blocked task dict.
            director_result: Director execution result.
            pm_state: Current PM state dict (may be mutated by caller
                after receiving the result).
            retry_count: Current retry count.
            max_retries: Maximum retries allowed.
            degrade_retry_budget: Budget for degrade retries.

        Returns:
            Serialized policy-result dict with keys:
            ``decision``, ``exit_code``, ``pm_state_patch``,
            ``audit_payload``, ``strategy``, ``reason``,
            ``task_status_update``.

        Raises:
            PmOrchestratorError: if policy evaluation fails.
        """
        try:
            from polaris.delivery.cli.pm.blocked_policy import (
                evaluate_blocked_policy as _evaluate_blocked_policy,
            )

            result = _evaluate_blocked_policy(
                strategy=strategy,
                task=task,
                director_result=director_result,
                pm_state=pm_state,
                retry_count=retry_count,
                max_retries=max_retries,
                degrade_retry_budget=degrade_retry_budget,
            )
            return {
                "decision": str(result.decision.value),
                "exit_code": int(result.exit_code),
                "pm_state_patch": dict(result.pm_state_patch),
                "audit_payload": dict(result.audit_payload),
                "strategy": str(result.strategy),
                "reason": str(result.reason),
                "task_status_update": (
                    dict(result.task_status_update) if result.task_status_update is not None else None
                ),
            }
        except (ImportError, RuntimeError, TypeError, ValueError) as exc:
            raise PmOrchestratorError(
                f"Blocked policy evaluation failed: {exc}",
                code="blocked_policy_failed",
                cause=exc,
            ) from exc

    # -- iteration finalization ---------------------------------------------

    @staticmethod
    def finalize_iteration(
        *,
        args: argparse.Namespace,
        workspace_full: str,
        iteration: int,
        status: str,
        state: dict[str, Any],
        context: dict[str, Any],
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Finalize a PM iteration via the ``pm_dispatch`` Cell.

        Args:
            args: CLI arguments namespace.
            workspace_full: Absolute workspace path.
            iteration: Current iteration number.
            status: Iteration status (``completed``, ``failed``, etc.).
            state: PM state dict (will be mutated by the Cell).
            context: Context dictionary with paths and metadata.
            result: Optional Director result to merge into state.

        Returns:
            Updated PM state dict.

        Raises:
            PmOrchestratorError: if finalization fails.
        """
        try:
            from polaris.cells.orchestration.pm_dispatch.public import (
                finalize_iteration as _finalize_iteration,
            )

            return _finalize_iteration(
                args=args,
                workspace_full=workspace_full,
                iteration=iteration,
                status=status,
                state=state,
                context=context,
                result=result,
            )
        except (ImportError, RuntimeError, TypeError, ValueError) as exc:
            raise PmOrchestratorError(
                f"PM iteration finalization failed: {exc}",
                code="pm_finalize_failed",
                cause=exc,
            ) from exc

    # -- spin guard ---------------------------------------------------------

    @staticmethod
    def handle_spin_guard(
        *,
        pm_state: dict[str, Any],
        reason: str,
        pm_report_full: str,
        run_events: str,
        dialogue_full: str,
        run_id: str,
        iteration: int,
        args: argparse.Namespace,
    ) -> bool:
        """Handle spin-guard activation via the ``pm_dispatch`` Cell.

        Args:
            pm_state: Current PM state dict.
            reason: Reason for spin-guard activation.
            pm_report_full: Path to PM report file.
            run_events: Path to runtime events JSONL file.
            dialogue_full: Path to dialogue JSONL file.
            run_id: Current run identifier.
            iteration: Current iteration number.
            args: CLI arguments namespace.

        Returns:
            ``True`` if spin guard was handled successfully.

        Raises:
            PmOrchestratorError: if the spin-guard handler fails.
        """
        try:
            from polaris.cells.orchestration.pm_dispatch.public import (
                handle_spin_guard as _handle_spin_guard,
            )

            return _handle_spin_guard(
                pm_state=pm_state,
                reason=reason,
                pm_report_full=pm_report_full,
                run_events=run_events,
                dialogue_full=dialogue_full,
                run_id=run_id,
                iteration=iteration,
                args=args,
            )
        except (ImportError, RuntimeError, TypeError, ValueError) as exc:
            raise PmOrchestratorError(
                f"PM spin-guard handling failed: {exc}",
                code="pm_spin_guard_failed",
                cause=exc,
            ) from exc

    # -- stop conditions ----------------------------------------------------

    @staticmethod
    def check_stop_conditions(
        workspace_full: str,
        pm_state: dict[str, Any],
        consecutive_failures: int,
        consecutive_blocked: int,
        args: argparse.Namespace,
    ) -> int | None:
        """Check legacy stop conditions via the ``pm_dispatch`` Cell.

        Args:
            workspace_full: Absolute workspace path.
            pm_state: Current PM state dict.
            consecutive_failures: Count of consecutive failures.
            consecutive_blocked: Count of consecutive blocked iterations.
            args: CLI arguments namespace.

        Returns:
            Exit code if a stop condition is triggered, ``None`` otherwise.
        """
        try:
            from polaris.cells.orchestration.pm_dispatch.public import (
                record_stop,
            )
            from polaris.delivery.cli.pm.orchestration_core import (
                check_stop_conditions as _check_stop_conditions,
            )

            stop_code = _check_stop_conditions(
                workspace_full,
                pm_state,
                consecutive_failures,
                consecutive_blocked,
                args,
            )
            if stop_code is not None:
                # ``record_stop`` is a Cell public helper that writes the
                # stop record to report/state without delivery involvement.
                record_stop(
                    pm_report_full=str(pm_state.get("pm_report_full", "")),
                    timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    iteration=int(pm_state.get("pm_iteration", 0)),
                    pm_state=pm_state,
                    pm_state_full=str(pm_state.get("pm_state_full", "")),
                    exit_code=stop_code,
                )
            return stop_code
        except (ImportError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("PM stop-condition check failed: %s", exc)
            return None

    # -- convenience: full iteration ----------------------------------------

    @classmethod
    def run_iteration(
        cls,
        ctx: PmIterationContext,
        *,
        state: Any,
        pm_state: dict[str, Any],
        paths: dict[str, str],
    ) -> PmIterationResult:
        """Run a complete PM iteration (planning + optional dispatch).

        This is the **primary high-level entry point** for delivery layers.
        It orchestrates the entire iteration while keeping all Cell-internal
        details hidden.

        Args:
            ctx: Lightweight iteration context.
            state: PM role state object (``PmRoleState`` equivalent).
            pm_state: Current PM state dict (may be mutated).
            paths: Resolved artifact paths.  Expected keys:
                ``workspace_full``, ``cache_root_full``, ``run_dir``,
                ``run_events``, ``dialogue_full``, ``pm_last_full``,
                ``pm_llm_events_full``, ``pm_state_full``, ``pm_report_full``,
                ``pm_history_full``, ``runtime_pm_tasks_full``, ``pm_out_full``,
                ``run_pm_tasks``, ``run_director_result``.

        Returns:
            ``PmIterationResult`` snapshot.
        """
        args = ctx.args
        if args is None:
            raise PmOrchestratorError("ctx.args is required for run_iteration", code="missing_args")

        workspace_full = paths.get("workspace_full", ctx.workspace)
        cache_root_full = paths.get("cache_root_full", "")
        run_dir = paths.get("run_dir", "")
        run_events = paths.get("run_events", "")
        dialogue_full = paths.get("dialogue_full", "")
        pm_last_full = paths.get("pm_last_full", "")
        pm_llm_events_full = paths.get("pm_llm_events_full", "")
        pm_state_full = paths.get("pm_state_full", "")
        pm_history_full = paths.get("pm_history_full", "")
        runtime_pm_tasks_full = paths.get("runtime_pm_tasks_full", "")
        pm_out_full = paths.get("pm_out_full", "")
        run_pm_tasks = paths.get("run_pm_tasks", "")
        run_director_result = paths.get("run_director_result", "")

        run_id = ctx.run_id or f"pm-{ctx.iteration:05d}"

        # 1. Planning
        planning_context = dict(ctx.planning_context)
        planning_context.setdefault("run_id", run_id)
        planning_context.setdefault("start_timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        planning_context.setdefault("run_events", run_events)
        planning_context.setdefault("dialogue_full", dialogue_full)
        planning_context.setdefault("pm_last_full", pm_last_full)
        planning_context.setdefault("pm_llm_events_full", pm_llm_events_full)
        planning_context.setdefault("pm_state_full", pm_state_full)
        planning_context.setdefault("trace_service", ctx.trace_service)

        exit_code, normalized = cls.run_planning(
            args=args,
            workspace_full=workspace_full,
            iteration=ctx.iteration,
            state=state,
            context=planning_context,
        )

        normalized = normalized if isinstance(normalized, dict) else {}
        task_count = len(normalized.get("tasks") or [])

        # 2. Dispatch (optional)
        chief_engineer_result: dict[str, Any] | None = None
        engine_dispatch: dict[str, Any] | None = None
        integration_qa_result: dict[str, Any] | None = None
        director_result: dict[str, Any] | None = None
        blocked_policy_result: dict[str, Any] | None = None

        if ctx.dispatch_enabled and exit_code == 0 and task_count > 0:
            dispatch_outcome = cls.run_dispatch(
                workspace_full=workspace_full,
                cache_root_full=cache_root_full,
                run_dir=run_dir,
                run_id=run_id,
                iteration=ctx.iteration,
                normalized=normalized,
                run_events=run_events,
                dialogue_full=dialogue_full,
                runtime_pm_tasks_full=runtime_pm_tasks_full,
                pm_out_full=pm_out_full,
                run_pm_tasks=run_pm_tasks,
                run_director_result=run_director_result,
                docs_stage=planning_context.get("docs_stage"),
            )
            exit_code = int(dispatch_outcome.get("exit_code") or 0)
            chief_engineer_result = (
                dispatch_outcome.get("chief_engineer_result")
                if isinstance(dispatch_outcome.get("chief_engineer_result"), dict)
                else None
            )
            engine_dispatch = (
                dispatch_outcome.get("engine_dispatch")
                if isinstance(dispatch_outcome.get("engine_dispatch"), dict)
                else None
            )
            integration_qa_result = (
                dispatch_outcome.get("integration_qa_result")
                if isinstance(dispatch_outcome.get("integration_qa_result"), dict)
                else None
            )
            director_result = (
                dispatch_outcome.get("director_result")
                if isinstance(dispatch_outcome.get("director_result"), dict)
                else None
            )

        # 3. Blocked policy (if Director status is blocked)
        if isinstance(director_result, dict) and str(director_result.get("status") or "").strip().lower() == "blocked":
            blocked_task = None
            blocked_task_id = director_result.get("task_id")
            if blocked_task_id and isinstance(normalized, dict):
                for t in normalized.get("tasks", []):
                    if isinstance(t, dict) and str(t.get("id") or "").strip() == str(blocked_task_id).strip():
                        blocked_task = t
                        break
            if blocked_task is None:
                blocked_task = {"task_id": blocked_task_id or "unknown"}

            policy_result = cls.evaluate_blocked_policy(
                strategy=str(getattr(args, "blocked_strategy", "auto") or "auto"),
                task=blocked_task,
                director_result=director_result,
                pm_state=pm_state,
                retry_count=int(director_result.get("qa_retry_count") or 0),
                max_retries=int(getattr(args, "max_director_retries", 5) or 5),
                degrade_retry_budget=int(getattr(args, "blocked_degrade_max_retries", 1) or 1),
            )
            blocked_policy_result = policy_result
            if policy_result.get("exit_code", 0) != 0:
                exit_code = int(policy_result["exit_code"])

        # 4. Finalization
        finalize_context = {
            "pm_state_full": pm_state_full,
            "pm_history_full": pm_history_full,
            "normalized": normalized,
            "start_timestamp": planning_context.get("start_timestamp", ""),
            "cache_root_full": cache_root_full,
            "run_id": run_id,
            "exit_code": exit_code,
            "backend": planning_context.get("backend", ""),
            "events_seq_start": planning_context.get("events_seq_start", 0),
            "run_events": run_events,
            "pm_llm_events_full": pm_llm_events_full,
            "trace_service": ctx.trace_service,
        }

        cls.finalize_iteration(
            args=args,
            workspace_full=workspace_full,
            iteration=ctx.iteration,
            status="completed" if exit_code == 0 else "failed",
            state=pm_state,
            context=finalize_context,
            result=director_result,
        )

        return PmIterationResult(
            exit_code=exit_code,
            run_id=run_id,
            iteration=ctx.iteration,
            task_count=task_count,
            status="completed" if exit_code == 0 else "failed",
            chief_engineer_result=chief_engineer_result,
            engine_dispatch=engine_dispatch,
            integration_qa_result=integration_qa_result,
            director_result=director_result,
            blocked_policy_result=blocked_policy_result,
            schema_warnings=tuple(str(w) for w in (normalized.get("schema_warnings") or [])),
            notes=str(normalized.get("notes") or "").strip(),
        )
