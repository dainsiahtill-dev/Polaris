"""Canonical run-completion barrier for Factory orchestration stages.

Orchestration sessions and ``_active_runs`` expose process lifecycle only.
Successful stage completion is projected from a committed TransactionKernel
turn outcome or a fact-backed TaskRuntime terminal row. This keeps Factory
from promoting stale session status into execution truth.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from polaris.cells.orchestration.orchestration_engine.public.service import OrchestrationCommandService
    from polaris.cells.runtime.task_runtime.public.contracts import ObservableTaskRowsProjectionV1

logger = logging.getLogger(__name__)

_CANONICAL_OUTCOME_SETTLEMENT_SECONDS = 2.0
_CANONICAL_POLL_SECONDS = 0.05
_TASK_RUNTIME_TERMINAL_STATES = frozenset(
    {"blocked", "cancelled", "completed", "completed_verified", "failed", "timeout"}
)
_TASK_RUNTIME_FAILURE_STATES = frozenset({"blocked", "failed", "timeout"})
_CANONICAL_STATUS_PRECEDENCE = {
    "completed": 0,
    "cancelled": 1,
    "failed": 2,
    "blocked": 3,
}


class RunCompletionAuthority(str, Enum):
    """Select the fact boundary required to settle an orchestration run.

    Planning and review roles may use their orchestration lifecycle result only
    to continue into their stage-specific contract or evidence validation.
    Director materialization must instead settle through committed TaskRuntime
    execution facts. Neither mode, by itself, grants Factory delivery
    verification authority.
    """

    ROLE_LIFECYCLE = "role_lifecycle"
    TASK_RUNTIME_EXECUTION_FACT = "task_runtime_execution_fact"


class RunCompletionWaiter:
    """Builds orchestration services and waits for orchestration runs to settle."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)

    def _observable_task_rows_projection(self) -> ObservableTaskRowsProjectionV1 | None:
        """Read TaskRuntime rows through the typed public authority boundary."""

        try:
            from polaris.cells.runtime.task_runtime.public.service import (
                TaskRuntimeService,
            )

            return TaskRuntimeService(str(self.workspace)).query_observable_task_rows_projection()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.debug("TaskRuntime typed observable projection unavailable: %s", exc)
            return None

    def _execution_rows(
        self,
        *,
        run_id: str,
        projection: ObservableTaskRowsProjectionV1 | None = None,
    ) -> list[dict[str, Any]]:
        """Return fact-projected TaskRuntime rows associated with one run.

        TaskRuntime's observable projection is the only status input used by the
        Factory execution barrier.  Keeping the lookup in one helper prevents
        cancellation checks and progress checks from drifting onto different
        row sources.

        Complexity:
            O(r) time and memory over observable TaskRuntime rows.
        """

        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return []
        selected_projection = projection or self._observable_task_rows_projection()
        if selected_projection is None:
            return []
        return [
            dict(row)
            for row in selected_projection.rows
            if isinstance(row, Mapping) and self._row_matches_run(row, run_id=normalized_run_id)
        ]

    def _active_execution_rows(self, *, run_id: str) -> list[dict[str, Any]]:
        """Return active, unexpired rows from the canonical TaskRuntime projection."""

        projection = self._observable_task_rows_projection()
        if not self._projection_is_authoritative(projection):
            return []
        return [
            row
            for row in self._execution_rows(run_id=run_id, projection=projection)
            if self._row_is_active(row) and self._row_has_live_canonical_execution_fact(row)
        ]

    @staticmethod
    def _projection_is_authoritative(
        projection: ObservableTaskRowsProjectionV1 | None,
    ) -> bool:
        if projection is None:
            return False
        readiness = dict(projection.readiness)
        return (
            projection.authoritative is True
            and not projection.degraded
            and projection.source == "task_runtime.execution_fact"
            and readiness.get("ready") is True
        )

    @staticmethod
    def _row_has_canonical_fact_source(row: Mapping[str, Any]) -> bool:
        metadata = row.get("metadata")
        metadata_map = metadata if isinstance(metadata, Mapping) else {}
        return (
            str(metadata_map.get("source") or "").strip() == "task_runtime.execution_fact"
            and str(metadata_map.get("status_source") or "").strip() == "task_runtime.execution_fact"
        )

    @staticmethod
    def _parse_utc_timestamp(value: Any) -> datetime | None:
        token = str(value or "").strip()
        if not token:
            return None
        try:
            parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)

    @classmethod
    def _row_has_live_canonical_execution_fact(
        cls,
        row: Mapping[str, Any],
        *,
        now: datetime | None = None,
    ) -> bool:
        """Require a positive fact sequence and a live owner-cell lease."""

        if not cls._row_has_canonical_fact_source(row):
            return False
        sequence = row.get("fact_event_seq")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            return False
        heartbeat = cls._parse_utc_timestamp(row.get("last_heartbeat_at"))
        lease_expires = cls._parse_utc_timestamp(row.get("lease_expires_at"))
        if heartbeat is None or lease_expires is None:
            return False
        reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        return heartbeat <= reference < lease_expires and heartbeat <= lease_expires

    def active_execution_progress_marker(self, *, run_id: str) -> tuple[tuple[str, str, str, str], ...]:
        """Return a stable marker for observable progress in one active run.

        The append-only fact sequence is authoritative. Heartbeat and lease
        timestamps establish that the owner-cell execution is still live.
        Consumers compare markers; they must not interpret the values as
        permissions or task completion.

        Complexity:
            O(r log r) time and O(r) memory over active rows.
        """

        markers: list[tuple[str, str, str, str]] = []
        for row in self._active_execution_rows(run_id=run_id):
            metadata = row.get("metadata")
            metadata_map = metadata if isinstance(metadata, dict) else {}
            runtime_execution = metadata_map.get("runtime_execution")
            runtime_execution_map = runtime_execution if isinstance(runtime_execution, dict) else {}
            markers.append(
                (
                    str(row.get("id") or row.get("task_id") or "").strip(),
                    str(row.get("fact_event_seq") or runtime_execution_map.get("fact_event_seq") or "").strip(),
                    str(row.get("last_heartbeat_at") or runtime_execution_map.get("last_heartbeat_at") or "").strip(),
                    str(row.get("status") or row.get("execution_state") or "").strip().lower(),
                )
            )
        return tuple(sorted(markers))

    @staticmethod
    def _row_matches_run(row: Mapping[str, Any], *, run_id: str) -> bool:
        """Return whether an observable row belongs to ``run_id``.

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
        execution_fact = metadata_map.get("task_runtime_execution_fact")
        execution_fact_map = execution_fact if isinstance(execution_fact, dict) else {}
        child_run_ids = {
            str(row.get("workflow_run_id") or "").strip(),
            str(row.get("run_id") or "").strip(),
            str(runtime_execution_map.get("run_id") or "").strip(),
            str(execution_fact_map.get("run_id") or "").strip(),
        }
        child_run_ids.discard("")
        if not child_run_ids:
            return False
        if child_run_ids == {normalized_run_id}:
            return True

        # Some Factory-owned waits are keyed by the parent Factory run rather
        # than one Director child run.  Accept that relationship only through
        # the typed top-level parent projection and only when every child
        # identity agrees.  This preserves the legitimate parent barrier
        # without reviving the former "any alias matches" ambiguity.
        parent_factory_run_id = str(row.get("factory_run_id") or "").strip()
        return parent_factory_run_id == normalized_run_id and len(child_run_ids) == 1

    @staticmethod
    def _row_is_active(row: Mapping[str, Any]) -> bool:
        """Return whether a fact-projected TaskRuntime row is active."""

        metadata = row.get("metadata")
        metadata_map = metadata if isinstance(metadata, dict) else {}
        runtime_execution = metadata_map.get("runtime_execution")
        runtime_execution_map = runtime_execution if isinstance(runtime_execution, dict) else {}
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

    @staticmethod
    def _canonical_row_status(row: Mapping[str, Any]) -> str:
        return str(row.get("execution_state") or row.get("status") or row.get("state") or "").strip().lower()

    def _task_runtime_terminal_result(
        self,
        *,
        run_id: str,
        projection: ObservableTaskRowsProjectionV1,
    ) -> CommandResult | None:
        """Project a terminal result from committed TaskRuntime facts.

        Every matching row must have a positive FactStream sequence and a
        terminal state. A partial or file-fallback projection is therefore
        never promoted to terminal authority.
        """

        rows = self._execution_rows(run_id=run_id, projection=projection)
        if not rows:
            return None
        sequences: list[int] = []
        statuses: list[str] = []
        task_ids: list[str] = []
        for row in rows:
            if not self._row_has_canonical_fact_source(row):
                return CommandResult(
                    run_id=run_id,
                    status="blocked",
                    message="TaskRuntime observable row is not sourced from canonical execution facts",
                    metadata={
                        "canonical_authoritative": False,
                        "degraded": True,
                        "terminal_source": "task_runtime_transitional_projection_blocked",
                        "task_id": str(row.get("task_id") or row.get("id") or "").strip(),
                    },
                )
            sequence = row.get("fact_event_seq")
            if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
                return None
            status = self._canonical_row_status(row)
            if status not in _TASK_RUNTIME_TERMINAL_STATES:
                return None
            sequences.append(sequence)
            statuses.append(status)
            task_id = str(row.get("task_id") or row.get("id") or "").strip()
            if task_id:
                task_ids.append(task_id)

        if any(status in _TASK_RUNTIME_FAILURE_STATES for status in statuses):
            result_status = "failed"
        elif any(status == "cancelled" for status in statuses):
            result_status = "cancelled"
        else:
            result_status = "completed"
        return CommandResult(
            run_id=run_id,
            status=result_status,
            message=f"TaskRuntime canonical projection reached {result_status}",
            metadata={
                "canonical_authoritative": True,
                "terminal_source": "task_runtime.execution_fact",
                "fact_event_seq": max(sequences),
                "task_ids": task_ids,
                "task_statuses": statuses,
                "task_row_read_model_source": "task_runtime.execution_fact",
            },
        )

    def _committed_turn_outcome_result(
        self,
        *,
        run_id: str,
        process_terminal: bool,
    ) -> CommandResult | None:
        """Project the latest committed TransactionKernel outcome.

        A turn outcome only closes the orchestration run after its process has
        terminated; otherwise a completed intermediate turn could be mistaken
        for the end of a continuation workflow.
        """

        if not process_terminal:
            return None
        try:
            from polaris.cells.events.fact_stream.public import QueryFactEventsV1, query_fact_events

            result = query_fact_events(
                QueryFactEventsV1(
                    workspace=str(self.workspace),
                    stream="roles.kernel.turn_outcomes",
                    event_type="turn_outcome_committed",
                    run_id=run_id,
                    limit=1000,
                )
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.debug("Committed turn outcome unavailable for run %s: %s", run_id, exc)
            return None
        if not result.events:
            return None
        event = result.events[-1]
        payload = event.get("payload")
        payload_map = payload if isinstance(payload, Mapping) else {}
        if str(payload_map.get("schema_version") or "").strip() != "roles.kernel.turn_outcome_fact.v1":
            return None
        outcome = payload_map.get("outcome")
        outcome_map = outcome if isinstance(outcome, Mapping) else {}
        outcome_status = str(outcome_map.get("outcome_status") or "").strip().lower()
        if outcome_status not in {"cancelled", "completed", "failed", "handed_off", "panic"}:
            return None
        if outcome_status == "completed":
            result_status = "completed"
        elif outcome_status == "cancelled":
            result_status = "cancelled"
        else:
            result_status = "failed"
        sequence = event.get("seq")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            return None
        return CommandResult(
            run_id=run_id,
            status=result_status,
            message=f"TransactionKernel committed turn outcome reached {outcome_status}",
            metadata={
                "canonical_authoritative": True,
                "terminal_source": "roles.kernel.turn_outcomes",
                "fact_event_seq": sequence,
                "fact_event_id": str(event.get("event_id") or "").strip(),
                "outcome_hash": str(payload_map.get("outcome_hash") or "").strip(),
                "resolution_code": str(outcome_map.get("resolution_code") or "").strip(),
                "failure_class": str(outcome_map.get("failure_class") or "").strip(),
            },
        )

    def canonical_terminal_result(
        self,
        *,
        run_id: str,
        process_terminal: bool = False,
    ) -> CommandResult | None:
        """Return the canonical terminal projection for one child run."""

        projection = self._observable_task_rows_projection()
        if projection is None:
            readiness: dict[str, Any] = {
                "ready": False,
                "blocking_reasons": ["task_runtime_typed_projection_unavailable"],
            }
        else:
            readiness = dict(projection.readiness)
        if not self._projection_is_authoritative(projection):
            return CommandResult(
                run_id=run_id,
                status="blocked",
                message="TaskRuntime fact-only observable projection is not ready",
                reason_code="task_runtime_fact_projection_not_ready",
                metadata={
                    "canonical_authoritative": False,
                    "degraded": True,
                    "terminal_source": "task_runtime_cutover_readiness",
                    "task_row_read_model_source": (projection.source if projection is not None else "unavailable"),
                    "task_row_read_model_cutover_readiness": readiness,
                },
            )

        task_runtime_result = self._task_runtime_terminal_result(
            run_id=run_id,
            projection=projection,
        )
        turn_outcome_result = self._committed_turn_outcome_result(
            run_id=run_id,
            process_terminal=process_terminal,
        )
        if task_runtime_result is None:
            # A TransactionKernel turn outcome settles one role turn, not the
            # Director task execution. Under TaskRuntime authority it may veto
            # an already-terminal TaskRuntime result through the conflict
            # matrix below, but it must never grant completion by itself.
            return None
        if turn_outcome_result is None:
            return task_runtime_result

        task_runtime_status = str(task_runtime_result.status or "").strip().lower()
        turn_outcome_status = str(turn_outcome_result.status or "").strip().lower()
        selected = max(
            (task_runtime_result, turn_outcome_result),
            key=lambda item: _CANONICAL_STATUS_PRECEDENCE.get(
                str(item.status or "").strip().lower(),
                _CANONICAL_STATUS_PRECEDENCE["failed"],
            ),
        )
        metadata = selected.metadata if isinstance(selected.metadata, dict) else {}
        return CommandResult(
            run_id=run_id,
            status=str(selected.status or "failed").strip().lower(),
            message=(
                selected.message
                if task_runtime_status == turn_outcome_status
                else (
                    "Canonical terminal projections conflicted; "
                    f"TaskRuntime={task_runtime_status}, TurnOutcome={turn_outcome_status}; "
                    f"selected={selected.status}"
                )
            ),
            metadata={
                **metadata,
                "canonical_authoritative": True,
                "terminal_source": "canonical_conflict_matrix",
                "canonical_conflict": task_runtime_status != turn_outcome_status,
                "task_runtime_status": task_runtime_status,
                "turn_outcome_status": turn_outcome_status,
            },
        )

    def active_execution_barrier_result(self, *, run_id: str, reason: str) -> CommandResult | None:
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
        active_rows = self._active_execution_rows(run_id=normalized_run_id)
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

    def _active_task_runtime_barrier_result(self, *, run_id: str, reason: str) -> CommandResult | None:
        """Compatibility delegate for internal callers during barrier cutover."""

        return self.active_execution_barrier_result(run_id=run_id, reason=reason)

    def build_orchestration_service(self, context: dict[str, Any]) -> Any:
        from polaris.bootstrap.config import Settings
        from polaris.cells.orchestration.pm_dispatch.public.service import OrchestrationCommandService

        settings = context.get("settings") or Settings(workspace=Path(self.workspace))
        return OrchestrationCommandService(settings)

    async def cancel_active_run(self, run_id: str, *, reason: str) -> CommandResult | None:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return None
        canonical_result = self.canonical_terminal_result(
            run_id=normalized_run_id,
            process_terminal=True,
        )
        if canonical_result is not None:
            return canonical_result
        barrier_result = self._active_task_runtime_barrier_result(run_id=normalized_run_id, reason=reason)
        if barrier_result is not None:
            logger.info(
                "Factory cancellation left active TaskRuntime execution intact for run %s: %s",
                normalized_run_id,
                barrier_result.metadata,
            )
            return barrier_result
        try:
            from polaris.cells.orchestration.workflow_runtime.public import (
                get_orchestration_service,
            )

            orchestration_service = await get_orchestration_service()
            cancel_run = getattr(orchestration_service, "cancel_run", None)
            if not callable(cancel_run):
                return None
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
        return None

    async def wait(
        self,
        service: OrchestrationCommandService,
        initial_result: CommandResult,
        timeout_seconds: int = 300,
        *,
        cancel_event: asyncio.Event | None = None,
        abort_checker: Callable[[], Awaitable[str | None]] | None = None,
        cancel_on_timeout: bool = True,
        authority: RunCompletionAuthority = RunCompletionAuthority.TASK_RUNTIME_EXECUTION_FACT,
    ) -> CommandResult:
        """Wait until the run has a canonical terminal projection.

        ``CommandResult`` and orchestration task state are lifecycle hints. A
        successful hint is never returned until TaskRuntime or the durable
        TransactionKernel outcome stream proves terminal completion. Failure
        hints remain fail-closed and may terminate early because they cannot
        authorize verified success.
        """

        if authority is RunCompletionAuthority.ROLE_LIFECYCLE:
            return await self._wait_role_lifecycle(
                service,
                initial_result,
                timeout_seconds=timeout_seconds,
                cancel_event=cancel_event,
                abort_checker=abort_checker,
                cancel_on_timeout=cancel_on_timeout,
            )

        terminal_statuses = {"completed", "failed", "cancelled", "timeout", "blocked", "success"}
        failure_statuses = {"failed", "cancelled", "timeout", "blocked"}
        run_id = str(initial_result.run_id or "").strip()
        if not run_id:
            return initial_result

        from polaris.cells.orchestration.workflow_runtime.public import (
            get_orchestration_service,
        )

        orchestration_service = await get_orchestration_service()
        active_runs = getattr(orchestration_service, "_active_runs", {})
        active_task = active_runs.get(run_id) if isinstance(active_runs, dict) else None
        process_terminal = str(initial_result.status or "").strip().lower() in terminal_statuses
        lifecycle_result = initial_result
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, float(timeout_seconds))
        settlement_deadline: float | None = (
            min(deadline, loop.time() + _CANONICAL_OUTCOME_SETTLEMENT_SECONDS) if process_terminal else None
        )
        settlement_progress_marker = self.active_execution_progress_marker(run_id=run_id) if process_terminal else None
        deferred_cancel_reason = ""
        lifecycle_failure_deferred_for_active_execution = False

        while True:
            canonical = self.canonical_terminal_result(
                run_id=run_id,
                process_terminal=process_terminal,
            )
            canonical_metadata = (
                canonical.metadata if canonical is not None and isinstance(canonical.metadata, dict) else {}
            )
            if canonical is not None and canonical_metadata.get("canonical_authoritative") is True:
                return canonical

            lifecycle_status = str(lifecycle_result.status or "").strip().lower()
            if lifecycle_status in failure_statuses:
                active_execution = self._active_task_runtime_barrier_result(
                    run_id=run_id,
                    reason="orchestration_lifecycle_failure",
                )
                if active_execution is not None:
                    # The orchestration lifecycle is diagnostic only. A matching
                    # TaskRuntime execution fact still owns the admitted child
                    # attempt, so preserve the original execution deadline and
                    # wait for its canonical terminal fact. The fixed settlement
                    # window begins only after the owner cell stops reporting an
                    # active execution.
                    process_terminal = True
                    if not deferred_cancel_reason:
                        settlement_deadline = None
                        settlement_progress_marker = self.active_execution_progress_marker(run_id=run_id)
                    lifecycle_failure_deferred_for_active_execution = True
                elif lifecycle_failure_deferred_for_active_execution:
                    # The active row may disappear one projection tick before
                    # its terminal fact becomes visible. Spend only the bounded
                    # canonical settlement window, still capped by the original
                    # admitted execution deadline.
                    process_terminal = True
                    if settlement_deadline is None:
                        settlement_deadline = min(
                            deadline,
                            loop.time() + _CANONICAL_OUTCOME_SETTLEMENT_SECONDS,
                        )
                        settlement_progress_marker = self.active_execution_progress_marker(run_id=run_id)
                else:
                    metadata = dict(lifecycle_result.metadata or {})
                    metadata.update(
                        {
                            "canonical_authoritative": False,
                            "terminal_source": "orchestration_lifecycle_failure",
                        }
                    )
                    lifecycle_result.metadata = metadata
                    return lifecycle_result

            if cancel_event is not None and cancel_event.is_set() and not deferred_cancel_reason:
                barrier_result = self._active_task_runtime_barrier_result(
                    run_id=run_id,
                    reason="factory_cancelled",
                )
                if barrier_result is not None:
                    deferred_cancel_reason = "factory_cancelled"
                    settlement_deadline = min(
                        deadline,
                        loop.time() + _CANONICAL_OUTCOME_SETTLEMENT_SECONDS,
                    )
                    settlement_progress_marker = self.active_execution_progress_marker(run_id=run_id)
                    continue
                barrier_result = await self.cancel_active_run(run_id, reason="factory_cancelled")
                if barrier_result is not None:
                    return barrier_result
                canonical = self.canonical_terminal_result(run_id=run_id, process_terminal=True)
                if canonical is not None:
                    return canonical
                return CommandResult(
                    run_id=run_id,
                    status="cancelled",
                    message="Run cancelled: factory_cancelled",
                    metadata={
                        "canonical_authoritative": False,
                        "terminal_source": "orchestration_lifecycle_cancel",
                        "cancel_signal_sent": True,
                    },
                )

            if abort_checker is not None:
                try:
                    abort_reason = await abort_checker()
                except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                    logger.debug("Factory abort checker failed for run %s: %s", run_id, exc)
                    abort_reason = None
                if abort_reason and abort_reason != "run_not_found":
                    barrier_result = await self.cancel_active_run(run_id, reason=abort_reason)
                    if barrier_result is not None:
                        return barrier_result
                    return CommandResult(
                        run_id=run_id,
                        status="cancelled",
                        message=f"Run cancelled: {abort_reason}",
                        metadata={
                            "canonical_authoritative": False,
                            "terminal_source": "orchestration_lifecycle_abort",
                        },
                    )

            if isinstance(active_task, asyncio.Task) and active_task.done() and not process_terminal:
                try:
                    active_task.result()
                except asyncio.CancelledError:
                    lifecycle_result = CommandResult(
                        run_id=run_id,
                        status="cancelled",
                        message="Run cancelled: orchestration_task_cancelled",
                    )
                except (RuntimeError, ValueError, OSError, TypeError) as exc:
                    lifecycle_result = CommandResult(
                        run_id=run_id,
                        status="failed",
                        message=f"Run failed: {exc}",
                    )
                else:
                    lifecycle_result = cast("CommandResult", await service.query_run_status(run_id))
                process_terminal = True
                settlement_deadline = min(
                    deadline,
                    loop.time() + _CANONICAL_OUTCOME_SETTLEMENT_SECONDS,
                )
                settlement_progress_marker = self.active_execution_progress_marker(run_id=run_id)
                continue

            if not isinstance(active_task, asyncio.Task):
                observed = cast("CommandResult", await service.query_run_status(run_id))
                observed_status = str(observed.status or "").strip().lower()
                lifecycle_result = observed
                if observed_status in terminal_statuses and not process_terminal:
                    process_terminal = True
                    settlement_deadline = settlement_deadline or min(
                        deadline,
                        loop.time() + _CANONICAL_OUTCOME_SETTLEMENT_SECONDS,
                    )
                    settlement_progress_marker = self.active_execution_progress_marker(run_id=run_id)
                    continue

            now = loop.time()
            if settlement_deadline is not None and now >= settlement_deadline:
                if process_terminal and not deferred_cancel_reason:
                    current_progress_marker = self.active_execution_progress_marker(run_id=run_id)
                    if current_progress_marker != settlement_progress_marker and current_progress_marker != ():
                        settlement_progress_marker = current_progress_marker
                        settlement_deadline = min(
                            deadline,
                            loop.time() + _CANONICAL_OUTCOME_SETTLEMENT_SECONDS,
                        )
                        logger.debug(
                            "Extending settlement deadline for run %s due to active execution progress",
                            run_id,
                        )
                        continue
                if deferred_cancel_reason:
                    barrier_result = self._active_task_runtime_barrier_result(
                        run_id=run_id,
                        reason=deferred_cancel_reason,
                    )
                    if barrier_result is not None:
                        metadata = dict(barrier_result.metadata or {})
                        metadata.update(
                            {
                                "barrier_cancel_deferred": True,
                                "deferred_cancel_reason": deferred_cancel_reason,
                            }
                        )
                        barrier_result.metadata = metadata
                        return barrier_result
                return CommandResult(
                    run_id=run_id,
                    status="failed",
                    message="Run process terminated without a committed canonical outcome",
                    reason_code="canonical_terminal_projection_missing",
                    metadata={
                        "canonical_authoritative": False,
                        "terminal_source": "canonical_projection_barrier",
                        "lifecycle_status": str(lifecycle_result.status or "").strip(),
                        "failure_class": "LEDGER_PROJECTION_INCOMPLETE",
                        "responsible_layer": "execution_control_plane",
                    },
                )
            if now >= deadline:
                if cancel_on_timeout:
                    barrier_result = self._active_task_runtime_barrier_result(
                        run_id=run_id,
                        reason="factory_stage_timeout",
                    )
                    if barrier_result is not None:
                        return barrier_result
                    barrier_result = await self.cancel_active_run(run_id, reason="factory_stage_timeout")
                    if barrier_result is not None:
                        return barrier_result
                return CommandResult(
                    run_id=run_id,
                    status="timeout",
                    message=f"Run timed out after {timeout_seconds} seconds",
                    metadata={
                        "canonical_authoritative": False,
                        "terminal_source": "canonical_projection_barrier",
                        "cancel_signal_sent": bool(cancel_on_timeout),
                        "cancel_reason": "factory_stage_timeout",
                        "inflight_run_continues": not cancel_on_timeout,
                    },
                )
            await asyncio.sleep(min(_CANONICAL_POLL_SECONDS, max(0.0, deadline - now)))

    async def _wait_role_lifecycle(
        self,
        service: OrchestrationCommandService,
        initial_result: CommandResult,
        *,
        timeout_seconds: int,
        cancel_event: asyncio.Event | None,
        abort_checker: Callable[[], Awaitable[str | None]] | None,
        cancel_on_timeout: bool,
    ) -> CommandResult:
        """Settle a non-materialization role call without granting verification.

        The result is only a role-process lifecycle fact. PM, CE, and QA stages
        must still validate their authoritative contract, blueprint, or Run
        Ledger evidence before succeeding.
        """

        terminal_statuses = {"completed", "failed", "cancelled", "timeout", "blocked", "success"}
        run_id = str(initial_result.run_id or "").strip()
        if str(initial_result.status or "").strip().lower() in terminal_statuses or not run_id:
            return initial_result

        if abort_checker is not None:
            try:
                abort_reason = await abort_checker()
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                logger.debug("Factory abort checker failed for run %s: %s", run_id, exc)
                abort_reason = None
            if abort_reason and abort_reason != "run_not_found":
                barrier_result = await self.cancel_active_run(run_id, reason=abort_reason)
                if barrier_result is not None:
                    return barrier_result
                return CommandResult(
                    run_id=run_id,
                    status="cancelled",
                    message=f"Run cancelled: {abort_reason}",
                    metadata={
                        "canonical_authoritative": False,
                        "terminal_source": "role_lifecycle_abort",
                    },
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
                barrier_result = await self.cancel_active_run(run_id, reason="factory_cancelled")
                if barrier_result is not None:
                    return barrier_result
                return CommandResult(
                    run_id=run_id,
                    status="cancelled",
                    message="Run cancelled: factory_cancelled",
                    metadata={
                        "canonical_authoritative": False,
                        "terminal_source": "role_lifecycle_cancel",
                    },
                )
            if completed_reason == "timeout":
                if cancel_on_timeout:
                    barrier_result = await self.cancel_active_run(run_id, reason="factory_stage_timeout")
                    if barrier_result is not None:
                        return barrier_result
                return CommandResult(
                    run_id=run_id,
                    status="timeout",
                    message=f"Run timed out after {timeout_seconds} seconds",
                    metadata={
                        "canonical_authoritative": False,
                        "terminal_source": "role_lifecycle_timeout",
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
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                return CommandResult(
                    run_id=run_id,
                    status="failed",
                    message=f"Run failed: {exc}",
                )
            return cast("CommandResult", await service.query_run_status(run_id))
        finally:
            for waiter in waiters:
                if not waiter.done():
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
