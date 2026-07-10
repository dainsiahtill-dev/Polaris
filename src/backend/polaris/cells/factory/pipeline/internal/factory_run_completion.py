"""Orchestration run-completion waiter for the factory stage executor.

Holds the orchestration-service build glue and the run-wait race extracted
verbatim from ``OrchestrationStageExecutor``. ``OrchestrationStageExecutor``
keeps same-named delegating shims (``_build_orchestration_service`` /
``_wait_run_completion`` / ``_resolve_cancel_event`` / ``_resolve_abort_checker``)
so the test-overridden / monkeypatched entry points stay intact.

Behavior preservation notes (load-bearing — do NOT alter):

* The cross-cell imports stay LAZY (in-function): ``Settings`` /
  ``OrchestrationCommandService`` inside ``build_orchestration_service`` and
  ``get_orchestration_service`` inside ``wait`` must not hoist to module scope
  (import-cycle guard).
* The orchestration task is discovered via ``getattr(orchestration_service,
  "_active_runs", {})`` — duck-typed reach, moved verbatim.
* The ``finally`` cleanup cancels outstanding waiters and drains them under
  ``contextlib.suppress(asyncio.CancelledError)`` — the concurrency-correctness
  path is preserved exactly.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from polaris.cells.orchestration.orchestration_engine.public.service import OrchestrationCommandService

logger = logging.getLogger(__name__)


class RunCompletionWaiter:
    """Builds orchestration services and waits for orchestration runs to settle."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)

    @staticmethod
    def _row_matches_active_run(row: dict[str, Any], *, run_id: str) -> bool:
        """Return whether a task row still represents active work for ``run_id``.

        The factory timeout/cancel path must not invalidate a Director lease
        after the provider response has reached tool dispatch.  TaskRuntime is
        the execution-owner cell, so this helper only consumes its observable
        read model and treats ambiguous rows as non-matches.

        Complexity:
            O(1) time and memory for one already-loaded row.
        """

        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return False
        metadata = row.get("metadata")
        metadata_map = metadata if isinstance(metadata, dict) else {}
        runtime_execution = metadata_map.get("runtime_execution")
        runtime_execution_map = runtime_execution if isinstance(runtime_execution, dict) else {}
        row_run_ids = {
            str(row.get("workflow_run_id") or "").strip(),
            str(row.get("run_id") or "").strip(),
            str(row.get("factory_run_id") or "").strip(),
            str(metadata_map.get("factory_run_id") or "").strip(),
            str(metadata_map.get("factory_bench_factory_run_id") or "").strip(),
            str(runtime_execution_map.get("run_id") or "").strip(),
            str(runtime_execution_map.get("factory_run_id") or "").strip(),
        }
        if normalized_run_id not in row_run_ids:
            return False
        row_status = str(row.get("status") or row.get("state") or row.get("execution_state") or "").strip().lower()
        session_status = str(runtime_execution_map.get("status") or "").strip().lower()
        if bool(row.get("running")):
            return True
        return row_status in {
            "active",
            "claimed",
            "in_progress",
            "in_design",
            "in_execution",
            "in_qa",
            "running",
            "processing",
            "executing",
        } or session_status in {"active", "claimed", "in_progress", "running"}

    def _active_task_runtime_barrier_result(self, *, run_id: str, reason: str) -> CommandResult | None:
        """Return a non-mutating cancellation result when TaskRuntime is active.

        Boundary:
            This is the waiter-level counterpart of the Factory stage executor
            active-task barrier.  It prevents the cancellation owner
            ``cancel_active_run`` from bulk-suspending an active Director
            session and creating a secondary ``session_not_active`` failure.
            The factory stage may stop waiting; the child run remains valid so
            tool dispatch/effect receipts can settle into the execution ledger.
        """

        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return None
        try:
            from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService

            rows = TaskRuntimeService(str(self.workspace)).list_observable_task_rows()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.debug(
                "TaskRuntime active-run barrier unavailable for run %s: %s",
                normalized_run_id,
                exc,
            )
            return None
        active_rows = [
            row for row in rows if isinstance(row, dict) and self._row_matches_active_run(row, run_id=normalized_run_id)
        ]
        if not active_rows:
            return None
        status = "cancelled" if reason == "factory_cancelled" else "timeout"
        return CommandResult(
            run_id=normalized_run_id,
            status=status,
            message=f"Director run left active for execution-control-plane barrier: {reason}",
            metadata={
                "cancel_signal_sent": False,
                "cancel_reason": reason,
                "inflight_run_continues": True,
                "terminal_source": "task_runtime_active_execution_barrier",
                "active_task_count": len(active_rows),
                "active_task_ids": [
                    str(row.get("id") or row.get("task_id") or "").strip()
                    for row in active_rows
                    if str(row.get("id") or row.get("task_id") or "").strip()
                ],
            },
        )

    def build_orchestration_service(self, context: dict[str, Any]) -> Any:
        from polaris.bootstrap.config import Settings
        from polaris.cells.orchestration.pm_dispatch.public.service import OrchestrationCommandService

        settings = context.get("settings") or Settings(workspace=Path(self.workspace))
        return OrchestrationCommandService(settings)

    async def cancel_active_run(self, run_id: str, *, reason: str) -> None:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return
        barrier_result = self._active_task_runtime_barrier_result(run_id=normalized_run_id, reason=reason)
        if barrier_result is not None:
            logger.info(
                "Factory cancellation left active TaskRuntime execution intact for run %s: %s",
                normalized_run_id,
                barrier_result.metadata,
            )
            return
        try:
            from polaris.cells.orchestration.workflow_runtime.public import (
                get_orchestration_service,
            )

            orchestration_service = await get_orchestration_service()
            cancel_run = getattr(orchestration_service, "cancel_run", None)
            if not callable(cancel_run):
                return
            try:
                result = cancel_run(normalized_run_id, force=True)
            except TypeError:
                result = cancel_run(normalized_run_id)
            if inspect.isawaitable(result):
                await result
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("Failed to propagate factory cancellation to run %s: %s", normalized_run_id, exc)
        except Exception as exc:
            if exc.__class__.__name__ != "NotFoundError":
                raise
            logger.warning(
                "Factory cancellation target run %s was already absent from orchestration service: %s",
                normalized_run_id,
                exc,
            )
        else:
            logger.info("Propagated factory cancellation to run %s: %s", normalized_run_id, reason)
        try:
            from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService

            suspend_result = TaskRuntimeService(str(self.workspace)).suspend_active_executions_for_run(
                normalized_run_id,
                reason=reason,
                metadata={
                    "cancelled_by": "factory_run_completion",
                    "cancel_reason": reason,
                },
            )
            logger.info(
                "Suspended task runtime leases for run %s: %s",
                normalized_run_id,
                suspend_result,
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning(
                "Failed to suspend task runtime leases for cancelled run %s: %s",
                normalized_run_id,
                exc,
            )

    async def wait(
        self,
        service: OrchestrationCommandService,
        initial_result: CommandResult,
        timeout_seconds: int = 300,
        *,
        cancel_event: asyncio.Event | None = None,
        abort_checker: Callable[[], Awaitable[str | None]] | None = None,
        cancel_on_timeout: bool = True,
    ) -> CommandResult:
        terminal_statuses = {"completed", "failed", "cancelled", "timeout", "blocked"}
        run_id = str(initial_result.run_id or "").strip()

        if initial_result.status in terminal_statuses or not run_id:
            return initial_result

        if abort_checker is not None:
            try:
                abort_reason = await abort_checker()
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                logger.debug("Factory abort checker failed for run %s: %s", run_id, exc)
                abort_reason = None
            if abort_reason:
                if abort_reason == "run_not_found":
                    logger.warning(
                        "Factory abort checker returned run_not_found while waiting orchestration run %s; "
                        "continuing instead of cancelling a child Director run from an ambiguous factory-run "
                        "store projection",
                        run_id,
                    )
                else:
                    await self.cancel_active_run(run_id, reason=abort_reason)
                    return CommandResult(
                        run_id=run_id,
                        status="cancelled",
                        message=f"Run cancelled: {abort_reason}",
                    )

        from polaris.cells.orchestration.workflow_runtime.public import (
            get_orchestration_service,
        )

        orchestration_service = await get_orchestration_service()
        active_runs = getattr(orchestration_service, "_active_runs", {})
        active_task = active_runs.get(run_id) if isinstance(active_runs, dict) else None
        if not isinstance(active_task, asyncio.Task):
            return cast("CommandResult", await service.query_run_status(run_id))

        waiters: dict[asyncio.Future[Any], str] = {
            asyncio.ensure_future(asyncio.shield(active_task)): "orchestration",
            asyncio.create_task(asyncio.sleep(max(0.0, float(timeout_seconds)))): "timeout",
        }
        if cancel_event is not None:
            waiters[asyncio.create_task(cancel_event.wait())] = "cancel"

        try:
            done, _pending = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            completed_reason = ""
            completed_waiter: asyncio.Future[Any] | None = None
            for preferred_reason in ("cancel", "orchestration", "timeout"):
                for waiter in done:
                    if waiters.get(waiter) == preferred_reason:
                        completed_reason = preferred_reason
                        completed_waiter = waiter
                        break
                if completed_waiter is not None:
                    break

            if completed_reason == "cancel":
                barrier_result = self._active_task_runtime_barrier_result(
                    run_id=run_id,
                    reason="factory_cancelled",
                )
                if barrier_result is not None:
                    return barrier_result
                await self.cancel_active_run(run_id, reason="factory_cancelled")
                return CommandResult(
                    run_id=run_id,
                    status="cancelled",
                    message="Run cancelled: factory_cancelled",
                )

            if completed_reason == "timeout":
                if cancel_on_timeout:
                    barrier_result = self._active_task_runtime_barrier_result(
                        run_id=run_id,
                        reason="factory_stage_timeout",
                    )
                    if barrier_result is not None:
                        return barrier_result
                    await self.cancel_active_run(run_id, reason="factory_stage_timeout")
                return CommandResult(
                    run_id=run_id,
                    status="timeout",
                    message=f"Run timed out after {timeout_seconds} seconds",
                    metadata={
                        "cancel_signal_sent": bool(cancel_on_timeout),
                        "cancel_reason": "factory_stage_timeout",
                        "inflight_run_continues": not cancel_on_timeout,
                    },
                )

            if completed_waiter is None:
                return cast("CommandResult", await service.query_run_status(run_id))

            try:
                completed_waiter.result()
            except asyncio.CancelledError:
                return CommandResult(
                    run_id=run_id,
                    status="cancelled",
                    message="Run cancelled: orchestration_task_cancelled",
                )
            except (RuntimeError, ValueError, OSError, TypeError) as exc:
                return CommandResult(
                    run_id=run_id,
                    status="failed",
                    message=f"Run failed: {exc}",
                )

            return cast("CommandResult", await service.query_run_status(run_id))
        finally:
            for waiter in waiters:
                if waiter.done():
                    continue
                waiter.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(*waiters.keys(), return_exceptions=True)

    @staticmethod
    def resolve_cancel_event(context: dict[str, Any]) -> asyncio.Event | None:
        event = context.get("_factory_cancel_event")
        if isinstance(event, asyncio.Event):
            return event
        return None

    @staticmethod
    def resolve_abort_checker(
        context: dict[str, Any],
    ) -> Callable[[], Awaitable[str | None]] | None:
        checker = context.get("_factory_abort_checker")
        if callable(checker):
            return cast("Callable[[], Awaitable[str | None]]", checker)
        return None
