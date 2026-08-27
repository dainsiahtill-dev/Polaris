"""Collaborator mixin for TaskRuntimeService (_mixin_facts_events)."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Mapping, cast

from polaris.cells.events.fact_stream.public.contracts import (
    AppendFactEventCommandV1,
    FactEventAppendedV1,
    FactStreamError,
    QueryFactStreamHeadV1,
)
from polaris.cells.runtime.task_runtime.internal.task_board import (
    TaskStatus,
)
from polaris.cells.runtime.task_runtime.public.contracts import (
    TASK_RUNTIME_EXECUTION_SOURCE_V1,
    TASK_RUNTIME_EXECUTION_STREAM_V1,
    TaskRuntimeExecutionFactV1,
)
from polaris.kernelone.storage import resolve_storage_roots

from ..execution_session import (
    TaskExecutionSession,
    TaskExecutionSessionWriteReceipt,
    _coerce_fact_event_seq,
    build_task_execution_transition_result,
    build_task_runtime_execution_event_append_result,
    build_task_runtime_execution_event_payload,
    build_task_runtime_metadata,
    is_terminal_session_status,
    project_task_row_runtime_state,
    project_task_runtime_realtime_event_payload,
)
from ._helpers import (
    _FACT_APPEND_CAS_MAX_ATTEMPTS,
    TaskExecutionSessionWriteConflictError,
    _execution_event_failure_evidence,
    _execution_event_projection_evidence,
    logger,
)
from ._late_bindings import (
    append_fact_event,
    query_fact_stream_head,
    utc_now_iso,
)
from ._mixin_base import _ServiceMixinBase

if TYPE_CHECKING:
    pass


_SESSION_TERMINAL_EXECUTION_EVENT_TYPES = frozenset(
    {
        "cancelled",
        "completed",
        "failed",
        "suspended",
    }
)


class _FactsEventsMixin(_ServiceMixinBase):
    """Method group extracted losslessly from TaskRuntimeService."""

    def _apply_reopen_downstream_reblocks(
        self,
        *,
        reopened_task_id: int,
        dependent_ids: list[int],
    ) -> list[dict[str, Any]]:
        """Re-block direct dependents after a prerequisite task is reopened.

        The operation is O(d) over direct dependents from the reopened task row.
        Each dependent row mutation is followed by a task-runtime execution fact
        so QA rework cannot silently rewrite downstream scheduling state.
        """

        events: list[dict[str, Any]] = []
        for dependent_id in dependent_ids:
            _normalized_dependent_id, dependent = self._task_entity_for_dependency_side_effect(dependent_id)
            if dependent is None:
                continue
            before_row = self._augment_task_row(dependent.to_dict())
            previous_blockers = self._row_blocker_ids(before_row)
            next_blockers = list(previous_blockers)
            if reopened_task_id not in next_blockers:
                next_blockers.append(reopened_task_id)
            previous_status = str(before_row.get("status") or "").strip().lower()
            next_status: TaskStatus | None = TaskStatus.BLOCKED if previous_status in {"pending", "ready"} else None
            if next_blockers == previous_blockers and next_status is None:
                continue
            updated = self._board.update(
                dependent_id,
                status=next_status,
                blocked_by=next_blockers,
                allow_dependency_status=True,
            )
            if updated is None:
                events.append(
                    {
                        "ok": False,
                        "event_type": "downstream_dependency_reblock_failed",
                        "task_id": dependent_id,
                        "reason": "task_update_failed",
                        "failure_class": "task_state_write_failed",
                    }
                )
                continue
            after_row = self._augment_task_row(updated.to_dict())
            events.append(
                self._append_execution_event(
                    "downstream_dependency_reblocked",
                    task_row=after_row,
                    session=None,
                    details={
                        "reopened_task_id": reopened_task_id,
                        "previous_status": previous_status,
                        "previous_blockers": previous_blockers,
                        "active_blockers": self._row_blocker_ids(after_row),
                    },
                )
            )
        return events

    def _apply_dependency_completion_side_effects(
        self,
        *,
        completed_task_id: int,
        dependent_rows_before: list[dict[str, Any]],
        dependency_satisfaction: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Apply and record dependent-row changes caused by satisfied capability.

        The operation is O(d) over rows that explicitly blocked on the
        parent task, where d is the number of direct dependents captured before
        the parent transition.  The normal case is terminal completion.  A
        failed Director task may also supply TaskRuntime-owned materialization
        proof; the failed status remains unchanged while only its committed
        file capability satisfies the dependency.
        """

        events: list[dict[str, Any]] = []
        should_notify_ready = False
        satisfaction = dict(dependency_satisfaction or {})
        satisfaction_kind = str(satisfaction.get("kind") or "completed").strip()
        for before_row in dependent_rows_before:
            dependent_id = self.normalize_task_id(before_row.get("id"))
            if dependent_id is None:
                continue
            previous_blockers = self._row_blocker_ids(before_row)
            active_blockers = [blocker_id for blocker_id in previous_blockers if blocker_id != completed_task_id]
            if previous_blockers == active_blockers:
                continue

            updated = self._board.update(
                dependent_id,
                blocked_by=active_blockers,
                allow_dependency_status=True,
            )
            if updated is None:
                events.append(
                    {
                        "ok": False,
                        "event_type": "dependency_row_update_failed",
                        "task_id": dependent_id,
                        "reason": "task_update_failed",
                        "failure_class": "task_state_write_failed",
                    }
                )
                continue

            after_row = self._augment_task_row(updated.to_dict())
            active_blockers = self._row_blocker_ids(after_row)
            status = str(after_row.get("status") or "").strip().lower()
            event_type = (
                "dependencies_unblocked"
                if not active_blockers and status in {"pending", "ready"}
                else "dependency_blockers_refreshed"
            )
            if event_type == "dependencies_unblocked":
                should_notify_ready = True
            events.append(
                self._append_execution_event(
                    event_type,
                    task_row=after_row,
                    session=None,
                    details={
                        "completed_task_id": completed_task_id,
                        "dependency_satisfied_task_id": completed_task_id,
                        "dependency_satisfaction_kind": satisfaction_kind,
                        "dependency_satisfaction_evidence": satisfaction,
                        "previous_blockers": previous_blockers,
                        "active_blockers": active_blockers,
                    },
                )
            )
        if should_notify_ready:
            self._board.notify_ready_tasks()
        return events

    @staticmethod
    def _build_terminal_execution_transition_result(
        *,
        reason: str,
        task_row: dict[str, Any],
        session: TaskExecutionSession,
        execution_event: dict[str, Any],
    ) -> dict[str, Any]:
        """Project terminal transitions through the shared execution result builder."""

        return build_task_execution_transition_result(
            success=True,
            reason=reason,
            task_row=task_row,
            session=session,
            execution_event=execution_event,
        )

    @staticmethod
    def _with_dependency_execution_events(
        result: dict[str, Any],
        dependency_events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not dependency_events:
            return result
        projected = dict(result)
        projected["dependency_execution_events"] = [dict(event) for event in dependency_events]
        failed_events = [event for event in dependency_events if not bool(event.get("ok"))]
        if failed_events and bool(projected.get("success")):
            projected["requested_reason"] = str(projected.get("reason") or "")
            projected["reason"] = str(failed_events[0].get("reason") or "dependency_transition_failed")
            projected["success"] = False
            projected["failure_class"] = str(failed_events[0].get("failure_class") or "ledger_append_failed")
            projected["state_mutation_applied"] = True
        return projected

    def _append_execution_event(
        self,
        event_type: str,
        *,
        task_row: dict[str, Any],
        session: TaskExecutionSession | None,
        details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        event_type_str = str(event_type or "").strip().lower() or "unknown"
        try:
            transition_id, transition_timestamp = self._execution_transition_identity(
                event_type=event_type_str,
                session=session,
            )
        except (RuntimeError, ValueError) as exc:
            event_details = self._row_write_receipt_details_for_task(task_row)
            event_details.update(self._session_write_receipt_details_for_session(session))
            return build_task_runtime_execution_event_append_result(
                event_type=event_type_str,
                details=event_details,
                append_error=f"execution transition identity unavailable: {exc}",
                failure_evidence=_execution_event_failure_evidence(exc, stage="transition_identity"),
            )
        event_details = self._row_write_receipt_details_for_task(task_row)
        event_details.update(self._session_write_receipt_details_for_session(session))
        for key, value in dict(details or {}).items():
            if key in {"row_write_receipt", "session_write_receipt"}:
                continue
            event_details[key] = value
        payload = build_task_runtime_execution_event_payload(
            event_type=event_type,
            workspace=self.workspace,
            task_row=task_row,
            session=session,
            details=event_details,
            timestamp=transition_timestamp,
        )
        try:
            fact = TaskRuntimeExecutionFactV1.from_payload(
                transition_id=transition_id,
                payload=payload,
            )
            payload = fact.to_record()
            event_type_str = fact.event_type
            appended = self._append_execution_fact(
                event_type_str=event_type_str,
                payload=payload,
            )
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "Failed to append task runtime execution event %s: %s",
                event_type_str,
                exc,
            )
            return build_task_runtime_execution_event_append_result(
                event_type=event_type_str,
                details=event_details,
                append_error=str(exc),
                failure_evidence=_execution_event_failure_evidence(exc, stage="fact_append"),
            )
        payload["fact_event_id"] = appended.event_id
        payload["fact_stream"] = appended.stream
        payload["fact_storage_path"] = appended.storage_path
        if appended.appended_seq is not None:
            payload["fact_event_seq"] = int(appended.appended_seq)
        try:
            published = self._publish_factory_execution_event(payload)
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "Failed to publish task runtime execution event %s: %s",
                event_type_str,
                exc,
            )
            return build_task_runtime_execution_event_append_result(
                event_type=event_type_str,
                fact_event_id=appended.event_id,
                fact_stream=appended.stream,
                fact_storage_path=appended.storage_path,
                fact_event_seq=appended.appended_seq,
                details=event_details,
                publish_error=str(exc),
                failure_evidence=_execution_event_failure_evidence(exc, stage="event_publish"),
            )
        if not published:
            factory_run_id = str(payload.get("factory_run_id") or "").strip()
            if factory_run_id:
                return build_task_runtime_execution_event_append_result(
                    event_type=event_type_str,
                    fact_event_id=appended.event_id,
                    fact_stream=appended.stream,
                    fact_storage_path=appended.storage_path,
                    fact_event_seq=appended.appended_seq,
                    details=event_details,
                    publish_error="factory_execution_event_publish_returned_false",
                    projection_evidence=_execution_event_projection_evidence(
                        factory_run_id=factory_run_id,
                        fact_event_id=appended.event_id,
                        fact_stream=appended.stream,
                        fact_event_seq=appended.appended_seq,
                    ),
                )
        return build_task_runtime_execution_event_append_result(
            event_type=event_type_str,
            fact_event_id=appended.event_id,
            fact_stream=appended.stream,
            fact_storage_path=appended.storage_path,
            fact_event_seq=appended.appended_seq,
            details=event_details,
            published=published,
        )

    def _execution_transition_identity(
        self,
        *,
        event_type: str,
        session: TaskExecutionSession | None,
    ) -> tuple[str, str | None]:
        """Return one event identity and an optional stable transition time.

        New terminal transitions already carry their identifier because
        ``mark_completed``/``mark_failed`` generate it before the session write.
        Reuse that identifier only for the event that projects the terminal
        outcome. A later control event can legitimately carry the same terminal
        session as evidence (for example ``reopened`` or
        ``same_task_local_rework_prepared``); reusing the terminal identifier for
        those events collapses distinct actions into one FactStream idempotency
        key. The write below remains a compatibility migration for terminal
        sessions persisted before the field existed.
        """

        event_type_str = str(event_type or "").strip().lower()
        if (
            event_type_str not in _SESSION_TERMINAL_EXECUTION_EVENT_TYPES
            or session is None
            or not is_terminal_session_status(session.status)
        ):
            return f"task-transition-{uuid.uuid4().hex}", None

        transition_id = str(session.terminal_transition_id or "").strip()
        if not transition_id:
            transition_id = session.ensure_terminal_transition_id()
            if not self._write_session(session):
                raise TaskExecutionSessionWriteConflictError("failed to persist terminal execution transition identity")
        transition_timestamp = str(session.released_at or session.last_heartbeat_at or "").strip() or None
        return transition_id, transition_timestamp

    def _row_write_receipt_details_for_task(self, task_row: Mapping[str, Any]) -> dict[str, Any]:
        """Return row-write receipt details for this task row identity."""

        task_id = self.normalize_task_id(task_row.get("id"))
        if task_id is None:
            return {}
        receipt = self._board.row_write_receipt_for_task(task_id)
        if receipt is None:
            return {}
        return {"row_write_receipt": receipt.to_dict()}

    def _session_write_receipt_for_session(
        self,
        session: TaskExecutionSession | None,
    ) -> TaskExecutionSessionWriteReceipt | None:
        """Return the latest successful session-write receipt for one session identity."""

        if session is None:
            return None
        task_id = self.normalize_task_id(session.task_id)
        if task_id is None:
            return None
        session_id = str(session.session_id or "").strip()
        if not session_id:
            return None
        with self._session_write_receipt_lock:
            return self._session_write_receipts_by_identity.get((task_id, session_id))

    def _session_write_receipt_details_for_session(
        self,
        session: TaskExecutionSession | None,
    ) -> dict[str, Any]:
        """Return session-write receipt details for this session identity."""

        receipt = self._session_write_receipt_for_session(session)
        if receipt is None:
            return {}
        return {"session_write_receipt": receipt.to_dict()}

    def _append_execution_fact(
        self,
        *,
        event_type_str: str,
        payload: dict[str, Any],
    ) -> FactEventAppendedV1:
        """Append through the TaskRuntime CAS boundary."""

        return self._append_execution_fact_with_cas(
            event_type_str=event_type_str,
            payload=payload,
        )

    def _next_execution_fact_expected_seq(self) -> int:
        """Return the next canonical sequence for the execution fact stream.

        The value is an optimistic CAS expectation, not an allocation.  The
        FactStream remains the only sequence allocator and rejects a stale
        expectation when another writer wins the race.

        Complexity:
            O(1) retained event memory and two bounded FactStream queries.
        """

        return cast(
            int,
            query_fact_stream_head(
                QueryFactStreamHeadV1(
                    workspace=self.workspace,
                    stream=TASK_RUNTIME_EXECUTION_STREAM_V1,
                )
            ).next_expected_seq,
        )

    def _append_execution_fact_with_cas(
        self,
        *,
        event_type_str: str,
        payload: dict[str, Any],
    ) -> FactEventAppendedV1:
        """Append an execution fact with optimistic CAS and bounded retry.

        Local writers are serialized to avoid needless self-contention; the
        ``expected_seq`` contract still arbitrates independent services and
        processes.  Only sequence drift is retryable.  Every other FactStream
        failure propagates to the execution-event receipt as a hard append
        failure.

        Complexity:
            O(a) constant-time cursor reads and append attempts for ``a`` CAS
            retries, bounded by ``_FACT_APPEND_CAS_MAX_ATTEMPTS``; O(1)
            auxiliary memory. Cursor recovery inside FactStream is O(n) only
            when its sequence index is absent or corrupt.
        """

        idempotency_key = str(payload.get("idempotency_key") or "").strip() or None
        replay_expected_seq: int | None = None
        last_drift: FactStreamError | None = None
        with self._execution_fact_append_lock:
            for _attempt in range(_FACT_APPEND_CAS_MAX_ATTEMPTS):
                expected_seq = (
                    replay_expected_seq if replay_expected_seq is not None else self._next_execution_fact_expected_seq()
                )
                try:
                    return append_fact_event(
                        AppendFactEventCommandV1(
                            workspace=self.workspace,
                            stream=TASK_RUNTIME_EXECUTION_STREAM_V1,
                            event_type=event_type_str,
                            payload=payload,
                            source=TASK_RUNTIME_EXECUTION_SOURCE_V1,
                            run_id=str(payload.get("run_id") or "").strip() or None,
                            task_id=str(payload.get("task_id") or "").strip() or None,
                            correlation_id=str(payload.get("session_id") or "").strip() or None,
                            idempotency_key=idempotency_key,
                            expected_seq=expected_seq,
                        )
                    )
                except FactStreamError as exc:
                    if exc.code != "expected_seq_drift":
                        raise
                    last_drift = exc
                    existing_seq = _coerce_fact_event_seq(exc.details.get("existing_seq"))
                    replay_expected_seq = existing_seq if idempotency_key and existing_seq is not None else None
        raise FactStreamError(
            "task_runtime.execution CAS retry budget exhausted",
            code="execution_fact_cas_exhausted",
            details={
                "workspace": self.workspace,
                "attempts": _FACT_APPEND_CAS_MAX_ATTEMPTS,
                "last_error": str(last_drift or "expected_seq_drift"),
            },
        )

    def _publish_factory_execution_event(self, payload: dict[str, Any]) -> bool:
        factory_run_id = str(payload.get("factory_run_id") or "").strip()
        if not factory_run_id:
            return False
        fact_event_id = str(payload.get("fact_event_id") or "").strip()
        if not fact_event_id:
            raise ValueError("fact_event_id is required for runtime.v2 execution wakeup")
        fact_event_seq = _coerce_fact_event_seq(payload.get("fact_event_seq"))
        if fact_event_seq is None:
            raise ValueError("fact_event_seq is required for runtime.v2 execution wakeup")
        try:
            roots = resolve_storage_roots(self.workspace)
            workspace_key = str(getattr(roots, "workspace_key", "") or "").strip()
            if not workspace_key:
                return False
            from polaris.infrastructure.log_pipeline.jetstream_publisher import (
                get_log_jetstream_publisher,
            )

            event_payload = project_task_runtime_realtime_event_payload(payload)
            director_run_id = str(event_payload.get("run_id") or "").strip()
            if director_run_id and director_run_id != factory_run_id:
                event_payload["director_run_id"] = director_run_id
            event_payload["type"] = "task_runtime_execution"
            event_payload["stage"] = "director_dispatch"
            event_payload["message"] = (
                f"Director task {event_payload.get('task_id') or '<unknown>'} "
                f"{event_payload.get('event_type') or 'updated'}"
            )
            envelope = {
                "schema_version": "runtime.v2",
                "event_id": fact_event_id,
                "workspace_key": workspace_key,
                "run_id": factory_run_id,
                "channel": f"event.factory:{factory_run_id}",
                "kind": "task_runtime_execution",
                "ts": event_payload.get("timestamp") or utc_now_iso(),
                "cursor": fact_event_seq,
                "trace_id": None,
                "payload": event_payload,
                "meta": {
                    "source": TASK_RUNTIME_EXECUTION_SOURCE_V1,
                    "fact_event_id": fact_event_id,
                    "fact_event_seq": fact_event_seq,
                    "fact_stream": TASK_RUNTIME_EXECUTION_STREAM_V1,
                },
            }
            return cast(
                bool,
                get_log_jetstream_publisher().publish(
                    subject=f"hp.runtime.{workspace_key}.event.factory.{factory_run_id}",
                    payload=envelope,
                ),
            )
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.debug("Task runtime factory progress publish failed: %s", exc)
            return False

    def _augment_task_row(self, row: dict[str, Any]) -> dict[str, Any]:
        task_id = self.normalize_task_id(row.get("id"))
        if task_id is None:
            return dict(row)

        session = self._read_session(task_id)
        return self._augment_task_row_with_session(row, session=session)

    def _augment_task_row_with_session(
        self,
        row: Mapping[str, Any],
        *,
        session: TaskExecutionSession | None,
    ) -> dict[str, Any]:
        """Project a row from a caller-owned session snapshot without locking.

        Settlement projection uses the snapshot committed by its first phase;
        re-reading here would reacquire the session lock while an outer caller
        may still own its cooperative file lock.
        """

        row_copy = dict(row)
        terminal_session_superseded = False
        if session is not None:
            terminal_session_superseded = is_terminal_session_status(
                session.status
            ) and self._row_mapping_authorizes_retry_over_terminal_session(row_copy, session)
        return project_task_row_runtime_state(
            row_copy,
            task_status_value=row_copy.get("status"),
            session=session,
            terminal_session_superseded=terminal_session_superseded,
        )

    def _build_runtime_metadata(
        self,
        *,
        session: TaskExecutionSession,
        effective_status: str,
        resume_state: str,
        extra_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return build_task_runtime_metadata(
            session=session,
            effective_status=effective_status,
            resume_state=resume_state,
            extra_metadata=extra_metadata,
        )
