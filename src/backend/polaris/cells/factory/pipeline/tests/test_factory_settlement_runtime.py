from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast

import pytest
from nats.errors import Error as NatsError
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy, ReplayPolicy
from polaris.cells.control_plane.run_ledger.public import (
    FACTORY_SETTLEMENT_BARRIER_SCHEMA_V1,
    AppendRunLedgerEventCommandV1,
    FactorySettlementBarrierResultV1,
    RunLedgerAppendResultV1,
)
from polaris.cells.events.fact_stream.public import (
    AppendFactEventCommandV1,
    BootstrapFactStreamWorkspaceCommandV1,
    QueryFactEventsV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.factory.pipeline.internal import factory_settlement_runtime as runtime_module
from polaris.cells.factory.pipeline.internal.factory_settlement_consumer import (
    FactorySettlementBarrierQuery,
    FactorySettlementConsumer,
    FactorySettlementFencedError,
    FactorySettlementPermanentError,
    FactorySettlementRecoveryRequiredError,
    FactorySettlementRetryableError,
    SettlementDecision,
    SettlementOutcome,
    SettlementReplayReport,
)
from polaris.cells.factory.pipeline.internal.factory_settlement_runtime import (
    DurableJetStreamSettlementWakeBridge,
    FactoryRunServiceSettlementAdapter,
    FactorySettlementRuntime,
    FactorySettlementRuntimeError,
    FactorySettlementRuntimeRegistry,
    FactorySettlementWakeBridgeError,
    FactStreamPublicServiceAdapter,
    RunLedgerFactorySettlementBarrierAdapter,
    SettlementWakeClient,
    create_factory_settlement_runtime,
)
from polaris.cells.factory.pipeline.public import (
    FactoryWorkspaceRunLeaseConflictError,
    FactoryWorkspaceRunLeaseStateV1,
    FactoryWorkspaceRunLeaseStorageError,
    FactoryWorkspaceRunLeaseV1,
)
from polaris.cells.runtime.task_runtime.public import (
    DirectedEffectRecoverySweepItemV1,
    DirectedEffectRecoverySweepResultV1,
    ReconcileAmbiguousDirectedEffectsCommandV1,
)
from polaris.infrastructure.messaging.nats.nats_types import JetStreamConstants


def _lease(workspace: Path, *, token: int = 7, run_id: str = "factory-1") -> FactoryWorkspaceRunLeaseV1:
    timestamp = "2026-07-13T00:00:00+00:00"
    return FactoryWorkspaceRunLeaseV1(
        workspace=str(workspace.resolve()),
        run_id=run_id,
        state=FactoryWorkspaceRunLeaseStateV1.ACTIVE,
        version=1,
        fencing_token=token,
        acquired_at=timestamp,
        updated_at=timestamp,
        expires_at="2099-07-13T00:00:00+00:00",
    )


def _barrier_query(workspace: Path, *, token: int = 7) -> FactorySettlementBarrierQuery:
    return FactorySettlementBarrierQuery(
        workspace=str(workspace.resolve()),
        factory_run_id="factory-1",
        source_fact_event_id="fact-1",
        source_fact_seq=11,
        source_run_id="director-1",
        workspace_fencing_token=token,
    )


def _barrier_result(
    workspace: Path,
    *,
    schema_version: str = FACTORY_SETTLEMENT_BARRIER_SCHEMA_V1,
    closed: bool = True,
    release_allowed: bool = True,
) -> FactorySettlementBarrierResultV1:
    return FactorySettlementBarrierResultV1(
        schema_version=schema_version,
        workspace=str(workspace.resolve()),
        factory_run_id="factory-1",
        closed=closed,
        passed=False,
        release_allowed=release_allowed,
        barrier_hash="barrier-hash",
        missing_required_modalities=(),
        failed_required_modalities=("command",),
        task_lifecycle_count=2,
        tool_lifecycle_count=1,
        active_lifecycle_count=0,
        open_lifecycle_count=0,
        failed_lifecycle_count=1,
        expected_effect_count=1,
        effect_receipt_count=1,
        open_effect_count=0,
        evidence_refs=("receipt-1",),
        blocking_reasons=("lifecycle_failed",),
        consumed_run_ids=("director-1",),
    )


class RecordingAuthoritySink:
    def __init__(self, *, current_fencing_token: int = 7) -> None:
        self.queries: list[FactorySettlementBarrierQuery] = []
        self.current_fencing_token = current_fencing_token

    def bind(self, query: FactorySettlementBarrierQuery) -> int:
        self.queries.append(query)
        return self.current_fencing_token


class RecordingFactoryService:
    def __init__(self, workspace: Path, *, error: Exception | None = None) -> None:
        self.workspace = workspace.resolve()
        self.error = error
        self.settle_calls: list[str] = []
        self.settle_tokens: list[int | None] = []
        self.recover_calls: list[tuple[str, int, str]] = []

    async def settle_terminal_run(
        self,
        run_id: str,
        *,
        expected_fencing_token: int | None = None,
    ) -> object:
        self.settle_calls.append(run_id)
        self.settle_tokens.append(expected_fencing_token)
        if self.error is not None:
            raise self.error
        return {"run_id": run_id}

    async def recover_stale_workspace_owner(
        self,
        run_id: str,
        *,
        expected_fencing_token: int,
        reason: str,
    ) -> object:
        self.recover_calls.append((run_id, expected_fencing_token, reason))
        if self.error is not None:
            raise self.error
        return {"run_id": run_id, "fencing_token": expected_fencing_token}


class AckTrackingMessage:
    def __init__(self, *, data: bytes = b"") -> None:
        self.data = data
        self.ack_calls = 0
        self.acked = asyncio.Event()

    async def ack(self) -> None:
        self.ack_calls += 1
        self.acked.set()


class FailingAckMessage(AckTrackingMessage):
    async def ack(self) -> None:
        self.ack_calls += 1
        raise NatsError("injected ACK transport failure")


class RecordingJetStreamSubscription:
    def __init__(self) -> None:
        self._messages: asyncio.Queue[AckTrackingMessage] = asyncio.Queue()
        self.consumer_config: ConsumerConfig | None = None
        self.consumer_info_calls = 0
        self.next_message_calls = 0
        self.unsubscribe_calls = 0

    @property
    def pending_msgs(self) -> int:
        return self._messages.qsize()

    async def consumer_info(self) -> RecordingConsumerInfo:
        self.consumer_info_calls += 1
        if self.consumer_config is None:
            raise AssertionError("consumer_info must follow durable subscription binding")
        return RecordingConsumerInfo(self.consumer_config)

    async def next_msg(self, timeout: float | None = None) -> AckTrackingMessage:
        if timeout is not None:
            raise AssertionError("durable wake subscription must not use a finite idle timeout")
        self.next_message_calls += 1
        return await self._messages.get()

    async def unsubscribe(self) -> None:
        self.unsubscribe_calls += 1

    def deliver(self, message: AckTrackingMessage) -> None:
        self._messages.put_nowait(message)


def _runtime_wake_payload(
    kind: str,
    *,
    payload: dict[str, Any] | None = None,
) -> bytes:
    return json.dumps(
        {
            "schema_version": "runtime.v2",
            "kind": kind,
            "event_id": "runtime-event-1",
            "payload": payload or {},
        }
    ).encode("utf-8")


class RecordingConsumerInfo:
    def __init__(self, config: ConsumerConfig) -> None:
        self.config = config


class RecordingJetStreamContext:
    def __init__(
        self,
        subscription: RecordingJetStreamSubscription,
        *,
        ready_immediately: bool = True,
        existing_durable_name: str | None = None,
        existing_consumer_config: ConsumerConfig | None = None,
    ) -> None:
        self._subscription = subscription
        self._ready = asyncio.Event()
        if ready_immediately:
            self._ready.set()
        self.subscribe_started = asyncio.Event()
        self.subscribe_completed = asyncio.Event()
        self.subject = ""
        self.config: Any = None
        self.manual_ack: bool | None = None
        self.durable_name: str | None = None
        self.subscribe_calls = 0
        self.create_calls = 0
        self.bind_calls = 0
        self._consumer_configs: dict[str, ConsumerConfig] = {}
        if existing_durable_name is not None and existing_consumer_config is not None:
            self._consumer_configs[existing_durable_name] = existing_consumer_config

    async def subscribe(
        self,
        subject: str,
        *,
        durable: str,
        config: ConsumerConfig,
        manual_ack: bool,
    ) -> RecordingJetStreamSubscription:
        self.subject = subject
        self.config = config
        self.manual_ack = manual_ack
        self.durable_name = durable
        self.subscribe_calls += 1
        existing = self._consumer_configs.get(durable)
        if existing is None:
            self._consumer_configs[durable] = config
            existing = config
            self.create_calls += 1
        else:
            self.bind_calls += 1
        self._subscription.consumer_config = existing
        self.subscribe_started.set()
        await self._ready.wait()
        self.subscribe_completed.set()
        return self._subscription

    def mark_ready(self) -> None:
        self._ready.set()


class RecordingWakeClient:
    def __init__(self, jetstream: RecordingJetStreamContext) -> None:
        self._jetstream = jetstream

    @property
    def jetstream(self) -> RecordingJetStreamContext:
        return self._jetstream


def _wake_consumer_config(durable_name: str, subject: str) -> ConsumerConfig:
    return ConsumerConfig(
        durable_name=durable_name,
        deliver_policy=DeliverPolicy.NEW,
        ack_policy=AckPolicy.EXPLICIT,
        ack_wait=JetStreamConstants.CONSUMER_ACK_WAIT_SECONDS,
        max_deliver=-1,
        max_ack_pending=JetStreamConstants.CONSUMER_MAX_ACK_PENDING,
        filter_subject=subject,
        replay_policy=ReplayPolicy.INSTANT,
        flow_control=False,
        headers_only=False,
    )


async def _wait_for_bridge_report(
    bridge: DurableJetStreamSettlementWakeBridge,
) -> SettlementReplayReport:
    async with asyncio.timeout(1.0):
        while bridge.last_report is None:
            await asyncio.sleep(0)
    report = bridge.last_report
    if report is None:
        raise AssertionError("wake bridge did not publish a replay report")
    return report


class RecordingConsumer:
    def __init__(self, workspace: Path) -> None:
        self.workspace = str(workspace.resolve())
        self.start_calls = 0
        self.wake_calls = 0
        self.stop_calls = 0

    async def start(self) -> SettlementReplayReport:
        self.start_calls += 1
        return SettlementReplayReport(decisions=(), started_now=True)

    async def wake(self, source_fact_event_id: str | None = None) -> SettlementReplayReport:
        del source_fact_event_id
        self.wake_calls += 1
        return SettlementReplayReport(decisions=())

    async def stop(self) -> None:
        self.stop_calls += 1


class RecordingWakeBridge:
    def __init__(self) -> None:
        self.start_calls = 0
        self.stop_calls = 0
        self.is_healthy = True
        self.failure: FactorySettlementWakeBridgeError | None = None

    async def start(self) -> bool:
        self.start_calls += 1
        return True

    async def stop(self) -> bool:
        self.stop_calls += 1
        return True


def test_fact_stream_adapter_uses_real_public_append_and_query(tmp_path: Path) -> None:
    workspace = str(tmp_path.resolve())
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=workspace,
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="factory_settlement_adapter_test_setup",
        )
    )
    adapter = FactStreamPublicServiceAdapter()
    appended = adapter.append(
        AppendFactEventCommandV1(
            workspace=workspace,
            stream="factory.settlement",
            event_type="wake",
            payload={"workspace": workspace},
            source="factory.pipeline.tests",
            idempotency_key="runtime-test-1",
        )
    )

    result = adapter.query(
        QueryFactEventsV1(
            workspace=workspace,
            stream="factory.settlement",
        )
    )

    assert result.events[0]["event_id"] == appended.event_id
    assert result.events[0]["payload"]["workspace"] == workspace


@pytest.mark.asyncio
async def test_create_runtime_delegates_bootstrap_before_adapter_construction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    commands: list[BootstrapFactStreamWorkspaceCommandV1] = []

    def record_bootstrap(command: BootstrapFactStreamWorkspaceCommandV1) -> object:
        commands.append(command)
        return object()

    monkeypatch.setattr(runtime_module, "bootstrap_fact_stream_workspace", record_bootstrap)

    runtime = await create_factory_settlement_runtime(
        str(tmp_path),
        enable_wake_bridge=False,
        wake_bridge_required=False,
    )
    try:
        assert len(commands) == 1
        assert commands[0].workspace == str(tmp_path.resolve())
        assert commands[0].streams == fact_stream_bootstrap_streams()
        assert commands[0].maintenance_reason == "factory_settlement_runtime_startup"
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_create_runtime_reconciles_ambiguous_effects_and_projects_run_ledger(
    tmp_path: Path,
) -> None:
    recovery_commands: list[ReconcileAmbiguousDirectedEffectsCommandV1] = []
    ledger_commands: list[AppendRunLedgerEventCommandV1] = []
    item = DirectedEffectRecoverySweepItemV1(
        factory_run_id="factory-1",
        session_id="session-1",
        task_id=17,
        operation_id="effect-1",
        code="recovery_pending",
        state="RECOVERY_PENDING",
        version=4,
        event_id="event-1",
        evidence_ref="task-runtime://event-1",
        evidence_hash="a" * 64,
    )

    def recover(
        command: ReconcileAmbiguousDirectedEffectsCommandV1,
    ) -> DirectedEffectRecoverySweepResultV1:
        recovery_commands.append(command)
        return DirectedEffectRecoverySweepResultV1(
            ok=True,
            code="reconciled",
            workspace=command.workspace,
            scanned_session_count=1,
            items=(item,),
        )

    def append(command: AppendRunLedgerEventCommandV1) -> RunLedgerAppendResultV1:
        ledger_commands.append(command)
        return RunLedgerAppendResultV1(receipt={"ok": True})

    runtime = await create_factory_settlement_runtime(
        str(tmp_path),
        enable_wake_bridge=False,
        wake_bridge_required=False,
        directed_effect_recovery_handler=recover,
        run_ledger_append_handler=append,
    )
    try:
        assert len(recovery_commands) == 1
        recovery_command = recovery_commands[0]
        assert recovery_command.workspace == str(tmp_path.resolve())
        assert recovery_command.reason == "factory settlement startup recovery"
        assert len(ledger_commands) == 1
        event = ledger_commands[0].event
        assert ledger_commands[0].run_id == "factory-1"
        assert event["event_type"] == "directed_effect_recovery"
        assert event["physical_evidence"] == {"effect_recovery": item.to_record()}
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_create_runtime_fails_closed_when_recovery_fact_has_no_factory_authority(
    tmp_path: Path,
) -> None:
    orphan = DirectedEffectRecoverySweepItemV1(
        factory_run_id="",
        session_id="session-orphan",
        task_id=1,
        operation_id="operation-orphan",
        code="dead_lettered",
        state="DEAD_LETTER",
        version=4,
        event_id="event-orphan",
        evidence_ref="task-runtime://event-orphan",
        evidence_hash="a" * 64,
    )

    def recover(
        command: ReconcileAmbiguousDirectedEffectsCommandV1,
    ) -> DirectedEffectRecoverySweepResultV1:
        return DirectedEffectRecoverySweepResultV1(
            ok=True,
            code="reconciled",
            workspace=command.workspace,
            scanned_session_count=1,
            items=(orphan,),
        )

    with pytest.raises(FactorySettlementRuntimeError) as raised:
        await create_factory_settlement_runtime(
            str(tmp_path),
            enable_wake_bridge=False,
            wake_bridge_required=False,
            directed_effect_recovery_handler=recover,
        )

    assert raised.value.code == "factory_settlement_directed_effect_recovery_projection_authority_missing"


@pytest.mark.asyncio
async def test_create_runtime_fails_closed_when_directed_effect_recovery_fails(
    tmp_path: Path,
) -> None:
    def recover(
        command: ReconcileAmbiguousDirectedEffectsCommandV1,
    ) -> DirectedEffectRecoverySweepResultV1:
        return DirectedEffectRecoverySweepResultV1(
            ok=False,
            code="partial_failure",
            workspace=command.workspace,
            scanned_session_count=1,
            failures=({"code": "operation_event_stream_invalid"},),
        )

    with pytest.raises(FactorySettlementRuntimeError) as raised:
        await create_factory_settlement_runtime(
            str(tmp_path),
            enable_wake_bridge=False,
            wake_bridge_required=False,
            directed_effect_recovery_handler=recover,
        )

    assert raised.value.code == "factory_settlement_directed_effect_recovery_failed"


@pytest.mark.asyncio
async def test_create_runtime_retries_durable_recovery_projection_after_append_failure(
    tmp_path: Path,
) -> None:
    item = DirectedEffectRecoverySweepItemV1(
        factory_run_id="factory-1",
        session_id="session-1",
        task_id=17,
        operation_id="effect-1",
        code="dead_lettered",
        state="DEAD_LETTER",
        version=4,
        event_id="event-dead-letter-1",
        evidence_ref="task-runtime://event-dead-letter-1",
        evidence_hash="b" * 64,
    )
    recover_calls = 0
    append_attempts: list[AppendRunLedgerEventCommandV1] = []

    def recover(
        command: ReconcileAmbiguousDirectedEffectsCommandV1,
    ) -> DirectedEffectRecoverySweepResultV1:
        nonlocal recover_calls
        recover_calls += 1
        return DirectedEffectRecoverySweepResultV1(
            ok=True,
            code="reconciled",
            workspace=command.workspace,
            scanned_session_count=1,
            items=(item,),
        )

    def fail_append(command: AppendRunLedgerEventCommandV1) -> RunLedgerAppendResultV1:
        append_attempts.append(command)
        raise RuntimeError("simulated append failure")

    with pytest.raises(FactorySettlementRuntimeError) as raised:
        await create_factory_settlement_runtime(
            str(tmp_path),
            enable_wake_bridge=False,
            wake_bridge_required=False,
            directed_effect_recovery_handler=recover,
            run_ledger_append_handler=fail_append,
        )
    assert raised.value.code == "factory_settlement_directed_effect_recovery_projection_failed"

    def succeed_append(command: AppendRunLedgerEventCommandV1) -> RunLedgerAppendResultV1:
        append_attempts.append(command)
        return RunLedgerAppendResultV1(receipt={"ok": True})

    runtime = await create_factory_settlement_runtime(
        str(tmp_path),
        enable_wake_bridge=False,
        wake_bridge_required=False,
        directed_effect_recovery_handler=recover,
        run_ledger_append_handler=succeed_append,
    )
    try:
        assert recover_calls == 2
        assert len(append_attempts) == 2
        assert append_attempts[0].event == append_attempts[1].event
        assert append_attempts[1].event["physical_evidence"] == {"effect_recovery": item.to_record()}
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_real_empty_workspace_runtime_replays_on_startup(tmp_path: Path) -> None:
    runtime = await create_factory_settlement_runtime(
        str(tmp_path),
        enable_wake_bridge=False,
        wake_bridge_required=False,
    )

    report = await runtime.start()
    try:
        assert report.started_now is True
        assert report.ack_safe is True
        assert report.decisions == ()
    finally:
        await runtime.stop()


def test_barrier_adapter_calls_only_injected_run_ledger_query(tmp_path: Path) -> None:
    sink = RecordingAuthoritySink()
    calls: list[tuple[str, str]] = []

    def query_handler(workspace: str | Path, factory_run_id: str) -> FactorySettlementBarrierResultV1:
        calls.append((str(workspace), factory_run_id))
        return _barrier_result(tmp_path)

    adapter = RunLedgerFactorySettlementBarrierAdapter(
        authority_sink=sink,
        query_handler=query_handler,
    )
    query = _barrier_query(tmp_path)

    snapshot = adapter.query(query)

    assert calls == [(query.workspace, "factory-1")]
    assert sink.queries == [query]
    assert snapshot.source_fact_visible is True
    assert snapshot.closed is True
    assert snapshot.release_allowed is True
    assert snapshot.workspace_fencing_token == 7
    assert snapshot.evidence["failed_required_modalities"] == ("command",)


def test_barrier_adapter_uses_current_lease_token_instead_of_source_token(
    tmp_path: Path,
) -> None:
    sink = RecordingAuthoritySink(current_fencing_token=13)

    def query_handler(
        workspace: str | Path,
        factory_run_id: str,
    ) -> FactorySettlementBarrierResultV1:
        del workspace, factory_run_id
        return _barrier_result(tmp_path)

    adapter = RunLedgerFactorySettlementBarrierAdapter(
        authority_sink=sink,
        query_handler=query_handler,
    )
    query = _barrier_query(tmp_path, token=7)

    snapshot = adapter.query(query)

    assert query.workspace_fencing_token == 7
    assert snapshot.workspace_fencing_token == 13
    assert sink.queries == [query]


def test_barrier_adapter_rejects_unknown_schema(tmp_path: Path) -> None:
    sink = RecordingAuthoritySink()

    def query_handler(
        workspace: str | Path,
        factory_run_id: str,
    ) -> FactorySettlementBarrierResultV1:
        del workspace, factory_run_id
        return _barrier_result(
            tmp_path,
            schema_version="run_ledger.factory_settlement_barrier.v2",
        )

    adapter = RunLedgerFactorySettlementBarrierAdapter(
        authority_sink=sink,
        query_handler=query_handler,
    )

    with pytest.raises(FactorySettlementRetryableError) as raised:
        adapter.query(_barrier_query(tmp_path))

    assert raised.value.code == "unsupported_factory_settlement_barrier_schema"


@pytest.mark.parametrize(
    ("closed", "release_allowed"),
    ((False, True), (True, False)),
)
def test_barrier_adapter_rejects_release_invariant_mismatch(
    tmp_path: Path,
    closed: bool,
    release_allowed: bool,
) -> None:
    sink = RecordingAuthoritySink()

    def query_handler(
        workspace: str | Path,
        factory_run_id: str,
    ) -> FactorySettlementBarrierResultV1:
        del workspace, factory_run_id
        return _barrier_result(
            tmp_path,
            closed=closed,
            release_allowed=release_allowed,
        )

    adapter = RunLedgerFactorySettlementBarrierAdapter(
        authority_sink=sink,
        query_handler=query_handler,
    )

    with pytest.raises(FactorySettlementRetryableError) as raised:
        adapter.query(_barrier_query(tmp_path))

    assert raised.value.code == "invalid_factory_settlement_barrier"


@pytest.mark.asyncio
async def test_factory_service_expired_lease_becomes_recovery_required(tmp_path: Path) -> None:
    lease = _lease(tmp_path)
    error = FactoryWorkspaceRunLeaseConflictError(
        "lease expired",
        code="factory_workspace_run_lease_expired",
        requested_run_id="factory-1",
        current_lease=lease,
    )
    service = RecordingFactoryService(tmp_path, error=error)
    adapter = FactoryRunServiceSettlementAdapter(
        workspace=str(tmp_path),
        service=service,
        lease_reader=lambda: lease,
    )
    adapter.bind(_barrier_query(tmp_path))

    with pytest.raises(FactorySettlementRecoveryRequiredError) as raised:
        await adapter.settle_terminal_run("factory-1")

    assert raised.value.code == "factory_workspace_run_lease_expired"
    assert service.settle_tokens == [7]


@pytest.mark.asyncio
async def test_factory_service_storage_failure_is_retryable(tmp_path: Path) -> None:
    lease = _lease(tmp_path)
    service = RecordingFactoryService(
        tmp_path,
        error=FactoryWorkspaceRunLeaseStorageError("lease store unavailable"),
    )
    adapter = FactoryRunServiceSettlementAdapter(
        workspace=str(tmp_path),
        service=service,
        lease_reader=lambda: lease,
    )
    adapter.bind(_barrier_query(tmp_path))

    with pytest.raises(FactorySettlementRetryableError) as raised:
        await adapter.settle_terminal_run("factory-1")

    assert raised.value.code == "factory_workspace_run_lease_storage_error"


@pytest.mark.asyncio
async def test_factory_service_invalid_run_is_permanent(tmp_path: Path) -> None:
    lease = _lease(tmp_path)
    service = RecordingFactoryService(tmp_path, error=ValueError("Run factory-1 not found"))
    adapter = FactoryRunServiceSettlementAdapter(
        workspace=str(tmp_path),
        service=service,
        lease_reader=lambda: lease,
    )
    adapter.bind(_barrier_query(tmp_path))

    with pytest.raises(FactorySettlementPermanentError) as raised:
        await adapter.settle_terminal_run("factory-1")

    assert raised.value.code == "factory_settlement_invalid_service_request"


@pytest.mark.asyncio
async def test_factory_service_rejects_stale_source_token_before_mutation(tmp_path: Path) -> None:
    service = RecordingFactoryService(tmp_path)
    adapter = FactoryRunServiceSettlementAdapter(
        workspace=str(tmp_path),
        service=service,
        lease_reader=lambda: _lease(tmp_path, token=8),
    )
    adapter.bind(_barrier_query(tmp_path, token=7))

    with pytest.raises(FactorySettlementFencedError) as raised:
        await adapter.settle_terminal_run("factory-1")

    assert raised.value.code == "factory_workspace_run_fenced"
    assert service.settle_calls == []


@pytest.mark.asyncio
async def test_factory_service_exposes_current_token_for_stale_run_replay_without_mutation(tmp_path: Path) -> None:
    """A prior-run fact must be fenced by token comparison, not crash boot-time replay."""
    service = RecordingFactoryService(tmp_path)
    adapter = FactoryRunServiceSettlementAdapter(
        workspace=str(tmp_path),
        service=service,
        lease_reader=lambda: _lease(tmp_path, token=8, run_id="factory-2"),
    )

    current_token = adapter.bind(_barrier_query(tmp_path, token=7))

    assert current_token == 8
    with pytest.raises(FactorySettlementFencedError) as raised:
        await adapter.settle_terminal_run("factory-1")
    assert raised.value.code == "factory_workspace_run_fenced"
    assert service.settle_calls == []


@pytest.mark.asyncio
async def test_durable_wake_bridge_start_waits_for_subscription_ready() -> None:
    subscription = RecordingJetStreamSubscription()
    jetstream = RecordingJetStreamContext(subscription, ready_immediately=False)

    async def wake() -> SettlementReplayReport:
        return SettlementReplayReport(decisions=())

    bridge = DurableJetStreamSettlementWakeBridge(
        client=RecordingWakeClient(jetstream),
        subject="hp.runtime.workspace.>",
        durable_name="factory-settlement-workspace",
        wake=wake,
    )

    start_task = asyncio.create_task(bridge.start())
    await asyncio.wait_for(jetstream.subscribe_started.wait(), timeout=1.0)
    assert start_task.done() is False

    jetstream.mark_ready()
    assert await asyncio.wait_for(start_task, timeout=1.0) is True
    assert jetstream.subscribe_completed.is_set() is True
    assert jetstream.subject == "hp.runtime.workspace.>"
    assert jetstream.durable_name == "factory-settlement-workspace"
    assert jetstream.config.durable_name == "factory-settlement-workspace"
    assert jetstream.config.ack_wait == JetStreamConstants.CONSUMER_ACK_WAIT_SECONDS
    assert jetstream.config.as_dict()["ack_wait"] == (JetStreamConstants.CONSUMER_ACK_WAIT_SECONDS * 1_000_000_000)
    assert jetstream.manual_ack is True
    assert subscription.consumer_info_calls == 1

    assert await bridge.stop() is True
    assert subscription.unsubscribe_calls == 1


def test_consumer_config_converts_ack_wait_seconds_to_jetstream_nanoseconds() -> None:
    config = ConsumerConfig(
        durable_name="factory-settlement-ack-wait-regression",
        ack_policy=AckPolicy.EXPLICIT,
        ack_wait=JetStreamConstants.CONSUMER_ACK_WAIT_SECONDS,
    )

    assert config.ack_wait == JetStreamConstants.CONSUMER_ACK_WAIT_SECONDS
    assert config.as_dict()["ack_wait"] == (JetStreamConstants.CONSUMER_ACK_WAIT_SECONDS * 1_000_000_000)


@pytest.mark.asyncio
async def test_durable_wake_bridge_creates_once_then_restarts_by_binding_existing_durable() -> None:
    subject = "hp.runtime.workspace.>"
    durable_name = "factory-settlement-v1-workspace"
    subscription = RecordingJetStreamSubscription()
    jetstream = RecordingJetStreamContext(subscription)

    async def wake() -> SettlementReplayReport:
        return SettlementReplayReport(decisions=())

    first = DurableJetStreamSettlementWakeBridge(
        client=RecordingWakeClient(jetstream),
        subject=subject,
        durable_name=durable_name,
        wake=wake,
    )
    assert await first.start() is True
    assert jetstream.create_calls == 1
    assert jetstream.bind_calls == 0
    assert await first.stop() is True

    restarted = DurableJetStreamSettlementWakeBridge(
        client=RecordingWakeClient(jetstream),
        subject=subject,
        durable_name=durable_name,
        wake=wake,
    )
    assert await restarted.start() is True
    try:
        assert jetstream.subscribe_calls == 2
        assert jetstream.create_calls == 1
        assert jetstream.bind_calls == 1
        assert jetstream.durable_name == durable_name
    finally:
        await restarted.stop()


@pytest.mark.asyncio
async def test_durable_wake_bridge_verifies_the_complete_server_consumer_contract() -> None:
    subject = "hp.runtime.workspace.>"
    durable_name = "factory-settlement-v1-workspace"
    config = _wake_consumer_config(durable_name, subject)
    subscription = RecordingJetStreamSubscription()
    jetstream = RecordingJetStreamContext(
        subscription,
        existing_durable_name=durable_name,
        existing_consumer_config=config,
    )
    bridge = DurableJetStreamSettlementWakeBridge(
        client=RecordingWakeClient(jetstream),
        subject=subject,
        durable_name=durable_name,
        wake=lambda: asyncio.sleep(0, result=SettlementReplayReport(decisions=())),
    )

    assert await bridge.start() is True
    try:
        assert jetstream.create_calls == 0
        assert jetstream.bind_calls == 1
        assert subscription.consumer_info_calls == 1
        server_config = subscription.consumer_config
        assert server_config is not None
        assert server_config.as_dict()["ack_wait"] == (JetStreamConstants.CONSUMER_ACK_WAIT_SECONDS * 1_000_000_000)
    finally:
        await bridge.stop()


@pytest.mark.parametrize("field", ("flow_control", "headers_only"))
@pytest.mark.parametrize(("expected_value", "actual_value"), ((False, None), (None, False)))
def test_durable_wake_bridge_normalizes_omitted_false_boolean_fields(
    field: str,
    expected_value: object,
    actual_value: object,
) -> None:
    subject = "hp.runtime.workspace.>"
    durable_name = "factory-settlement-v1-workspace"
    expected = _wake_consumer_config(durable_name, subject)
    actual = _wake_consumer_config(durable_name, subject)
    setattr(expected, field, expected_value)
    setattr(actual, field, actual_value)
    bridge = DurableJetStreamSettlementWakeBridge(
        client=RecordingWakeClient(RecordingJetStreamContext(RecordingJetStreamSubscription())),
        subject=subject,
        durable_name=durable_name,
        wake=lambda: asyncio.sleep(0, result=SettlementReplayReport(decisions=())),
    )

    bridge._assert_consumer_config_matches(expected=expected, actual=actual)


@pytest.mark.parametrize("field", ("flow_control", "headers_only"))
def test_durable_wake_bridge_does_not_normalize_expected_true_to_omitted(
    field: str,
) -> None:
    subject = "hp.runtime.workspace.>"
    durable_name = "factory-settlement-v1-workspace"
    expected = _wake_consumer_config(durable_name, subject)
    actual = _wake_consumer_config(durable_name, subject)
    setattr(expected, field, True)
    setattr(actual, field, None)
    bridge = DurableJetStreamSettlementWakeBridge(
        client=RecordingWakeClient(RecordingJetStreamContext(RecordingJetStreamSubscription())),
        subject=subject,
        durable_name=durable_name,
        wake=lambda: asyncio.sleep(0, result=SettlementReplayReport(decisions=())),
    )

    with pytest.raises(FactorySettlementWakeBridgeError) as raised:
        bridge._assert_consumer_config_matches(expected=expected, actual=actual)

    assert raised.value.code == "factory_settlement_wake_consumer_config_drift"
    assert field in raised.value.details["mismatches"]


@pytest.mark.parametrize("field", ("flow_control", "headers_only"))
def test_durable_wake_bridge_rejects_expected_false_actual_true(field: str) -> None:
    """expected=False / actual=True must remain fail-closed (real drift).

    The None≡False normalization is scoped to server-omitted falsy fields; it
    must never mask a genuine False→True regression on the safety booleans.
    """
    subject = "hp.runtime.workspace.>"
    durable_name = "factory-settlement-v1-workspace"
    expected = _wake_consumer_config(durable_name, subject)
    actual = _wake_consumer_config(durable_name, subject)
    setattr(expected, field, False)
    setattr(actual, field, True)
    bridge = DurableJetStreamSettlementWakeBridge(
        client=RecordingWakeClient(RecordingJetStreamContext(RecordingJetStreamSubscription())),
        subject=subject,
        durable_name=durable_name,
        wake=lambda: asyncio.sleep(0, result=SettlementReplayReport(decisions=())),
    )

    with pytest.raises(FactorySettlementWakeBridgeError) as raised:
        bridge._assert_consumer_config_matches(expected=expected, actual=actual)

    assert raised.value.code == "factory_settlement_wake_consumer_config_drift"
    assert field in raised.value.details["mismatches"]


@pytest.mark.parametrize("field", ("flow_control", "headers_only"))
def test_durable_wake_bridge_false_equivalence_scoped_to_safety_booleans(field: str) -> None:
    """The None≡False equivalence must stay scoped to the two safety booleans.

    A None on any OTHER safety field (here deliver_group) must not be
    normalized into a pass; only flow_control/headers_only get the
    falsy-omission treatment.
    """
    subject = "hp.runtime.workspace.>"
    durable_name = "factory-settlement-v1-workspace"
    expected = _wake_consumer_config(durable_name, subject)
    actual = _wake_consumer_config(durable_name, subject)
    expected.deliver_group = "settlement-workers"
    actual.deliver_group = None
    bridge = DurableJetStreamSettlementWakeBridge(
        client=RecordingWakeClient(RecordingJetStreamContext(RecordingJetStreamSubscription())),
        subject=subject,
        durable_name=durable_name,
        wake=lambda: asyncio.sleep(0, result=SettlementReplayReport(decisions=())),
    )

    with pytest.raises(FactorySettlementWakeBridgeError) as raised:
        bridge._assert_consumer_config_matches(expected=expected, actual=actual)

    assert raised.value.code == "factory_settlement_wake_consumer_config_drift"
    assert "deliver_group" in raised.value.details["mismatches"]


@pytest.mark.parametrize("field", ("flow_control", "headers_only"))
@pytest.mark.parametrize(("expected_value", "actual_value"), ((False, 0), (True, 1)))
def test_durable_wake_bridge_rejects_integer_boolean_lookalikes(
    field: str,
    expected_value: bool,
    actual_value: int,
) -> None:
    subject = "hp.runtime.workspace.>"
    durable_name = "factory-settlement-v1-workspace"
    expected = _wake_consumer_config(durable_name, subject)
    actual = _wake_consumer_config(durable_name, subject)
    setattr(expected, field, expected_value)
    setattr(actual, field, actual_value)
    bridge = DurableJetStreamSettlementWakeBridge(
        client=RecordingWakeClient(RecordingJetStreamContext(RecordingJetStreamSubscription())),
        subject=subject,
        durable_name=durable_name,
        wake=lambda: asyncio.sleep(0, result=SettlementReplayReport(decisions=())),
    )

    with pytest.raises(FactorySettlementWakeBridgeError) as raised:
        bridge._assert_consumer_config_matches(expected=expected, actual=actual)

    assert raised.value.code == "factory_settlement_wake_consumer_config_drift"
    assert raised.value.details["mismatches"][field] == {
        "expected": expected_value,
        "actual": actual_value,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "actual"),
    (
        ("durable_name", "factory-settlement-v1-other-workspace"),
        ("ack_policy", AckPolicy.NONE),
        ("ack_wait", JetStreamConstants.CONSUMER_ACK_WAIT_SECONDS + 1),
        ("filter_subject", "hp.runtime.other.>"),
        ("filter_subjects", ["hp.runtime.workspace.>", "hp.runtime.other.>"]),
        ("max_deliver", 3),
        ("max_ack_pending", JetStreamConstants.CONSUMER_MAX_ACK_PENDING + 1),
        ("backoff", [JetStreamConstants.CONSUMER_ACK_WAIT_SECONDS + 1]),
        ("deliver_policy", DeliverPolicy.ALL),
        ("replay_policy", ReplayPolicy.ORIGINAL),
        ("deliver_group", "settlement-workers"),
        ("flow_control", True),
        ("headers_only", True),
    ),
)
async def test_durable_wake_bridge_rejects_each_critical_consumer_config_drift(
    field: str,
    actual: object,
) -> None:
    subject = "hp.runtime.workspace.>"
    durable_name = "factory-settlement-v1-workspace"
    server_config = _wake_consumer_config(durable_name, subject)
    setattr(server_config, field, actual)
    subscription = RecordingJetStreamSubscription()
    jetstream = RecordingJetStreamContext(
        subscription,
        existing_durable_name=durable_name,
        existing_consumer_config=server_config,
    )
    bridge = DurableJetStreamSettlementWakeBridge(
        client=RecordingWakeClient(jetstream),
        subject=subject,
        durable_name=durable_name,
        wake=lambda: asyncio.sleep(0, result=SettlementReplayReport(decisions=())),
    )

    with pytest.raises(FactorySettlementWakeBridgeError) as raised:
        await bridge.start()

    failure = raised.value
    assert failure.code == "factory_settlement_wake_consumer_config_drift"
    assert failure.details["durable_name"] == durable_name
    assert field in failure.details["mismatches"]
    assert jetstream.create_calls == 0
    assert jetstream.bind_calls == 1
    assert subscription.consumer_info_calls == 1
    assert subscription.unsubscribe_calls == 1
    assert bridge.failure is failure
    assert bridge.is_healthy is False


@pytest.mark.asyncio
async def test_runtime_start_projects_typed_durable_config_drift_evidence(tmp_path: Path) -> None:
    subject = "hp.runtime.workspace.>"
    durable_name = "factory-settlement-v1-workspace"
    server_config = _wake_consumer_config(durable_name, subject)
    server_config.ack_policy = AckPolicy.NONE
    subscription = RecordingJetStreamSubscription()
    jetstream = RecordingJetStreamContext(
        subscription,
        existing_durable_name=durable_name,
        existing_consumer_config=server_config,
    )
    bridge = DurableJetStreamSettlementWakeBridge(
        client=RecordingWakeClient(jetstream),
        subject=subject,
        durable_name=durable_name,
        wake=lambda: asyncio.sleep(0, result=SettlementReplayReport(decisions=())),
    )
    runtime = FactorySettlementRuntime(
        consumer=cast(FactorySettlementConsumer, RecordingConsumer(tmp_path)),
        wake_bridge=bridge,
    )

    with pytest.raises(FactorySettlementRuntimeError) as raised:
        await runtime.start()

    assert raised.value.code == "factory_settlement_wake_bridge_start_failed"
    assert raised.value.details["wake_bridge_code"] == "factory_settlement_wake_consumer_config_drift"
    evidence = raised.value.details["wake_bridge_evidence"]
    assert "ack_policy" in evidence["mismatches"]
    assert bridge.is_healthy is False


@pytest.mark.asyncio
async def test_durable_wake_bridge_remains_healthy_while_idle() -> None:
    subscription = RecordingJetStreamSubscription()
    bridge = DurableJetStreamSettlementWakeBridge(
        client=RecordingWakeClient(RecordingJetStreamContext(subscription)),
        subject="hp.runtime.workspace.>",
        durable_name="factory-settlement-workspace",
        wake=lambda: asyncio.sleep(0, result=SettlementReplayReport(decisions=())),
    )

    await bridge.start()
    try:
        await asyncio.sleep(0)
        assert subscription.next_message_calls == 1
        assert bridge.is_healthy is True
        assert bridge.failure is None
    finally:
        await bridge.stop()


@pytest.mark.asyncio
async def test_durable_wake_bridge_withholds_ack_when_replay_is_not_ack_safe() -> None:
    subscription = RecordingJetStreamSubscription()
    jetstream = RecordingJetStreamContext(subscription)
    message = AckTrackingMessage()
    decision = SettlementDecision(
        source_fact_event_id="fact-1",
        source_fact_seq=1,
        factory_run_id="factory-1",
        workspace_fencing_token=7,
        outcome=SettlementOutcome.PENDING,
        ack_safe=False,
        reason_code="run_ledger_barrier_open",
    )

    async def wake() -> SettlementReplayReport:
        return SettlementReplayReport(decisions=(decision,))

    bridge = DurableJetStreamSettlementWakeBridge(
        client=RecordingWakeClient(jetstream),
        subject="hp.runtime.workspace.>",
        durable_name="factory-settlement-workspace",
        wake=wake,
    )
    await bridge.start()
    try:
        subscription.deliver(message)
        report = await _wait_for_bridge_report(bridge)

        assert bridge.durable_ack_supported is True
        assert bridge.delivery_mode == "jetstream_durable_explicit_ack"
        assert report.ack_safe is False
        assert message.ack_calls == 0
    finally:
        await bridge.stop()


@pytest.mark.asyncio
async def test_durable_wake_bridge_replays_open_barrier_once_per_delivery() -> None:
    subscription = RecordingJetStreamSubscription()
    first = AckTrackingMessage()
    call_times: list[float] = []
    open_decision = SettlementDecision(
        source_fact_event_id="fact-open",
        source_fact_seq=1,
        factory_run_id="factory-open",
        workspace_fencing_token=7,
        outcome=SettlementOutcome.PENDING,
        ack_safe=False,
        reason_code="run_ledger_barrier_open",
    )

    async def wake() -> SettlementReplayReport:
        call_times.append(asyncio.get_running_loop().time())
        return SettlementReplayReport(decisions=(open_decision,))

    bridge = DurableJetStreamSettlementWakeBridge(
        client=RecordingWakeClient(RecordingJetStreamContext(subscription)),
        subject="hp.runtime.workspace.>",
        durable_name="factory-settlement-workspace",
        wake=wake,
        replay_backoff_seconds=0.05,
    )
    await bridge.start()
    try:
        subscription.deliver(first)
        await _wait_for_bridge_report(bridge)
        await asyncio.sleep(0.1)

        assert len(call_times) == 1
        assert first.ack_calls == 0
    finally:
        await bridge.stop()


@pytest.mark.asyncio
async def test_durable_wake_bridge_retryable_error_does_not_block_next_delivery() -> None:
    subscription = RecordingJetStreamSubscription()
    first = AckTrackingMessage()
    second = AckTrackingMessage()
    call_times: list[float] = []

    async def wake() -> SettlementReplayReport:
        call_times.append(asyncio.get_running_loop().time())
        if len(call_times) == 2:
            return SettlementReplayReport(decisions=())
        raise FactorySettlementRetryableError(
            "synthetic retryable replay failure",
            code="synthetic_retryable_replay_failure",
        )

    bridge = DurableJetStreamSettlementWakeBridge(
        client=RecordingWakeClient(RecordingJetStreamContext(subscription)),
        subject="hp.runtime.workspace.>",
        durable_name="factory-settlement-workspace",
        wake=wake,
        replay_backoff_seconds=0.05,
    )
    await bridge.start()
    try:
        subscription.deliver(first)
        subscription.deliver(second)
        await asyncio.wait_for(second.acked.wait(), timeout=1.0)

        assert len(call_times) == 2
        assert first.ack_calls == 0
        assert second.ack_calls == 1
    finally:
        await bridge.stop()


@pytest.mark.asyncio
async def test_durable_wake_bridge_open_barrier_does_not_block_later_deliveries() -> None:
    subscription = RecordingJetStreamSubscription()
    first = AckTrackingMessage()
    later = [AckTrackingMessage() for _ in range(10)]
    wake_calls = 0
    open_decision = SettlementDecision(
        source_fact_event_id="fact-open",
        source_fact_seq=1,
        factory_run_id="factory-open",
        workspace_fencing_token=7,
        outcome=SettlementOutcome.PENDING,
        ack_safe=False,
        reason_code="run_ledger_barrier_open",
    )

    async def wake() -> SettlementReplayReport:
        nonlocal wake_calls
        wake_calls += 1
        if wake_calls > 1:
            return SettlementReplayReport(decisions=())
        return SettlementReplayReport(decisions=(open_decision,))

    bridge = DurableJetStreamSettlementWakeBridge(
        client=RecordingWakeClient(RecordingJetStreamContext(subscription)),
        subject="hp.runtime.workspace.>",
        durable_name="factory-settlement-workspace",
        wake=wake,
        replay_backoff_seconds=0.01,
        replay_max_backoff_seconds=0.02,
    )
    await bridge.start()
    try:
        subscription.deliver(first)
        for message in later:
            subscription.deliver(message)
        await asyncio.wait_for(later[-1].acked.wait(), timeout=1.0)

        assert wake_calls == 11
        assert first.ack_calls == 0
        assert all(message.ack_calls == 1 for message in later)
    finally:
        await bridge.stop()


@pytest.mark.asyncio
async def test_durable_wake_bridge_keeps_unidentified_deliveries_fail_closed() -> None:
    subscription = RecordingJetStreamSubscription()
    messages = [AckTrackingMessage() for _ in range(10)]
    for message in messages:
        subscription.deliver(message)
    wake_calls = 0

    async def wake() -> SettlementReplayReport:
        nonlocal wake_calls
        wake_calls += 1
        return SettlementReplayReport(decisions=())

    bridge = DurableJetStreamSettlementWakeBridge(
        client=RecordingWakeClient(RecordingJetStreamContext(subscription)),
        subject="hp.runtime.workspace.>",
        durable_name="factory-settlement-workspace",
        wake=wake,
    )
    await bridge.start()
    try:
        await asyncio.wait_for(messages[-1].acked.wait(), timeout=1.0)

        assert wake_calls == len(messages)
        assert subscription.next_message_calls >= len(messages)
        assert all(message.ack_calls == 1 for message in messages)
    finally:
        await bridge.stop()


@pytest.mark.asyncio
async def test_durable_wake_bridge_routes_task_runtime_delivery_by_fact_id() -> None:
    subscription = RecordingJetStreamSubscription()
    message = AckTrackingMessage(
        data=_runtime_wake_payload(
            "task_runtime_execution",
            payload={
                "fact_stream": "task_runtime.execution",
                "fact_event_id": "fact-terminal-1",
            },
        )
    )
    hints: list[str] = []
    open_decision = SettlementDecision(
        source_fact_event_id="fact-terminal-1",
        source_fact_seq=7,
        factory_run_id="factory-1",
        workspace_fencing_token=9,
        outcome=SettlementOutcome.PENDING,
        ack_safe=False,
        reason_code="run_ledger_barrier_open",
    )

    async def full_wake() -> SettlementReplayReport:
        raise AssertionError("TaskRuntime delivery must not trigger a full replay")

    async def hinted_wake(source_fact_event_id: str) -> SettlementReplayReport:
        hints.append(source_fact_event_id)
        return SettlementReplayReport(decisions=(open_decision,))

    bridge = DurableJetStreamSettlementWakeBridge(
        client=RecordingWakeClient(RecordingJetStreamContext(subscription)),
        subject="hp.runtime.workspace.>",
        durable_name="factory-settlement-workspace",
        wake=full_wake,
        wake_by_source_fact=hinted_wake,
        replay_backoff_seconds=0.01,
    )
    await bridge.start()
    try:
        subscription.deliver(message)
        await _wait_for_bridge_report(bridge)
        await asyncio.sleep(0.05)

        assert hints == ["fact-terminal-1"]
        assert message.ack_calls == 0
    finally:
        await bridge.stop()


@pytest.mark.asyncio
async def test_durable_wake_bridge_acks_llm_telemetry_without_replay() -> None:
    subscription = RecordingJetStreamSubscription()
    message = AckTrackingMessage(data=_runtime_wake_payload("llm.state"))
    wake_calls = 0

    async def wake() -> SettlementReplayReport:
        nonlocal wake_calls
        wake_calls += 1
        return SettlementReplayReport(decisions=())

    bridge = DurableJetStreamSettlementWakeBridge(
        client=RecordingWakeClient(RecordingJetStreamContext(subscription)),
        subject="hp.runtime.workspace.>",
        durable_name="factory-settlement-workspace",
        wake=wake,
    )
    await bridge.start()
    try:
        subscription.deliver(message)
        await asyncio.wait_for(message.acked.wait(), timeout=1.0)

        assert wake_calls == 0
        assert message.ack_calls == 1
    finally:
        await bridge.stop()


@pytest.mark.asyncio
async def test_durable_wake_bridge_rechecks_control_plane_once() -> None:
    subscription = RecordingJetStreamSubscription()
    message = AckTrackingMessage(
        data=_runtime_wake_payload("control_plane_ledger_projection_update")
    )
    wake_calls = 0
    open_decision = SettlementDecision(
        source_fact_event_id="fact-open",
        source_fact_seq=1,
        factory_run_id="factory-open",
        workspace_fencing_token=7,
        outcome=SettlementOutcome.PENDING,
        ack_safe=False,
        reason_code="run_ledger_barrier_open",
    )

    async def wake() -> SettlementReplayReport:
        nonlocal wake_calls
        wake_calls += 1
        return SettlementReplayReport(decisions=(open_decision,))

    bridge = DurableJetStreamSettlementWakeBridge(
        client=RecordingWakeClient(RecordingJetStreamContext(subscription)),
        subject="hp.runtime.workspace.>",
        durable_name="factory-settlement-workspace",
        wake=wake,
        replay_backoff_seconds=0.01,
    )
    await bridge.start()
    try:
        subscription.deliver(message)
        await asyncio.wait_for(message.acked.wait(), timeout=1.0)
        await asyncio.sleep(0.03)

        assert wake_calls == 1
        assert message.ack_calls == 1
    finally:
        await bridge.stop()


@pytest.mark.asyncio
async def test_durable_wake_bridge_acks_after_ack_safe_replay() -> None:
    subscription = RecordingJetStreamSubscription()
    jetstream = RecordingJetStreamContext(subscription)
    message = AckTrackingMessage()

    async def wake() -> SettlementReplayReport:
        return SettlementReplayReport(decisions=())

    bridge = DurableJetStreamSettlementWakeBridge(
        client=RecordingWakeClient(jetstream),
        subject="hp.runtime.workspace.>",
        durable_name="factory-settlement-workspace",
        wake=wake,
    )
    await bridge.start()
    try:
        subscription.deliver(message)
        await asyncio.wait_for(message.acked.wait(), timeout=1.0)

        assert message.ack_calls == 1
        assert bridge.last_report is not None
        assert bridge.last_report.ack_safe is True
    finally:
        await bridge.stop()


@pytest.mark.asyncio
async def test_durable_wake_bridge_surfaces_nats_ack_failure_without_acknowledging(
    tmp_path: Path,
) -> None:
    subscription = RecordingJetStreamSubscription()
    jetstream = RecordingJetStreamContext(subscription)
    message = FailingAckMessage()

    async def wake() -> SettlementReplayReport:
        return SettlementReplayReport(decisions=())

    bridge = DurableJetStreamSettlementWakeBridge(
        client=RecordingWakeClient(jetstream),
        subject="hp.runtime.workspace.>",
        durable_name="factory-settlement-workspace",
        wake=wake,
    )
    consumer = RecordingConsumer(tmp_path)
    runtime = FactorySettlementRuntime(
        consumer=cast(FactorySettlementConsumer, consumer),
        wake_bridge=bridge,
    )
    await runtime.start()
    try:
        subscription.deliver(message)
        async with asyncio.timeout(1.0):
            while bridge.failure is None:
                await asyncio.sleep(0)

        failure = bridge.failure
        assert isinstance(failure, FactorySettlementWakeBridgeError)
        assert failure.code == "factory_settlement_wake_ack_failed"
        assert message.ack_calls == 1
        assert bridge.is_healthy is False
        assert runtime.is_running is False
        with pytest.raises(FactorySettlementRuntimeError) as raised:
            await runtime.wake()
        assert raised.value.code == "factory_settlement_wake_bridge_unhealthy"
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_durable_wake_bridge_stop_waits_for_active_replay() -> None:
    subscription = RecordingJetStreamSubscription()
    jetstream = RecordingJetStreamContext(subscription)
    message = AckTrackingMessage()
    replay_started = asyncio.Event()
    release_replay = asyncio.Event()

    async def wake() -> SettlementReplayReport:
        replay_started.set()
        await release_replay.wait()
        return SettlementReplayReport(decisions=())

    bridge = DurableJetStreamSettlementWakeBridge(
        client=RecordingWakeClient(jetstream),
        subject="hp.runtime.workspace.>",
        durable_name="factory-settlement-workspace",
        wake=wake,
    )
    await bridge.start()
    subscription.deliver(message)
    await asyncio.wait_for(replay_started.wait(), timeout=1.0)

    stop_task = asyncio.create_task(bridge.stop())
    await asyncio.sleep(0)
    assert stop_task.done() is False

    release_replay.set()
    assert await asyncio.wait_for(stop_task, timeout=1.0) is True
    assert message.ack_calls == 1
    assert subscription.unsubscribe_calls == 1


@pytest.mark.asyncio
async def test_registry_enforces_workspace_singleton_start_wake_stop(tmp_path: Path) -> None:
    registry = FactorySettlementRuntimeRegistry()
    consumer = RecordingConsumer(tmp_path)
    runtime = FactorySettlementRuntime(
        consumer=cast(FactorySettlementConsumer, consumer),
        wake_bridge=None,
    )
    factory_calls = 0

    async def runtime_factory(workspace: str) -> FactorySettlementRuntime:
        nonlocal factory_calls
        assert workspace == str(tmp_path.resolve())
        factory_calls += 1
        return runtime

    first = await registry.start(str(tmp_path), runtime_factory=runtime_factory)
    second = await registry.start(str(tmp_path / "."), runtime_factory=runtime_factory)
    replay = await registry.wake(str(tmp_path))

    assert first.started_now is True
    assert second.already_started is True
    assert replay.ack_safe is True
    assert factory_calls == 1
    assert consumer.start_calls == 1
    assert consumer.wake_calls == 1
    assert await registry.stop(str(tmp_path)) is True
    assert await registry.stop(str(tmp_path)) is False
    assert consumer.stop_calls == 1


@pytest.mark.asyncio
async def test_runtime_replays_again_after_wake_subscription_closes_startup_race(
    tmp_path: Path,
) -> None:
    consumer = RecordingConsumer(tmp_path)
    bridge = RecordingWakeBridge()
    runtime = FactorySettlementRuntime(
        consumer=cast(FactorySettlementConsumer, consumer),
        wake_bridge=cast(DurableJetStreamSettlementWakeBridge, bridge),
    )

    report = await runtime.start()

    assert report.started_now is True
    assert bridge.start_calls == 1
    assert consumer.start_calls == 1
    assert consumer.wake_calls == 1

    await runtime.stop()
    assert bridge.stop_calls == 1
    assert consumer.stop_calls == 1


@pytest.mark.asyncio
async def test_optional_wake_client_failure_keeps_startup_replay(tmp_path: Path) -> None:
    async def unavailable_client() -> SettlementWakeClient:
        raise RuntimeError("NATS unavailable")

    runtime = await create_factory_settlement_runtime(
        str(tmp_path),
        enable_wake_bridge=True,
        wake_bridge_required=False,
        wake_client_factory=unavailable_client,
    )
    report = await runtime.start()
    try:
        assert report.started_now is True
        assert runtime.wake_bridge is None
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_required_wake_client_failure_fails_closed(tmp_path: Path) -> None:
    async def unavailable_client() -> SettlementWakeClient:
        raise RuntimeError("NATS unavailable")

    with pytest.raises(FactorySettlementRuntimeError) as raised:
        await create_factory_settlement_runtime(
            str(tmp_path),
            enable_wake_bridge=True,
            wake_bridge_required=True,
            wake_client_factory=unavailable_client,
        )

    assert raised.value.code == "factory_settlement_wake_bridge_unavailable"
