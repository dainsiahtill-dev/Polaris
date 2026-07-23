from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.events.fact_stream.public.contracts import (
    AppendFactEventCommandV1,
    FactEventAppendedV1,
    FactStreamQueryResultV1,
    QueryFactEventsV1,
)
from polaris.cells.events.fact_stream.public.service import append_fact_event, query_fact_events
from polaris.cells.factory.pipeline.internal.factory_settlement_consumer import (
    TASK_RUNTIME_EXECUTION_STREAM,
    FactorySettlementBarrierQuery,
    FactorySettlementBarrierSnapshot,
    FactorySettlementConsumer,
    FactorySettlementConsumerError,
    FactorySettlementLifecycleError,
    FactorySettlementRecoveryRequiredError,
    FactorySettlementRetryableError,
    SettlementOutcome,
)
from polaris.cells.factory.pipeline.internal.factory_settlement_journal import (
    FACTORY_SETTLEMENT_SOURCE,
    FACTORY_SETTLEMENT_STREAM,
    FactorySettlementJournal,
    SettlementIdentity,
    SettlementJournalStatus,
    SettlementJournalValidationError,
    SettlementPendingPhase,
)
from polaris.cells.runtime.task_runtime.public.contracts import (
    TASK_RUNTIME_EXECUTION_FACT_SCHEMA_V1,
    TaskRuntimeExecutionFactV1,
)


class FactStreamAdapter:
    """Exercise the real FactStream while exposing the injected port shape."""

    def __init__(self) -> None:
        self.queries: list[QueryFactEventsV1] = []
        self.appends: list[AppendFactEventCommandV1] = []
        self._event_updates: dict[tuple[str, str], dict[str, object]] = {}
        self._event_removals: dict[tuple[str, str], frozenset[str]] = {}

    def query(self, query: QueryFactEventsV1, /) -> FactStreamQueryResultV1:
        self.queries.append(query)
        result = query_fact_events(query)
        events: list[dict[str, Any]] = []
        for raw_event in result.events:
            event = dict(raw_event)
            event_id = str(event.get("event_id") or "")
            key = (query.stream, event_id)
            for field_name in self._event_removals.get(key, frozenset()):
                event.pop(field_name, None)
            event.update(self._event_updates.get(key, {}))
            events.append(event)
        return FactStreamQueryResultV1(
            workspace=result.workspace,
            stream=result.stream,
            events=tuple(events),
            total=result.total,
            next_offset=result.next_offset,
        )

    def append(self, command: AppendFactEventCommandV1, /) -> FactEventAppendedV1:
        self.appends.append(command)
        return append_fact_event(command)

    def tamper_event(
        self,
        *,
        stream: str,
        event_id: str,
        updates: dict[str, object] | None = None,
        remove: frozenset[str] | None = None,
    ) -> None:
        key = (stream, event_id)
        self._event_updates[key] = dict(updates or {})
        self._event_removals[key] = remove or frozenset()


class CountingFactStreamAdapter(FactStreamAdapter):
    """Count FactStream page reads without replacing the real service path."""

    def __init__(self) -> None:
        super().__init__()
        self.query_counts: dict[str, int] = {}

    def query(self, query: QueryFactEventsV1, /) -> FactStreamQueryResultV1:
        self.query_counts[query.stream] = self.query_counts.get(query.stream, 0) + 1
        return super().query(query)

    def reset_query_counts(self) -> None:
        self.query_counts.clear()


class ConcurrentCheckpointFactStreamAdapter(FactStreamAdapter):
    """Synchronize checkpoint writers to exercise the journal CAS boundary."""

    def __init__(self) -> None:
        super().__init__()
        self._checkpoint_barrier = Barrier(2)

    def append(self, command: AppendFactEventCommandV1, /) -> FactEventAppendedV1:
        if (
            command.stream == FACTORY_SETTLEMENT_STREAM
            and command.event_type == SettlementJournalStatus.CHECKPOINT.value
        ):
            self._checkpoint_barrier.wait(timeout=5)
        return super().append(command)


@dataclass
class MutableBarrier:
    workspace: str
    fencing_token: int
    source_fact_visible: bool = True
    closed: bool = True
    release_allowed: bool = True
    evidence_refs: object | None = None

    def __post_init__(self) -> None:
        self.queries: list[FactorySettlementBarrierQuery] = []

    def query(self, query: FactorySettlementBarrierQuery, /) -> FactorySettlementBarrierSnapshot:
        self.queries.append(query)
        evidence_refs = self.evidence_refs
        if evidence_refs is None:
            evidence_refs = (f"{TASK_RUNTIME_EXECUTION_STREAM}:{query.source_fact_event_id}:{query.source_fact_seq}",)
        return FactorySettlementBarrierSnapshot(
            workspace=self.workspace,
            factory_run_id=query.factory_run_id,
            source_fact_visible=self.source_fact_visible,
            closed=self.closed,
            release_allowed=self.release_allowed,
            workspace_fencing_token=self.fencing_token,
            barrier_hash=f"barrier-{len(self.queries)}",
            blocking_reasons=() if self.closed else ("lifecycle_open",),
            evidence={
                "source_fact_event_id": query.source_fact_event_id,
                "evidence_refs": evidence_refs,
            },
        )


class RecordingFactoryRuns:
    def __init__(self) -> None:
        self.settle_calls: list[str] = []
        self.recover_calls: list[tuple[str, int, str]] = []

    async def settle_terminal_run(self, run_id: str) -> object:
        self.settle_calls.append(run_id)
        return {"run_id": run_id, "settled": True}

    async def recover_stale_workspace_owner(
        self,
        run_id: str,
        *,
        expected_fencing_token: int,
        reason: str,
    ) -> object:
        self.recover_calls.append((run_id, expected_fencing_token, reason))
        return {"run_id": run_id, "recovered": True}


class RetryableFactoryRuns(RecordingFactoryRuns):
    async def settle_terminal_run(self, run_id: str) -> object:
        self.settle_calls.append(run_id)
        raise FactorySettlementRetryableError("temporary settlement failure", code="temporary_settlement_failure")


class BlockingFactoryRuns(RecordingFactoryRuns):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def settle_terminal_run(self, run_id: str) -> object:
        self.settle_calls.append(run_id)
        self.entered.set()
        await self.release.wait()
        return {"run_id": run_id, "settled": True}


class CrashOnceFactoryRuns(RecordingFactoryRuns):
    def __init__(self) -> None:
        super().__init__()
        self.crashed = False

    async def settle_terminal_run(self, run_id: str) -> object:
        self.settle_calls.append(run_id)
        if not self.crashed:
            self.crashed = True
            raise SimulatedConsumerCrashError("process terminated after durable pending")
        return {"run_id": run_id, "settled": True}


class RecoveryFactoryRuns(RecordingFactoryRuns):
    async def settle_terminal_run(self, run_id: str) -> object:
        self.settle_calls.append(run_id)
        raise FactorySettlementRecoveryRequiredError(
            "workspace lease expired",
            code="factory_workspace_run_lease_expired",
        )


class SimulatedConsumerCrashError(RuntimeError):
    """Synthetic unhandled process failure used by crash-recovery coverage."""


def _workspace(tmp_path: Path) -> str:
    workspace = str(tmp_path.resolve())
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=workspace,
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="factory_settlement_consumer_test_bootstrap",
        )
    )
    return workspace


def _append_source_fact(
    fact_stream: FactStreamAdapter,
    workspace: str,
    *,
    factory_run_id: str | None = "factory-run-1",
    fencing_token: int = 7,
    event_type: str = "completed",
    status: str = "completed",
    execution_state: str | None = None,
    payload_workspace: str | None = None,
    payload_schema: object = TASK_RUNTIME_EXECUTION_FACT_SCHEMA_V1,
    suffix: str = "1",
) -> FactEventAppendedV1:
    typed_fact = TaskRuntimeExecutionFactV1(
        transition_id=f"transition-{suffix}",
        event_type=event_type,
        workspace=payload_workspace if payload_workspace is not None else workspace,
        task_id=f"task-{suffix}",
        status=status,
        execution_state=execution_state or status,
        occurred_at="2026-07-13T00:00:00Z",
        payload={
            "factory_run_id": factory_run_id,
            "run_id": "director-child-run",
            "workspace_lease_fencing_token": fencing_token,
        },
    )
    payload = typed_fact.to_record()
    payload.update(
        {
            "schema_version": payload_schema,
            "workspace": payload_workspace if payload_workspace is not None else workspace,
            "run_id": "director-child-run",
            "workspace_lease_fencing_token": fencing_token,
        }
    )
    if factory_run_id is None:
        payload.pop("factory_run_id", None)
    else:
        payload["factory_run_id"] = factory_run_id
    return fact_stream.append(
        AppendFactEventCommandV1(
            workspace=workspace,
            stream=TASK_RUNTIME_EXECUTION_STREAM,
            event_type=event_type,
            payload=payload,
            source="runtime.task_runtime",
            run_id="director-child-run",
            task_id=f"task-{suffix}",
            idempotency_key=f"source-{suffix}",
        )
    )


def _build_consumer(
    *,
    workspace: str,
    fact_stream: FactStreamAdapter,
    barrier: MutableBarrier,
    factory_runs: RecordingFactoryRuns,
) -> tuple[FactorySettlementConsumer, FactorySettlementJournal]:
    journal = FactorySettlementJournal(workspace=workspace, fact_stream=fact_stream)
    consumer = FactorySettlementConsumer(
        workspace=workspace,
        fact_stream=fact_stream,
        journal=journal,
        barrier=barrier,
        factory_runs=factory_runs,
    )
    return consumer, journal


def _journal_events(fact_stream: FactStreamAdapter, workspace: str) -> tuple[dict[str, Any], ...]:
    return fact_stream.query(
        QueryFactEventsV1(
            workspace=workspace,
            stream=FACTORY_SETTLEMENT_STREAM,
            limit=1000,
        )
    ).events


def _append_terminal_dead_letter(
    journal: FactorySettlementJournal,
    *,
    workspace: str,
    source_fact_event_id: str,
    source_fact_seq: int,
) -> tuple[SettlementIdentity, FactEventAppendedV1]:
    """Create the durable terminal provenance required by one checkpoint."""

    identity = SettlementIdentity(
        workspace=workspace,
        source_fact_event_id=source_fact_event_id,
        factory_run_id="factory-run-1",
        workspace_fencing_token=7,
    )
    appended = journal.append_dead_letter(
        identity,
        source_fact_seq=source_fact_seq,
        reason_code="test_terminal_decision",
        barrier_hash="barrier-test",
        evidence_refs=("test:terminal",),
    )
    return identity, appended


@pytest.mark.asyncio
async def test_start_replays_source_and_persists_applied_before_ack(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    fact_stream = FactStreamAdapter()
    source = _append_source_fact(fact_stream, workspace)
    barrier = MutableBarrier(workspace=workspace, fencing_token=7)
    factory_runs = RecordingFactoryRuns()
    consumer, journal = _build_consumer(
        workspace=workspace,
        fact_stream=fact_stream,
        barrier=barrier,
        factory_runs=factory_runs,
    )

    report = await consumer.start()

    assert report.started_now is True
    assert report.ack_safe is True
    assert [decision.outcome for decision in report.decisions] == [SettlementOutcome.APPLIED]
    assert factory_runs.settle_calls == ["factory-run-1"]
    assert barrier.queries[-1].source_fact_event_id == source.event_id
    statuses = [event["event_type"] for event in _journal_events(fact_stream, workspace)]
    assert statuses == [
        SettlementJournalStatus.PENDING.value,
        SettlementJournalStatus.APPLIED.value,
        SettlementJournalStatus.CHECKPOINT.value,
    ]
    assert all(
        query.stream in {TASK_RUNTIME_EXECUTION_STREAM, FACTORY_SETTLEMENT_STREAM} for query in fact_stream.queries
    )
    audit_records = [
        record
        for record in journal.records()
        if record.status in {SettlementJournalStatus.PENDING, SettlementJournalStatus.APPLIED}
    ]
    expected_ref = f"{TASK_RUNTIME_EXECUTION_STREAM}:{source.event_id}:1"
    assert [record.status for record in audit_records] == [
        SettlementJournalStatus.PENDING,
        SettlementJournalStatus.APPLIED,
    ]
    assert all(record.payload["barrier_hash"] == "barrier-1" for record in audit_records)
    assert all(record.payload["evidence_refs"] == [expected_ref] for record in audit_records)


@pytest.mark.asyncio
async def test_unknown_terminal_event_type_uses_typed_terminal_status(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    fact_stream = FactStreamAdapter()
    _append_source_fact(
        fact_stream,
        workspace,
        event_type="novel_terminal_verdict",
        status="completed",
    )
    barrier = MutableBarrier(workspace=workspace, fencing_token=7)
    factory_runs = RecordingFactoryRuns()
    consumer, _ = _build_consumer(
        workspace=workspace,
        fact_stream=fact_stream,
        barrier=barrier,
        factory_runs=factory_runs,
    )

    report = await consumer.start()

    assert report.ack_safe is True
    assert report.decisions[0].outcome is SettlementOutcome.APPLIED
    assert factory_runs.settle_calls == ["factory-run-1"]


@pytest.mark.asyncio
async def test_empty_replay_is_ack_safe(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    fact_stream = FactStreamAdapter()
    barrier = MutableBarrier(workspace=workspace, fencing_token=7)
    factory_runs = RecordingFactoryRuns()
    consumer, journal = _build_consumer(
        workspace=workspace,
        fact_stream=fact_stream,
        barrier=barrier,
        factory_runs=factory_runs,
    )

    started = await consumer.start()
    replayed = await consumer.replay()
    started_again = await consumer.start()

    assert started.started_now is True
    assert replayed.decisions == ()
    assert started_again.already_started is True
    assert started.ack_safe is True
    assert replayed.ack_safe is True
    assert started_again.ack_safe is True
    assert journal.records() == ()
    assert barrier.queries == []
    assert factory_runs.settle_calls == []


@pytest.mark.asyncio
async def test_replay_reads_source_and_journal_once_per_page(tmp_path: Path) -> None:
    """Regression: replay page reads grow with pages, not source facts squared."""

    async def replay_counts(size: int) -> tuple[tuple[int, int], tuple[int, int]]:
        workspace = _workspace(tmp_path / f"count-{size}")
        fact_stream = CountingFactStreamAdapter()
        for index in range(size):
            _append_source_fact(
                fact_stream,
                workspace,
                factory_run_id=None,
                suffix=f"count-{index}",
            )
        barrier = MutableBarrier(workspace=workspace, fencing_token=7)
        factory_runs = RecordingFactoryRuns()
        first, _ = _build_consumer(
            workspace=workspace,
            fact_stream=fact_stream,
            barrier=barrier,
            factory_runs=factory_runs,
        )
        initial = await first.start()
        assert len(initial.decisions) == size
        catch_up_counts = (
            fact_stream.query_counts.get(TASK_RUNTIME_EXECUTION_STREAM, 0),
            fact_stream.query_counts.get(FACTORY_SETTLEMENT_STREAM, 0),
        )

        fact_stream.reset_query_counts()
        replay, _ = _build_consumer(
            workspace=workspace,
            fact_stream=fact_stream,
            barrier=barrier,
            factory_runs=factory_runs,
        )
        report = await replay.start()

        assert report.decisions == ()
        replay_counts = (
            fact_stream.query_counts.get(TASK_RUNTIME_EXECUTION_STREAM, 0),
            fact_stream.query_counts.get(FACTORY_SETTLEMENT_STREAM, 0),
        )
        return catch_up_counts, replay_counts

    small_catch_up, small_replay = await replay_counts(3)
    large_catch_up, large_replay = await replay_counts(1001)

    assert small_catch_up == (1, 1)
    assert large_catch_up == (2, 1)
    assert small_replay == (1, 1)
    assert large_replay == (2, 2)


@pytest.mark.asyncio
async def test_non_factory_terminal_fact_is_ignored_checkpointed_and_does_not_block_factory_fact(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    fact_stream = FactStreamAdapter()
    non_factory = _append_source_fact(
        fact_stream,
        workspace,
        factory_run_id=None,
        suffix="non-factory",
    )
    factory = _append_source_fact(fact_stream, workspace, suffix="factory")
    barrier = MutableBarrier(workspace=workspace, fencing_token=7)
    factory_runs = RecordingFactoryRuns()
    consumer, journal = _build_consumer(
        workspace=workspace,
        fact_stream=fact_stream,
        barrier=barrier,
        factory_runs=factory_runs,
    )

    report = await consumer.start()

    assert report.ack_safe is True
    assert [decision.outcome for decision in report.decisions] == [
        SettlementOutcome.IGNORED,
        SettlementOutcome.APPLIED,
    ]
    assert report.decisions[0].source_fact_event_id == non_factory.event_id
    assert report.decisions[0].reason_code == "non_factory_terminal_fact"
    assert factory_runs.settle_calls == ["factory-run-1"]
    assert [query.source_fact_event_id for query in barrier.queries] == [factory.event_id]

    records = journal.records()
    assert [record.status for record in records] == [
        SettlementJournalStatus.CHECKPOINT,
        SettlementJournalStatus.PENDING,
        SettlementJournalStatus.APPLIED,
        SettlementJournalStatus.CHECKPOINT,
    ]
    assert records[0].settlement_key.startswith("ignored:")
    assert journal.latest_checkpoint_offset(source_stream=TASK_RUNTIME_EXECUTION_STREAM) == 2
    replayed = await consumer.replay()
    assert replayed.decisions == ()
    assert replayed.ack_safe is True


@pytest.mark.asyncio
async def test_duplicate_delivery_is_ack_safe_without_second_settlement(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    fact_stream = FactStreamAdapter()
    source = _append_source_fact(fact_stream, workspace)
    barrier = MutableBarrier(workspace=workspace, fencing_token=7)
    factory_runs = RecordingFactoryRuns()
    consumer, _ = _build_consumer(
        workspace=workspace,
        fact_stream=fact_stream,
        barrier=barrier,
        factory_runs=factory_runs,
    )
    await consumer.start()

    duplicate = await consumer.wake(source.event_id)

    assert duplicate.ack_safe is True
    assert duplicate.decisions[0].outcome is SettlementOutcome.DUPLICATE
    assert factory_runs.settle_calls == ["factory-run-1"]


@pytest.mark.asyncio
async def test_ledger_wake_rechecks_pending_source_without_polling(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    fact_stream = FactStreamAdapter()
    _append_source_fact(fact_stream, workspace)
    barrier = MutableBarrier(
        workspace=workspace,
        fencing_token=7,
        source_fact_visible=False,
        closed=False,
    )
    factory_runs = RecordingFactoryRuns()
    consumer, journal = _build_consumer(
        workspace=workspace,
        fact_stream=fact_stream,
        barrier=barrier,
        factory_runs=factory_runs,
    )

    pending = await consumer.start()
    identity = SettlementIdentity(workspace, "ignored", "ignored", 1)
    del identity

    assert pending.ack_safe is False
    assert pending.decisions[0].outcome is SettlementOutcome.PENDING
    assert factory_runs.settle_calls == []
    pending_records = [record for record in journal.records() if record.status is SettlementJournalStatus.PENDING]
    assert pending_records[-1].pending_phase is SettlementPendingPhase.WAITING_BARRIER
    assert pending_records[-1].payload["barrier_hash"] == "barrier-1"
    assert pending_records[-1].payload["evidence_refs"]

    barrier.source_fact_visible = True
    barrier.closed = True
    applied = await consumer.wake()

    assert applied.ack_safe is True
    assert applied.decisions[-1].outcome is SettlementOutcome.APPLIED
    assert factory_runs.settle_calls == ["factory-run-1"]
    assert len(barrier.queries) >= 2
    applied_records = [record for record in journal.records() if record.status is SettlementJournalStatus.APPLIED]
    assert applied_records[-1].payload["barrier_hash"] == "barrier-2"
    assert applied_records[-1].payload["evidence_refs"]


@pytest.mark.asyncio
async def test_repeated_open_barrier_wake_reuses_pending_journal_event(tmp_path: Path) -> None:
    """A changing barrier snapshot must not mutate one idempotency key.

    Every wake re-queries the live Run Ledger barrier, but while it remains
    open the already-durable waiting record is the stable pending fact.  A new
    barrier hash/evidence projection may not be appended under the same
    idempotency key because that creates a conflict loop instead of progress.
    """
    workspace = _workspace(tmp_path)
    fact_stream = FactStreamAdapter()
    _append_source_fact(fact_stream, workspace)
    barrier = MutableBarrier(
        workspace=workspace,
        fencing_token=7,
        source_fact_visible=False,
        closed=False,
    )
    factory_runs = RecordingFactoryRuns()
    consumer, journal = _build_consumer(
        workspace=workspace,
        fact_stream=fact_stream,
        barrier=barrier,
        factory_runs=factory_runs,
    )

    first = await consumer.start()
    second = await consumer.wake()
    third = await consumer.wake()

    assert [first.decisions[0].outcome, second.decisions[0].outcome, third.decisions[0].outcome] == [
        SettlementOutcome.PENDING,
        SettlementOutcome.PENDING,
        SettlementOutcome.PENDING,
    ]
    assert len(barrier.queries) == 3
    assert second.decisions[0].journal_event_id == first.decisions[0].journal_event_id
    assert third.decisions[0].journal_event_id == first.decisions[0].journal_event_id
    pending_records = [record for record in journal.records() if record.status is SettlementJournalStatus.PENDING]
    assert len(pending_records) == 1
    assert pending_records[0].payload["barrier_hash"] == "barrier-1"
    assert factory_runs.settle_calls == []

    barrier.source_fact_visible = True
    barrier.closed = True
    applied = await consumer.wake()

    assert applied.ack_safe is True
    assert applied.decisions[-1].outcome is SettlementOutcome.APPLIED
    assert factory_runs.settle_calls == ["factory-run-1"]


@pytest.mark.asyncio
async def test_repeated_retryable_apply_uses_claim_scoped_pending_identity(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    fact_stream = FactStreamAdapter()
    _append_source_fact(fact_stream, workspace)
    barrier = MutableBarrier(workspace=workspace, fencing_token=7)
    factory_runs = RetryableFactoryRuns()
    consumer, journal = _build_consumer(
        workspace=workspace,
        fact_stream=fact_stream,
        barrier=barrier,
        factory_runs=factory_runs,
    )

    first = await consumer.start()
    second = await consumer.wake()

    assert first.decisions[0].outcome is SettlementOutcome.RETRYABLE
    assert second.decisions[0].outcome is SettlementOutcome.RETRYABLE
    retry_records = [
        record
        for record in journal.records()
        if record.status is SettlementJournalStatus.PENDING
        and record.pending_phase is SettlementPendingPhase.WAITING_RETRY
    ]
    assert len(retry_records) == 2
    assert retry_records[0].event_id != retry_records[1].event_id
    assert retry_records[0].payload["claim_id"] != retry_records[1].payload["claim_id"]
    assert factory_runs.settle_calls == ["factory-run-1", "factory-run-1"]


@pytest.mark.asyncio
async def test_two_consumers_racing_apply_once(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    fact_stream = FactStreamAdapter()
    _append_source_fact(fact_stream, workspace)
    barrier = MutableBarrier(workspace=workspace, fencing_token=7)
    factory_runs = BlockingFactoryRuns()
    first, _ = _build_consumer(
        workspace=workspace,
        fact_stream=fact_stream,
        barrier=barrier,
        factory_runs=factory_runs,
    )
    second, _ = _build_consumer(
        workspace=workspace,
        fact_stream=fact_stream,
        barrier=barrier,
        factory_runs=factory_runs,
    )

    first_task = asyncio.create_task(first.start())
    await factory_runs.entered.wait()
    second_task = asyncio.create_task(second.start())
    factory_runs.release.set()
    first_report, second_report = await asyncio.gather(first_task, second_task)

    assert factory_runs.settle_calls == ["factory-run-1"]
    assert first_report.ack_safe is True
    assert second_report.ack_safe is True
    assert second_report.decisions[0].outcome is SettlementOutcome.DUPLICATE


@pytest.mark.asyncio
async def test_old_workspace_token_is_fenced_and_dead_lettered(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    fact_stream = FactStreamAdapter()
    _append_source_fact(fact_stream, workspace, fencing_token=7)
    barrier = MutableBarrier(workspace=workspace, fencing_token=8)
    factory_runs = RecordingFactoryRuns()
    consumer, journal = _build_consumer(
        workspace=workspace,
        fact_stream=fact_stream,
        barrier=barrier,
        factory_runs=factory_runs,
    )

    report = await consumer.start()

    assert report.ack_safe is True
    assert report.decisions[0].outcome is SettlementOutcome.DEAD_LETTER
    assert report.decisions[0].reason_code == "stale_workspace_fencing_token"
    assert factory_runs.settle_calls == []
    dead_letter = [record for record in journal.records() if record.status is SettlementJournalStatus.DEAD_LETTER][-1]
    assert dead_letter.payload["barrier_hash"] == "barrier-1"
    assert dead_letter.payload["evidence_refs"]


@pytest.mark.asyncio
async def test_missing_envelope_schema_is_dead_lettered_and_checkpointed(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    fact_stream = FactStreamAdapter()
    source = _append_source_fact(fact_stream, workspace)
    fact_stream.tamper_event(
        stream=TASK_RUNTIME_EXECUTION_STREAM,
        event_id=source.event_id,
        remove=frozenset({"schema_version"}),
    )
    barrier = MutableBarrier(workspace=workspace, fencing_token=7)
    factory_runs = RecordingFactoryRuns()
    consumer, _ = _build_consumer(
        workspace=workspace,
        fact_stream=fact_stream,
        barrier=barrier,
        factory_runs=factory_runs,
    )

    report = await consumer.start()

    assert report.ack_safe is True
    assert report.decisions[0].outcome is SettlementOutcome.DEAD_LETTER
    assert report.decisions[0].reason_code == "invalid_source_schema_version"
    assert factory_runs.settle_calls == []
    assert [event["event_type"] for event in _journal_events(fact_stream, workspace)] == [
        SettlementJournalStatus.DEAD_LETTER.value,
        SettlementJournalStatus.CHECKPOINT.value,
    ]


@pytest.mark.asyncio
async def test_future_envelope_schema_is_retryable_without_journal_or_checkpoint(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    fact_stream = FactStreamAdapter()
    source = _append_source_fact(fact_stream, workspace)
    fact_stream.tamper_event(
        stream=TASK_RUNTIME_EXECUTION_STREAM,
        event_id=source.event_id,
        updates={"schema_version": 2},
    )
    barrier = MutableBarrier(workspace=workspace, fencing_token=7)
    factory_runs = RecordingFactoryRuns()
    consumer, _ = _build_consumer(
        workspace=workspace,
        fact_stream=fact_stream,
        barrier=barrier,
        factory_runs=factory_runs,
    )

    report = await consumer.start()

    assert report.ack_safe is False
    assert report.decisions[0].outcome is SettlementOutcome.RETRYABLE
    assert report.decisions[0].reason_code == "unsupported_source_schema_version"
    assert _journal_events(fact_stream, workspace) == ()
    assert factory_runs.settle_calls == []


@pytest.mark.asyncio
async def test_invalid_schema_and_workspace_are_dead_lettered(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    fact_stream = FactStreamAdapter()
    _append_source_fact(
        fact_stream,
        workspace,
        payload_schema="task_runtime.execution/1",
        suffix="bad-schema",
    )
    _append_source_fact(
        fact_stream,
        workspace,
        payload_workspace=str(tmp_path / "foreign"),
        suffix="bad-workspace",
    )
    barrier = MutableBarrier(workspace=workspace, fencing_token=7)
    factory_runs = RecordingFactoryRuns()
    consumer, journal = _build_consumer(
        workspace=workspace,
        fact_stream=fact_stream,
        barrier=barrier,
        factory_runs=factory_runs,
    )

    report = await consumer.start()

    assert report.ack_safe is True
    assert [decision.reason_code for decision in report.decisions] == [
        "unsupported_task_runtime_schema",
        "source_workspace_mismatch",
    ]
    assert factory_runs.settle_calls == []
    statuses = [event["event_type"] for event in _journal_events(fact_stream, workspace)]
    assert statuses.count(SettlementJournalStatus.DEAD_LETTER.value) == 2
    assert statuses.count(SettlementJournalStatus.CHECKPOINT.value) == 2
    invalid_dead_letters = [
        record for record in journal.records() if record.status is SettlementJournalStatus.DEAD_LETTER
    ]
    assert all(record.payload["barrier_hash"] == "" for record in invalid_dead_letters)
    assert all(record.payload["evidence_refs"] == [] for record in invalid_dead_letters)


@pytest.mark.asyncio
async def test_malformed_barrier_evidence_refs_fail_closed_before_settlement(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    fact_stream = FactStreamAdapter()
    _append_source_fact(fact_stream, workspace)
    barrier = MutableBarrier(
        workspace=workspace,
        fencing_token=7,
        evidence_refs="not-a-reference-sequence",
    )
    factory_runs = RecordingFactoryRuns()
    consumer, journal = _build_consumer(
        workspace=workspace,
        fact_stream=fact_stream,
        barrier=barrier,
        factory_runs=factory_runs,
    )

    with pytest.raises(FactorySettlementConsumerError) as exc_info:
        await consumer.start()

    assert exc_info.value.code == "run_ledger_barrier_evidence_refs_invalid"
    assert factory_runs.settle_calls == []
    assert journal.records() == ()


@pytest.mark.asyncio
async def test_crash_after_pending_is_recovered_by_startup_replay(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    fact_stream = FactStreamAdapter()
    source = _append_source_fact(fact_stream, workspace)
    barrier = MutableBarrier(workspace=workspace, fencing_token=7)
    factory_runs = CrashOnceFactoryRuns()
    first, journal = _build_consumer(
        workspace=workspace,
        fact_stream=fact_stream,
        barrier=barrier,
        factory_runs=factory_runs,
    )

    with pytest.raises(SimulatedConsumerCrashError):
        await first.start()

    identity = SettlementIdentity(workspace, source.event_id, "factory-run-1", 7)
    pending = journal.state_for(identity)
    assert pending is not None
    assert pending.pending_phase is SettlementPendingPhase.APPLYING

    restarted, _ = _build_consumer(
        workspace=workspace,
        fact_stream=fact_stream,
        barrier=barrier,
        factory_runs=factory_runs,
    )
    recovered = await restarted.start()

    assert recovered.ack_safe is True
    assert recovered.decisions[0].outcome is SettlementOutcome.APPLIED
    assert factory_runs.settle_calls == ["factory-run-1", "factory-run-1"]


@pytest.mark.asyncio
async def test_expired_lease_uses_explicit_recovery_before_applied(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    fact_stream = FactStreamAdapter()
    _append_source_fact(fact_stream, workspace, fencing_token=11)
    barrier = MutableBarrier(workspace=workspace, fencing_token=11)
    factory_runs = RecoveryFactoryRuns()
    consumer, _ = _build_consumer(
        workspace=workspace,
        fact_stream=fact_stream,
        barrier=barrier,
        factory_runs=factory_runs,
    )

    report = await consumer.start()

    assert report.ack_safe is True
    assert report.decisions[0].outcome is SettlementOutcome.APPLIED
    assert factory_runs.recover_calls == [("factory-run-1", 11, "factory_settlement_consumer_lease_expired")]


@pytest.mark.asyncio
async def test_forged_high_checkpoint_offset_fails_closed(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    fact_stream = FactStreamAdapter()
    source = _append_source_fact(fact_stream, workspace)
    journal = FactorySettlementJournal(workspace=workspace, fact_stream=fact_stream)
    identity, terminal = _append_terminal_dead_letter(
        journal,
        workspace=workspace,
        source_fact_event_id=source.event_id,
        source_fact_seq=1,
    )
    assert terminal.appended_seq is not None
    fact_stream.append(
        AppendFactEventCommandV1(
            workspace=workspace,
            stream=FACTORY_SETTLEMENT_STREAM,
            event_type=SettlementJournalStatus.CHECKPOINT.value,
            payload={
                "schema_version": "factory.settlement.journal/1",
                "workspace": workspace,
                "source_stream": TASK_RUNTIME_EXECUTION_STREAM,
                "source_fact_event_id": source.event_id,
                "source_fact_seq": 1,
                "next_source_offset": 999,
                "settlement_key": identity.digest,
                "previous_checkpoint_event_id": "",
                "previous_checkpoint_event_seq": 0,
                "previous_next_source_offset": 0,
                "journal_expected_seq": terminal.appended_seq + 1,
                "decision_kind": "journal_terminal",
                "decision_event_id": terminal.event_id,
                "decision_event_seq": terminal.appended_seq,
                "decision_status": SettlementJournalStatus.DEAD_LETTER.value,
            },
            source=FACTORY_SETTLEMENT_SOURCE,
        )
    )
    barrier = MutableBarrier(workspace=workspace, fencing_token=7)
    factory_runs = RecordingFactoryRuns()
    consumer = FactorySettlementConsumer(
        workspace=workspace,
        fact_stream=fact_stream,
        journal=journal,
        barrier=barrier,
        factory_runs=factory_runs,
    )

    with pytest.raises(SettlementJournalValidationError) as exc_info:
        await consumer.start()

    assert exc_info.value.code == "checkpoint_offset_not_contiguous"
    assert factory_runs.settle_calls == []


def test_checkpoint_rejects_later_real_source_without_prior_checkpoint(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    fact_stream = FactStreamAdapter()
    _append_source_fact(fact_stream, workspace, suffix="first")
    later = _append_source_fact(fact_stream, workspace, suffix="later")
    journal = FactorySettlementJournal(workspace=workspace, fact_stream=fact_stream)
    identity, _ = _append_terminal_dead_letter(
        journal,
        workspace=workspace,
        source_fact_event_id=later.event_id,
        source_fact_seq=2,
    )

    with pytest.raises(SettlementJournalValidationError) as exc_info:
        journal.append_checkpoint(
            source_stream=TASK_RUNTIME_EXECUTION_STREAM,
            source_fact_event_id=later.event_id,
            source_fact_seq=2,
            next_source_offset=2,
            settlement_key=identity.digest,
        )

    assert exc_info.value.code == "checkpoint_offset_not_contiguous"
    assert journal.latest_checkpoint_offset(source_stream=TASK_RUNTIME_EXECUTION_STREAM) == 0


def test_checkpoint_rejects_backward_offset_after_durable_checkpoint(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    fact_stream = FactStreamAdapter()
    first = _append_source_fact(fact_stream, workspace, suffix="first")
    second = _append_source_fact(fact_stream, workspace, suffix="second")
    journal = FactorySettlementJournal(workspace=workspace, fact_stream=fact_stream)
    first_identity, _ = _append_terminal_dead_letter(
        journal,
        workspace=workspace,
        source_fact_event_id=first.event_id,
        source_fact_seq=1,
    )
    journal.append_checkpoint(
        source_stream=TASK_RUNTIME_EXECUTION_STREAM,
        source_fact_event_id=first.event_id,
        source_fact_seq=1,
        next_source_offset=1,
        settlement_key=first_identity.digest,
    )
    second_identity, _ = _append_terminal_dead_letter(
        journal,
        workspace=workspace,
        source_fact_event_id=second.event_id,
        source_fact_seq=2,
    )

    with pytest.raises(SettlementJournalValidationError) as exc_info:
        journal.append_checkpoint(
            source_stream=TASK_RUNTIME_EXECUTION_STREAM,
            source_fact_event_id=second.event_id,
            source_fact_seq=2,
            next_source_offset=1,
            settlement_key=second_identity.digest,
        )

    assert exc_info.value.code == "checkpoint_offset_not_contiguous"
    assert journal.latest_checkpoint_offset(source_stream=TASK_RUNTIME_EXECUTION_STREAM) == 1


def test_contiguous_checkpoint_chain_recovers_after_journal_restart(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    fact_stream = FactStreamAdapter()
    first = _append_source_fact(fact_stream, workspace, suffix="first")
    second = _append_source_fact(fact_stream, workspace, suffix="second")
    journal = FactorySettlementJournal(workspace=workspace, fact_stream=fact_stream)
    first_identity, _ = _append_terminal_dead_letter(
        journal,
        workspace=workspace,
        source_fact_event_id=first.event_id,
        source_fact_seq=1,
    )
    first_checkpoint = journal.append_checkpoint(
        source_stream=TASK_RUNTIME_EXECUTION_STREAM,
        source_fact_event_id=first.event_id,
        source_fact_seq=1,
        next_source_offset=1,
        settlement_key=first_identity.digest,
    )
    second_identity, _ = _append_terminal_dead_letter(
        journal,
        workspace=workspace,
        source_fact_event_id=second.event_id,
        source_fact_seq=2,
    )
    second_checkpoint = journal.append_checkpoint(
        source_stream=TASK_RUNTIME_EXECUTION_STREAM,
        source_fact_event_id=second.event_id,
        source_fact_seq=2,
        next_source_offset=2,
        settlement_key=second_identity.digest,
    )

    recovered = FactorySettlementJournal(workspace=workspace, fact_stream=fact_stream)

    assert recovered.latest_checkpoint_offset(source_stream=TASK_RUNTIME_EXECUTION_STREAM) == 2
    checkpoints = [record for record in recovered.records() if record.status is SettlementJournalStatus.CHECKPOINT]
    assert checkpoints[-1].payload["previous_checkpoint_event_id"] == first_checkpoint.event_id
    assert checkpoints[-1].payload["previous_checkpoint_event_seq"] == first_checkpoint.appended_seq
    assert checkpoints[-1].event_id == second_checkpoint.event_id


def test_concurrent_checkpoint_append_uses_single_cas_commit_and_is_idempotent(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    fact_stream = ConcurrentCheckpointFactStreamAdapter()
    source = _append_source_fact(fact_stream, workspace)
    first = FactorySettlementJournal(workspace=workspace, fact_stream=fact_stream)
    second = FactorySettlementJournal(workspace=workspace, fact_stream=fact_stream)
    identity, terminal = _append_terminal_dead_letter(
        first,
        workspace=workspace,
        source_fact_event_id=source.event_id,
        source_fact_seq=1,
    )

    def append_checkpoint(journal: FactorySettlementJournal) -> FactEventAppendedV1:
        return journal.append_checkpoint(
            source_stream=TASK_RUNTIME_EXECUTION_STREAM,
            source_fact_event_id=source.event_id,
            source_fact_seq=1,
            next_source_offset=1,
            settlement_key=identity.digest,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_result = executor.submit(append_checkpoint, first)
        second_result = executor.submit(append_checkpoint, second)
        appended = (first_result.result(timeout=10), second_result.result(timeout=10))

    checkpoints = [
        event
        for event in _journal_events(fact_stream, workspace)
        if event["event_type"] == SettlementJournalStatus.CHECKPOINT.value
    ]
    checkpoint_commands = [
        command for command in fact_stream.appends if command.event_type == SettlementJournalStatus.CHECKPOINT.value
    ]
    assert terminal.appended_seq is not None
    assert len(checkpoints) == 1
    assert appended[0].event_id == appended[1].event_id == checkpoints[0]["event_id"]
    assert {command.expected_seq for command in checkpoint_commands} == {terminal.appended_seq + 1}


@pytest.mark.parametrize(
    ("event_type", "payload_schema", "expected_code"),
    [
        ("future_status", "factory.settlement.journal/1", "unknown_journal_status"),
        ("pending", "factory.settlement.journal/2", "unsupported_journal_schema"),
    ],
)
@pytest.mark.asyncio
async def test_unknown_journal_schema_or_status_fails_closed(
    tmp_path: Path,
    event_type: str,
    payload_schema: str,
    expected_code: str,
) -> None:
    workspace = _workspace(tmp_path)
    fact_stream = FactStreamAdapter()
    fact_stream.append(
        AppendFactEventCommandV1(
            workspace=workspace,
            stream=FACTORY_SETTLEMENT_STREAM,
            event_type=event_type,
            payload={
                "schema_version": payload_schema,
                "workspace": workspace,
                "source_fact_event_id": "source-1",
                "source_fact_seq": 1,
                "settlement_key": "settlement-1",
            },
            source=FACTORY_SETTLEMENT_SOURCE,
        )
    )
    journal = FactorySettlementJournal(workspace=workspace, fact_stream=fact_stream)

    with pytest.raises(SettlementJournalValidationError) as exc_info:
        journal.records()

    assert exc_info.value.code == expected_code


@pytest.mark.asyncio
async def test_stop_is_idempotent_and_wake_after_stop_fails_closed(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    fact_stream = FactStreamAdapter()
    barrier = MutableBarrier(workspace=workspace, fencing_token=7)
    factory_runs = RecordingFactoryRuns()
    consumer, _ = _build_consumer(
        workspace=workspace,
        fact_stream=fact_stream,
        barrier=barrier,
        factory_runs=factory_runs,
    )
    await consumer.start()

    await consumer.stop()
    await consumer.stop()

    with pytest.raises(FactorySettlementLifecycleError):
        await consumer.wake()
