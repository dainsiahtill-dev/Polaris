"""Private mixin _Mixin04 for OrchestrationStageExecutor."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from polaris.cells.orchestration.orchestration_engine.public.service import OrchestrationCommandService

from polaris.cells.control_plane.run_ledger.public import FailureClassV1
from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult
from polaris.kernelone.constants import (
    MAX_LLM_PROVIDER_TIMEOUT_SECONDS,  # noqa: F401 — re-exported for characterization-test surface
)

from ..factory_deadline_calculations import (  # noqa: F401 — re-exported for characterization-test surface
    _CHIEF_ENGINEER_EXECUTION_ATTEMPT_SETTLEMENT_GRACE_SECONDS,
    _CHIEF_ENGINEER_LLM_TIMEOUT_ENV_KEYS,
    _DEFAULT_CHIEF_ENGINEER_LLM_TIMEOUT_SECONDS,
    ChiefEngineerExecutionAttemptLeaseBudget as _ChiefEngineerExecutionAttemptLeaseBudget,
)
from ..factory_run_completion import RunCompletionWaiter
from ._helpers import (
    _remaining_monotonic_seconds,
)

logger = logging.getLogger("polaris.cells.factory.pipeline.internal.factory_stage_executor")


class _Mixin04:
    """Method group extracted from OrchestrationStageExecutor (lossless)."""

    @staticmethod
    def _is_authoritative_terminal_probe(result: CommandResult | None) -> bool:
        """Reject transient, explicitly non-authoritative read-model probes.

        ``RunCompletionWaiter.canonical_terminal_result`` intentionally returns
        a blocked diagnostic while TaskRuntime's fact projection is catching up
        with an active session file.  That diagnostic is observability evidence,
        not a terminal child-run verdict.  Treating it as terminal lets Factory
        fail and drain a healthy Director task before its execution lease ends.
        """

        if result is None:
            return False
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        return metadata.get("canonical_authoritative") is not False

    async def _settle_inflight_director_run_after_timeout(
        self,
        service: OrchestrationCommandService,
        *,
        run_id: str,
        grace_seconds: int,
        cancel_event: asyncio.Event | None = None,
        abort_checker: Callable[[], Awaitable[str | None]] | None = None,
    ) -> CommandResult | None:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return None
        if grace_seconds <= 0:
            barrier_result = self._active_director_task_barrier_result(
                run_id=normalized_run_id,
                reason="factory_stage_timeout",
                grace_seconds=0,
            )
            if barrier_result is not None:
                return self._execution_barrier_timeout_result(
                    barrier_result,
                    grace_seconds=0,
                )
            return CommandResult(
                run_id=normalized_run_id,
                status="timeout",
                message="Director run timed out before timeout settle grace",
                metadata={
                    "cancel_signal_sent": False,
                    "cancel_reason": "factory_stage_timeout",
                    "timeout_settle_grace_seconds": 0,
                    "inflight_run_continues": True,
                    "failure_class": FailureClassV1.RESOURCE_BUDGET_EXHAUSTED.value,
                    "responsible_layer": "execution_control_plane",
                },
            )
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        hard_limit_seconds = max(0.0, float(grace_seconds))
        hard_deadline = started_at + hard_limit_seconds
        cancellation_reserve_seconds = min(0.25, hard_limit_seconds * 0.25)
        observation_deadline = hard_deadline - cancellation_reserve_seconds
        progress_marker = self._active_director_execution_progress_marker(run_id=normalized_run_id)
        progress_extensions = 0
        deferred_cancel_reason = ""

        async def _cancel_within_hard_deadline(reason: str) -> tuple[CommandResult | None, bool]:
            remaining_seconds = _remaining_monotonic_seconds(hard_deadline)
            if remaining_seconds <= 0:
                return None, False
            try:
                return (
                    await asyncio.wait_for(
                        self._run_completion_waiter.cancel_active_run(
                            normalized_run_id,
                            reason=reason,
                        ),
                        timeout=remaining_seconds,
                    ),
                    True,
                )
            except TimeoutError:
                return None, False

        while True:
            canonical_probe = self._run_completion_waiter.canonical_terminal_result(
                run_id=normalized_run_id,
                process_terminal=False,
            )
            if self._is_authoritative_terminal_probe(canonical_probe):
                assert canonical_probe is not None
                return self._with_execution_barrier_progress(
                    canonical_probe,
                    progress_extensions=progress_extensions,
                    elapsed_seconds=loop.time() - started_at,
                    max_total_seconds=hard_limit_seconds,
                    deferred_cancel_reason=deferred_cancel_reason,
                )
            if cancel_event is not None and cancel_event.is_set() and not deferred_cancel_reason:
                barrier_result = self._active_director_task_barrier_result(
                    run_id=normalized_run_id,
                    reason="factory_cancelled",
                    grace_seconds=grace_seconds,
                )
                if barrier_result is not None:
                    deferred_cancel_reason = "factory_cancelled"
                else:
                    barrier_result, cancel_completed = await _cancel_within_hard_deadline("factory_cancelled")
                    if barrier_result is not None:
                        return barrier_result
                    return CommandResult(
                        run_id=normalized_run_id,
                        status="cancelled",
                        message="Run cancelled: factory_cancelled",
                        metadata={
                            "cancel_signal_sent": cancel_completed,
                            "inflight_run_continues": not cancel_completed,
                        },
                    )
            if abort_checker is not None and not deferred_cancel_reason:
                with contextlib.suppress(AttributeError, OSError, RuntimeError, TypeError, ValueError):
                    abort_remaining_seconds = _remaining_monotonic_seconds(observation_deadline)
                    abort_reason = (
                        await asyncio.wait_for(
                            abort_checker(),
                            timeout=abort_remaining_seconds,
                        )
                        if abort_remaining_seconds > 0
                        else None
                    )
                    if abort_reason:
                        barrier_result = self._active_director_task_barrier_result(
                            run_id=normalized_run_id,
                            reason=abort_reason,
                            grace_seconds=grace_seconds,
                        )
                        if barrier_result is not None:
                            deferred_cancel_reason = abort_reason
                        else:
                            barrier_result, cancel_completed = await _cancel_within_hard_deadline(abort_reason)
                            if barrier_result is not None:
                                return barrier_result
                            return CommandResult(
                                run_id=normalized_run_id,
                                status="cancelled",
                                message=f"Run cancelled: {abort_reason}",
                                metadata={
                                    "cancel_signal_sent": cancel_completed,
                                    "inflight_run_continues": not cancel_completed,
                                },
                            )

            process_terminal = False
            query_remaining_seconds = _remaining_monotonic_seconds(observation_deadline)
            if query_remaining_seconds > 0:
                with contextlib.suppress(
                    AttributeError,
                    OSError,
                    RuntimeError,
                    TimeoutError,
                    TypeError,
                    ValueError,
                ):
                    lifecycle_probe = await asyncio.wait_for(
                        service.query_run_status(normalized_run_id),
                        timeout=query_remaining_seconds,
                    )
                    process_terminal = str(lifecycle_probe.status or "").strip().lower() in {
                        "blocked",
                        "cancelled",
                        "completed",
                        "failed",
                        "success",
                        "timeout",
                    }
            canonical_probe = self._run_completion_waiter.canonical_terminal_result(
                run_id=normalized_run_id,
                process_terminal=process_terminal,
            )
            if self._is_authoritative_terminal_probe(canonical_probe):
                assert canonical_probe is not None
                return self._with_execution_barrier_progress(
                    canonical_probe,
                    progress_extensions=progress_extensions,
                    elapsed_seconds=loop.time() - started_at,
                    max_total_seconds=hard_limit_seconds,
                    deferred_cancel_reason=deferred_cancel_reason,
                )

            next_progress_marker = self._active_director_execution_progress_marker(run_id=normalized_run_id)
            if next_progress_marker and next_progress_marker != progress_marker:
                progress_marker = next_progress_marker
                progress_extensions += 1

            remaining = observation_deadline - loop.time()
            if remaining <= 0:
                barrier_result = self._active_director_task_barrier_result(
                    run_id=normalized_run_id,
                    reason="factory_stage_timeout",
                    grace_seconds=grace_seconds,
                )
                if barrier_result is not None:
                    timeout_result = self._execution_barrier_timeout_result(
                        barrier_result,
                        grace_seconds=grace_seconds,
                    )
                    return self._with_execution_barrier_progress(
                        timeout_result,
                        progress_extensions=progress_extensions,
                        elapsed_seconds=loop.time() - started_at,
                        max_total_seconds=hard_limit_seconds,
                        deferred_cancel_reason=deferred_cancel_reason,
                    )
                barrier_result, cancel_completed = await _cancel_within_hard_deadline("factory_stage_timeout")
                if barrier_result is not None:
                    return barrier_result
                return CommandResult(
                    run_id=normalized_run_id,
                    status="timeout",
                    message="Director run timed out after timeout settle grace",
                    metadata={
                        "cancel_signal_sent": cancel_completed,
                        "cancel_reason": "factory_stage_timeout",
                        "timeout_settle_grace_seconds": grace_seconds,
                        "inflight_run_continues": not cancel_completed,
                    },
                )
            await asyncio.sleep(min(2.0, remaining))

    def _active_director_execution_progress_marker(
        self,
        *,
        run_id: str,
    ) -> tuple[tuple[str, str, str, str], ...]:
        """Read the TaskRuntime-owned progress marker for a child run."""

        progress_probe = getattr(self._run_completion_waiter, "active_execution_progress_marker", None)
        if not callable(progress_probe):
            return ()
        with contextlib.suppress(RuntimeError, OSError, TypeError, ValueError):
            marker = progress_probe(run_id=run_id)
            if isinstance(marker, tuple):
                return tuple(item for item in marker if isinstance(item, tuple) and len(item) == 4)
        return ()

    @staticmethod
    def _with_execution_barrier_progress(
        result: CommandResult,
        *,
        progress_extensions: int,
        elapsed_seconds: float,
        max_total_seconds: float,
        deferred_cancel_reason: str = "",
    ) -> CommandResult:
        """Attach hard-deadline and progress evidence to a barrier result."""

        normalized_cancel_reason = str(deferred_cancel_reason or "").strip()
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        progress_metadata: dict[str, Any] = {
            "barrier_elapsed_seconds": round(max(0.0, elapsed_seconds), 3),
            "barrier_max_total_seconds": round(max(0.0, max_total_seconds), 3),
        }
        if progress_extensions > 0:
            progress_metadata.update(
                {
                    "barrier_progress_extensions": progress_extensions,
                    "barrier_progress_source": "task_runtime_execution_fact",
                }
            )
        if normalized_cancel_reason:
            progress_metadata.update(
                {
                    "barrier_cancel_deferred": True,
                    "deferred_cancel_reason": normalized_cancel_reason,
                    "cancel_signal_sent": False,
                }
            )
        return CommandResult(
            run_id=str(result.run_id or "").strip(),
            status=str(result.status or "").strip(),
            message=result.message,
            reason_code=result.reason_code,
            stage_results=result.stage_results,
            started_at=result.started_at,
            completed_at=result.completed_at,
            artifacts=result.artifacts,
            metadata={
                **metadata,
                **progress_metadata,
            },
        )

    @staticmethod
    def _execution_barrier_timeout_result(
        result: CommandResult,
        *,
        grace_seconds: int,
    ) -> CommandResult:
        """Project a still-active child as an explicit control-plane timeout."""

        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        return CommandResult(
            run_id=str(result.run_id or "").strip(),
            status="timeout",
            message="Director child execution remained active after settlement barrier timeout",
            reason_code=result.reason_code,
            stage_results=result.stage_results,
            started_at=result.started_at,
            completed_at=result.completed_at,
            artifacts=result.artifacts,
            metadata={
                **metadata,
                "cancel_signal_sent": False,
                "inflight_run_continues": True,
                "timeout_settle_grace_seconds": grace_seconds,
                "barrier_state": "timeout",
                "barrier_timeout": True,
                "failure_class": FailureClassV1.RESOURCE_BUDGET_EXHAUSTED.value,
                "responsible_layer": "execution_control_plane",
            },
        )

    def _active_director_task_barrier_result(
        self,
        *,
        run_id: str,
        reason: str,
        grace_seconds: int,
    ) -> CommandResult | None:
        """Leave an active Director lease intact while external cancellation settles.

        Factory deadlines are outside the Director tool-dispatch transaction. If
        TaskRuntime still reports active work, suspending the child lease creates
        a secondary ``session_not_active`` failure and hides the actual
        execution-control-plane condition. The factory stage may stop waiting,
        but the child execution remains valid so tool/effect receipts can settle
        into the ledger.
        """

        barrier_probe = getattr(self._run_completion_waiter, "active_execution_barrier_result", None)
        if not callable(barrier_probe):
            return None
        with contextlib.suppress(RuntimeError, OSError, TypeError, ValueError):
            result = barrier_probe(run_id=run_id, reason=reason)
            if isinstance(result, CommandResult):
                metadata = result.metadata if isinstance(result.metadata, dict) else {}
                return CommandResult(
                    run_id=str(result.run_id or run_id).strip(),
                    status=str(result.status or "").strip(),
                    message=result.message,
                    reason_code=result.reason_code,
                    stage_results=result.stage_results,
                    started_at=result.started_at,
                    completed_at=result.completed_at,
                    artifacts=result.artifacts,
                    metadata={
                        **metadata,
                        "timeout_settle_grace_seconds": grace_seconds,
                    },
                )
        return None

    @staticmethod
    def _resolve_cancel_event(context: dict[str, Any]) -> asyncio.Event | None:
        return RunCompletionWaiter.resolve_cancel_event(context)

    @staticmethod
    def _resolve_abort_checker(
        context: dict[str, Any],
    ) -> Callable[[], Awaitable[str | None]] | None:
        return RunCompletionWaiter.resolve_abort_checker(context)
