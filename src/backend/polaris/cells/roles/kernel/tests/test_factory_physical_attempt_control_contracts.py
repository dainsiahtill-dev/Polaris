"""B3.4 public physical-attempt control contract tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from typing import Any

import pytest
from polaris.cells.roles.kernel.public.physical_attempt_control import (
    ABORT_FACTORY_PHYSICAL_ATTEMPT_RESERVATION_SCHEMA,
    BEGIN_FACTORY_PHYSICAL_ATTEMPT_START_SCHEMA,
    COMMIT_FACTORY_PHYSICAL_ATTEMPT_START_SCHEMA,
    FACTORY_PHYSICAL_ATTEMPT_CUTOFF_VIEW_SCHEMA,
    FACTORY_PHYSICAL_ATTEMPT_DEFINITE_START_NOT_PERSISTED_PROOF_SCHEMA,
    FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
    FACTORY_PHYSICAL_ATTEMPT_LEASE_SCHEMA,
    FACTORY_PHYSICAL_ATTEMPT_RESERVATION_SCHEMA,
    FACTORY_PHYSICAL_ATTEMPT_START_PERMIT_SCHEMA,
    FAIL_FACTORY_PHYSICAL_ATTEMPT_TERMINAL_SCHEMA,
    MARK_FACTORY_PHYSICAL_ATTEMPT_START_AMBIGUOUS_SCHEMA,
    PROVIDER_ATTEMPT_START_RECEIPT_SCHEMA,
    PROVIDER_ATTEMPT_TERMINAL_RECEIPT_SCHEMA,
    RESERVE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA,
    SETTLE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA,
    AbortFactoryPhysicalAttemptReservationV1,
    BeginFactoryPhysicalAttemptStartV1,
    CommitFactoryPhysicalAttemptStartV1,
    FactoryPhysicalAttemptBudgetStateV1,
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

_HASH_A = "a" * 64
_HASH_B = "b" * 64
_HASH_C = "c" * 64
_HASH_D = "d" * 64


def _reserve(**overrides: object) -> ReserveFactoryPhysicalAttemptV1:
    values: dict[str, object] = {
        "schema_version": RESERVE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA,
        "verification_scope": "factory",
        "factory_run_id": "factory-run-1",
        "run_id": "role-run-1",
        "role": "director",
        "turn_id": "turn-1",
        "call_id": "call-1",
        "request_freeze_id": "freeze-1",
        "execution_authority_hash": _HASH_A,
        "attempt_budget": 32,
        "provider": "test-provider",
        "model": "test-model",
        "semantic_request_hash": _HASH_B,
        "physical_wire_hash": _HASH_C,
    }
    values.update(overrides)
    return ReserveFactoryPhysicalAttemptV1(**values)  # type: ignore[arg-type]


def _reservation(**overrides: object) -> FactoryPhysicalAttemptReservationV1:
    values = {
        **{field.name: getattr(_reserve(), field.name) for field in fields(ReserveFactoryPhysicalAttemptV1)},
        "schema_version": FACTORY_PHYSICAL_ATTEMPT_RESERVATION_SCHEMA,
        "reservation_id": "reservation-1",
        "provider_request_id": "provider-request-1",
        "authority_attempt_ordinal": 1,
        "composite_request_hash": _HASH_D,
    }
    values.update(overrides)
    return FactoryPhysicalAttemptReservationV1(**values)  # type: ignore[arg-type]


def _begin(**overrides: object) -> BeginFactoryPhysicalAttemptStartV1:
    values = {
        **{field.name: getattr(_reservation(), field.name) for field in fields(FactoryPhysicalAttemptReservationV1)},
        "schema_version": BEGIN_FACTORY_PHYSICAL_ATTEMPT_START_SCHEMA,
    }
    values.update(overrides)
    return BeginFactoryPhysicalAttemptStartV1(**values)  # type: ignore[arg-type]


def _permit(**overrides: object) -> FactoryPhysicalAttemptStartPermitV1:
    values = {
        **{field.name: getattr(_reservation(), field.name) for field in fields(FactoryPhysicalAttemptReservationV1)},
        "schema_version": FACTORY_PHYSICAL_ATTEMPT_START_PERMIT_SCHEMA,
        "start_permit_id": "start-permit-1",
    }
    values.update(overrides)
    return FactoryPhysicalAttemptStartPermitV1(**values)  # type: ignore[arg-type]


def _start_receipt(**overrides: object) -> ProviderAttemptStartReceiptV1:
    values = {
        **{field.name: getattr(_permit(), field.name) for field in fields(FactoryPhysicalAttemptStartPermitV1)},
        "schema_version": PROVIDER_ATTEMPT_START_RECEIPT_SCHEMA,
        "lifecycle_event_id": "start-event-1",
        "logical_sequence": 1,
        "event_hash": _HASH_A,
        "phase": "start",
        "durability_acked": True,
    }
    values.update(overrides)
    return ProviderAttemptStartReceiptV1(**values)  # type: ignore[arg-type]


def _commit(**overrides: object) -> CommitFactoryPhysicalAttemptStartV1:
    values = {
        **{field.name: getattr(_permit(), field.name) for field in fields(FactoryPhysicalAttemptStartPermitV1)},
        "schema_version": COMMIT_FACTORY_PHYSICAL_ATTEMPT_START_SCHEMA,
        "start_receipt": _start_receipt(),
    }
    values.update(overrides)
    return CommitFactoryPhysicalAttemptStartV1(**values)  # type: ignore[arg-type]


def _lease(**overrides: object) -> FactoryPhysicalAttemptLeaseV1:
    values = {
        **{field.name: getattr(_permit(), field.name) for field in fields(FactoryPhysicalAttemptStartPermitV1)},
        "schema_version": FACTORY_PHYSICAL_ATTEMPT_LEASE_SCHEMA,
        "lease_id": "lease-1",
        "start_receipt": _start_receipt(),
    }
    values.update(overrides)
    return FactoryPhysicalAttemptLeaseV1(**values)  # type: ignore[arg-type]


def _terminal_receipt(**overrides: object) -> ProviderAttemptTerminalReceiptV1:
    values = {
        **{field.name: getattr(_lease(), field.name) for field in fields(FactoryPhysicalAttemptLeaseV1)},
        "schema_version": PROVIDER_ATTEMPT_TERMINAL_RECEIPT_SCHEMA,
        "lifecycle_event_id": "terminal-event-1",
        "logical_sequence": 2,
        "event_hash": _HASH_B,
        "phase": "terminal",
        "durability_acked": True,
        "terminal_status": "completed",
    }
    values.pop("start_receipt")
    values.update(overrides)
    return ProviderAttemptTerminalReceiptV1(**values)  # type: ignore[arg-type]


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
        "execution_authority_hash": _HASH_A,
        "attempt_budget": 32,
    }
    values.update(overrides)
    return FactoryPhysicalAttemptGrantViewV1(**values)  # type: ignore[arg-type]


def _cutoff_view(**overrides: object) -> FactoryPhysicalAttemptCutoffViewV1:
    values: dict[str, object] = {
        "schema_version": FACTORY_PHYSICAL_ATTEMPT_CUTOFF_VIEW_SCHEMA,
        "grant": _grant_view(),
        "run_id": "role-run-1",
        "turn_id": "turn-1",
        "call_id": "call-1",
        "request_freeze_id": "freeze-1",
        "provider": "test-provider",
        "model": "test-model",
        "semantic_request_hash": _HASH_B,
        "physical_wire_hash": _HASH_C,
    }
    values.update(overrides)
    return FactoryPhysicalAttemptCutoffViewV1(**values)  # type: ignore[arg-type]


def _definite_start_absent_proof(
    permit: FactoryPhysicalAttemptStartPermitV1 | None = None,
    **overrides: object,
) -> FactoryPhysicalAttemptDefiniteStartNotPersistedProofV1:
    values: dict[str, object] = {
        "schema_version": FACTORY_PHYSICAL_ATTEMPT_DEFINITE_START_NOT_PERSISTED_PROOF_SCHEMA,
        "start_permit": permit or _permit(),
        "proof_id": "start-absent-proof-1",
        "lifecycle_head_sequence": 11,
        "lifecycle_head_hash": _HASH_D,
        "proof_kind": "definite_start_not_persisted",
        "durability_acked": True,
    }
    values.update(overrides)
    return FactoryPhysicalAttemptDefiniteStartNotPersistedProofV1(**values)  # type: ignore[arg-type]


def test_commands_and_results_are_frozen_exact_identity_contracts() -> None:
    reserve = _reserve()
    reservation = _reservation()
    permit = _permit()
    receipt = _start_receipt()
    lease = _lease()
    terminal = _terminal_receipt()

    assert reservation.factory_run_id == reserve.factory_run_id
    assert permit.provider_request_id == reservation.provider_request_id
    assert receipt.start_permit_id == permit.start_permit_id
    assert lease.start_receipt == receipt
    assert terminal.lease_id == lease.lease_id
    assert terminal.terminal_status == "completed"
    with pytest.raises(FrozenInstanceError):
        reservation.attempt_budget = 1  # type: ignore[misc]


def test_factory_owned_grant_and_cutoff_views_bind_complete_authority() -> None:
    grant = _grant_view()
    cutoff = _cutoff_view(grant=grant)

    assert type(cutoff.grant) is FactoryPhysicalAttemptGrantViewV1
    assert cutoff.grant.stage == "director_dispatch"
    assert cutoff.grant.workspace_fencing_token == 7
    assert cutoff.grant.stage_claim_attempt == 2
    assert cutoff.grant.stage_claim_nonce == "stage-nonce-1"
    assert cutoff.run_id == "role-run-1"
    assert cutoff.provider == "test-provider"
    assert cutoff.semantic_request_hash == _HASH_B


@pytest.mark.parametrize(
    ("factory", "override", "error_type", "error"),
    [
        (_reserve, {"verification_scope": "ordinary"}, ValueError, "verification_scope_mismatch"),
        (_reserve, {"factory_run_id": ""}, ValueError, "factory_run_id_missing"),
        (_reserve, {"role": "unknown"}, ValueError, "role_final_request_policy_unknown_role"),
        (_reserve, {"attempt_budget": True}, TypeError, "attempt_budget_type_invalid"),
        (_reserve, {"attempt_budget": 0}, ValueError, "attempt_budget_invalid"),
        (_reserve, {"execution_authority_hash": "A" * 64}, ValueError, "execution_authority_hash_invalid"),
        (_reserve, {"semantic_request_hash": 1}, TypeError, "semantic_request_hash_type_invalid"),
        (_reservation, {"authority_attempt_ordinal": True}, TypeError, "authority_attempt_ordinal_type_invalid"),
        (_reservation, {"authority_attempt_ordinal": 0}, ValueError, "authority_attempt_ordinal_invalid"),
        (_start_receipt, {"durability_acked": False}, ValueError, "durability_ack_required"),
        (_start_receipt, {"phase": "terminal"}, ValueError, "provider_attempt_start_receipt_phase_mismatch"),
        (_terminal_receipt, {"logical_sequence": True}, TypeError, "logical_sequence_type_invalid"),
        (_terminal_receipt, {"terminal_status": "success"}, ValueError, "terminal_status_invalid"),
        (_terminal_receipt, {"terminal_status": "unknown"}, ValueError, "terminal_status_invalid"),
        (_grant_view, {"workspace_fencing_token": True}, TypeError, "workspace_fencing_token_type_invalid"),
        (_grant_view, {"stage_claim_attempt": 0}, ValueError, "stage_claim_attempt_invalid"),
    ],
)
def test_contracts_reject_coercion_and_malformed_identity(
    factory: object,
    override: dict[str, object],
    error_type: type[Exception],
    error: str,
) -> None:
    with pytest.raises(error_type, match=error):
        factory(**override)  # type: ignore[operator]


def test_commands_require_exact_nested_receipt_types() -> None:
    class _StartReceiptSubclass(ProviderAttemptStartReceiptV1):
        pass

    receipt = _start_receipt()
    subclass = _StartReceiptSubclass(**{field.name: getattr(receipt, field.name) for field in fields(receipt)})
    with pytest.raises(TypeError, match="provider_attempt_start_receipt_exact_type_required"):
        _commit(start_receipt=subclass)

    with pytest.raises(TypeError, match="provider_attempt_terminal_receipt_exact_type_required"):
        SettleFactoryPhysicalAttemptV1(
            schema_version=SETTLE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA,
            lease=_lease(),
            terminal_receipt=object(),  # type: ignore[arg-type]
        )


def test_nested_receipt_and_lease_identity_must_exact_match() -> None:
    with pytest.raises(ValueError, match="provider_attempt_start_receipt_identity_mismatch"):
        _commit(start_receipt=replace(_start_receipt(), call_id="other-call"))
    with pytest.raises(ValueError, match="factory_physical_attempt_lease_start_receipt_identity_mismatch"):
        _lease(start_receipt=replace(_start_receipt(), authority_attempt_ordinal=2))
    with pytest.raises(ValueError, match="settle_factory_physical_attempt_identity_mismatch"):
        SettleFactoryPhysicalAttemptV1(
            schema_version=SETTLE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA,
            lease=_lease(),
            terminal_receipt=replace(_terminal_receipt(), start_permit_id="other-permit"),
        )


def test_all_control_commands_are_constructible_with_exact_nested_identity() -> None:
    reservation = _reservation()
    permit = _permit()
    lease = _lease()

    assert BeginFactoryPhysicalAttemptStartV1(**_as_kwargs(_begin())) == _begin()
    assert CommitFactoryPhysicalAttemptStartV1(**_as_kwargs(_commit())) == _commit()
    assert (
        AbortFactoryPhysicalAttemptReservationV1(
            schema_version=ABORT_FACTORY_PHYSICAL_ATTEMPT_RESERVATION_SCHEMA,
            reservation=reservation,
            start_permit=None,
            definite_start_not_persisted_proof=None,
        ).reservation
        is reservation
    )
    assert (
        MarkFactoryPhysicalAttemptStartAmbiguousV1(
            schema_version=MARK_FACTORY_PHYSICAL_ATTEMPT_START_AMBIGUOUS_SCHEMA,
            start_permit=permit,
            reason_code="fsync_ack_unknown",
        ).start_permit
        is permit
    )
    assert (
        SettleFactoryPhysicalAttemptV1(
            schema_version=SETTLE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA,
            lease=lease,
            terminal_receipt=_terminal_receipt(),
        ).lease
        is lease
    )
    assert (
        FailFactoryPhysicalAttemptTerminalV1(
            schema_version=FAIL_FACTORY_PHYSICAL_ATTEMPT_TERMINAL_SCHEMA,
            lease=lease,
            failure_code="terminal_fsync_failed",
            error_type="OSError",
            error="terminal fsync failed",
        ).lease
        is lease
    )


def _as_kwargs(value: Any) -> dict[str, object]:
    return {field.name: getattr(value, field.name) for field in fields(value)}


class _ControlPort:
    def reserve(self, command: ReserveFactoryPhysicalAttemptV1) -> FactoryPhysicalAttemptReservationV1:
        raise NotImplementedError

    def begin_start(self, command: BeginFactoryPhysicalAttemptStartV1) -> FactoryPhysicalAttemptStartPermitV1:
        raise NotImplementedError

    def commit_started(self, command: CommitFactoryPhysicalAttemptStartV1) -> FactoryPhysicalAttemptLeaseV1:
        raise NotImplementedError

    def abort_reservation(
        self, command: AbortFactoryPhysicalAttemptReservationV1
    ) -> FactoryPhysicalAttemptBudgetStateV1:
        raise NotImplementedError

    def mark_start_ambiguous(
        self, command: MarkFactoryPhysicalAttemptStartAmbiguousV1
    ) -> FactoryPhysicalAttemptBudgetStateV1:
        raise NotImplementedError

    def settle(self, command: SettleFactoryPhysicalAttemptV1) -> FactoryPhysicalAttemptBudgetStateV1:
        raise NotImplementedError

    def terminal_persistence_failed(
        self, command: FailFactoryPhysicalAttemptTerminalV1
    ) -> FactoryPhysicalAttemptBudgetStateV1:
        raise NotImplementedError


def test_control_port_is_runtime_checkable_and_has_only_locked_sync_methods() -> None:
    assert isinstance(_ControlPort(), FactoryPhysicalAttemptControlPort)
    assert {name for name in FactoryPhysicalAttemptControlPort.__dict__ if not name.startswith("_")} == {
        "reserve",
        "begin_start",
        "commit_started",
        "abort_reservation",
        "mark_start_ambiguous",
        "settle",
        "terminal_persistence_failed",
    }


def test_budget_state_rejects_inconsistent_state_derived_totals() -> None:
    state = FactoryPhysicalAttemptBudgetStateV1(
        schema_version="factory.physical_attempt_budget_state.v1",
        factory_run_id="factory-run-1",
        execution_authority_hash=_HASH_A,
        attempt_budget=32,
        registered=True,
        revoked=False,
        closed=False,
        reserved_count=2,
        start_persisting_count=1,
        ambiguous_count=0,
        committed_count=3,
        recovered_count=0,
        terminal_count=2,
        aborted_count=1,
        terminal_failure_count=0,
        consumed_attempts=3,
        remaining_attempts=27,
        inflight_count=3,
        settled=False,
    )
    assert state.remaining_attempts == 27
    with pytest.raises(ValueError, match="consumed_attempts_inconsistent"):
        replace(state, consumed_attempts=2)
    with pytest.raises(ValueError, match="remaining_attempts_inconsistent"):
        replace(state, remaining_attempts=28)
    with pytest.raises(ValueError, match="recovered_count_inconsistent"):
        replace(state, recovered_count=4)
    with pytest.raises(ValueError, match="budget_state_overcommitted"):
        replace(
            state,
            reserved_count=31,
            start_persisting_count=0,
            committed_count=3,
            consumed_attempts=3,
            remaining_attempts=0,
            inflight_count=34,
        )
    with pytest.raises(ValueError, match="terminal_outcome_count_inconsistent"):
        replace(
            state,
            committed_count=3,
            terminal_count=2,
            terminal_failure_count=2,
            consumed_attempts=3,
            remaining_attempts=27,
            inflight_count=3,
        )


def test_start_persisting_abort_requires_exact_permit_and_definite_absence_proof() -> None:
    permit = _permit()
    proof = _definite_start_absent_proof(permit)
    command = AbortFactoryPhysicalAttemptReservationV1(
        schema_version=ABORT_FACTORY_PHYSICAL_ATTEMPT_RESERVATION_SCHEMA,
        reservation=_reservation(),
        start_permit=permit,
        definite_start_not_persisted_proof=proof,
    )
    assert command.definite_start_not_persisted_proof is proof
    with pytest.raises(TypeError, match="factory_physical_attempt_definite_start_proof_exact_type_required"):
        AbortFactoryPhysicalAttemptReservationV1(
            schema_version=ABORT_FACTORY_PHYSICAL_ATTEMPT_RESERVATION_SCHEMA,
            reservation=_reservation(),
            start_permit=permit,
            definite_start_not_persisted_proof=object(),  # type: ignore[arg-type]
        )
