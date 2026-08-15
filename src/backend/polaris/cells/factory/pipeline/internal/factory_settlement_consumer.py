"""Event-woken durable consumer for terminal Factory settlement."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import weakref
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

from polaris.cells.events.fact_stream.public.contracts import (
    FactStreamError,
    QueryFactEventsV1,
    QueryFactStreamHeadV1,
)
from polaris.cells.runtime.task_runtime.public.contracts import (
    TASK_RUNTIME_EXECUTION_FACT_SCHEMA_V1,
    TASK_RUNTIME_EXECUTION_SOURCE_V1 as TASK_RUNTIME_EXECUTION_SOURCE,
    TASK_RUNTIME_EXECUTION_STREAM_V1 as TASK_RUNTIME_EXECUTION_STREAM,
    TaskRuntimeExecutionFactV1,
)

from .factory_settlement_journal import (
    FactorySettlementJournal,
    FactStreamPort,
    SettlementIdentity,
    SettlementJournalReplaySnapshot,
    SettlementJournalStatus,
    SettlementPendingPhase,
)

_FACT_STREAM_ENVELOPE_VERSION = 1
_SOURCE_PAGE_SIZE = 1000
_TASK_RUNTIME_SCHEMA_PREFIX = "task-runtime.execution-fact/"
_OPEN_BARRIER_REPLAY_COOLDOWN_SECONDS = 5.0

logger = logging.getLogger(__name__)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _adapter_result_from_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    snapshot = _mapping(payload.get("task_row_snapshot"))
    metadata = _mapping(snapshot.get("metadata")) or _mapping(payload.get("metadata"))
    adapter = metadata.get("adapter_result")
    if isinstance(adapter, Mapping):
        return adapter
    adapter = payload.get("adapter_result")
    return adapter if isinstance(adapter, Mapping) else {}


def _is_rematerialize_no_write_terminal(payload: Mapping[str, Any]) -> bool:
    """Return True for rematerialize completes that must not enter waiting_barrier.

    Live L2-12 seq 515: TASK-1 rematerialize completed with a CE projection,
    write_tool_evidence=None, and empty new/modified files.  That fact still
    had factory_run_id so settlement treated it as a factory terminal and
    parked FIFO on an open run-level barrier.
    """

    adapter = _adapter_result_from_payload(payload)
    if not adapter or adapter.get("write_tool_evidence") is True:
        return False
    snapshot = _mapping(payload.get("task_row_snapshot"))
    metadata = _mapping(snapshot.get("metadata")) or _mapping(payload.get("metadata"))
    return bool(metadata.get("task_completion_projection"))


class FactorySettlementConsumerError(RuntimeError):
    """Base error raised by the settlement consumer boundary."""

    def __init__(self, message: str, *, code: str) -> None:
        normalized_message = str(message or "").strip()
        normalized_code = str(code or "").strip()
        if not normalized_message or not normalized_code:
            raise ValueError("settlement errors require non-empty message and code")
        super().__init__(normalized_message)
        self.code = normalized_code


class FactorySettlementLifecycleError(FactorySettlementConsumerError):
    """Raised when a lifecycle method is used before start or after stop."""


class SourceFactNotFoundError(FactorySettlementConsumerError):
    """Raised when a wake hint references no durable source fact."""


class SourceFactValidationError(FactorySettlementConsumerError):
    """Raised when an authoritative source fact cannot be safely interpreted."""


class UnsupportedSourceSchemaError(SourceFactValidationError):
    """Raised for a well-formed future schema that may become readable later."""


class FactorySettlementPortError(FactorySettlementConsumerError):
    """Base error translated by the FactoryRunService adapter."""


class FactorySettlementRetryableError(FactorySettlementPortError):
    """A transient settlement failure that must remain pending."""


class FactorySettlementPermanentError(FactorySettlementPortError):
    """A permanent service rejection that is safe to dead-letter."""


class FactorySettlementFencedError(FactorySettlementPermanentError):
    """The requested Factory workspace authority has been fenced."""


class FactorySettlementRecoveryRequiredError(FactorySettlementRetryableError):
    """The lease expired and requires explicit stale-owner recovery."""


@dataclass(frozen=True, slots=True)
class FactorySettlementBarrierQuery:
    """Exact source fact and Factory scope required for a barrier read."""

    workspace: str
    factory_run_id: str
    source_fact_event_id: str
    source_fact_seq: int
    source_run_id: str
    workspace_fencing_token: int


@dataclass(frozen=True, slots=True)
class FactorySettlementBarrierSnapshot:
    """Composed Run Ledger barrier plus current lease authority."""

    workspace: str
    factory_run_id: str
    source_fact_visible: bool
    closed: bool
    release_allowed: bool
    workspace_fencing_token: int
    barrier_hash: str
    blocking_reasons: tuple[str, ...] = ()
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("workspace", "factory_run_id", "barrier_hash"):
            value = str(getattr(self, field_name) or "").strip()
            if not value:
                raise ValueError(f"{field_name} must be a non-empty string")
            object.__setattr__(self, field_name, value)
        token = self.workspace_fencing_token
        if isinstance(token, bool) or not isinstance(token, int) or token < 1:
            raise ValueError("workspace_fencing_token must be an int >= 1")
        object.__setattr__(
            self,
            "blocking_reasons",
            tuple(str(reason).strip() for reason in self.blocking_reasons if str(reason).strip()),
        )
        object.__setattr__(self, "evidence", dict(self.evidence))


class FactorySettlementBarrierPort(Protocol):
    """Read-only Run Ledger barrier dependency."""

    def query(self, query: FactorySettlementBarrierQuery, /) -> FactorySettlementBarrierSnapshot:
        """Return the current barrier and workspace fencing authority."""


class FactoryRunSettlementPort(Protocol):
    """Mutation boundary implemented by a FactoryRunService adapter."""

    async def settle_terminal_run(self, run_id: str) -> object:
        """Idempotently settle one terminal Factory run."""

    async def recover_stale_workspace_owner(
        self,
        run_id: str,
        *,
        expected_fencing_token: int,
        reason: str,
    ) -> object:
        """Fence and release an expired owner under the expected token."""


@dataclass(frozen=True, slots=True)
class ValidatedTaskRuntimeSourceFact:
    """Fully validated TaskRuntime fact plus its durable envelope identity."""

    event_id: str
    event_seq: int
    fact: TaskRuntimeExecutionFactV1
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class _SourceReplaySnapshot:
    """One replay-scoped immutable projection of task-runtime FactStream pages."""

    events: tuple[Mapping[str, Any], ...]
    events_by_offset: Mapping[int, Mapping[str, Any]]
    events_by_id: Mapping[str, tuple[Mapping[str, Any], int]]


@dataclass(frozen=True, slots=True)
class TaskRuntimeSettlementFact:
    """Validated terminal TaskRuntime fact used as settlement evidence."""

    event_id: str
    event_seq: int
    event_type: str
    workspace: str
    factory_run_id: str
    source_run_id: str
    workspace_fencing_token: int
    task_runtime_fact: TaskRuntimeExecutionFactV1
    payload: Mapping[str, Any]

    @property
    def identity(self) -> SettlementIdentity:
        return SettlementIdentity(
            workspace=self.workspace,
            source_fact_event_id=self.event_id,
            factory_run_id=self.factory_run_id,
            workspace_fencing_token=self.workspace_fencing_token,
        )


class SettlementOutcome(StrEnum):
    """Consumer decision states exposed to wake adapters."""

    APPLIED = "applied"
    CONTENDED = "contended"
    DEAD_LETTER = "dead_letter"
    DUPLICATE = "duplicate"
    IGNORED = "ignored"
    PENDING = "pending"
    RETRYABLE = "retryable"


@dataclass(frozen=True, slots=True)
class SettlementDecision:
    """One durable source-fact decision and its ACK safety."""

    source_fact_event_id: str
    source_fact_seq: int
    factory_run_id: str
    workspace_fencing_token: int
    outcome: SettlementOutcome
    ack_safe: bool
    journal_event_id: str = ""
    reason_code: str = ""


@dataclass(frozen=True, slots=True)
class SettlementReplayReport:
    """Result of one finite startup replay or wake drain."""

    decisions: tuple[SettlementDecision, ...]
    started_now: bool = False
    already_started: bool = False

    @property
    def ack_safe(self) -> bool:
        return all(decision.ack_safe for decision in self.decisions)

    @property
    def transport_ack_safe(self) -> bool:
        """Return whether the JetStream wake may be ACKed.

        ``ack_safe`` still owns source-checkpoint advancement.  A durable
        ``run_ledger_barrier_open`` pending must not advance the checkpoint,
        but withholding the transport ACK caused JetStream to redeliver the
        same wake immediately.  Live L2-12 retry-5: TASK-2 completed sat in
        waiting_barrier while director_dispatch was still in flight; the
        redelivery storm starved TASK-3-tests before its first LLM call.
        """

        if self.ack_safe:
            return True
        if not self.decisions:
            return False
        return all(
            decision.ack_safe or decision.reason_code == "run_ledger_barrier_open" for decision in self.decisions
        )


_RUN_LOCKS_GUARD = threading.Lock()
_RUN_LOCKS: weakref.WeakValueDictionary[tuple[int, str, str, int], asyncio.Lock] = weakref.WeakValueDictionary()


def _run_lock(identity: SettlementIdentity) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    key = (
        id(loop),
        identity.workspace,
        identity.factory_run_id,
        identity.workspace_fencing_token,
    )
    with _RUN_LOCKS_GUARD:
        lock = _RUN_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _RUN_LOCKS[key] = lock
        return lock


def _canonical_workspace(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("workspace must be a non-empty string")
    return os.path.normcase(str(Path(normalized).expanduser().resolve(strict=False)))


def _barrier_evidence_refs(snapshot: FactorySettlementBarrierSnapshot) -> tuple[str, ...]:
    """Validate and detach evidence references from one barrier snapshot."""

    raw_refs = snapshot.evidence.get("evidence_refs")
    if raw_refs is None:
        return ()
    if not isinstance(raw_refs, Sequence) or isinstance(raw_refs, (str, bytes, bytearray)):
        raise FactorySettlementConsumerError(
            "Run Ledger barrier evidence_refs must be a sequence of strings",
            code="run_ledger_barrier_evidence_refs_invalid",
        )

    normalized: set[str] = set()
    for raw_ref in raw_refs:
        if not isinstance(raw_ref, str):
            raise FactorySettlementConsumerError(
                "Run Ledger barrier evidence_refs must contain only strings",
                code="run_ledger_barrier_evidence_refs_invalid",
            )
        reference = raw_ref.strip()
        if reference:
            normalized.add(reference)
    return tuple(sorted(normalized))


def _positive_int(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SourceFactValidationError(
            f"{field_name} must be an integer >= 1",
            code=f"invalid_{field_name}",
        )
    if value < 1:
        raise SourceFactValidationError(
            f"{field_name} must be an integer >= 1",
            code=f"invalid_{field_name}",
        )
    return value


def _require_envelope_version(value: object, *, field_name: str) -> None:
    if value == _FACT_STREAM_ENVELOPE_VERSION and not isinstance(value, bool):
        return
    if isinstance(value, int) and not isinstance(value, bool) and value > _FACT_STREAM_ENVELOPE_VERSION:
        raise UnsupportedSourceSchemaError(
            f"source fact {field_name} is newer than this consumer",
            code=f"unsupported_source_{field_name}",
        )
    raise SourceFactValidationError(
        f"source fact {field_name} must be exactly {_FACT_STREAM_ENVELOPE_VERSION}",
        code=f"invalid_source_{field_name}",
    )


def _require_task_runtime_schema(value: object) -> None:
    if value == TASK_RUNTIME_EXECUTION_FACT_SCHEMA_V1:
        return
    if isinstance(value, str) and value.startswith(_TASK_RUNTIME_SCHEMA_PREFIX):
        suffix = value.removeprefix(_TASK_RUNTIME_SCHEMA_PREFIX)
        if suffix.isdigit() and int(suffix) > 1:
            raise UnsupportedSourceSchemaError(
                "TaskRuntime payload schema is newer than this consumer",
                code="unsupported_task_runtime_schema",
            )
    raise SourceFactValidationError(
        "TaskRuntime payload schema is missing or malformed",
        code="unsupported_task_runtime_schema",
    )


def _source_event_identity(event: Mapping[str, Any]) -> tuple[str, int]:
    event_id = str(event.get("event_id") or "").strip()
    raw_seq = event.get("seq")
    event_seq = raw_seq if isinstance(raw_seq, int) and not isinstance(raw_seq, bool) else 0
    return event_id, max(0, event_seq)


def _fencing_token(payload: Mapping[str, Any]) -> int:
    details = _mapping(payload.get("details"))
    snapshot = _mapping(payload.get("task_row_snapshot"))
    metadata = _mapping(snapshot.get("metadata"))
    lease = _mapping(
        payload.get("factory_workspace_run_lease")
        or details.get("factory_workspace_run_lease")
        or metadata.get("factory_workspace_run_lease")
    )
    raw_token = (
        payload.get("workspace_lease_fencing_token")
        or payload.get("factory_workspace_fencing_token")
        or details.get("workspace_lease_fencing_token")
        or details.get("factory_workspace_fencing_token")
        or lease.get("fencing_token")
    )
    return _positive_int(raw_token, field_name="workspace_fencing_token")


class FactorySettlementConsumer:
    """Finite, event-woken consumer over durable TaskRuntime facts.

    ``start`` performs exactly one startup replay. ``wake`` performs one
    finite re-read after a JetStream or ledger signal. Neither method waits,
    sleeps, or treats the transport message as settlement authority.
    """

    def __init__(
        self,
        *,
        workspace: str,
        fact_stream: FactStreamPort,
        journal: FactorySettlementJournal,
        barrier: FactorySettlementBarrierPort,
        factory_runs: FactoryRunSettlementPort,
    ) -> None:
        self._workspace = _canonical_workspace(workspace)
        if _canonical_workspace(journal.workspace) != self._workspace:
            raise ValueError("journal workspace must match consumer workspace")
        self._fact_stream = fact_stream
        self._journal = journal
        self._barrier = barrier
        self._factory_runs = factory_runs
        self._lifecycle_lock = asyncio.Lock()
        self._started = False
        self._stopped = False
        self._journal_snapshot: SettlementJournalReplaySnapshot | None = None
        self._last_open_barrier_replay_at = 0.0

    @property
    def workspace(self) -> str:
        return self._workspace

    async def start(self) -> SettlementReplayReport:
        """Start idempotently and perform one crash-recovery replay."""

        async with self._lifecycle_lock:
            if self._started and not self._stopped:
                return SettlementReplayReport(decisions=(), already_started=True)
            self._started = True
            self._stopped = False
            decisions = await self._replay_locked(recover_applying=True)
            return SettlementReplayReport(decisions=decisions, started_now=True)

    async def replay(self) -> SettlementReplayReport:
        """Perform one finite durable replay while running."""

        async with self._lifecycle_lock:
            self._require_running()
            decisions = await self._replay_locked(recover_applying=True)
            return SettlementReplayReport(decisions=decisions)

    async def wake(self, source_fact_event_id: str | None = None) -> SettlementReplayReport:
        """Handle one wake signal by re-reading source facts and the barrier."""

        async with self._lifecycle_lock:
            self._require_running()
            hinted_id = str(source_fact_event_id or "").strip()
            if not hinted_id:
                decisions = await self._replay_locked(recover_applying=False)
                return SettlementReplayReport(decisions=decisions)

            event, source_offset = self._find_source_fact(hinted_id)
            decision = await self._process_source_event(
                event,
                recover_applying=False,
            )
            checkpoint = self._journal.latest_checkpoint_offset(source_stream=TASK_RUNTIME_EXECUTION_STREAM)
            if decision.ack_safe and source_offset == checkpoint:
                self._checkpoint(
                    decision,
                    next_source_offset=source_offset + 1,
                    source_event=event,
                )
            return SettlementReplayReport(decisions=(decision,))

    async def stop(self) -> None:
        """Stop idempotently; no background task or timer requires cancellation."""

        async with self._lifecycle_lock:
            self._stopped = True
            self._started = False
            self._journal_snapshot = None

    def _require_running(self) -> None:
        if not self._started or self._stopped:
            raise FactorySettlementLifecycleError(
                "Factory settlement consumer is not running",
                code="factory_settlement_consumer_not_running",
            )

    async def _replay_locked(self, *, recover_applying: bool) -> tuple[SettlementDecision, ...]:
        prefix_decisions: tuple[SettlementDecision, ...] = ()
        journal_snapshot = self._journal_snapshot
        if (
            not recover_applying
            and journal_snapshot is not None
            and self._last_open_barrier_replay_at > 0.0
            and (time.monotonic() - self._last_open_barrier_replay_at) < _OPEN_BARRIER_REPLAY_COOLDOWN_SECONDS
        ):
            checkpoint = journal_snapshot.latest_checkpoint_offset(source_stream=TASK_RUNTIME_EXECUTION_STREAM)
            # source_events is keyed as query_offset+1.  The journal checkpoint
            # is the next 0-based source offset to process.
            head_event = journal_snapshot.source_events.get(checkpoint + 1)
            if head_event is not None:
                logger.warning(
                    "[settlement.startup] phase=decision_replay status=head_recheck checkpoint=%d",
                    checkpoint,
                )
                head_decision = await self._process_source_event(
                    head_event,
                    recover_applying=False,
                    journal_snapshot=journal_snapshot,
                )
                if not head_decision.ack_safe:
                    if head_decision.reason_code == "run_ledger_barrier_open":
                        self._last_open_barrier_replay_at = time.monotonic()
                    return (head_decision,)
                self._checkpoint(
                    head_decision,
                    next_source_offset=checkpoint + 1,
                    source_event=head_event,
                    journal_snapshot=journal_snapshot,
                )
                self._last_open_barrier_replay_at = 0.0
                prefix_decisions = (head_decision,)
                # Barrier closed: continue into the normal tail replay below.
        if journal_snapshot is None:
            source_started_at = time.monotonic()
            logger.warning("[settlement.startup] phase=source_snapshot status=started")
            source_snapshot = await asyncio.to_thread(self._read_source_replay_snapshot)
            logger.warning(
                "[settlement.startup] phase=source_snapshot status=completed duration_ms=%d events=%d",
                int((time.monotonic() - source_started_at) * 1000),
                len(source_snapshot.events),
            )
            journal_started_at = time.monotonic()
            logger.warning("[settlement.startup] phase=journal_snapshot status=started")
            journal_snapshot = self._journal.open_replay_snapshot(
                source_stream=TASK_RUNTIME_EXECUTION_STREAM,
                source_events=source_snapshot.events_by_offset,
            )
            logger.warning(
                "[settlement.startup] phase=journal_snapshot status=completed duration_ms=%d",
                int((time.monotonic() - journal_started_at) * 1000),
            )
            checkpoint = journal_snapshot.latest_checkpoint_offset(source_stream=TASK_RUNTIME_EXECUTION_STREAM)
            replay_events = source_snapshot.events[checkpoint:]
        else:
            # The first startup replay has already validated the complete
            # source and checkpoint chains.  The bridge-start catch-up must
            # close only the race window after that head; re-reading the full
            # 32 MiB TaskRuntime stream and 5.7 MiB settlement journal blocked
            # L1-04 readiness long enough for the Launcher identity timeout.
            # Rebuild the in-memory snapshot from its already-validated
            # records plus the durable source tail.  Appends during this
            # lifecycle continue to update the replacement snapshot.
            checkpoint = journal_snapshot.latest_checkpoint_offset(source_stream=TASK_RUNTIME_EXECUTION_STREAM)
            source_snapshot = await asyncio.to_thread(
                self._read_source_replay_snapshot,
                start_offset=checkpoint,
            )
            source_events = dict(journal_snapshot.source_events)
            source_events.update(source_snapshot.events_by_offset)
            journal_snapshot = SettlementJournalReplaySnapshot.create(
                source_stream=TASK_RUNTIME_EXECUTION_STREAM,
                source_events=source_events,
                records=journal_snapshot.records,
                current_seq=journal_snapshot.current_seq,
                checkpoint_chain=journal_snapshot.checkpoint_chain(source_stream=TASK_RUNTIME_EXECUTION_STREAM),
            )
            replay_events = source_snapshot.events
        self._journal_snapshot = journal_snapshot
        replay_started_at = time.monotonic()
        logger.warning(
            "[settlement.startup] phase=decision_replay status=started events=%d checkpoint=%d",
            len(replay_events),
            checkpoint,
        )
        decisions: list[SettlementDecision] = []
        for source_offset, event in enumerate(replay_events, start=checkpoint):
            decision = await self._process_source_event(
                event,
                recover_applying=recover_applying,
                journal_snapshot=journal_snapshot,
            )
            decisions.append(decision)
            if not decision.ack_safe:
                if decision.reason_code == "run_ledger_barrier_open":
                    self._last_open_barrier_replay_at = time.monotonic()
                return tuple(decisions)
            self._checkpoint(
                decision,
                next_source_offset=source_offset + 1,
                source_event=event,
                journal_snapshot=journal_snapshot,
            )
        logger.warning(
            "[settlement.startup] phase=decision_replay status=completed duration_ms=%d decisions=%d",
            int((time.monotonic() - replay_started_at) * 1000),
            len(prefix_decisions) + len(decisions),
        )
        return (*prefix_decisions, *decisions)

    async def _process_source_event(
        self,
        event: Mapping[str, Any],
        *,
        recover_applying: bool,
        journal_snapshot: SettlementJournalReplaySnapshot | None = None,
    ) -> SettlementDecision:
        try:
            validated = self._validate_task_runtime_fact(event)
        except UnsupportedSourceSchemaError as exc:
            event_id, event_seq = _source_event_identity(event)
            return SettlementDecision(
                source_fact_event_id=event_id or "unsupported-source-fact",
                source_fact_seq=event_seq,
                factory_run_id="",
                workspace_fencing_token=0,
                outcome=SettlementOutcome.RETRYABLE,
                ack_safe=False,
                reason_code=exc.code,
            )
        except SourceFactValidationError as exc:
            return self._dead_letter_invalid_source(
                event=event,
                error=exc,
                journal_snapshot=journal_snapshot,
            )

        if not validated.fact.terminal:
            return self._ignored_decision(
                validated,
                reason_code="non_terminal_source_fact",
            )
        if not str(validated.payload.get("factory_run_id") or "").strip():
            return self._ignored_decision(
                validated,
                reason_code="non_factory_terminal_fact",
            )
        if _is_rematerialize_no_write_terminal(validated.payload):
            return self._ignored_decision(
                validated,
                reason_code="rematerialize_no_write_terminal_fact",
            )
        try:
            source = self._to_settlement_fact(validated)
        except SourceFactValidationError as exc:
            return self._dead_letter_invalid_source(
                event=event,
                error=exc,
                journal_snapshot=journal_snapshot,
            )

        lock = _run_lock(source.identity)
        async with lock:
            return await self._decide_and_apply(
                source,
                recover_applying=recover_applying,
                journal_snapshot=journal_snapshot,
            )

    async def _decide_and_apply(
        self,
        source: TaskRuntimeSettlementFact,
        *,
        recover_applying: bool,
        journal_snapshot: SettlementJournalReplaySnapshot | None = None,
    ) -> SettlementDecision:
        identity = source.identity
        state = self._journal.state_for(identity, snapshot=journal_snapshot)
        if state is not None and state.status is SettlementJournalStatus.APPLIED:
            return self._terminal_decision(
                source,
                outcome=SettlementOutcome.DUPLICATE,
                journal_event_id=state.event_id,
                reason_code="already_applied",
            )
        if state is not None and state.status is SettlementJournalStatus.DEAD_LETTER:
            return self._terminal_decision(
                source,
                outcome=SettlementOutcome.DEAD_LETTER,
                journal_event_id=state.event_id,
                reason_code=str(state.payload.get("reason_code") or "already_dead_lettered"),
            )

        barrier = await self._query_barrier(source)
        evidence_refs = _barrier_evidence_refs(barrier)
        if barrier.workspace_fencing_token != source.workspace_fencing_token:
            return self._append_dead_letter_decision(
                source,
                identity=identity,
                source_fact_seq=source.event_seq,
                reason_code="stale_workspace_fencing_token",
                barrier_hash=barrier.barrier_hash,
                evidence_refs=evidence_refs,
                journal_snapshot=journal_snapshot,
            )
        if not barrier.source_fact_visible or not barrier.closed:
            if state is not None and state.status is SettlementJournalStatus.PENDING:
                # Live L1-09 restart: journal already advanced to applying /
                # waiting_retry while the Run Ledger barrier is still open.
                # Replaying waiting_barrier with a new barrier_hash reuses the
                # stable idempotency key and crashes lifespan
                # (FactStreamError idempotency_conflict fields=payload).
                return SettlementDecision(
                    source_fact_event_id=source.event_id,
                    source_fact_seq=source.event_seq,
                    factory_run_id=source.factory_run_id,
                    workspace_fencing_token=source.workspace_fencing_token,
                    outcome=SettlementOutcome.PENDING,
                    ack_safe=False,
                    journal_event_id=state.event_id,
                    reason_code="run_ledger_barrier_open",
                )
            pending = self._journal.append_waiting_barrier(
                identity,
                source_fact_seq=source.event_seq,
                barrier_hash=barrier.barrier_hash,
                evidence_refs=evidence_refs,
                snapshot=journal_snapshot,
            )
            return SettlementDecision(
                source_fact_event_id=source.event_id,
                source_fact_seq=source.event_seq,
                factory_run_id=source.factory_run_id,
                workspace_fencing_token=source.workspace_fencing_token,
                outcome=SettlementOutcome.PENDING,
                ack_safe=False,
                journal_event_id=pending.event_id,
                reason_code="run_ledger_barrier_open",
            )
        if not barrier.release_allowed:
            return self._append_dead_letter_decision(
                source,
                identity=identity,
                source_fact_seq=source.event_seq,
                reason_code="run_ledger_release_not_allowed",
                barrier_hash=barrier.barrier_hash,
                evidence_refs=evidence_refs,
                journal_snapshot=journal_snapshot,
            )

        state = self._journal.state_for(identity, snapshot=journal_snapshot)
        recovery = bool(
            recover_applying
            and state is not None
            and state.status is SettlementJournalStatus.PENDING
            and state.pending_phase is SettlementPendingPhase.APPLYING
        )
        if (
            state is not None
            and state.status is SettlementJournalStatus.PENDING
            and state.pending_phase is SettlementPendingPhase.APPLYING
            and not recovery
        ):
            return SettlementDecision(
                source_fact_event_id=source.event_id,
                source_fact_seq=source.event_seq,
                factory_run_id=source.factory_run_id,
                workspace_fencing_token=source.workspace_fencing_token,
                outcome=SettlementOutcome.CONTENDED,
                ack_safe=False,
                journal_event_id=state.event_id,
                reason_code="settlement_claim_inflight",
            )

        claim = self._journal.try_claim(
            identity,
            source_fact_seq=source.event_seq,
            recovery=recovery,
            barrier_hash=barrier.barrier_hash,
            evidence_refs=evidence_refs,
            snapshot=journal_snapshot,
        )
        if claim is None:
            latest = self._journal.state_for(identity, snapshot=journal_snapshot)
            if latest is not None and latest.status is SettlementJournalStatus.APPLIED:
                return self._terminal_decision(
                    source,
                    outcome=SettlementOutcome.DUPLICATE,
                    journal_event_id=latest.event_id,
                    reason_code="concurrent_apply_completed",
                )
            return SettlementDecision(
                source_fact_event_id=source.event_id,
                source_fact_seq=source.event_seq,
                factory_run_id=source.factory_run_id,
                workspace_fencing_token=source.workspace_fencing_token,
                outcome=SettlementOutcome.CONTENDED,
                ack_safe=False,
                journal_event_id=latest.event_id if latest is not None else "",
                reason_code="settlement_claim_contended",
            )

        try:
            await self._apply_settlement(source)
        except FactorySettlementRetryableError as exc:
            pending = self._journal.append_waiting_retry(
                identity,
                source_fact_seq=source.event_seq,
                claim_id=claim.claim_id,
                error_code=exc.code,
                barrier_hash=barrier.barrier_hash,
                evidence_refs=evidence_refs,
                snapshot=journal_snapshot,
            )
            return SettlementDecision(
                source_fact_event_id=source.event_id,
                source_fact_seq=source.event_seq,
                factory_run_id=source.factory_run_id,
                workspace_fencing_token=source.workspace_fencing_token,
                outcome=SettlementOutcome.RETRYABLE,
                ack_safe=False,
                journal_event_id=pending.event_id,
                reason_code=exc.code,
            )
        except FactorySettlementPermanentError as exc:
            dead_letter = self._journal.append_dead_letter(
                identity,
                source_fact_seq=source.event_seq,
                reason_code=exc.code,
                barrier_hash=barrier.barrier_hash,
                evidence_refs=evidence_refs,
                snapshot=journal_snapshot,
            )
            return self._terminal_decision(
                source,
                outcome=SettlementOutcome.DEAD_LETTER,
                journal_event_id=dead_letter.event_id,
                reason_code=exc.code,
            )

        applied = self._journal.append_applied(
            identity,
            source_fact_seq=source.event_seq,
            barrier_hash=barrier.barrier_hash,
            evidence_refs=evidence_refs,
            snapshot=journal_snapshot,
        )
        return self._terminal_decision(
            source,
            outcome=SettlementOutcome.APPLIED,
            journal_event_id=applied.event_id,
            reason_code="settlement_applied",
        )

    async def _apply_settlement(
        self,
        source: TaskRuntimeSettlementFact,
    ) -> None:
        try:
            await self._factory_runs.settle_terminal_run(source.factory_run_id)
        except FactorySettlementRecoveryRequiredError:
            await self._factory_runs.recover_stale_workspace_owner(
                source.factory_run_id,
                expected_fencing_token=source.workspace_fencing_token,
                reason="factory_settlement_consumer_lease_expired",
            )

    async def _query_barrier(
        self,
        source: TaskRuntimeSettlementFact,
    ) -> FactorySettlementBarrierSnapshot:
        # Live L2-12 epoch 7: query_factory_settlement_barrier is O(T+C) and
        # synchronous.  Running it on the asyncio loop while rematerialize
        # completes sat in waiting_barrier starved Director heartbeats.
        query = FactorySettlementBarrierQuery(
            workspace=self._workspace,
            factory_run_id=source.factory_run_id,
            source_fact_event_id=source.event_id,
            source_fact_seq=source.event_seq,
            source_run_id=source.source_run_id,
            workspace_fencing_token=source.workspace_fencing_token,
        )
        snapshot = await asyncio.to_thread(self._barrier.query, query)
        if _canonical_workspace(snapshot.workspace) != self._workspace:
            raise FactorySettlementConsumerError(
                "Run Ledger barrier returned a foreign workspace",
                code="run_ledger_barrier_workspace_mismatch",
            )
        if snapshot.factory_run_id != source.factory_run_id:
            raise FactorySettlementConsumerError(
                "Run Ledger barrier returned a foreign Factory run",
                code="run_ledger_barrier_factory_run_mismatch",
            )
        return snapshot

    def _validate_task_runtime_fact(
        self,
        event: Mapping[str, Any],
    ) -> ValidatedTaskRuntimeSourceFact:
        event_id = str(event.get("event_id") or "").strip()
        if not event_id:
            raise SourceFactValidationError(
                "source fact event_id is missing",
                code="invalid_source_event_id",
            )
        if str(event.get("stream") or "").strip() != TASK_RUNTIME_EXECUTION_STREAM:
            raise SourceFactValidationError(
                "source fact stream is not task_runtime.execution",
                code="invalid_source_stream",
            )
        if str(event.get("source") or "").strip() != TASK_RUNTIME_EXECUTION_SOURCE:
            raise SourceFactValidationError(
                "source fact source is not runtime.task_runtime",
                code="invalid_source_source",
            )
        _require_envelope_version(
            event.get("schema_version"),
            field_name="schema_version",
        )
        _require_envelope_version(
            event.get("event_version"),
            field_name="event_version",
        )
        payload_raw = event.get("payload")
        if not isinstance(payload_raw, Mapping):
            raise SourceFactValidationError(
                "source fact payload must be a mapping",
                code="invalid_source_payload",
            )
        payload = dict(payload_raw)
        _require_task_runtime_schema(payload.get("schema_version"))
        transition_id = str(payload.get("transition_id") or "").strip()
        try:
            typed_fact = TaskRuntimeExecutionFactV1.from_payload(
                transition_id=transition_id,
                payload=payload,
            )
        except (TypeError, ValueError) as exc:
            raise SourceFactValidationError(
                "TaskRuntime payload does not satisfy TaskRuntimeExecutionFactV1",
                code="invalid_task_runtime_payload",
            ) from exc
        if _canonical_workspace(typed_fact.workspace) != self._workspace:
            raise SourceFactValidationError(
                "source fact workspace does not match the consumer",
                code="source_workspace_mismatch",
            )
        envelope_event_type = str(event.get("event_type") or "").strip().lower()
        if envelope_event_type != typed_fact.event_type:
            raise SourceFactValidationError(
                "source envelope event_type does not match the typed payload",
                code="source_event_type_mismatch",
            )
        payload_terminal = payload.get("terminal")
        if not isinstance(payload_terminal, bool) or payload_terminal is not typed_fact.terminal:
            raise SourceFactValidationError(
                "TaskRuntime payload terminal flag disagrees with typed status",
                code="task_runtime_terminal_mismatch",
            )
        if str(payload.get("idempotency_key") or "").strip() != typed_fact.idempotency_key:
            raise SourceFactValidationError(
                "TaskRuntime payload idempotency key is invalid",
                code="task_runtime_idempotency_key_mismatch",
            )
        return ValidatedTaskRuntimeSourceFact(
            event_id=event_id,
            event_seq=_positive_int(event.get("seq"), field_name="source_fact_seq"),
            fact=typed_fact,
            payload=payload,
        )

    def _to_settlement_fact(
        self,
        source: ValidatedTaskRuntimeSourceFact,
    ) -> TaskRuntimeSettlementFact:
        if not source.fact.terminal:
            raise SourceFactValidationError(
                "non-terminal TaskRuntime fact cannot enter Factory settlement",
                code="non_terminal_factory_settlement_fact",
            )
        factory_run_id = str(source.payload.get("factory_run_id") or "").strip()
        if not factory_run_id:
            raise SourceFactValidationError(
                "source fact factory_run_id is missing",
                code="invalid_factory_run_id",
            )
        return TaskRuntimeSettlementFact(
            event_id=source.event_id,
            event_seq=source.event_seq,
            event_type=source.fact.event_type,
            workspace=self._workspace,
            factory_run_id=factory_run_id,
            source_run_id=str(source.payload.get("run_id") or "").strip(),
            workspace_fencing_token=_fencing_token(source.payload),
            task_runtime_fact=source.fact,
            payload=source.payload,
        )

    @staticmethod
    def _ignored_decision(
        source: ValidatedTaskRuntimeSourceFact,
        *,
        reason_code: str,
    ) -> SettlementDecision:
        return SettlementDecision(
            source_fact_event_id=source.event_id,
            source_fact_seq=source.event_seq,
            factory_run_id="",
            workspace_fencing_token=0,
            outcome=SettlementOutcome.IGNORED,
            ack_safe=True,
            reason_code=reason_code,
        )

    def _dead_letter_invalid_source(
        self,
        *,
        event: Mapping[str, Any],
        error: SourceFactValidationError,
        journal_snapshot: SettlementJournalReplaySnapshot | None = None,
    ) -> SettlementDecision:
        event_id, event_seq = _source_event_identity(event)
        dead_letter = self._journal.append_invalid_dead_letter(
            source_event=event,
            source_fact_event_id=event_id,
            source_fact_seq=event_seq,
            reason_code=error.code,
            snapshot=journal_snapshot,
        )
        return SettlementDecision(
            source_fact_event_id=event_id or dead_letter.event_id,
            source_fact_seq=event_seq,
            factory_run_id="",
            workspace_fencing_token=0,
            outcome=SettlementOutcome.DEAD_LETTER,
            ack_safe=True,
            journal_event_id=dead_letter.event_id,
            reason_code=error.code,
        )

    def _validate_source_page(self, workspace: str, stream: str) -> None:
        if _canonical_workspace(workspace) != self._workspace:
            raise FactorySettlementConsumerError(
                "TaskRuntime source query returned a foreign workspace",
                code="task_runtime_query_workspace_mismatch",
            )
        if stream != TASK_RUNTIME_EXECUTION_STREAM:
            raise FactorySettlementConsumerError(
                "TaskRuntime source query returned a foreign stream",
                code="task_runtime_query_stream_mismatch",
            )

    def _find_source_fact(self, event_id: str) -> tuple[dict[str, Any], int]:
        target = str(event_id or "").strip()
        if not target:
            raise ValueError("event_id must be a non-empty string")
        offset = 0
        while True:
            page = self._fact_stream.query(
                QueryFactEventsV1(
                    workspace=self._workspace,
                    stream=TASK_RUNTIME_EXECUTION_STREAM,
                    limit=_SOURCE_PAGE_SIZE,
                    offset=offset,
                )
            )
            self._validate_source_page(page.workspace, page.stream)
            for index, event in enumerate(page.events):
                if str(event.get("event_id") or "").strip() == target:
                    return dict(event), offset + index
            if page.next_offset == 0:
                raise SourceFactNotFoundError(
                    f"No task_runtime.execution fact exists for {target}",
                    code="source_fact_not_found",
                )
            if page.next_offset <= offset:
                raise FactorySettlementConsumerError(
                    "task_runtime.execution returned a non-advancing cursor",
                    code="task_runtime_execution_non_advancing_cursor",
                )
            offset = page.next_offset

    def _read_source_replay_snapshot(self, *, start_offset: int = 0) -> _SourceReplaySnapshot:
        """Read task-runtime FactStream pages once and index immutable events."""

        if start_offset < 0:
            raise ValueError("start_offset must be non-negative")
        # Production FactStream exposes a tail-only head query.  If the
        # checkpoint already equals that head, avoid an offset query whose
        # legacy JSONL implementation deserializes every historical heartbeat.
        head_reader = getattr(self._fact_stream, "head", None)
        if start_offset > 0 and callable(head_reader):
            head = head_reader(
                QueryFactStreamHeadV1(
                    workspace=self._workspace,
                    stream=TASK_RUNTIME_EXECUTION_STREAM,
                )
            )
            self._validate_source_page(head.workspace, head.stream)
            current_seq = int(head.current_seq)
            if current_seq < start_offset:
                raise FactorySettlementConsumerError(
                    "task_runtime.execution is shorter than the validated checkpoint",
                    code="task_runtime_execution_checkpoint_beyond_head",
                )
            if current_seq == start_offset:
                return _SourceReplaySnapshot(
                    events=(),
                    events_by_offset=MappingProxyType({}),
                    events_by_id=MappingProxyType({}),
                )
        events: list[Mapping[str, Any]] = []
        events_by_offset: dict[int, Mapping[str, Any]] = {}
        events_by_id: dict[str, tuple[Mapping[str, Any], int]] = {}
        offset = start_offset
        while True:
            page = self._fact_stream.query(
                QueryFactEventsV1(
                    workspace=self._workspace,
                    stream=TASK_RUNTIME_EXECUTION_STREAM,
                    limit=_SOURCE_PAGE_SIZE,
                    offset=offset,
                )
            )
            self._validate_source_page(page.workspace, page.stream)
            if page.total < start_offset:
                raise FactorySettlementConsumerError(
                    "task_runtime.execution is shorter than the validated checkpoint",
                    code="task_runtime_execution_checkpoint_beyond_head",
                )
            for index, raw_event in enumerate(page.events):
                event = MappingProxyType(dict(raw_event))
                source_offset = offset + index
                event_id = str(event.get("event_id") or "").strip()
                if event_id and event_id in events_by_id:
                    raise FactorySettlementConsumerError(
                        "task_runtime.execution contains duplicate event identities",
                        code="task_runtime_execution_duplicate_event_id",
                    )
                events.append(event)
                events_by_offset[source_offset + 1] = event
                if event_id:
                    events_by_id[event_id] = (event, source_offset)
            if page.next_offset == 0:
                return _SourceReplaySnapshot(
                    events=tuple(events),
                    events_by_offset=MappingProxyType(events_by_offset),
                    events_by_id=MappingProxyType(events_by_id),
                )
            if page.next_offset <= offset:
                raise FactorySettlementConsumerError(
                    "task_runtime.execution returned a non-advancing cursor",
                    code="task_runtime_execution_non_advancing_cursor",
                )
            offset = page.next_offset

    def _checkpoint(
        self,
        decision: SettlementDecision,
        *,
        next_source_offset: int,
        source_event: Mapping[str, Any] | None = None,
        journal_snapshot: SettlementJournalReplaySnapshot | None = None,
    ) -> None:
        settlement_key = (
            SettlementIdentity(
                workspace=self._workspace,
                source_fact_event_id=decision.source_fact_event_id,
                factory_run_id=decision.factory_run_id,
                workspace_fencing_token=decision.workspace_fencing_token,
            ).digest
            if decision.factory_run_id and decision.workspace_fencing_token > 0
            else f"{decision.outcome.value}:{decision.journal_event_id or decision.source_fact_event_id}"
        )
        self._journal.append_checkpoint(
            source_stream=TASK_RUNTIME_EXECUTION_STREAM,
            source_fact_event_id=decision.source_fact_event_id,
            source_fact_seq=decision.source_fact_seq,
            next_source_offset=next_source_offset,
            settlement_key=settlement_key,
            snapshot=journal_snapshot,
            source_event=source_event,
        )

    def _append_dead_letter_decision(
        self,
        source: TaskRuntimeSettlementFact,
        *,
        identity: SettlementIdentity,
        source_fact_seq: int,
        reason_code: str,
        barrier_hash: str,
        evidence_refs: Sequence[str],
        journal_snapshot: SettlementJournalReplaySnapshot | None,
    ) -> SettlementDecision:
        """Append one terminal dead letter or adopt its durable race winner.

        Barrier audit evidence is observational and may grow while two wake
        consumers race.  FactStream must continue rejecting a same-key,
        different-payload append.  At this owner boundary, however, the exact
        same settlement identity + terminal status + reason means the first
        durable record already owns the decision.  Reload it and acknowledge
        the source fact instead of redelivering forever.
        """

        normalized_reason = str(reason_code or "invalid_settlement_fact").strip()
        try:
            dead_letter = self._journal.append_dead_letter(
                identity,
                source_fact_seq=source_fact_seq,
                reason_code=normalized_reason,
                barrier_hash=barrier_hash,
                evidence_refs=evidence_refs,
                snapshot=journal_snapshot,
            )
            event_id = dead_letter.event_id
        except FactStreamError as exc:
            if exc.code != "idempotency_conflict":
                raise
            if journal_snapshot is not None:
                self._journal.refresh_replay_snapshot(journal_snapshot)
            existing = self._journal.state_for(identity, snapshot=journal_snapshot)
            if (
                existing is None
                or existing.status is not SettlementJournalStatus.DEAD_LETTER
                or str(existing.payload.get("reason_code") or "").strip() != normalized_reason
            ):
                raise
            event_id = existing.event_id
        return self._terminal_decision(
            source,
            outcome=SettlementOutcome.DEAD_LETTER,
            journal_event_id=event_id,
            reason_code=normalized_reason,
        )

    @staticmethod
    def _terminal_decision(
        source: TaskRuntimeSettlementFact,
        *,
        outcome: SettlementOutcome,
        journal_event_id: str,
        reason_code: str,
    ) -> SettlementDecision:
        return SettlementDecision(
            source_fact_event_id=source.event_id,
            source_fact_seq=source.event_seq,
            factory_run_id=source.factory_run_id,
            workspace_fencing_token=source.workspace_fencing_token,
            outcome=outcome,
            ack_safe=True,
            journal_event_id=journal_event_id,
            reason_code=reason_code,
        )


__all__ = [
    "TASK_RUNTIME_EXECUTION_SOURCE",
    "TASK_RUNTIME_EXECUTION_STREAM",
    "FactoryRunSettlementPort",
    "FactorySettlementBarrierPort",
    "FactorySettlementBarrierQuery",
    "FactorySettlementBarrierSnapshot",
    "FactorySettlementConsumer",
    "FactorySettlementConsumerError",
    "FactorySettlementFencedError",
    "FactorySettlementLifecycleError",
    "FactorySettlementPermanentError",
    "FactorySettlementPortError",
    "FactorySettlementRecoveryRequiredError",
    "FactorySettlementRetryableError",
    "SettlementDecision",
    "SettlementOutcome",
    "SettlementReplayReport",
    "SourceFactNotFoundError",
    "SourceFactValidationError",
    "TaskRuntimeSettlementFact",
    "UnsupportedSourceSchemaError",
]
