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

    def build_orchestration_service(self, context: dict[str, Any]) -> Any:
        from polaris.bootstrap.config import Settings
        from polaris.cells.orchestration.pm_dispatch.public.service import OrchestrationCommandService

        settings = context.get("settings") or Settings(workspace=Path(self.workspace))
        return OrchestrationCommandService(settings)

    async def cancel_active_run(self, run_id: str, *, reason: str) -> None:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
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
                await self.cancel_active_run(run_id, reason="factory_cancelled")
                return CommandResult(
                    run_id=run_id,
                    status="cancelled",
                    message="Run cancelled: factory_cancelled",
                )

            if completed_reason == "timeout":
                await self.cancel_active_run(run_id, reason="factory_stage_timeout")
                return CommandResult(
                    run_id=run_id,
                    status="timeout",
                    message=f"Run timed out after {timeout_seconds} seconds",
                    metadata={
                        "cancel_signal_sent": True,
                        "cancel_reason": "factory_stage_timeout",
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
