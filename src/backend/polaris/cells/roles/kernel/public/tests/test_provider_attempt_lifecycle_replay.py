from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from polaris.cells.events.fact_stream.public import (
    AppendSegmentedFactEventCommandV1,
    BootstrapFactStreamWorkspaceCommandV1,
    append_segmented_fact_event,
    bootstrap_fact_stream_workspace,
)
from polaris.cells.roles.kernel.internal.llm_caller.final_provider_attempt_lifecycle import (
    StrictProviderAttemptLifecycleStore,
)
from polaris.cells.roles.kernel.public.physical_attempt_control import (
    FACTORY_PHYSICAL_ATTEMPT_LEASE_SCHEMA,
    FACTORY_PHYSICAL_ATTEMPT_START_PERMIT_SCHEMA,
    FactoryPhysicalAttemptLeaseV1,
    FactoryPhysicalAttemptStartPermitV1,
    ProviderAttemptStartReceiptV1,
)
from polaris.cells.roles.kernel.public.provider_attempt_lifecycle_replay import (
    APPEND_FACTORY_PROVIDER_ATTEMPT_RECOVERY_TERMINAL_SCHEMA,
    QUERY_FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SCHEMA,
    AppendFactoryProviderAttemptRecoveryTerminalV1,
    QueryFactoryProviderAttemptLifecycleReplayV1,
    append_factory_provider_attempt_recovery_terminal,
    factory_provider_attempt_lifecycle_stream,
    factory_provider_attempt_recovery_lease_id,
    query_factory_provider_attempt_lifecycle_replay,
)
from polaris.kernelone.llm.engine.contracts import FrozenFinalProviderAttemptV1


class _RecoveryFence:
    verification_scope = "factory"

    def __init__(self, factory_run_id: str) -> None:
        self.factory_run_id = factory_run_id
        self.revalidation_count = 0

    @contextmanager
    def hold_recovery_terminal(
        self,
        command: AppendFactoryProviderAttemptRecoveryTerminalV1,
    ) -> Iterator[Callable[[], None]]:
        assert command.attempt.factory_run_id == self.factory_run_id

        def revalidate() -> None:
            self.revalidation_count += 1

        revalidate()
        yield revalidate
        revalidate()


def _bootstrap(workspace: Path) -> None:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            streams=("task_runtime.execution",),
            maintenance_reason="provider_attempt_lifecycle_replay_test",
        )
    )


def _attempt() -> FrozenFinalProviderAttemptV1:
    return FrozenFinalProviderAttemptV1(
        provider_request_id="provider-request-1",
        request_freeze_id="freeze-1",
        factory_run_id="factory-run-1",
        scope_id="factory-run-1",
        run_id="run-1",
        turn_id="turn-1",
        call_id="call-1",
        role="director",
        provider="openai",
        model="model-1",
        attempt_number=1,
        verification_scope="factory",
        execution_authority_hash="f" * 64,
        attempt_budget=32,
        authority_attempt_ordinal=1,
        semantic_candidate_hash="d" * 64,
        semantic_request_hash="a" * 64,
        physical_wire_hash="b" * 64,
        composite_request_hash="c" * 64,
        dispatch_view={},
        durable_view={},
    )


def _permit(attempt: FrozenFinalProviderAttemptV1) -> FactoryPhysicalAttemptStartPermitV1:
    return FactoryPhysicalAttemptStartPermitV1(
        schema_version=FACTORY_PHYSICAL_ATTEMPT_START_PERMIT_SCHEMA,
        verification_scope="factory",
        factory_run_id=attempt.factory_run_id,
        run_id=attempt.run_id,
        role=attempt.role,
        turn_id=attempt.turn_id,
        call_id=attempt.call_id,
        request_freeze_id=attempt.request_freeze_id,
        execution_authority_hash=attempt.execution_authority_hash,
        attempt_budget=attempt.attempt_budget,
        provider=attempt.provider,
        model=attempt.model,
        semantic_request_hash=attempt.semantic_request_hash,
        physical_wire_hash=attempt.physical_wire_hash,
        composite_request_hash=attempt.composite_request_hash,
        reservation_id="reservation-1",
        provider_request_id=attempt.provider_request_id,
        authority_attempt_ordinal=attempt.authority_attempt_ordinal,
        start_permit_id="start-permit-1",
    )


def _lease(
    permit: FactoryPhysicalAttemptStartPermitV1,
    receipt: ProviderAttemptStartReceiptV1,
) -> FactoryPhysicalAttemptLeaseV1:
    return FactoryPhysicalAttemptLeaseV1(
        schema_version=FACTORY_PHYSICAL_ATTEMPT_LEASE_SCHEMA,
        verification_scope=permit.verification_scope,
        factory_run_id=permit.factory_run_id,
        run_id=permit.run_id,
        role=permit.role,
        turn_id=permit.turn_id,
        call_id=permit.call_id,
        request_freeze_id=permit.request_freeze_id,
        execution_authority_hash=permit.execution_authority_hash,
        attempt_budget=permit.attempt_budget,
        provider=permit.provider,
        model=permit.model,
        semantic_request_hash=permit.semantic_request_hash,
        physical_wire_hash=permit.physical_wire_hash,
        composite_request_hash=permit.composite_request_hash,
        reservation_id=permit.reservation_id,
        provider_request_id=permit.provider_request_id,
        authority_attempt_ordinal=permit.authority_attempt_ordinal,
        start_permit_id=permit.start_permit_id,
        lease_id="lease-1",
        start_receipt=receipt,
    )


def _query(workspace: Path) -> QueryFactoryProviderAttemptLifecycleReplayV1:
    return QueryFactoryProviderAttemptLifecycleReplayV1(
        schema_version=QUERY_FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SCHEMA,
        workspace=str(workspace),
        factory_run_id="factory-run-1",
    )


def test_public_replay_retains_unmatched_durable_start_without_authorizing_dispatch(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    lifecycle = StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(tmp_path),
        factory_run_id="factory-run-1",
    )
    attempt = _attempt()
    start_receipt = lifecycle.append_start(
        attempt,
        start_permit=_permit(attempt),
        context_snapshot_ref="d" * 24,
        pin_hash="e" * 64,
    )
    assert type(start_receipt) is ProviderAttemptStartReceiptV1

    replay = query_factory_provider_attempt_lifecycle_replay(_query(tmp_path))

    assert replay.captured_head.total_count == 1
    assert [fact.phase for fact in replay.facts] == ["start"]
    assert replay.facts[0].provider_request_id == attempt.provider_request_id
    assert replay.facts[0].semantic_candidate_hash == attempt.semantic_candidate_hash
    assert replay.facts[0].semantic_candidate_hash != replay.facts[0].semantic_request_hash
    assert not hasattr(replay, "reserve")
    assert not hasattr(replay, "physical_attempt_control_port")


def test_public_replay_reads_legacy_single_hash_fact_under_old_equality_rule(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    lifecycle = StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(tmp_path),
        factory_run_id="factory-run-1",
    )
    attempt = _attempt()
    permit = _permit(attempt)
    payload = lifecycle._base_payload(
        attempt,
        context_snapshot_ref="d" * 24,
        pin_hash="e" * 64,
    )
    payload.pop("semantic_candidate_hash")
    payload.update(lifecycle._factory_identity_payload(permit))
    append_segmented_fact_event(
        AppendSegmentedFactEventCommandV1(
            workspace=str(tmp_path),
            logical_stream=factory_provider_attempt_lifecycle_stream("factory-run-1"),
            event_type="provider_attempt.started",
            source="roles.kernel",
            payload=payload,
            idempotency_key=f"{attempt.provider_request_id}:start",
        )
    )

    replay = query_factory_provider_attempt_lifecycle_replay(_query(tmp_path))

    assert replay.facts[0].semantic_candidate_hash == attempt.semantic_request_hash
    assert replay.facts[0].semantic_candidate_hash != attempt.semantic_candidate_hash


def test_recovery_terminal_cas_appends_once_and_idempotently_rereads(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    lifecycle = StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(tmp_path),
        factory_run_id="factory-run-1",
    )
    attempt = _attempt()
    permit = _permit(attempt)
    start = lifecycle.append_start(
        attempt,
        start_permit=permit,
        context_snapshot_ref="d" * 24,
        pin_hash="e" * 64,
    )
    assert type(start) is ProviderAttemptStartReceiptV1
    lease = replace(
        _lease(permit, start),
        lease_id=factory_provider_attempt_recovery_lease_id(
            attempt.factory_run_id,
            attempt.provider_request_id,
        ),
    )
    replay = query_factory_provider_attempt_lifecycle_replay(_query(tmp_path))
    command = AppendFactoryProviderAttemptRecoveryTerminalV1(
        schema_version=APPEND_FACTORY_PROVIDER_ATTEMPT_RECOVERY_TERMINAL_SCHEMA,
        workspace=str(tmp_path),
        attempt=attempt,
        lease=lease,
        context_snapshot_ref="d" * 24,
        pin_hash="e" * 64,
        expected_lifecycle_head_sequence=replay.captured_head.global_seq,
        expected_lifecycle_head_hash=replay.captured_head.head_hash,
    )

    fence = _RecoveryFence(attempt.factory_run_id)
    first = append_factory_provider_attempt_recovery_terminal(command, recovery_fence=fence)
    second = append_factory_provider_attempt_recovery_terminal(command, recovery_fence=fence)

    assert first == second
    assert fence.revalidation_count >= 6
    assert first.terminal_status == "cancelled"
    assert [fact.phase for fact in query_factory_provider_attempt_lifecycle_replay(_query(tmp_path)).facts] == [
        "start",
        "terminal",
    ]


def test_public_replay_exact_pairs_start_and_terminal_at_one_captured_head(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    lifecycle = StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(tmp_path),
        factory_run_id="factory-run-1",
    )
    attempt = _attempt()
    permit = _permit(attempt)
    start = lifecycle.append_start(
        attempt,
        start_permit=permit,
        context_snapshot_ref="d" * 24,
        pin_hash="e" * 64,
    )
    assert type(start) is ProviderAttemptStartReceiptV1
    lifecycle.append_terminal(
        attempt,
        lease=_lease(permit, start),
        context_snapshot_ref="d" * 24,
        pin_hash="e" * 64,
        status="completed",
    )

    replay = query_factory_provider_attempt_lifecycle_replay(_query(tmp_path))

    assert replay.captured_head.total_count == 2
    assert [fact.phase for fact in replay.facts] == ["start", "terminal"]
    assert replay.facts[1].lease_id == "lease-1"
    assert replay.facts[1].terminal_status == "completed"
    assert replay.facts[1].logical_sequence > replay.facts[0].logical_sequence
