"""Canonical run-completion barrier for Factory orchestration stages.

Orchestration sessions and ``_active_runs`` expose process lifecycle only.
Successful stage completion is projected from a committed TransactionKernel
turn outcome or a fact-backed TaskRuntime terminal row. This keeps Factory
from promoting stale session status into execution truth.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
import json
import logging
import time
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
_TASK_RUNTIME_PROJECTION_CACHE_TTL_SECONDS = 0.5
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
        self._task_runtime_projection_cache: ObservableTaskRowsProjectionV1 | None = None
        self._task_runtime_projection_cached_at = 0.0

    def _observable_task_rows_projection(self) -> ObservableTaskRowsProjectionV1 | None:
        """Read TaskRuntime rows through the typed public authority boundary.

        Factory wait polls at 20 Hz. Each miss re-enters TaskRuntime fact
        projection, which tail-reads (and on miss, full-parses) the
        ``task_runtime.execution`` stream. Live L2-12 evidence: 130MiB stream,
        1MiB tail/tick, multi-GiB rchar, event-loop/GIL starve, Director
        heartbeat death. Cache the typed projection briefly so the wait loop
        only re-reads when the TTL expires.
        """

        now = time.monotonic()
        cached = self._task_runtime_projection_cache
        if (
            cached is not None
            and (now - self._task_runtime_projection_cached_at) < _TASK_RUNTIME_PROJECTION_CACHE_TTL_SECONDS
        ):
            return cached
        try:
            from polaris.cells.runtime.task_runtime.public.service import (
                TaskRuntimeService,
            )

            projection = TaskRuntimeService(str(self.workspace)).query_observable_task_rows_projection()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.debug("TaskRuntime typed observable projection unavailable: %s", exc)
            return None
        self._task_runtime_projection_cache = projection
        self._task_runtime_projection_cached_at = now
        return projection

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
        projection: ObservableTaskRowsProjectionV1 | None = None,
    ) -> CommandResult | None:
        """Project only committed turns belonging to each current terminal owner.

        TaskRuntime remains completion authority. Role facts can veto its
        terminal result; a parent run or a latest unrelated turn is not an owner.
        Public strict readers retain V2 records and expand authenticated V3
        payloads. Each protocol is paged and its captured head is rechecked.
        """
        if not process_terminal:
            return None
        from polaris.cells.events.fact_stream.public import (
            QueryFactEventsV1,
            QueryFactStreamHeadV1,
            QueryPayloadFactEventsV1,
            QuerySegmentedFactLedgerHeadV1,
            query_fact_events,
            query_fact_stream_head,
            query_payload_fact_events,
            query_payload_fact_ledger_presence,
            query_segmented_fact_ledger_head,
        )
        from polaris.cells.roles.kernel.public.turn_contracts import TurnOutcome
        from polaris.cells.runtime.task_runtime.public import TaskRuntimeService
        from polaris.kernelone.storage import resolve_workspace_runtime_identity

        def unknown(reason: str) -> CommandResult:
            return CommandResult(
                run_id=run_id,
                status="blocked",
                message="Current owner role outcome cannot be observed authoritatively",
                metadata={
                    "canonical_authoritative": False,
                    "terminal_source": "roles.kernel.turn_outcomes",
                    "reason": "role_turn_outcome_observation_unknown",
                    "observation_error": reason,
                },
            )

        try:
            rows = self._execution_rows(run_id=run_id, projection=projection)
            selectors: dict[tuple[str, str], set[str]] = {}
            for row in rows:
                metadata = row.get("metadata")
                runtime = metadata.get("runtime_execution") if isinstance(metadata, Mapping) else None
                fact = metadata.get("task_runtime_execution_fact") if isinstance(metadata, Mapping) else None
                if (
                    not isinstance(metadata, Mapping)
                    or not isinstance(runtime, Mapping)
                    or not isinstance(fact, Mapping)
                ):
                    return unknown("current_task_owner_identity_missing")
                child = str(runtime.get("run_id") or "").strip()
                external = str(runtime.get("external_task_id") or "").strip()
                session = str(runtime.get("session_id") or "").strip()
                attempt = runtime.get("attempt")
                if (
                    not child
                    or not external
                    or not session
                    or type(attempt) is not int
                    or attempt < 1
                    or str(runtime.get("task_id")) != str(row.get("task_id") or row.get("id"))
                    or tuple(str(fact.get(key) or "") for key in ("run_id", "session_id", "task_id"))
                    != (child, session, str(runtime.get("task_id")))
                    or fact.get("attempt") != attempt
                ):
                    return unknown("current_task_owner_identity_conflict")
                parent = str(row.get("factory_run_id") or "").strip()
                if parent != str(metadata.get("factory_run_id") or "").strip():
                    return unknown("current_task_parent_identity_conflict")
                key = (external, session)
                if key in selectors:
                    return unknown("current_task_owner_identity_ambiguous")
                selectors[key] = {value for value in (child, parent) if value}
            if not selectors:
                return None
            storage = resolve_workspace_runtime_identity(str(self.workspace))
            legacy_stream = "roles.kernel.turn_outcomes"
            payload_stream = "roles.kernel.turn_outcomes.v3"
            legacy_head_query = QueryFactStreamHeadV1(
                workspace=str(self.workspace), stream=legacy_stream, strict_integrity=True
            )
            legacy_head = query_fact_stream_head(legacy_head_query)
            events: list[tuple[str, dict[str, Any]]] = []
            offset = 0
            while True:
                page = query_fact_events(
                    QueryFactEventsV1(
                        workspace=str(self.workspace),
                        stream=legacy_stream,
                        event_type="turn_outcome_committed",
                        limit=100,
                        offset=offset,
                        strict_integrity=True,
                    )
                )
                events.extend((legacy_stream, event) for event in page.events)
                if len(page.events) < 100:
                    break
                offset += len(page.events)
            if query_fact_stream_head(legacy_head_query) != legacy_head:
                return unknown("role_outcome_legacy_head_changed")
            payload_head_query = QuerySegmentedFactLedgerHeadV1(
                workspace=str(self.workspace), logical_stream=payload_stream
            )
            payload_present = query_payload_fact_ledger_presence(payload_head_query)
            payload_head = None
            if payload_present:
                continuation = None
                while True:
                    payload_page = query_payload_fact_events(
                        QueryPayloadFactEventsV1(
                            workspace=str(self.workspace),
                            logical_stream=payload_stream,
                            limit=100,
                            continuation=continuation,
                        )
                    )
                    if payload_head is None:
                        payload_head = payload_page.captured_head
                    elif payload_page.captured_head != payload_head:
                        return unknown("role_outcome_payload_head_changed")
                    events.extend(
                        (payload_stream, event)
                        for event in payload_page.events
                        if event.get("event_type") == "turn_outcome_committed"
                    )
                    continuation = payload_page.continuation
                    if continuation is None:
                        break
                if query_segmented_fact_ledger_head(payload_head_query) != payload_head:
                    return unknown("role_outcome_payload_head_changed")
            if query_payload_fact_ledger_presence(payload_head_query) != payload_present:
                return unknown("role_outcome_payload_presence_changed")

            closing_projection = TaskRuntimeService(str(self.workspace)).query_observable_task_rows_projection()
            if (
                not self._projection_is_authoritative(closing_projection)
                or closing_projection.workspace != storage.workspace_abs
                or self._execution_rows(run_id=run_id, projection=closing_projection) != rows
            ):
                return unknown("current_task_owner_changed_during_role_query")
            if query_fact_stream_head(legacy_head_query) != legacy_head:
                return unknown("role_outcome_legacy_head_changed")
            if payload_present and query_segmented_fact_ledger_head(payload_head_query) != payload_head:
                return unknown("role_outcome_payload_head_changed")
            if query_payload_fact_ledger_presence(payload_head_query) != payload_present:
                return unknown("role_outcome_payload_presence_changed")

            latest: dict[tuple[str, str], tuple[str, int, CommandResult]] = {}
            for stream, event in events:
                payload = event.get("expanded_payload") if stream == payload_stream else event.get("payload")
                if not isinstance(payload, Mapping):
                    return unknown("committed_role_payload_missing")
                external = str(payload.get("task_id") or "").strip()
                outcome_run = str(payload.get("run_id") or "").strip()
                candidates = [key for key, runs in selectors.items() if key[0] == external and outcome_run in runs]
                if not candidates:
                    continue
                binding = payload.get("session_binding")
                session = str(binding.get("session_id") or "") if isinstance(binding, Mapping) else ""
                if not session:
                    return unknown("committed_role_session_binding_missing")
                key = (external, session)
                if key not in candidates:
                    continue  # A superseded session is not the current task owner.
                event_metadata = event.get("metadata") if stream == legacy_stream else event
                if not isinstance(event_metadata, Mapping):
                    return unknown("committed_role_record_metadata_missing")
                schema = (
                    "roles.kernel.turn_outcome_fact.v3"
                    if stream == payload_stream
                    else "roles.kernel.turn_outcome_fact.v2"
                )
                provenance = payload.get("provenance")
                outcome_payload = payload.get("outcome")
                turn = str(payload.get("turn_id") or "")
                transition = str(payload.get("transition_id") or "")
                if (
                    payload.get("schema_version") != schema
                    or event.get("source") != "roles.kernel.transaction"
                    or not turn
                    or not transition
                    or not isinstance(provenance, Mapping)
                    or dict(provenance)
                    != {
                        "workspace": storage.workspace_abs,
                        "run_id": outcome_run,
                        "task_id": external,
                        "turn_id": turn,
                        "transition_id": transition,
                    }
                    or payload.get("storage_identity") != storage.to_record()
                    or event_metadata.get("idempotency_key")
                    != ":".join((stream, storage.token, outcome_run, external, turn, transition))
                    or not isinstance(outcome_payload, Mapping)
                ):
                    return unknown("committed_role_provenance_invalid")
                if stream == legacy_stream and (
                    (event_metadata.get("run_id"), event_metadata.get("task_id")) != (outcome_run, external)
                    or event_metadata.get("provenance") != provenance
                    or event_metadata.get("storage_identity") != storage.to_record()
                ):
                    return unknown("committed_role_header_identity_invalid")
                outcome = TurnOutcome.model_validate(outcome_payload)
                outcome_hash = hashlib.sha256(
                    json.dumps(dict(outcome_payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                        "utf-8"
                    )
                ).hexdigest()
                if (outcome.run_id, str(outcome.turn_id), payload.get("outcome_hash")) != (
                    outcome_run,
                    turn,
                    outcome_hash,
                ):
                    return unknown("committed_role_outcome_hash_or_identity_invalid")
                seq = event.get("global_seq") if stream == payload_stream else event.get("seq")
                if type(seq) is not int or seq < 1 or not event.get("event_id"):
                    return unknown("committed_role_sequence_invalid")
                state = outcome.outcome_status.value
                status = "completed" if state == "completed" else "cancelled" if state == "cancelled" else "failed"
                result = CommandResult(
                    run_id=run_id,
                    status=status,
                    message=f"Committed current-owner turn outcome reached {state}",
                    metadata={
                        "canonical_authoritative": True,
                        "terminal_source": stream,
                        "fact_stream": stream,
                        "fact_event_seq": seq,
                        "fact_event_id": event["event_id"],
                        "outcome_hash": outcome_hash,
                        "fact_event_hash": event.get("event_hash")
                        if stream == payload_stream
                        else event.get("integrity_digest"),
                        "payload_ref": event.get("payload") if stream == payload_stream else None,
                        "owner_run_id": outcome_run,
                        "owner_task_id": external,
                        "owner_session_id": session,
                        "resolution_code": outcome.resolution_code.value,
                        "failure_class": outcome.failure_class.value if outcome.failure_class else None,
                    },
                )
                previous = latest.get(key)
                if previous is not None and previous[0] != stream:
                    return unknown("current_owner_present_in_both_role_protocols")
                if previous is None or seq > previous[1]:
                    latest[key] = (stream, seq, result)
            if not latest:
                return None
            results = [entry[2] for entry in latest.values()]
            selected = max(results, key=lambda result: _CANONICAL_STATUS_PRECEDENCE[result.status])
            selected.metadata["current_owner_outcomes"] = [dict(result.metadata) for result in results]
            return selected
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.debug("Canonical current-owner turn outcome lookup failed: %s", exc)
            return unknown(str(exc))

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

        if projection is None:
            return CommandResult(
                run_id=run_id,
                status="blocked",
                message="TaskRuntime fact-only observable projection is not ready",
                reason_code="task_runtime_fact_projection_not_ready",
                metadata={
                    "canonical_authoritative": False,
                    "degraded": True,
                    "terminal_source": "task_runtime_cutover_readiness",
                    "task_row_read_model_source": "unavailable",
                    "task_row_read_model_cutover_readiness": readiness,
                },
            )

        task_runtime_result = self._task_runtime_terminal_result(
            run_id=run_id,
            projection=projection,
        )
        if task_runtime_result is None:
            # A TransactionKernel turn outcome settles one role turn, not the
            # Director task execution. Under TaskRuntime authority it may veto
            # an already-terminal TaskRuntime result through the conflict
            # matrix below, but it must never grant completion by itself.
            # Querying roles.kernel.turn_outcomes before TaskRuntime is
            # terminal is also a live event-loop hazard: JsonlEventStore
            # still full-scans the stream (11MiB+/tick on L2-12) even when
            # this method discards the result.
            return None
        turn_outcome_result = self._committed_turn_outcome_result(
            run_id=run_id,
            process_terminal=process_terminal,
            projection=projection,
        )
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
                "canonical_authoritative": metadata.get("canonical_authoritative") is True,
                "terminal_source": "canonical_conflict_matrix",
                "canonical_conflict": task_runtime_status != turn_outcome_status,
                "task_runtime_status": task_runtime_status,
                "turn_outcome_status": turn_outcome_status,
                "turn_outcome_observation": dict(turn_outcome_result.metadata or {}),
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

    @staticmethod
    def _is_missing_orchestration_snapshot(result: CommandResult | None) -> bool:
        """Return whether a lifecycle hint is 'run not created yet', not a failure."""

        if result is None:
            return False
        return str(result.reason_code or "").strip() == "RUN_NOT_FOUND"

    async def _canonical_terminal_result_offloop(
        self,
        *,
        run_id: str,
        process_terminal: bool,
    ) -> CommandResult | None:
        """Project the canonical terminal result off the asyncio event loop."""

        return await asyncio.to_thread(
            self.canonical_terminal_result,
            run_id=run_id,
            process_terminal=process_terminal,
        )

    def build_orchestration_service(self, context: dict[str, Any]) -> Any:
        from polaris.bootstrap.config import Settings
        from polaris.cells.orchestration.pm_dispatch.public.service import OrchestrationCommandService

        settings = context.get("settings") or Settings(workspace=Path(self.workspace))
        return OrchestrationCommandService(settings)

    async def cancel_active_run(self, run_id: str, *, reason: str) -> CommandResult | None:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return None
        canonical_result = await self._canonical_terminal_result_offloop(
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
            # Live L2-12 task 259: Director lease expired after settlement
            # head_recheck starved heartbeats.  Orchestration had already
            # dropped `_run_locks[director-aa724e5f4540]`.  cancel_run then
            # raised InvalidStateError and fail-closed the whole
            # director_dispatch stage.  "not active" is the same already-gone
            # case as NotFoundError.
            if exc.__class__.__name__ not in {"NotFoundError", "InvalidStateError"}:
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
            canonical = await self._canonical_terminal_result_offloop(
                run_id=run_id,
                process_terminal=process_terminal,
            )
            canonical_metadata = (
                canonical.metadata if canonical is not None and isinstance(canonical.metadata, dict) else {}
            )
            if canonical is not None and canonical_metadata.get("canonical_authoritative") is True:
                return canonical

            lifecycle_status = str(lifecycle_result.status or "").strip().lower()
            if lifecycle_status in failure_statuses and not self._is_missing_orchestration_snapshot(lifecycle_result):
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
                canonical = await self._canonical_terminal_result_offloop(
                    run_id=run_id,
                    process_terminal=True,
                )
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
                live_execution = self._active_task_runtime_barrier_result(
                    run_id=run_id,
                    reason="orchestration_snapshot_deferred",
                )
                if live_execution is None:
                    observed = cast("CommandResult", await service.query_run_status(run_id))
                    if not self._is_missing_orchestration_snapshot(observed):
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
