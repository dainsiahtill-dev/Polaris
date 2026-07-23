"""B3.4 Factory-owned physical-attempt coordinator tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, replace
from threading import Barrier, Event, Thread
from typing import Any, cast

import pytest
from polaris.cells.factory.pipeline.internal import factory_physical_attempt_coordinator as coordinator_module
from polaris.cells.factory.pipeline.internal.factory_physical_attempt_coordinator import (
    FactoryPhysicalAttemptControlError,
    FactoryPhysicalAttemptCoordinator,
    FactoryPhysicalAttemptLiveControlPort,
)
from polaris.cells.factory.pipeline.internal.factory_run_admission import FactoryWorkspaceRunAdmission
from polaris.cells.roles.kernel.public.physical_attempt_control import (
    ABORT_FACTORY_PHYSICAL_ATTEMPT_RESERVATION_SCHEMA,
    BEGIN_FACTORY_PHYSICAL_ATTEMPT_START_SCHEMA,
    COMMIT_FACTORY_PHYSICAL_ATTEMPT_START_SCHEMA,
    FACTORY_PHYSICAL_ATTEMPT_CUTOFF_VIEW_SCHEMA,
    FACTORY_PHYSICAL_ATTEMPT_DEFINITE_START_NOT_PERSISTED_PROOF_SCHEMA,
    FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
    FAIL_FACTORY_PHYSICAL_ATTEMPT_TERMINAL_SCHEMA,
    MARK_FACTORY_PHYSICAL_ATTEMPT_START_AMBIGUOUS_SCHEMA,
    PROVIDER_ATTEMPT_START_RECEIPT_SCHEMA,
    PROVIDER_ATTEMPT_TERMINAL_RECEIPT_SCHEMA,
    RESERVE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA,
    SETTLE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA,
    AbortFactoryPhysicalAttemptReservationV1,
    BeginFactoryPhysicalAttemptStartV1,
    CommitFactoryPhysicalAttemptStartV1,
    FactoryPhysicalAttemptControlPort,
    FactoryPhysicalAttemptCutoffViewV1,
    FactoryPhysicalAttemptDefiniteStartNotPersistedProofV1,
    FactoryPhysicalAttemptGrantViewV1,
    FactoryPhysicalAttemptLeaseV1,
    FactoryPhysicalAttemptReservationV1,
    FactoryPhysicalAttemptStartPermitV1,
    FailFactoryPhysicalAttemptTerminalV1,
    MarkFactoryPhysicalAttemptStartAmbiguousV1,
    ProviderAttemptStartReceiptV1,
    ProviderAttemptTerminalReceiptV1,
    ReserveFactoryPhysicalAttemptV1,
    SettleFactoryPhysicalAttemptV1,
)
from polaris.kernelone.events.final_request_evidence import canonical_role_final_request_hash
from polaris.kernelone.llm.engine.contracts import ProviderAttemptDrainError

_AUTHORITY_A = "a" * 64
_AUTHORITY_B = "b" * 64
_SEMANTIC = "c" * 64
_WIRE = "d" * 64
_COMPOSITE = "e" * 64


def _command(
    *,
    factory_run_id: str = "factory-run-1",
    execution_authority_hash: str = _AUTHORITY_A,
    run_id: str = "role-run-1",
    request_freeze_id: str = "freeze-1",
    call_id: str = "call-1",
    attempt_budget: int = 32,
    **overrides: object,
) -> ReserveFactoryPhysicalAttemptV1:
    values: dict[str, object] = {
        "schema_version": RESERVE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA,
        "verification_scope": "factory",
        "factory_run_id": factory_run_id,
        "run_id": run_id,
        "role": "director",
        "turn_id": "turn-1",
        "call_id": call_id,
        "request_freeze_id": request_freeze_id,
        "execution_authority_hash": execution_authority_hash,
        "attempt_budget": attempt_budget,
        "provider": "test-provider",
        "model": "test-model",
        "semantic_request_hash": _SEMANTIC,
        "physical_wire_hash": _WIRE,
    }
    values.update(overrides)
    return ReserveFactoryPhysicalAttemptV1(**values)  # type: ignore[arg-type]


def _coordinator(
    *,
    factory_run_id: str = "factory-run-1",
    execution_authority_hash: str = _AUTHORITY_A,
    controlled_run_id: str = "role-run-1",
    freezes: tuple[str, ...] = ("freeze-1",),
    attempt_budget: int = 32,
) -> FactoryPhysicalAttemptCoordinator:
    coordinator = FactoryPhysicalAttemptCoordinator(factory_run_id=factory_run_id)
    grant = _grant_view(
        factory_run_id=factory_run_id,
        execution_authority_hash=execution_authority_hash,
        attempt_budget=attempt_budget,
    )
    coordinator.register_grant(grant)
    for index, freeze_id in enumerate(freezes, start=1):
        coordinator.register_cutoff(
            _cutoff_view(
                grant=grant,
                run_id=controlled_run_id,
                request_freeze_id=freeze_id,
                call_id=f"call-{index}",
            )
        )
    return coordinator


def _grant_view(**overrides: object) -> FactoryPhysicalAttemptGrantViewV1:
    values: dict[str, object] = {
        "schema_version": FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
        "verification_scope": "factory",
        "factory_run_id": "factory-run-1",
        "role": "director",
        "stage": "director_dispatch",
        "workspace_fencing_token": 7,
        "stage_claim_attempt": 2,
        "stage_claim_nonce": "stage-nonce-1",
        "execution_authority_hash": _AUTHORITY_A,
        "attempt_budget": 32,
    }
    values.update(overrides)
    return FactoryPhysicalAttemptGrantViewV1(**values)  # type: ignore[arg-type]


def _cutoff_view(
    grant: FactoryPhysicalAttemptGrantViewV1 | None = None,
    **overrides: object,
) -> FactoryPhysicalAttemptCutoffViewV1:
    values: dict[str, object] = {
        "schema_version": FACTORY_PHYSICAL_ATTEMPT_CUTOFF_VIEW_SCHEMA,
        "grant": grant or _grant_view(),
        "run_id": "role-run-1",
        "turn_id": "turn-1",
        "call_id": "call-1",
        "request_freeze_id": "freeze-1",
        "provider": "test-provider",
        "model": "test-model",
        "semantic_request_hash": _SEMANTIC,
        "physical_wire_hash": _WIRE,
    }
    values.update(overrides)
    return FactoryPhysicalAttemptCutoffViewV1(**values)  # type: ignore[arg-type]


def _register_command_cutoff(
    coordinator: FactoryPhysicalAttemptCoordinator,
    command: ReserveFactoryPhysicalAttemptV1,
) -> None:
    coordinator.register_cutoff(
        _cutoff_view(
            grant=_grant_view(
                factory_run_id=command.factory_run_id,
                execution_authority_hash=command.execution_authority_hash,
                attempt_budget=command.attempt_budget,
            ),
            run_id=command.run_id,
            turn_id=command.turn_id,
            call_id=command.call_id,
            request_freeze_id=command.request_freeze_id,
            provider=command.provider,
            model=command.model,
            semantic_request_hash=command.semantic_request_hash,
            physical_wire_hash=command.physical_wire_hash,
        )
    )


def test_live_control_port_registers_wire_cutoff_through_exact_seven_method_capability() -> None:
    port = FactoryPhysicalAttemptLiveControlPort(
        factory_run_id="factory-run-1",
        revalidate_active_stage_claim=lambda _grant: None,
    )
    grant = _grant_view()
    port.register_grant(grant)

    assert isinstance(port, FactoryPhysicalAttemptControlPort)
    reservation = port.reserve(_command())
    assert reservation.execution_authority_hash == grant.execution_authority_hash
    assert reservation.physical_wire_hash == _WIRE
    assert port.budget_state(_AUTHORITY_A).reserved_count == 1

    second = port.reserve(_command(call_id="call-2", physical_wire_hash="9" * 64))
    assert second.provider_request_id != reservation.provider_request_id
    assert second.authority_attempt_ordinal == 2


def test_live_control_port_revalidates_durable_stage_fence_before_reserve_and_begin_start(tmp_path: Any) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    admission = FactoryWorkspaceRunAdmission(workspace, state_root=tmp_path / "runtime" / "factory")
    lease = admission.acquire("factory-run-1")
    stage_lease = admission.claim_stage(
        lease.run_id,
        fencing_token=lease.fencing_token,
        stage="director_dispatch",
        nonce="stage-nonce-1",
    )
    claim = stage_lease.stage_execution_claim
    assert claim is not None

    def revalidate_active_stage_claim(grant: FactoryPhysicalAttemptGrantViewV1) -> None:
        with admission.hold_active_stage_claim(
            grant.factory_run_id,
            fencing_token=grant.workspace_fencing_token,
            stage=grant.stage,
            attempt=grant.stage_claim_attempt,
            nonce=grant.stage_claim_nonce,
        ) as revalidate:
            revalidate()

    port = FactoryPhysicalAttemptLiveControlPort(
        factory_run_id=lease.run_id,
        revalidate_active_stage_claim=revalidate_active_stage_claim,
    )
    grant = _grant_view(
        factory_run_id=lease.run_id,
        workspace_fencing_token=lease.fencing_token,
        stage_claim_attempt=claim.attempt,
        stage_claim_nonce=claim.nonce,
    )
    port.register_grant(grant)
    reservation = port.reserve(_command(factory_run_id=lease.run_id))
    permit = port.begin_start(_begin(reservation))

    admission.claim_lifecycle_operation(
        lease.run_id,
        operation="recover_run",
        nonce="restart-replay-fence",
        acquire_if_available=False,
        expected_fencing_token=lease.fencing_token,
        replay_fence=True,
    )

    with pytest.raises(FactoryPhysicalAttemptControlError, match="factory_physical_attempt_authority_closed"):
        port.register_grant(grant)
    with pytest.raises(FactoryPhysicalAttemptControlError, match="factory_physical_attempt_authority_closed"):
        port.reserve(_command(factory_run_id=lease.run_id, call_id="stale-call"))
    with pytest.raises(FactoryPhysicalAttemptControlError, match="factory_physical_attempt_authority_closed"):
        port.begin_start(_begin(reservation))
    with pytest.raises(FactoryPhysicalAttemptControlError, match="factory_physical_attempt_authority_closed"):
        port.commit_started(_commit(permit))


def test_live_control_port_never_revalidates_storage_while_local_control_lock_is_held() -> None:
    authority_lock_active = False
    coordinator_call_active = False
    revalidation_count = 0

    class _TrackingAuthorityLock:
        def __enter__(self) -> None:
            nonlocal authority_lock_active
            assert not authority_lock_active
            authority_lock_active = True

        def __exit__(self, *_exc: object) -> None:
            nonlocal authority_lock_active
            authority_lock_active = False

    port: FactoryPhysicalAttemptLiveControlPort

    def revalidate_active_stage_claim(_grant: FactoryPhysicalAttemptGrantViewV1) -> None:
        nonlocal revalidation_count
        assert not authority_lock_active
        assert not coordinator_call_active
        revalidation_count += 1

    port = FactoryPhysicalAttemptLiveControlPort(
        factory_run_id="factory-run-1",
        revalidate_active_stage_claim=revalidate_active_stage_claim,
    )
    port._authority_lock = _TrackingAuthorityLock()  # type: ignore[assignment]

    def track_coordinator_call(name: str) -> None:
        original = getattr(port._coordinator, name)

        def wrapped(*args: object, **kwargs: object) -> object:
            nonlocal coordinator_call_active
            assert authority_lock_active
            assert not coordinator_call_active
            coordinator_call_active = True
            try:
                return original(*args, **kwargs)
            finally:
                coordinator_call_active = False

        setattr(port._coordinator, name, wrapped)

    for method_name in ("register_grant", "reserve", "begin_start", "commit_started"):
        track_coordinator_call(method_name)

    port.register_grant(_grant_view())
    reservation = port.reserve(_command())
    permit = port.begin_start(_begin(reservation))
    port.commit_started(_commit(permit))

    assert revalidation_count == 8
    assert not authority_lock_active
    assert not coordinator_call_active


def test_concurrent_replay_fence_rolls_back_unexposed_reservation(tmp_path: Any) -> None:
    admission = FactoryWorkspaceRunAdmission(tmp_path, state_root=tmp_path / "runtime")
    lease = admission.acquire("factory-run-1")
    stage_lease = admission.claim_stage(
        lease.run_id,
        fencing_token=lease.fencing_token,
        stage="director_dispatch",
        nonce="stage-nonce-1",
    )
    claim = stage_lease.stage_execution_claim
    assert claim is not None

    def revalidate_active_stage_claim(grant: FactoryPhysicalAttemptGrantViewV1) -> None:
        with admission.hold_active_stage_claim(
            grant.factory_run_id,
            fencing_token=grant.workspace_fencing_token,
            stage=grant.stage,
            attempt=grant.stage_claim_attempt,
            nonce=grant.stage_claim_nonce,
        ) as revalidate:
            revalidate()

    port = FactoryPhysicalAttemptLiveControlPort(
        factory_run_id=lease.run_id,
        revalidate_active_stage_claim=revalidate_active_stage_claim,
    )
    port.register_grant(
        _grant_view(
            factory_run_id=lease.run_id,
            workspace_fencing_token=lease.fencing_token,
            stage_claim_attempt=claim.attempt,
            stage_claim_nonce=claim.nonce,
        )
    )

    mutation_entered = Event()
    release_mutation = Event()
    original_reserve = port._coordinator.reserve

    def paused_reserve(command: ReserveFactoryPhysicalAttemptV1) -> FactoryPhysicalAttemptReservationV1:
        mutation_entered.set()
        assert release_mutation.wait(timeout=5.0)
        return original_reserve(command)

    port._coordinator.reserve = paused_reserve  # type: ignore[method-assign]
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(port.reserve, _command(factory_run_id=lease.run_id))
        assert mutation_entered.wait(timeout=5.0)
        admission.claim_lifecycle_operation(
            lease.run_id,
            operation="recover_run",
            nonce="restart-replay-fence",
            acquire_if_available=False,
            expected_fencing_token=lease.fencing_token,
            replay_fence=True,
        )
        release_mutation.set()
        with pytest.raises(FactoryPhysicalAttemptControlError, match="factory_physical_attempt_authority_closed"):
            future.result(timeout=5.0)

    assert port.drain_snapshot().blocking_reservation_ids == ()
    assert port.snapshot().inflight_request_ids == ()


def test_failed_post_revalidation_exposes_no_start_permit_or_outbound_lease() -> None:
    revalidation_calls = 0
    fail_at: int | None = None

    def revalidate(_grant: FactoryPhysicalAttemptGrantViewV1) -> None:
        nonlocal revalidation_calls
        revalidation_calls += 1
        if fail_at == revalidation_calls:
            raise FactoryPhysicalAttemptControlError("factory_physical_attempt_authority_closed")

    port = FactoryPhysicalAttemptLiveControlPort(
        factory_run_id="factory-run-1",
        revalidate_active_stage_claim=revalidate,
    )
    port.register_grant(_grant_view())
    reservation = port.reserve(_command())

    fail_at = revalidation_calls + 2
    with pytest.raises(FactoryPhysicalAttemptControlError, match="factory_physical_attempt_authority_closed"):
        port.begin_start(_begin(reservation))
    assert port.drain_snapshot().blocking_reservation_ids == ()

    second_port = FactoryPhysicalAttemptLiveControlPort(
        factory_run_id="factory-run-1",
        revalidate_active_stage_claim=revalidate,
    )
    fail_at = None
    second_port.register_grant(_grant_view())
    second_reservation = second_port.reserve(_command())
    permit = second_port.begin_start(_begin(second_reservation))
    fail_at = revalidation_calls + 2
    with pytest.raises(FactoryPhysicalAttemptControlError, match="factory_physical_attempt_authority_closed"):
        second_port.commit_started(_commit(permit))
    assert second_port.drain_snapshot().blocking_reservation_ids == (second_reservation.reservation_id,)
    second_port.mark_start_ambiguous(
        MarkFactoryPhysicalAttemptStartAmbiguousV1(
            schema_version=MARK_FACTORY_PHYSICAL_ATTEMPT_START_AMBIGUOUS_SCHEMA,
            start_permit=permit,
            reason_code="post_revalidation_failed",
        )
    )


def test_live_control_port_rejects_cross_grant_command_before_cutoff_registration() -> None:
    port = FactoryPhysicalAttemptLiveControlPort(
        factory_run_id="factory-run-1",
        revalidate_active_stage_claim=lambda _grant: None,
    )
    port.register_grant(_grant_view())

    with pytest.raises(FactoryPhysicalAttemptControlError) as raised:
        port.reserve(_command(execution_authority_hash=_AUTHORITY_B))
    assert raised.value.code == "factory_physical_attempt_execution_authority_hash_mismatch"
    assert port.drain_snapshot().blocking_reservation_ids == ()


@pytest.mark.asyncio
async def test_live_control_port_is_the_factory_provider_drain_authority() -> None:
    port = FactoryPhysicalAttemptLiveControlPort(
        factory_run_id="factory-run-1",
        revalidate_active_stage_claim=lambda _grant: None,
    )
    port.register_grant(_grant_view())
    reservation = port.reserve(_command())

    with pytest.raises(ProviderAttemptDrainError) as pending:
        await port.wait_settled(
            verification_scope="factory",
            scope_id="factory-run-1",
            timeout_seconds=0.01,
        )
    assert pending.value.code == "provider_attempt_drain_timeout"
    assert pending.value.result.inflight_request_ids == (reservation.provider_request_id,)

    port.abort_reservation(
        AbortFactoryPhysicalAttemptReservationV1(
            schema_version=ABORT_FACTORY_PHYSICAL_ATTEMPT_RESERVATION_SCHEMA,
            reservation=reservation,
            start_permit=None,
            definite_start_not_persisted_proof=None,
        )
    )
    settled = await port.wait_settled(
        verification_scope="factory",
        scope_id="factory-run-1",
        timeout_seconds=0.1,
    )
    assert settled.settled is True
    assert settled.inflight_request_ids == ()


def _kwargs(value: Any) -> dict[str, object]:
    return {field.name: getattr(value, field.name) for field in fields(value)}


def _begin(reservation: FactoryPhysicalAttemptReservationV1) -> BeginFactoryPhysicalAttemptStartV1:
    values = _kwargs(reservation)
    values["schema_version"] = BEGIN_FACTORY_PHYSICAL_ATTEMPT_START_SCHEMA
    return BeginFactoryPhysicalAttemptStartV1(**values)  # type: ignore[arg-type]


def _start_receipt(permit: FactoryPhysicalAttemptStartPermitV1) -> ProviderAttemptStartReceiptV1:
    values = _kwargs(permit)
    values.update(
        schema_version=PROVIDER_ATTEMPT_START_RECEIPT_SCHEMA,
        lifecycle_event_id=f"start:{permit.provider_request_id}",
        logical_sequence=permit.authority_attempt_ordinal,
        event_hash=f"{permit.authority_attempt_ordinal:064x}",
        phase="start",
        durability_acked=True,
    )
    return ProviderAttemptStartReceiptV1(**values)  # type: ignore[arg-type]


def _commit(
    permit: FactoryPhysicalAttemptStartPermitV1,
    *,
    receipt: ProviderAttemptStartReceiptV1 | None = None,
) -> CommitFactoryPhysicalAttemptStartV1:
    values = _kwargs(permit)
    values.update(
        schema_version=COMMIT_FACTORY_PHYSICAL_ATTEMPT_START_SCHEMA,
        start_receipt=receipt or _start_receipt(permit),
    )
    return CommitFactoryPhysicalAttemptStartV1(**values)  # type: ignore[arg-type]


def _terminal_receipt(lease: FactoryPhysicalAttemptLeaseV1) -> ProviderAttemptTerminalReceiptV1:
    values = _kwargs(lease)
    values.pop("start_receipt")
    values.update(
        schema_version=PROVIDER_ATTEMPT_TERMINAL_RECEIPT_SCHEMA,
        lifecycle_event_id=f"terminal:{lease.provider_request_id}",
        logical_sequence=lease.authority_attempt_ordinal + 100,
        event_hash=f"{lease.authority_attempt_ordinal + 1000:064x}",
        phase="terminal",
        durability_acked=True,
        terminal_status="completed",
    )
    return ProviderAttemptTerminalReceiptV1(**values)  # type: ignore[arg-type]


def _definite_start_absent_proof(
    permit: FactoryPhysicalAttemptStartPermitV1,
) -> FactoryPhysicalAttemptDefiniteStartNotPersistedProofV1:
    return FactoryPhysicalAttemptDefiniteStartNotPersistedProofV1(
        schema_version=FACTORY_PHYSICAL_ATTEMPT_DEFINITE_START_NOT_PERSISTED_PROOF_SCHEMA,
        start_permit=permit,
        proof_id=f"proof:{permit.start_permit_id}",
        lifecycle_head_sequence=permit.authority_attempt_ordinal + 100,
        lifecycle_head_hash=f"{permit.authority_attempt_ordinal + 2000:064x}",
        proof_kind="definite_start_not_persisted",
        durability_acked=True,
    )


def _commit_one(
    coordinator: FactoryPhysicalAttemptCoordinator,
    reservation: FactoryPhysicalAttemptReservationV1,
) -> FactoryPhysicalAttemptLeaseV1:
    permit = coordinator.begin_start(_begin(reservation))
    return coordinator.commit_started(_commit(permit))


def _settle_one(
    coordinator: FactoryPhysicalAttemptCoordinator,
    lease: FactoryPhysicalAttemptLeaseV1,
) -> None:
    coordinator.settle(
        SettleFactoryPhysicalAttemptV1(
            schema_version=SETTLE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA,
            lease=lease,
            terminal_receipt=_terminal_receipt(lease),
        )
    )


def test_coordinator_is_explicit_runtime_port_not_global_registry() -> None:
    first = _coordinator()
    second = _coordinator(factory_run_id="factory-run-2")

    assert isinstance(first, FactoryPhysicalAttemptControlPort)
    assert first is not second
    assert first.factory_run_id == "factory-run-1"
    assert second.factory_run_id == "factory-run-2"


def test_identity_entropy_is_never_requested_while_coordinator_lock_is_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = _coordinator(attempt_budget=1)
    ownership_observations: list[bool] = []
    counter = 0

    class _UUID:
        def __init__(self, value: int) -> None:
            self.hex = f"{value:032x}"

    def fake_uuid4() -> _UUID:
        nonlocal counter
        counter += 1
        ownership_observations.append(cast(Any, coordinator._lock)._is_owned())
        return _UUID(counter)

    monkeypatch.setattr(coordinator_module.uuid, "uuid4", fake_uuid4)
    reservation = coordinator.reserve(_command(attempt_budget=1))
    permit = coordinator.begin_start(_begin(reservation))
    coordinator.commit_started(_commit(permit))

    assert ownership_observations == [False, False, False, False]


def test_sixty_four_competitors_on_budget_32_yield_exactly_32_reservations() -> None:
    coordinator = _coordinator()
    grant = _grant_view()
    for index in range(64):
        if index == 1:
            continue
        coordinator.register_cutoff(_cutoff_view(grant=grant, call_id=f"call-{index}"))
    barrier = Barrier(64)

    def reserve(index: int) -> tuple[str, object]:
        barrier.wait()
        try:
            return "ok", coordinator.reserve(_command(call_id=f"call-{index}"))
        except FactoryPhysicalAttemptControlError as exc:
            return "error", exc.code

    with ThreadPoolExecutor(max_workers=64) as executor:
        results = tuple(executor.map(reserve, range(64)))

    reservations = tuple(
        value for status, value in results if status == "ok" and isinstance(value, FactoryPhysicalAttemptReservationV1)
    )
    errors = tuple(value for status, value in results if status == "error")
    state = coordinator.budget_state(_AUTHORITY_A)
    assert len(reservations) == 32
    assert len({item.reservation_id for item in reservations}) == 32
    assert len({item.provider_request_id for item in reservations}) == 32
    assert sorted(item.authority_attempt_ordinal for item in reservations) == list(range(1, 33))
    assert errors == ("factory_physical_attempt_budget_exhausted",) * 32
    assert state.reserved_count == 32
    assert state.committed_count == 0
    assert state.consumed_attempts == 0
    assert state.remaining_attempts == 0


def test_same_hash_aggregates_across_freezes_and_distinct_grant_is_isolated() -> None:
    coordinator = _coordinator(freezes=("freeze-1", "freeze-2"), attempt_budget=2)
    other_grant = _grant_view(
        factory_run_id="factory-run-1",
        execution_authority_hash=_AUTHORITY_B,
        attempt_budget=2,
    )
    coordinator.register_grant(other_grant)
    coordinator.register_cutoff(
        _cutoff_view(
            grant=other_grant,
            run_id="role-run-2",
            request_freeze_id="freeze-b",
        )
    )

    first = coordinator.reserve(_command(attempt_budget=2, request_freeze_id="freeze-1"))
    second = coordinator.reserve(_command(attempt_budget=2, request_freeze_id="freeze-2", call_id="call-2"))
    other = coordinator.reserve(
        _command(
            attempt_budget=2,
            execution_authority_hash=_AUTHORITY_B,
            run_id="role-run-2",
            request_freeze_id="freeze-b",
        )
    )

    assert (first.authority_attempt_ordinal, second.authority_attempt_ordinal) == (1, 2)
    assert other.authority_attempt_ordinal == 1
    assert coordinator.budget_state(_AUTHORITY_A).remaining_attempts == 0
    assert coordinator.budget_state(_AUTHORITY_B).remaining_attempts == 1


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"factory_run_id": "factory-run-other"}, "factory_physical_attempt_factory_run_mismatch"),
        ({"role": "qa"}, "factory_physical_attempt_role_mismatch"),
        ({"run_id": "role-run-other"}, "factory_physical_attempt_controlled_run_mismatch"),
        ({"request_freeze_id": "freeze-other"}, "factory_physical_attempt_reservation_unknown"),
        ({"execution_authority_hash": _AUTHORITY_B}, "factory_physical_attempt_execution_authority_hash_mismatch"),
        ({"attempt_budget": 31}, "factory_physical_attempt_budget_mismatch"),
    ],
)
def test_forged_reservation_identity_has_zero_effect(override: dict[str, object], code: str) -> None:
    coordinator = _coordinator()
    with pytest.raises(FactoryPhysicalAttemptControlError) as raised:
        coordinator.reserve(cast(Any, _command)(**override))
    assert raised.value.code == code
    assert coordinator.budget_state(_AUTHORITY_A).reserved_count == 0


def test_reserve_rejects_subclass_without_effect() -> None:
    class _ReserveSubclass(ReserveFactoryPhysicalAttemptV1):
        pass

    command = _command()
    forged = _ReserveSubclass(**_kwargs(command))
    coordinator = _coordinator()
    with pytest.raises(FactoryPhysicalAttemptControlError) as raised:
        coordinator.reserve(forged)
    assert raised.value.code == "factory_physical_attempt_control_command_exact_type_required"
    assert coordinator.budget_state(_AUTHORITY_A).reserved_count == 0


def test_start_receipt_is_budget_linearization_and_terminal_ack_settles() -> None:
    coordinator = _coordinator(attempt_budget=2)
    reservation = coordinator.reserve(_command(attempt_budget=2))
    before = coordinator.budget_state(_AUTHORITY_A)
    permit = coordinator.begin_start(_begin(reservation))
    persisting = coordinator.budget_state(_AUTHORITY_A)
    lease = coordinator.commit_started(_commit(permit))
    committed = coordinator.budget_state(_AUTHORITY_A)

    assert before.consumed_attempts == 0
    assert persisting.start_persisting_count == 1
    assert persisting.consumed_attempts == 0
    assert committed.reserved_count == 0
    assert committed.committed_count == 1
    assert committed.consumed_attempts == 1
    assert committed.remaining_attempts == 1
    assert lease.start_receipt == _start_receipt(permit)

    _settle_one(coordinator, lease)
    settled = coordinator.budget_state(_AUTHORITY_A)
    assert settled.terminal_count == 1
    assert settled.committed_count == 1
    assert settled.consumed_attempts == 1
    assert settled.settled is True


def test_definite_start_failure_aborts_without_consumption_and_releases_capacity() -> None:
    coordinator = _coordinator(attempt_budget=1)
    reservation = coordinator.reserve(_command(attempt_budget=1))
    permit = coordinator.begin_start(_begin(reservation))

    state = coordinator.abort_reservation(
        AbortFactoryPhysicalAttemptReservationV1(
            schema_version=ABORT_FACTORY_PHYSICAL_ATTEMPT_RESERVATION_SCHEMA,
            reservation=reservation,
            start_permit=permit,
            definite_start_not_persisted_proof=_definite_start_absent_proof(permit),
        )
    )

    assert state.aborted_count == 1
    assert state.consumed_attempts == 0
    assert state.remaining_attempts == 1
    replacement_command = _command(attempt_budget=1, call_id="replacement")
    _register_command_cutoff(coordinator, replacement_command)
    replacement = coordinator.reserve(replacement_command)
    assert replacement.authority_attempt_ordinal == 2


def test_ambiguous_start_freezes_admission_and_blocks_drain() -> None:
    coordinator = _coordinator(attempt_budget=2)
    reservation = coordinator.reserve(_command(attempt_budget=2))
    permit = coordinator.begin_start(_begin(reservation))
    state = coordinator.mark_start_ambiguous(
        MarkFactoryPhysicalAttemptStartAmbiguousV1(
            schema_version=MARK_FACTORY_PHYSICAL_ATTEMPT_START_AMBIGUOUS_SCHEMA,
            start_permit=permit,
            reason_code="start_fsync_ack_unknown",
        )
    )

    assert state.ambiguous_count == 1
    assert state.consumed_attempts == 0
    assert state.remaining_attempts == 1
    assert state.settled is False
    blocked_command = _command(attempt_budget=2, call_id="blocked")
    _register_command_cutoff(coordinator, blocked_command)
    with pytest.raises(FactoryPhysicalAttemptControlError) as raised:
        coordinator.reserve(blocked_command)
    assert raised.value.code == "factory_physical_attempt_start_commit_ambiguous"
    drain = coordinator.drain_snapshot()
    assert drain.settled is False
    assert drain.blocking_reservation_ids == (reservation.reservation_id,)


def test_terminal_persistence_failure_remains_consumed_and_blocks_drain() -> None:
    coordinator = _coordinator(attempt_budget=1)
    lease = _commit_one(coordinator, coordinator.reserve(_command(attempt_budget=1)))
    state = coordinator.terminal_persistence_failed(
        FailFactoryPhysicalAttemptTerminalV1(
            schema_version=FAIL_FACTORY_PHYSICAL_ATTEMPT_TERMINAL_SCHEMA,
            lease=lease,
            failure_code="terminal_fsync_failed",
            error_type="OSError",
            error="terminal fsync failed",
        )
    )

    assert state.committed_count == 1
    assert state.consumed_attempts == 1
    assert state.terminal_failure_count == 1
    assert state.settled is False
    assert coordinator.drain_snapshot().terminal_failure_reservation_ids == (lease.reservation_id,)
    provider_drain = coordinator.provider_drain_snapshot()
    assert provider_drain.inflight_request_ids == (lease.provider_request_id,)
    assert provider_drain.terminal_failures[0].error_type == "OSError"
    assert provider_drain.terminal_failures[0].error == "terminal fsync failed"


def test_receipt_lease_and_ordinal_substitution_fail_closed() -> None:
    coordinator = _coordinator(attempt_budget=2)
    first = coordinator.reserve(_command(attempt_budget=2))
    second_command = _command(attempt_budget=2, call_id="call-2")
    _register_command_cutoff(coordinator, second_command)
    second = coordinator.reserve(second_command)
    first_permit = coordinator.begin_start(_begin(first))
    second_permit = coordinator.begin_start(_begin(second))

    forged_receipt = replace(
        _start_receipt(first_permit),
        provider_request_id=second_permit.provider_request_id,
    )
    with pytest.raises(ValueError, match="provider_attempt_start_receipt_identity_mismatch"):
        _commit(first_permit, receipt=forged_receipt)

    first_lease = coordinator.commit_started(_commit(first_permit))
    second_lease = coordinator.commit_started(_commit(second_permit))
    with pytest.raises(ValueError, match="factory_physical_attempt_lease_start_receipt_identity_mismatch"):
        replace(first_lease, authority_attempt_ordinal=second_lease.authority_attempt_ordinal)
    assert coordinator.budget_state(_AUTHORITY_A).terminal_count == 0


def test_duplicate_lifecycle_identity_and_terminal_sequence_fail_closed() -> None:
    coordinator = _coordinator(attempt_budget=2)
    first = coordinator.reserve(_command(attempt_budget=2))
    second_command = _command(attempt_budget=2, call_id="call-2")
    _register_command_cutoff(coordinator, second_command)
    second = coordinator.reserve(second_command)
    first_permit = coordinator.begin_start(_begin(first))
    second_permit = coordinator.begin_start(_begin(second))
    first_receipt = _start_receipt(first_permit)
    first_lease = coordinator.commit_started(_commit(first_permit, receipt=first_receipt))
    duplicate_event = replace(
        _start_receipt(second_permit),
        lifecycle_event_id=first_receipt.lifecycle_event_id,
    )
    with pytest.raises(FactoryPhysicalAttemptControlError) as duplicate:
        coordinator.commit_started(_commit(second_permit, receipt=duplicate_event))
    assert duplicate.value.code == "factory_physical_attempt_duplicate_identity"

    regressing_terminal = replace(
        _terminal_receipt(first_lease),
        logical_sequence=first_receipt.logical_sequence,
    )
    with pytest.raises(FactoryPhysicalAttemptControlError) as sequence:
        coordinator.settle(
            SettleFactoryPhysicalAttemptV1(
                schema_version=SETTLE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA,
                lease=first_lease,
                terminal_receipt=regressing_terminal,
            )
        )
    assert sequence.value.code == "factory_physical_attempt_terminal_unknown"
    assert coordinator.budget_state(_AUTHORITY_A).terminal_count == 0


def test_close_and_revoke_abort_plain_reserved_but_allow_started_resolution() -> None:
    coordinator = _coordinator(attempt_budget=3)
    reserved = coordinator.reserve(_command(attempt_budget=3))
    started_command = _command(attempt_budget=3, call_id="started")
    _register_command_cutoff(coordinator, started_command)
    started = coordinator.reserve(started_command)
    permit = coordinator.begin_start(_begin(started))
    revoked = Event()
    states: list[object] = []

    def revoke() -> None:
        states.append(coordinator.revoke_grant(_AUTHORITY_A))
        revoked.set()

    thread = Thread(target=revoke)
    thread.start()
    assert revoked.wait(0.05) is False
    state = coordinator.budget_state(_AUTHORITY_A)
    assert state.revoked is True
    assert state.aborted_count == 1
    assert state.start_persisting_count == 1
    with pytest.raises(FactoryPhysicalAttemptControlError) as raised:
        coordinator.reserve(_command(attempt_budget=3, call_id="after-revoke"))
    assert raised.value.code == "factory_physical_attempt_grant_revoked"

    coordinator.abort_reservation(
        AbortFactoryPhysicalAttemptReservationV1(
            schema_version=ABORT_FACTORY_PHYSICAL_ATTEMPT_RESERVATION_SCHEMA,
            reservation=started,
            start_permit=permit,
            definite_start_not_persisted_proof=_definite_start_absent_proof(permit),
        )
    )
    assert revoked.wait(1.0) is True
    thread.join(timeout=1.0)
    assert states
    assert coordinator.budget_state(_AUTHORITY_A).settled is True
    assert reserved.reservation_id != started.reservation_id


def test_revoke_race_has_no_live_reserved_descendants() -> None:
    coordinator = _coordinator(attempt_budget=32)
    barrier = Barrier(33)

    def reserve(index: int) -> str:
        barrier.wait()
        try:
            coordinator.reserve(_command(call_id=f"call-{index}"))
        except FactoryPhysicalAttemptControlError as exc:
            return exc.code
        return "reserved"

    def revoke() -> str:
        barrier.wait()
        coordinator.revoke_grant(_AUTHORITY_A)
        return "revoked"

    with ThreadPoolExecutor(max_workers=33) as executor:
        futures = [executor.submit(reserve, index) for index in range(32)]
        futures.append(executor.submit(revoke))
        outcomes = tuple(future.result() for future in futures)

    state = coordinator.budget_state(_AUTHORITY_A)
    assert "revoked" in outcomes
    assert state.revoked is True
    assert state.reserved_count == 0
    assert state.consumed_attempts == 0
    assert state.settled is True


def test_close_run_isolated_from_another_run_coordinator() -> None:
    first = _coordinator(attempt_budget=1)
    second = _coordinator(factory_run_id="factory-run-2", attempt_budget=1)
    first.reserve(_command(attempt_budget=1))
    second_reservation = second.reserve(_command(factory_run_id="factory-run-2", attempt_budget=1))

    first.close()

    assert first.budget_state(_AUTHORITY_A).closed is True
    assert first.budget_state(_AUTHORITY_A).aborted_count == 1
    assert second.budget_state(_AUTHORITY_A).reserved_count == 1
    assert second_reservation.factory_run_id == "factory-run-2"


def test_coordinator_computes_ordinal_bound_composite_hash() -> None:
    coordinator = _coordinator(attempt_budget=2)
    command = _command(attempt_budget=2)

    first = coordinator.reserve(command)
    second = coordinator.reserve(command)

    expected = canonical_role_final_request_hash(
        {
            "schema_version": "factory.physical_attempt.composite_request.v1",
            "verification_scope": "factory",
            "factory_run_id": "factory-run-1",
            "run_id": "role-run-1",
            "role": "director",
            "turn_id": "turn-1",
            "call_id": "call-1",
            "request_freeze_id": "freeze-1",
            "stage": "director_dispatch",
            "workspace_fencing_token": 7,
            "stage_claim_attempt": 2,
            "stage_claim_nonce": "stage-nonce-1",
            "execution_authority_hash": _AUTHORITY_A,
            "attempt_budget": 2,
            "provider": "test-provider",
            "model": "test-model",
            "semantic_request_hash": _SEMANTIC,
            "physical_wire_hash": _WIRE,
            "authority_attempt_ordinal": 1,
        }
    )
    assert first.composite_request_hash == expected
    assert first.composite_request_hash != second.composite_request_hash
    assert (first.authority_attempt_ordinal, second.authority_attempt_ordinal) == (1, 2)


@pytest.mark.parametrize(
    "grant_override",
    [
        {"stage": "qa"},
        {"workspace_fencing_token": 8},
        {"stage_claim_attempt": 3},
        {"stage_claim_nonce": "forged-nonce"},
        {"attempt_budget": 31},
    ],
)
def test_cutoff_rejects_forged_grant_view(grant_override: dict[str, object]) -> None:
    coordinator = FactoryPhysicalAttemptCoordinator(factory_run_id="factory-run-1")
    grant = _grant_view()
    coordinator.register_grant(grant)

    with pytest.raises(FactoryPhysicalAttemptControlError) as raised:
        coordinator.register_cutoff(_cutoff_view(grant=replace(grant, **grant_override)))
    assert raised.value.code == "factory_physical_attempt_grant_view_mismatch"


def test_grant_and_cutoff_registration_require_exact_types() -> None:
    class _GrantSubclass(FactoryPhysicalAttemptGrantViewV1):
        pass

    grant = _grant_view()
    coordinator = FactoryPhysicalAttemptCoordinator(factory_run_id="factory-run-1")
    forged = _GrantSubclass(**_kwargs(grant))
    with pytest.raises(FactoryPhysicalAttemptControlError) as grant_error:
        coordinator.register_grant(forged)
    assert grant_error.value.code == "factory_physical_attempt_grant_view_exact_type_required"

    coordinator.register_grant(grant)
    with pytest.raises(FactoryPhysicalAttemptControlError) as cutoff_error:
        coordinator.register_cutoff(cast(Any, object()))
    assert cutoff_error.value.code == "factory_physical_attempt_cutoff_view_exact_type_required"


@pytest.mark.parametrize(
    "override",
    [
        {"turn_id": "turn-forged"},
        {"call_id": "call-forged"},
        {"provider": "forged-provider"},
        {"model": "forged-model"},
        {"semantic_request_hash": "2" * 64},
        {"physical_wire_hash": "3" * 64},
    ],
)
def test_reserve_rejects_cutoff_request_substitution(override: dict[str, object]) -> None:
    coordinator = _coordinator()
    with pytest.raises(FactoryPhysicalAttemptControlError) as raised:
        coordinator.reserve(cast(Any, _command)(**override))
    assert raised.value.code == "factory_physical_attempt_cutoff_view_mismatch"
    assert coordinator.budget_state(_AUTHORITY_A).reserved_count == 0


def test_start_persisting_abort_rejects_reservation_only_and_forged_proof() -> None:
    coordinator = _coordinator(attempt_budget=1)
    reservation = coordinator.reserve(_command(attempt_budget=1))
    permit = coordinator.begin_start(_begin(reservation))

    reservation_only = AbortFactoryPhysicalAttemptReservationV1(
        schema_version=ABORT_FACTORY_PHYSICAL_ATTEMPT_RESERVATION_SCHEMA,
        reservation=reservation,
        start_permit=None,
        definite_start_not_persisted_proof=None,
    )
    with pytest.raises(FactoryPhysicalAttemptControlError) as absent:
        coordinator.abort_reservation(reservation_only)
    assert absent.value.code == "factory_physical_attempt_definite_start_proof_mismatch"

    forged_permit = replace(permit, call_id="forged-call")
    forged_proof = _definite_start_absent_proof(forged_permit)
    with pytest.raises(ValueError, match="abort_factory_physical_attempt_start_permit_identity_mismatch"):
        AbortFactoryPhysicalAttemptReservationV1(
            schema_version=ABORT_FACTORY_PHYSICAL_ATTEMPT_RESERVATION_SCHEMA,
            reservation=reservation,
            start_permit=forged_permit,
            definite_start_not_persisted_proof=forged_proof,
        )


def test_ambiguous_start_cannot_be_reclassified_by_ordinary_abort() -> None:
    coordinator = _coordinator(attempt_budget=1)
    reservation = coordinator.reserve(_command(attempt_budget=1))
    permit = coordinator.begin_start(_begin(reservation))
    coordinator.mark_start_ambiguous(
        MarkFactoryPhysicalAttemptStartAmbiguousV1(
            schema_version=MARK_FACTORY_PHYSICAL_ATTEMPT_START_AMBIGUOUS_SCHEMA,
            start_permit=permit,
            reason_code="durability_unknown",
        )
    )

    with pytest.raises(FactoryPhysicalAttemptControlError) as raised:
        coordinator.abort_reservation(
            AbortFactoryPhysicalAttemptReservationV1(
                schema_version=ABORT_FACTORY_PHYSICAL_ATTEMPT_RESERVATION_SCHEMA,
                reservation=reservation,
                start_permit=permit,
                definite_start_not_persisted_proof=_definite_start_absent_proof(permit),
            )
        )
    assert raised.value.code == "factory_physical_attempt_reservation_state_conflict"


@pytest.mark.parametrize("operation", ["revoke_grant", "close_grant", "close"])
def test_close_barrier_waits_for_begin_start_winner_to_reach_terminal(operation: str) -> None:
    coordinator = _coordinator(attempt_budget=1)
    reservation = coordinator.reserve(_command(attempt_budget=1))
    permit = coordinator.begin_start(_begin(reservation))
    returned = Event()

    def close_operation() -> None:
        if operation == "close":
            coordinator.close()
        else:
            getattr(coordinator, operation)(_AUTHORITY_A)
        returned.set()

    thread = Thread(target=close_operation)
    thread.start()
    assert returned.wait(0.05) is False
    lease = coordinator.commit_started(_commit(permit))
    assert returned.wait(0.05) is False
    _settle_one(coordinator, lease)
    assert returned.wait(1.0) is True
    thread.join(timeout=1.0)
    assert coordinator.budget_state(_AUTHORITY_A).terminal_count == 1


@pytest.mark.parametrize("field_name", ["lifecycle_event_id", "logical_sequence", "event_hash"])
def test_start_and_terminal_lifecycle_identity_namespace_is_unified(field_name: str) -> None:
    coordinator = _coordinator(attempt_budget=2)
    first = coordinator.reserve(_command(attempt_budget=2))
    second_command = _command(attempt_budget=2, call_id="call-2")
    _register_command_cutoff(coordinator, second_command)
    second = coordinator.reserve(second_command)
    first_lease = _commit_one(coordinator, first)
    second_permit = coordinator.begin_start(_begin(second))
    second_receipt = _start_receipt(second_permit)
    coordinator.commit_started(_commit(second_permit, receipt=second_receipt))
    terminal = _terminal_receipt(first_lease)
    terminal = replace(terminal, **{field_name: getattr(second_receipt, field_name)})

    with pytest.raises(FactoryPhysicalAttemptControlError) as raised:
        coordinator.settle(
            SettleFactoryPhysicalAttemptV1(
                schema_version=SETTLE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA,
                lease=first_lease,
                terminal_receipt=terminal,
            )
        )
    assert raised.value.code == "factory_physical_attempt_duplicate_identity"


def test_commit_and_settle_are_one_shot() -> None:
    coordinator = _coordinator(attempt_budget=1)
    reservation = coordinator.reserve(_command(attempt_budget=1))
    permit = coordinator.begin_start(_begin(reservation))
    command = _commit(permit)
    lease = coordinator.commit_started(command)
    with pytest.raises(FactoryPhysicalAttemptControlError) as duplicate_commit:
        coordinator.commit_started(command)
    assert duplicate_commit.value.code == "factory_physical_attempt_reservation_state_conflict"

    settle = SettleFactoryPhysicalAttemptV1(
        schema_version=SETTLE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA,
        lease=lease,
        terminal_receipt=_terminal_receipt(lease),
    )
    coordinator.settle(settle)
    with pytest.raises(FactoryPhysicalAttemptControlError) as duplicate_settle:
        coordinator.settle(settle)
    assert duplicate_settle.value.code == "factory_physical_attempt_terminal_unknown"


@pytest.mark.parametrize(
    "method_name",
    [
        "reserve",
        "begin_start",
        "commit_started",
        "abort_reservation",
        "mark_start_ambiguous",
        "settle",
        "terminal_persistence_failed",
    ],
)
def test_control_methods_reject_wrong_command_exact_type(method_name: str) -> None:
    coordinator = _coordinator()
    with pytest.raises(FactoryPhysicalAttemptControlError) as raised:
        getattr(coordinator, method_name)(cast(Any, object()))
    assert raised.value.code == "factory_physical_attempt_control_command_exact_type_required"
