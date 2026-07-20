"""Factory-owned B3.4 physical-attempt admission state machine.

This prerequisite coordinator is deliberately process-local and performs no
I/O.  FactoryRunService/binding injection, lifecycle persistence, transport,
replay, and B3.5 qualification remain separate follow-up slices.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field, fields
from enum import Enum

from polaris.cells.roles.kernel.public.physical_attempt_control import (
    FACTORY_PHYSICAL_ATTEMPT_BUDGET_STATE_SCHEMA,
    FACTORY_PHYSICAL_ATTEMPT_CUTOFF_VIEW_SCHEMA,
    FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
    FACTORY_PHYSICAL_ATTEMPT_LEASE_SCHEMA,
    FACTORY_PHYSICAL_ATTEMPT_RESERVATION_SCHEMA,
    FACTORY_PHYSICAL_ATTEMPT_START_PERMIT_SCHEMA,
    PROVIDER_ATTEMPT_START_RECEIPT_SCHEMA,
    PROVIDER_ATTEMPT_TERMINAL_RECEIPT_SCHEMA,
    AbortFactoryPhysicalAttemptReservationV1,
    BeginFactoryPhysicalAttemptStartV1,
    CommitFactoryPhysicalAttemptStartV1,
    FactoryPhysicalAttemptBudgetStateV1,
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
from polaris.cells.roles.kernel.public.provider_attempt_lifecycle_replay import (
    factory_provider_attempt_recovery_lease_id,
)
from polaris.kernelone.events.final_request_evidence import canonical_role_final_request_hash
from polaris.kernelone.llm.engine.contracts import (
    FrozenFinalProviderAttemptV1,
    ProviderAttemptDrainError,
    ProviderAttemptDrainResultV1,
    ProviderAttemptTerminalFailureV1,
)

_HASH_LENGTH = 64
_RESERVATION_IDENTITY_FIELDS = tuple(
    field_info.name for field_info in fields(FactoryPhysicalAttemptReservationV1) if field_info.name != "schema_version"
)
_START_PERMIT_IDENTITY_FIELDS = tuple(
    field_info.name for field_info in fields(FactoryPhysicalAttemptStartPermitV1) if field_info.name != "schema_version"
)
_LEASE_IDENTITY_FIELDS = tuple(
    field_info.name
    for field_info in fields(FactoryPhysicalAttemptLeaseV1)
    if field_info.name not in {"schema_version", "start_receipt"}
)
_CUTOFF_COMMAND_FIELDS = (
    "run_id",
    "turn_id",
    "call_id",
    "request_freeze_id",
    "provider",
    "model",
    "semantic_request_hash",
    "physical_wire_hash",
)
_TERMINAL_STATES = frozenset(
    {
        "ABORTED",
        "TERMINAL_ACKED",
        "TERMINAL_PERSISTENCE_FAILED",
    }
)


class FactoryPhysicalAttemptControlError(RuntimeError):
    """Stable fail-closed B3.4 admission error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _ReservationState(str, Enum):
    RESERVED = "RESERVED"
    START_PERSISTING = "START_PERSISTING"
    START_AMBIGUOUS = "START_AMBIGUOUS"
    START_COMMITTED = "START_COMMITTED"
    RECOVERED_START_ABORTING = "RECOVERED_START_ABORTING"
    ABORTED = "ABORTED"
    TERMINAL_ACKED = "TERMINAL_ACKED"
    TERMINAL_PERSISTENCE_FAILED = "TERMINAL_PERSISTENCE_FAILED"


@dataclass(slots=True)
class _ReservationRecord:
    reservation: FactoryPhysicalAttemptReservationV1
    state: _ReservationState = _ReservationState.RESERVED
    start_permit: FactoryPhysicalAttemptStartPermitV1 | None = None
    start_receipt: ProviderAttemptStartReceiptV1 | None = None
    lease: FactoryPhysicalAttemptLeaseV1 | None = None
    terminal_receipt: ProviderAttemptTerminalReceiptV1 | None = None
    recovered: bool = False
    failure_code: str = ""
    failure_error_type: str = ""
    failure_error: str = ""


@dataclass(slots=True)
class _GrantState:
    grant: FactoryPhysicalAttemptGrantViewV1
    cutoffs: dict[tuple[object, ...], FactoryPhysicalAttemptCutoffViewV1] = field(default_factory=dict)
    next_ordinal: int = 0
    revoked: bool = False
    closed: bool = False
    reservations: dict[str, _ReservationRecord] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FactoryPhysicalAttemptRunDrainSnapshot:
    """Immutable process-local structural drain view for one Factory run."""

    factory_run_id: str
    by_authority: tuple[FactoryPhysicalAttemptBudgetStateV1, ...]
    blocking_reservation_ids: tuple[str, ...]
    terminal_failure_reservation_ids: tuple[str, ...]
    settled: bool


@dataclass(frozen=True, slots=True)
class FactoryPhysicalAttemptRecoveryTerminalWorkV1:
    """One replay-fenced cancelled terminal append; never a dispatch lease."""

    attempt: FrozenFinalProviderAttemptV1
    lease: FactoryPhysicalAttemptLeaseV1
    context_snapshot_ref: str
    pin_hash: str

    def __post_init__(self) -> None:
        if type(self.attempt) is not FrozenFinalProviderAttemptV1:
            raise TypeError("frozen_final_provider_attempt_exact_type_required")
        FrozenFinalProviderAttemptV1.__post_init__(self.attempt)
        if type(self.lease) is not FactoryPhysicalAttemptLeaseV1:
            raise TypeError("factory_physical_attempt_lease_exact_type_required")
        FactoryPhysicalAttemptLeaseV1.__post_init__(self.lease)
        if (
            self.attempt.provider_request_id != self.lease.provider_request_id
            or self.attempt.composite_request_hash != self.lease.composite_request_hash
        ):
            raise ValueError("factory_physical_attempt_recovery_work_identity_mismatch")
        if len(self.context_snapshot_ref) != 24 or any(
            character not in "0123456789abcdef" for character in self.context_snapshot_ref
        ):
            raise ValueError("context_snapshot_ref_invalid")
        _hash64("pin_hash", self.pin_hash)


def _identifier(field_name: str, value: object) -> str:
    if type(value) is not str:
        raise TypeError(f"{field_name}_type_invalid")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name}_missing")
    return normalized


def _hash64(field_name: str, value: object) -> str:
    if type(value) is not str:
        raise TypeError(f"{field_name}_type_invalid")
    if len(value) != _HASH_LENGTH or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field_name}_invalid")
    return value


def _identity_equal(left: object, right: object, names: tuple[str, ...]) -> bool:
    return all(getattr(left, name, None) == getattr(right, name, None) for name in names)


def _cutoff_key(value: object) -> tuple[object, ...]:
    return tuple(getattr(value, field_name, None) for field_name in _CUTOFF_COMMAND_FIELDS)


def canonical_factory_physical_attempt_composite_hash(
    cutoff: FactoryPhysicalAttemptCutoffViewV1,
    ordinal: int,
) -> str:
    """Factory-owned canonical binding for one physical attempt candidate."""

    if type(cutoff) is not FactoryPhysicalAttemptCutoffViewV1:
        raise TypeError("factory_physical_attempt_cutoff_view_exact_type_required")
    FactoryPhysicalAttemptCutoffViewV1.__post_init__(cutoff)
    if type(ordinal) is not int or ordinal <= 0:
        raise ValueError("authority_attempt_ordinal_invalid")
    grant = cutoff.grant
    return canonical_role_final_request_hash(
        {
            "schema_version": "factory.physical_attempt.composite_request.v1",
            "verification_scope": grant.verification_scope,
            "factory_run_id": grant.factory_run_id,
            "run_id": cutoff.run_id,
            "role": grant.role,
            "turn_id": cutoff.turn_id,
            "call_id": cutoff.call_id,
            "request_freeze_id": cutoff.request_freeze_id,
            "stage": grant.stage,
            "workspace_fencing_token": grant.workspace_fencing_token,
            "stage_claim_attempt": grant.stage_claim_attempt,
            "stage_claim_nonce": grant.stage_claim_nonce,
            "execution_authority_hash": grant.execution_authority_hash,
            "attempt_budget": grant.attempt_budget,
            "provider": cutoff.provider,
            "model": cutoff.model,
            "semantic_request_hash": cutoff.semantic_request_hash,
            "physical_wire_hash": cutoff.physical_wire_hash,
            "authority_attempt_ordinal": ordinal,
        }
    )


class FactoryPhysicalAttemptCoordinator:
    """One explicit, run-scoped authority/budget coordinator.

    All methods are synchronous.  No method performs I/O, awaits, calls a
    provider, invokes a callback, or acquires a storage lock while the
    coordinator condition is owned.  Close methods publish closure atomically,
    then wait on the condition while releasing the lock.
    """

    def __init__(self, *, factory_run_id: str) -> None:
        self.factory_run_id = _identifier("factory_run_id", factory_run_id)
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._grants: dict[str, _GrantState] = {}
        self._provider_request_ids: set[str] = set()
        self._reservation_ids: set[str] = set()
        self._start_permit_ids: set[str] = set()
        self._lease_ids: set[str] = set()
        self._lifecycle_event_ids: set[str] = set()
        self._lifecycle_sequences: set[int] = set()
        self._lifecycle_event_hashes: set[str] = set()
        self._closed = False

    @classmethod
    def from_replay_candidate(
        cls,
        candidate: object,
    ) -> tuple[FactoryPhysicalAttemptCoordinator, tuple[FactoryPhysicalAttemptRecoveryTerminalWorkV1, ...]]:
        """Install a detached replay candidate as permanently closed state."""

        from .factory_physical_attempt_replay import (
            FactoryPhysicalAttemptReplayCandidateV1,
            FactoryPhysicalAttemptReplayRecordV1,
        )

        if type(candidate) is not FactoryPhysicalAttemptReplayCandidateV1:
            raise FactoryPhysicalAttemptControlError("factory_physical_attempt_replay_candidate_exact_type_required")
        FactoryPhysicalAttemptReplayCandidateV1.__post_init__(candidate)
        coordinator = cls(factory_run_id=candidate.fence.factory_run_id)
        recovery_work: list[FactoryPhysicalAttemptRecoveryTerminalWorkV1] = []
        with coordinator._condition:
            coordinator._closed = True
            for cutoff in candidate.role_evidence.cutoffs:
                request = cutoff.body.request
                authority = cutoff.body.authority
                grant_view = FactoryPhysicalAttemptGrantViewV1(
                    schema_version=FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
                    verification_scope="factory",
                    factory_run_id=cutoff.body.factory_run_id,
                    role=request.role,
                    stage=authority.stage,
                    workspace_fencing_token=authority.workspace_fencing_token,
                    stage_claim_attempt=authority.stage_claim_attempt,
                    stage_claim_nonce=authority.stage_claim_nonce,
                    execution_authority_hash=request.execution_authority_hash,
                    attempt_budget=request.attempt_budget,
                )
                existing = coordinator._grants.get(request.execution_authority_hash)
                if existing is None:
                    coordinator._grants[request.execution_authority_hash] = _GrantState(
                        grant=grant_view,
                        revoked=True,
                        closed=True,
                    )
                elif existing.grant != grant_view:
                    raise FactoryPhysicalAttemptControlError("factory_physical_attempt_replay_cross_view_identity")

            for replay_record in candidate.records:
                if type(replay_record) is not FactoryPhysicalAttemptReplayRecordV1:
                    raise FactoryPhysicalAttemptControlError(
                        "factory_physical_attempt_replay_record_exact_type_required"
                    )
                work = coordinator._install_replay_record_locked(replay_record)
                if work is not None:
                    recovery_work.append(work)
            coordinator._condition.notify_all()
        return coordinator, tuple(recovery_work)

    def _install_replay_record_locked(
        self, replay_record: object
    ) -> FactoryPhysicalAttemptRecoveryTerminalWorkV1 | None:
        from .factory_physical_attempt_replay import FactoryPhysicalAttemptReplayRecordV1

        if type(replay_record) is not FactoryPhysicalAttemptReplayRecordV1:
            raise FactoryPhysicalAttemptControlError("factory_physical_attempt_replay_record_exact_type_required")
        start = replay_record.start
        request = replay_record.cutoff.body.request
        grant_state = self._grants.get(start.execution_authority_hash)
        if grant_state is None:
            raise FactoryPhysicalAttemptControlError("factory_physical_attempt_execution_authority_hash_mismatch")
        cutoff_view = FactoryPhysicalAttemptCutoffViewV1(
            schema_version=FACTORY_PHYSICAL_ATTEMPT_CUTOFF_VIEW_SCHEMA,
            grant=grant_state.grant,
            run_id=request.run_id,
            turn_id=request.turn_id,
            call_id=request.call_id,
            request_freeze_id=request.request_freeze_id,
            provider=start.provider,
            model=start.model,
            semantic_request_hash=start.semantic_request_hash,
            physical_wire_hash=start.physical_wire_hash,
        )
        grant_state.cutoffs[_cutoff_key(cutoff_view)] = cutoff_view
        reservation = FactoryPhysicalAttemptReservationV1(
            schema_version=FACTORY_PHYSICAL_ATTEMPT_RESERVATION_SCHEMA,
            verification_scope="factory",
            factory_run_id=start.factory_run_id,
            run_id=start.run_id,
            role=start.role,
            turn_id=start.turn_id,
            call_id=start.call_id,
            request_freeze_id=start.request_freeze_id,
            execution_authority_hash=start.execution_authority_hash,
            attempt_budget=start.attempt_budget,
            provider=start.provider,
            model=start.model,
            semantic_request_hash=start.semantic_request_hash,
            physical_wire_hash=start.physical_wire_hash,
            composite_request_hash=start.composite_request_hash,
            reservation_id=start.reservation_id,
            provider_request_id=start.provider_request_id,
            authority_attempt_ordinal=start.authority_attempt_ordinal,
        )
        permit = FactoryPhysicalAttemptStartPermitV1(
            schema_version=FACTORY_PHYSICAL_ATTEMPT_START_PERMIT_SCHEMA,
            **{
                field_name: getattr(reservation, field_name)
                for field_name in _RESERVATION_IDENTITY_FIELDS
                if field_name != "schema_version"
            },
            start_permit_id=start.start_permit_id,
        )
        start_receipt = ProviderAttemptStartReceiptV1(
            schema_version=PROVIDER_ATTEMPT_START_RECEIPT_SCHEMA,
            **{
                field_name: getattr(permit, field_name)
                for field_name in _START_PERMIT_IDENTITY_FIELDS
                if field_name != "schema_version"
            },
            lifecycle_event_id=start.lifecycle_event_id,
            logical_sequence=start.logical_sequence,
            event_hash=start.event_hash,
            phase="start",
            durability_acked=True,
        )
        terminal = replay_record.terminal
        lease_id = (
            terminal.lease_id
            if terminal is not None
            else factory_provider_attempt_recovery_lease_id(start.factory_run_id, start.provider_request_id)
        )
        recovered = terminal is None or lease_id == factory_provider_attempt_recovery_lease_id(
            start.factory_run_id,
            start.provider_request_id,
        )
        lease = FactoryPhysicalAttemptLeaseV1(
            schema_version=FACTORY_PHYSICAL_ATTEMPT_LEASE_SCHEMA,
            **{
                field_name: getattr(permit, field_name)
                for field_name in _START_PERMIT_IDENTITY_FIELDS
                if field_name != "schema_version"
            },
            lease_id=lease_id,
            start_receipt=start_receipt,
        )
        record = _ReservationRecord(
            reservation=reservation,
            state=(
                _ReservationState.TERMINAL_ACKED if terminal is not None else _ReservationState.RECOVERED_START_ABORTING
            ),
            start_permit=permit,
            start_receipt=start_receipt,
            lease=lease,
            recovered=recovered,
        )
        if terminal is not None:
            record.terminal_receipt = ProviderAttemptTerminalReceiptV1(
                schema_version=PROVIDER_ATTEMPT_TERMINAL_RECEIPT_SCHEMA,
                **{
                    field_name: getattr(lease, field_name)
                    for field_name in _LEASE_IDENTITY_FIELDS
                    if field_name != "schema_version"
                },
                lifecycle_event_id=terminal.lifecycle_event_id,
                logical_sequence=terminal.logical_sequence,
                event_hash=terminal.event_hash,
                phase="terminal",
                durability_acked=True,
                terminal_status=terminal.terminal_status,
            )
        if (
            reservation.reservation_id in self._reservation_ids
            or reservation.provider_request_id in self._provider_request_ids
        ):
            raise FactoryPhysicalAttemptControlError("factory_physical_attempt_duplicate_identity")
        self._reservation_ids.add(reservation.reservation_id)
        self._provider_request_ids.add(reservation.provider_request_id)
        self._start_permit_ids.add(permit.start_permit_id)
        self._lease_ids.add(lease.lease_id)
        self._claim_lifecycle_identity_locked(start_receipt)
        if record.terminal_receipt is not None:
            self._claim_lifecycle_identity_locked(record.terminal_receipt)
        grant_state.reservations[reservation.reservation_id] = record
        grant_state.next_ordinal = max(grant_state.next_ordinal, reservation.authority_attempt_ordinal)
        if terminal is not None:
            return None
        attempt = FrozenFinalProviderAttemptV1(
            provider_request_id=start.provider_request_id,
            request_freeze_id=start.request_freeze_id,
            factory_run_id=start.factory_run_id,
            scope_id=start.scope_id,
            run_id=start.run_id,
            turn_id=start.turn_id,
            call_id=start.call_id,
            role=start.role,
            provider=start.provider,
            model=start.model,
            attempt_number=start.authority_attempt_ordinal,
            verification_scope="factory",
            execution_authority_hash=start.execution_authority_hash,
            attempt_budget=start.attempt_budget,
            authority_attempt_ordinal=start.authority_attempt_ordinal,
            semantic_request_hash=start.semantic_request_hash,
            physical_wire_hash=start.physical_wire_hash,
            composite_request_hash=start.composite_request_hash,
            dispatch_view={},
            durable_view={},
        )
        return FactoryPhysicalAttemptRecoveryTerminalWorkV1(
            attempt=attempt,
            lease=lease,
            context_snapshot_ref=start.context_snapshot_ref,
            pin_hash=start.pin_hash,
        )

    def register_grant(self, grant_view: FactoryPhysicalAttemptGrantViewV1) -> FactoryPhysicalAttemptBudgetStateV1:
        """Register one exact Factory-owned live grant."""

        if type(grant_view) is not FactoryPhysicalAttemptGrantViewV1:
            raise FactoryPhysicalAttemptControlError("factory_physical_attempt_grant_view_exact_type_required")
        FactoryPhysicalAttemptGrantViewV1.__post_init__(grant_view)
        with self._condition:
            if self._closed:
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_authority_closed")
            if grant_view.factory_run_id != self.factory_run_id:
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_factory_run_mismatch")
            if grant_view.execution_authority_hash in self._grants:
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_duplicate_identity")
            state = _GrantState(grant=grant_view)
            self._grants[grant_view.execution_authority_hash] = state
            return self._budget_state_locked(state)

    def register_cutoff(self, cutoff_view: FactoryPhysicalAttemptCutoffViewV1) -> FactoryPhysicalAttemptBudgetStateV1:
        """Bind one exact Factory-owned final-request cutoff to its grant."""

        if type(cutoff_view) is not FactoryPhysicalAttemptCutoffViewV1:
            raise FactoryPhysicalAttemptControlError("factory_physical_attempt_cutoff_view_exact_type_required")
        FactoryPhysicalAttemptCutoffViewV1.__post_init__(cutoff_view)
        with self._condition:
            grant = self._grant_locked(cutoff_view.grant.execution_authority_hash)
            self._require_grant_open_locked(grant)
            if cutoff_view.grant != grant.grant:
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_grant_view_mismatch")
            key = _cutoff_key(cutoff_view)
            if key in grant.cutoffs:
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_duplicate_identity")
            grant.cutoffs[key] = cutoff_view
            return self._budget_state_locked(grant)

    def revoke_grant(self, execution_authority_hash: str) -> FactoryPhysicalAttemptBudgetStateV1:
        with self._condition:
            grant = self._grant_locked(execution_authority_hash)
            grant.revoked = True
            self._abort_plain_reservations_locked(grant)
            self._condition.notify_all()
            self._wait_for_started_locked((grant,))
            return self._budget_state_locked(grant)

    def close_grant(self, execution_authority_hash: str) -> FactoryPhysicalAttemptBudgetStateV1:
        with self._condition:
            grant = self._grant_locked(execution_authority_hash)
            grant.closed = True
            grant.revoked = True
            self._abort_plain_reservations_locked(grant)
            self._condition.notify_all()
            self._wait_for_started_locked((grant,))
            return self._budget_state_locked(grant)

    def close(self) -> FactoryPhysicalAttemptRunDrainSnapshot:
        with self._condition:
            self._closed = True
            grants = tuple(self._grants.values())
            for grant in grants:
                grant.closed = True
                grant.revoked = True
                self._abort_plain_reservations_locked(grant)
            self._condition.notify_all()
            self._wait_for_started_locked(grants)
            return self._drain_snapshot_locked()

    def reserve(self, command: ReserveFactoryPhysicalAttemptV1) -> FactoryPhysicalAttemptReservationV1:
        self._require_command_type(command, ReserveFactoryPhysicalAttemptV1)
        ReserveFactoryPhysicalAttemptV1.__post_init__(command)
        reservation_id = self._mint_identity_candidate("reservation")
        provider_request_id = self._mint_identity_candidate("provider_request")
        with self._condition:
            if self._closed:
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_authority_closed")
            grant = self._grant_locked(command.execution_authority_hash)
            self._require_grant_open_locked(grant)
            cutoff = self._validate_reserve_authority_locked(grant, command)
            state = self._budget_state_locked(grant)
            if state.ambiguous_count:
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_start_commit_ambiguous")
            if state.remaining_attempts <= 0:
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_budget_exhausted")
            if reservation_id in self._reservation_ids or provider_request_id in self._provider_request_ids:
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_duplicate_identity")

            grant.next_ordinal += 1
            ordinal = grant.next_ordinal
            reservation = FactoryPhysicalAttemptReservationV1(
                schema_version=FACTORY_PHYSICAL_ATTEMPT_RESERVATION_SCHEMA,
                verification_scope=command.verification_scope,
                factory_run_id=command.factory_run_id,
                run_id=command.run_id,
                role=command.role,
                turn_id=command.turn_id,
                call_id=command.call_id,
                request_freeze_id=command.request_freeze_id,
                execution_authority_hash=command.execution_authority_hash,
                attempt_budget=command.attempt_budget,
                provider=command.provider,
                model=command.model,
                semantic_request_hash=command.semantic_request_hash,
                physical_wire_hash=command.physical_wire_hash,
                composite_request_hash=self._composite_request_hash(cutoff, ordinal),
                reservation_id=reservation_id,
                provider_request_id=provider_request_id,
                authority_attempt_ordinal=ordinal,
            )
            self._reservation_ids.add(reservation_id)
            self._provider_request_ids.add(provider_request_id)
            grant.reservations[reservation_id] = _ReservationRecord(reservation=reservation)
            return reservation

    def begin_start(self, command: BeginFactoryPhysicalAttemptStartV1) -> FactoryPhysicalAttemptStartPermitV1:
        self._require_command_type(command, BeginFactoryPhysicalAttemptStartV1)
        BeginFactoryPhysicalAttemptStartV1.__post_init__(command)
        start_permit_id = self._mint_identity_candidate("start_permit")
        with self._condition:
            grant, record = self._reservation_locked(command.execution_authority_hash, command.reservation_id)
            self._require_grant_open_locked(grant)
            self._require_reservation_identity(record, command)
            if record.state is not _ReservationState.RESERVED:
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_reservation_state_conflict")
            if start_permit_id in self._start_permit_ids:
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_duplicate_identity")
            permit = FactoryPhysicalAttemptStartPermitV1(
                schema_version=FACTORY_PHYSICAL_ATTEMPT_START_PERMIT_SCHEMA,
                verification_scope=record.reservation.verification_scope,
                factory_run_id=record.reservation.factory_run_id,
                run_id=record.reservation.run_id,
                role=record.reservation.role,
                turn_id=record.reservation.turn_id,
                call_id=record.reservation.call_id,
                request_freeze_id=record.reservation.request_freeze_id,
                execution_authority_hash=record.reservation.execution_authority_hash,
                attempt_budget=record.reservation.attempt_budget,
                provider=record.reservation.provider,
                model=record.reservation.model,
                semantic_request_hash=record.reservation.semantic_request_hash,
                physical_wire_hash=record.reservation.physical_wire_hash,
                composite_request_hash=record.reservation.composite_request_hash,
                reservation_id=record.reservation.reservation_id,
                provider_request_id=record.reservation.provider_request_id,
                authority_attempt_ordinal=record.reservation.authority_attempt_ordinal,
                start_permit_id=start_permit_id,
            )
            self._start_permit_ids.add(start_permit_id)
            record.start_permit = permit
            record.state = _ReservationState.START_PERSISTING
            return permit

    def commit_started(self, command: CommitFactoryPhysicalAttemptStartV1) -> FactoryPhysicalAttemptLeaseV1:
        self._require_command_type(command, CommitFactoryPhysicalAttemptStartV1)
        CommitFactoryPhysicalAttemptStartV1.__post_init__(command)
        lease_id = self._mint_identity_candidate("lease")
        with self._condition:
            _grant, record = self._reservation_locked(command.execution_authority_hash, command.reservation_id)
            if record.state is not _ReservationState.START_PERSISTING or record.start_permit is None:
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_reservation_state_conflict")
            if not _identity_equal(record.start_permit, command, _START_PERMIT_IDENTITY_FIELDS):
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_reservation_state_conflict")
            receipt = command.start_receipt
            if not _identity_equal(record.start_permit, receipt, _START_PERMIT_IDENTITY_FIELDS):
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_reservation_state_conflict")
            if record.start_receipt is not None or record.lease is not None:
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_duplicate_identity")
            if self._lifecycle_identity_used_locked(receipt) or lease_id in self._lease_ids:
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_duplicate_identity")
            lease = FactoryPhysicalAttemptLeaseV1(
                schema_version=FACTORY_PHYSICAL_ATTEMPT_LEASE_SCHEMA,
                verification_scope=record.start_permit.verification_scope,
                factory_run_id=record.start_permit.factory_run_id,
                run_id=record.start_permit.run_id,
                role=record.start_permit.role,
                turn_id=record.start_permit.turn_id,
                call_id=record.start_permit.call_id,
                request_freeze_id=record.start_permit.request_freeze_id,
                execution_authority_hash=record.start_permit.execution_authority_hash,
                attempt_budget=record.start_permit.attempt_budget,
                provider=record.start_permit.provider,
                model=record.start_permit.model,
                semantic_request_hash=record.start_permit.semantic_request_hash,
                physical_wire_hash=record.start_permit.physical_wire_hash,
                composite_request_hash=record.start_permit.composite_request_hash,
                reservation_id=record.start_permit.reservation_id,
                provider_request_id=record.start_permit.provider_request_id,
                authority_attempt_ordinal=record.start_permit.authority_attempt_ordinal,
                start_permit_id=record.start_permit.start_permit_id,
                lease_id=lease_id,
                start_receipt=receipt,
            )
            self._claim_lifecycle_identity_locked(receipt)
            self._lease_ids.add(lease_id)
            record.start_receipt = receipt
            record.lease = lease
            record.state = _ReservationState.START_COMMITTED
            self._condition.notify_all()
            return lease

    def abort_reservation(
        self,
        command: AbortFactoryPhysicalAttemptReservationV1,
    ) -> FactoryPhysicalAttemptBudgetStateV1:
        self._require_command_type(command, AbortFactoryPhysicalAttemptReservationV1)
        AbortFactoryPhysicalAttemptReservationV1.__post_init__(command)
        reservation = command.reservation
        with self._condition:
            grant, record = self._reservation_locked(
                reservation.execution_authority_hash,
                reservation.reservation_id,
            )
            self._require_reservation_identity(record, reservation)
            if record.state is _ReservationState.RESERVED:
                if command.start_permit is not None or command.definite_start_not_persisted_proof is not None:
                    raise FactoryPhysicalAttemptControlError("factory_physical_attempt_abort_proof_not_allowed")
            elif record.state is _ReservationState.START_PERSISTING:
                if record.start_permit is None:
                    raise FactoryPhysicalAttemptControlError("factory_physical_attempt_reservation_state_conflict")
                self._require_definite_start_absent_proof_locked(record, command)
            else:
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_reservation_state_conflict")
            record.state = _ReservationState.ABORTED
            self._condition.notify_all()
            return self._budget_state_locked(grant)

    def mark_start_ambiguous(
        self,
        command: MarkFactoryPhysicalAttemptStartAmbiguousV1,
    ) -> FactoryPhysicalAttemptBudgetStateV1:
        self._require_command_type(command, MarkFactoryPhysicalAttemptStartAmbiguousV1)
        MarkFactoryPhysicalAttemptStartAmbiguousV1.__post_init__(command)
        permit = command.start_permit
        with self._condition:
            grant, record = self._reservation_locked(permit.execution_authority_hash, permit.reservation_id)
            if (
                record.state is not _ReservationState.START_PERSISTING
                or record.start_permit is None
                or not _identity_equal(record.start_permit, permit, _START_PERMIT_IDENTITY_FIELDS)
            ):
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_reservation_state_conflict")
            record.state = _ReservationState.START_AMBIGUOUS
            self._condition.notify_all()
            return self._budget_state_locked(grant)

    def settle(self, command: SettleFactoryPhysicalAttemptV1) -> FactoryPhysicalAttemptBudgetStateV1:
        self._require_command_type(command, SettleFactoryPhysicalAttemptV1)
        SettleFactoryPhysicalAttemptV1.__post_init__(command)
        lease = command.lease
        with self._condition:
            grant, record = self._reservation_locked(lease.execution_authority_hash, lease.reservation_id)
            if (
                record.state
                not in {
                    _ReservationState.START_COMMITTED,
                    _ReservationState.RECOVERED_START_ABORTING,
                }
                or record.lease is None
            ):
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_terminal_unknown")
            if not _identity_equal(record.lease, lease, _LEASE_IDENTITY_FIELDS):
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_terminal_unknown")
            receipt = command.terminal_receipt
            if not _identity_equal(lease, receipt, _LEASE_IDENTITY_FIELDS):
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_terminal_unknown")
            if record.start_receipt is None or receipt.logical_sequence <= record.start_receipt.logical_sequence:
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_terminal_unknown")
            if record.state is _ReservationState.RECOVERED_START_ABORTING and receipt.terminal_status != "cancelled":
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_terminal_unknown")
            if self._lifecycle_identity_used_locked(receipt):
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_duplicate_identity")
            self._claim_lifecycle_identity_locked(receipt)
            record.terminal_receipt = receipt
            record.state = _ReservationState.TERMINAL_ACKED
            self._condition.notify_all()
            return self._budget_state_locked(grant)

    def terminal_persistence_failed(
        self,
        command: FailFactoryPhysicalAttemptTerminalV1,
    ) -> FactoryPhysicalAttemptBudgetStateV1:
        self._require_command_type(command, FailFactoryPhysicalAttemptTerminalV1)
        FailFactoryPhysicalAttemptTerminalV1.__post_init__(command)
        lease = command.lease
        with self._condition:
            grant, record = self._reservation_locked(lease.execution_authority_hash, lease.reservation_id)
            if (
                record.state
                not in {
                    _ReservationState.START_COMMITTED,
                    _ReservationState.RECOVERED_START_ABORTING,
                }
                or record.lease is None
                or not _identity_equal(record.lease, lease, _LEASE_IDENTITY_FIELDS)
            ):
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_terminal_unknown")
            record.failure_code = command.failure_code
            record.failure_error_type = command.error_type
            record.failure_error = command.error
            record.state = _ReservationState.TERMINAL_PERSISTENCE_FAILED
            self._condition.notify_all()
            return self._budget_state_locked(grant)

    def budget_state(self, execution_authority_hash: str) -> FactoryPhysicalAttemptBudgetStateV1:
        with self._condition:
            return self._budget_state_locked(self._grant_locked(execution_authority_hash))

    def drain_snapshot(self) -> FactoryPhysicalAttemptRunDrainSnapshot:
        with self._condition:
            return self._drain_snapshot_locked()

    def provider_drain_snapshot(self) -> ProviderAttemptDrainResultV1:
        with self._condition:
            return self._provider_drain_snapshot_locked()

    def wait_provider_settled(self, timeout_seconds: float | None = None) -> ProviderAttemptDrainResultV1:
        timeout = self._validated_timeout(timeout_seconds)
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while True:
                result = self._provider_drain_snapshot_locked()
                if result.terminal_failures:
                    raise ProviderAttemptDrainError(
                        "provider attempt terminal persistence failed",
                        code="provider_attempt_terminal_persistence_failed",
                        result=result,
                    )
                if result.settled:
                    return result
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProviderAttemptDrainError(
                        "provider attempt drain timed out",
                        code="provider_attempt_drain_timeout",
                        result=result,
                    )
                self._condition.wait(timeout=remaining)

    @staticmethod
    def _require_command_type(command: object, expected: type[object]) -> None:
        if type(command) is not expected:
            raise FactoryPhysicalAttemptControlError("factory_physical_attempt_control_command_exact_type_required")

    def _grant_locked(self, execution_authority_hash: str) -> _GrantState:
        try:
            authority_hash = _hash64("execution_authority_hash", execution_authority_hash)
        except (TypeError, ValueError) as exc:
            raise FactoryPhysicalAttemptControlError(
                "factory_physical_attempt_execution_authority_hash_mismatch"
            ) from exc
        grant = self._grants.get(authority_hash)
        if grant is None:
            raise FactoryPhysicalAttemptControlError("factory_physical_attempt_execution_authority_hash_mismatch")
        return grant

    @staticmethod
    def _require_grant_open_locked(grant: _GrantState) -> None:
        if grant.closed:
            raise FactoryPhysicalAttemptControlError("factory_physical_attempt_authority_closed")
        if grant.revoked:
            raise FactoryPhysicalAttemptControlError("factory_physical_attempt_grant_revoked")

    def _validate_reserve_authority_locked(
        self,
        grant: _GrantState,
        command: ReserveFactoryPhysicalAttemptV1,
    ) -> FactoryPhysicalAttemptCutoffViewV1:
        registered = grant.grant
        if command.verification_scope != "factory":
            raise FactoryPhysicalAttemptControlError("factory_physical_attempt_coordinator_scope_mismatch")
        if command.factory_run_id != self.factory_run_id or command.factory_run_id != registered.factory_run_id:
            raise FactoryPhysicalAttemptControlError("factory_physical_attempt_factory_run_mismatch")
        if command.role != registered.role:
            raise FactoryPhysicalAttemptControlError("factory_physical_attempt_role_mismatch")
        if command.execution_authority_hash != registered.execution_authority_hash:
            raise FactoryPhysicalAttemptControlError("factory_physical_attempt_execution_authority_hash_mismatch")
        if command.attempt_budget != registered.attempt_budget:
            raise FactoryPhysicalAttemptControlError("factory_physical_attempt_budget_mismatch")
        key = _cutoff_key(command)
        cutoff = grant.cutoffs.get(key)
        if cutoff is not None:
            return cutoff
        if not any(item.run_id == command.run_id for item in grant.cutoffs.values()):
            raise FactoryPhysicalAttemptControlError("factory_physical_attempt_controlled_run_mismatch")
        if not any(item.request_freeze_id == command.request_freeze_id for item in grant.cutoffs.values()):
            raise FactoryPhysicalAttemptControlError("factory_physical_attempt_reservation_unknown")
        raise FactoryPhysicalAttemptControlError("factory_physical_attempt_cutoff_view_mismatch")

    def _reservation_locked(
        self,
        execution_authority_hash: str,
        reservation_id: str,
    ) -> tuple[_GrantState, _ReservationRecord]:
        grant = self._grant_locked(execution_authority_hash)
        record = grant.reservations.get(reservation_id)
        if record is None:
            raise FactoryPhysicalAttemptControlError("factory_physical_attempt_reservation_unknown")
        return grant, record

    @staticmethod
    def _require_reservation_identity(record: _ReservationRecord, value: object) -> None:
        if not _identity_equal(record.reservation, value, _RESERVATION_IDENTITY_FIELDS):
            raise FactoryPhysicalAttemptControlError("factory_physical_attempt_reservation_state_conflict")

    @staticmethod
    def _require_definite_start_absent_proof_locked(
        record: _ReservationRecord,
        command: AbortFactoryPhysicalAttemptReservationV1,
    ) -> None:
        permit = command.start_permit
        proof = command.definite_start_not_persisted_proof
        if (
            type(permit) is not FactoryPhysicalAttemptStartPermitV1
            or type(proof) is not FactoryPhysicalAttemptDefiniteStartNotPersistedProofV1
            or record.start_permit is None
            or not _identity_equal(record.start_permit, permit, _START_PERMIT_IDENTITY_FIELDS)
            or not _identity_equal(record.start_permit, proof.start_permit, _START_PERMIT_IDENTITY_FIELDS)
        ):
            raise FactoryPhysicalAttemptControlError("factory_physical_attempt_definite_start_proof_mismatch")

    @staticmethod
    def _abort_plain_reservations_locked(grant: _GrantState) -> None:
        for record in grant.reservations.values():
            if record.state is _ReservationState.RESERVED:
                record.state = _ReservationState.ABORTED

    @staticmethod
    def _grant_has_active_started_locked(grant: _GrantState) -> bool:
        return any(record.state.value not in _TERMINAL_STATES for record in grant.reservations.values())

    def _wait_for_started_locked(self, grants: tuple[_GrantState, ...]) -> None:
        while any(self._grant_has_active_started_locked(grant) for grant in grants):
            self._condition.wait()

    def _lifecycle_identity_used_locked(
        self,
        receipt: ProviderAttemptStartReceiptV1 | ProviderAttemptTerminalReceiptV1,
    ) -> bool:
        return (
            receipt.lifecycle_event_id in self._lifecycle_event_ids
            or receipt.logical_sequence in self._lifecycle_sequences
            or receipt.event_hash in self._lifecycle_event_hashes
        )

    def _claim_lifecycle_identity_locked(
        self,
        receipt: ProviderAttemptStartReceiptV1 | ProviderAttemptTerminalReceiptV1,
    ) -> None:
        self._lifecycle_event_ids.add(receipt.lifecycle_event_id)
        self._lifecycle_sequences.add(receipt.logical_sequence)
        self._lifecycle_event_hashes.add(receipt.event_hash)

    @staticmethod
    def _composite_request_hash(cutoff: FactoryPhysicalAttemptCutoffViewV1, ordinal: int) -> str:
        return canonical_factory_physical_attempt_composite_hash(cutoff, ordinal)

    def _budget_state_locked(self, grant: _GrantState) -> FactoryPhysicalAttemptBudgetStateV1:
        records = tuple(grant.reservations.values())
        reserved_count = sum(
            record.state in {_ReservationState.RESERVED, _ReservationState.START_PERSISTING} for record in records
        )
        start_persisting_count = sum(record.state is _ReservationState.START_PERSISTING for record in records)
        ambiguous_count = sum(record.state is _ReservationState.START_AMBIGUOUS for record in records)
        committed_request_ids = {
            record.reservation.provider_request_id for record in records if record.start_receipt is not None
        }
        committed_count = len(committed_request_ids)
        recovered_count = sum(record.recovered for record in records)
        terminal_count = sum(record.state is _ReservationState.TERMINAL_ACKED for record in records)
        aborted_count = sum(record.state is _ReservationState.ABORTED for record in records)
        terminal_failure_count = sum(
            record.state is _ReservationState.TERMINAL_PERSISTENCE_FAILED for record in records
        )
        raw_remaining = grant.grant.attempt_budget - committed_count - reserved_count - ambiguous_count
        if raw_remaining < 0:
            raise FactoryPhysicalAttemptControlError("factory_physical_attempt_budget_state_invalid")
        inflight = reserved_count + ambiguous_count + committed_count - terminal_count
        settled = inflight == 0 and terminal_failure_count == 0
        return FactoryPhysicalAttemptBudgetStateV1(
            schema_version=FACTORY_PHYSICAL_ATTEMPT_BUDGET_STATE_SCHEMA,
            factory_run_id=grant.grant.factory_run_id,
            execution_authority_hash=grant.grant.execution_authority_hash,
            attempt_budget=grant.grant.attempt_budget,
            registered=True,
            revoked=grant.revoked,
            closed=grant.closed,
            reserved_count=reserved_count,
            start_persisting_count=start_persisting_count,
            ambiguous_count=ambiguous_count,
            committed_count=committed_count,
            recovered_count=recovered_count,
            terminal_count=terminal_count,
            aborted_count=aborted_count,
            terminal_failure_count=terminal_failure_count,
            consumed_attempts=committed_count,
            remaining_attempts=raw_remaining,
            inflight_count=inflight,
            settled=settled,
        )

    def _drain_snapshot_locked(self) -> FactoryPhysicalAttemptRunDrainSnapshot:
        states = tuple(
            self._budget_state_locked(self._grants[authority_hash]) for authority_hash in sorted(self._grants)
        )
        blocking: list[str] = []
        failures: list[str] = []
        for grant in self._grants.values():
            for reservation_id, record in grant.reservations.items():
                if record.state not in {_ReservationState.ABORTED, _ReservationState.TERMINAL_ACKED}:
                    blocking.append(reservation_id)
                if record.state is _ReservationState.TERMINAL_PERSISTENCE_FAILED:
                    failures.append(reservation_id)
        return FactoryPhysicalAttemptRunDrainSnapshot(
            factory_run_id=self.factory_run_id,
            by_authority=states,
            blocking_reservation_ids=tuple(sorted(blocking)),
            terminal_failure_reservation_ids=tuple(sorted(failures)),
            settled=all(state.settled for state in states),
        )

    def _provider_drain_snapshot_locked(self) -> ProviderAttemptDrainResultV1:
        blocking: dict[str, _ReservationRecord] = {}
        failures: dict[str, ProviderAttemptTerminalFailureV1] = {}
        for grant in self._grants.values():
            for record in grant.reservations.values():
                if record.state not in {_ReservationState.ABORTED, _ReservationState.TERMINAL_ACKED}:
                    request_id = record.reservation.provider_request_id
                    blocking[request_id] = record
                    if record.state is _ReservationState.TERMINAL_PERSISTENCE_FAILED:
                        failures[request_id] = ProviderAttemptTerminalFailureV1(
                            provider_request_id=request_id,
                            error_type=record.failure_error_type,
                            error=record.failure_error,
                        )
        inflight_request_ids = tuple(sorted(blocking))
        terminal_failures = tuple(failures[key] for key in sorted(failures))
        return ProviderAttemptDrainResultV1(
            verification_scope="factory",
            scope_id=self.factory_run_id,
            settled=not inflight_request_ids and not terminal_failures,
            inflight_request_ids=inflight_request_ids,
            terminal_failures=terminal_failures,
        )

    @staticmethod
    def _validated_timeout(timeout_seconds: float | None) -> float | None:
        if timeout_seconds is None:
            return None
        if isinstance(timeout_seconds, bool):
            raise ValueError("timeout_seconds must be positive or None")
        timeout = float(timeout_seconds)
        if timeout <= 0:
            raise ValueError("timeout_seconds must be positive or None")
        return timeout

    @staticmethod
    def _mint_identity_candidate(prefix: str) -> str:
        """Mint outside coordinator locks; callers claim uniqueness inside."""

        return f"{prefix}_{uuid.uuid4().hex}"


class FactoryPhysicalAttemptLiveControlPort:
    """Run-scoped Factory capability that wire-binds cutoffs before reserve.

    The public side remains the locked seven-method
    ``FactoryPhysicalAttemptControlPort``.  Factory-only grant registration and
    drain methods are deliberately outside that protocol.  Possession of this
    runtime object is the live capability; it never enters serializable state.
    """

    def __init__(self, *, factory_run_id: str) -> None:
        self.factory_run_id = _identifier("factory_run_id", factory_run_id)
        self._authority_lock = threading.RLock()
        self._coordinator = FactoryPhysicalAttemptCoordinator(factory_run_id=self.factory_run_id)
        self._grant_views: dict[str, FactoryPhysicalAttemptGrantViewV1] = {}
        self._controlled_child_run_ids: dict[str, str] = {}
        self._registered_cutoffs: dict[tuple[object, ...], FactoryPhysicalAttemptCutoffViewV1] = {}

    @classmethod
    def from_replay_candidate(
        cls,
        candidate: object,
    ) -> tuple[FactoryPhysicalAttemptLiveControlPort, tuple[FactoryPhysicalAttemptRecoveryTerminalWorkV1, ...]]:
        """Wrap one reconstructed coordinator in the exact live-port type.

        The reconstructed coordinator is permanently closed.  Rebuilding the
        port-side lookup maps preserves exact-type consumers while never
        restoring admission: ``register_grant`` and ``reserve`` still fail at
        the closed coordinator boundary.
        """

        coordinator, recovery_work = FactoryPhysicalAttemptCoordinator.from_replay_candidate(candidate)
        instance = cls(factory_run_id=coordinator.factory_run_id)
        with instance._authority_lock:
            instance._coordinator = coordinator
            for authority_hash, grant_state in coordinator._grants.items():
                instance._grant_views[authority_hash] = grant_state.grant
                run_ids = {record.reservation.run_id for record in grant_state.reservations.values()}
                if len(run_ids) > 1:
                    raise FactoryPhysicalAttemptControlError("factory_physical_attempt_replay_controlled_run_mismatch")
                if run_ids:
                    instance._controlled_child_run_ids[authority_hash] = next(iter(run_ids))
                for cutoff in grant_state.cutoffs.values():
                    instance._registered_cutoffs[(authority_hash, *_cutoff_key(cutoff))] = cutoff
        return instance, recovery_work

    @property
    def verification_scope(self) -> str:
        return "factory"

    @property
    def scope_id(self) -> str:
        return self.factory_run_id

    @property
    def inflight_request_ids(self) -> tuple[str, ...]:
        return self.snapshot().inflight_request_ids

    @property
    def admission_closed(self) -> bool:
        with self._authority_lock, self._coordinator._condition:
            return self._coordinator._closed

    def register_grant(self, grant_view: FactoryPhysicalAttemptGrantViewV1) -> FactoryPhysicalAttemptBudgetStateV1:
        if type(grant_view) is not FactoryPhysicalAttemptGrantViewV1:
            raise FactoryPhysicalAttemptControlError("factory_physical_attempt_grant_view_exact_type_required")
        FactoryPhysicalAttemptGrantViewV1.__post_init__(grant_view)
        with self._authority_lock:
            state = self._coordinator.register_grant(grant_view)
            self._grant_views[grant_view.execution_authority_hash] = grant_view
            return state

    def revoke_grant(self, execution_authority_hash: str) -> FactoryPhysicalAttemptBudgetStateV1:
        with self._authority_lock:
            return self._coordinator.revoke_grant(execution_authority_hash)

    def close_grant(self, execution_authority_hash: str) -> FactoryPhysicalAttemptBudgetStateV1:
        with self._authority_lock:
            return self._coordinator.close_grant(execution_authority_hash)

    def close(self) -> FactoryPhysicalAttemptRunDrainSnapshot:
        with self._authority_lock:
            return self._coordinator.close()

    def reserve(self, command: ReserveFactoryPhysicalAttemptV1) -> FactoryPhysicalAttemptReservationV1:
        if type(command) is not ReserveFactoryPhysicalAttemptV1:
            raise FactoryPhysicalAttemptControlError("factory_physical_attempt_control_command_exact_type_required")
        ReserveFactoryPhysicalAttemptV1.__post_init__(command)
        with self._authority_lock:
            grant = self._grant_views.get(command.execution_authority_hash)
            if grant is None:
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_execution_authority_hash_mismatch")
            if command.factory_run_id != self.factory_run_id or command.factory_run_id != grant.factory_run_id:
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_factory_run_mismatch")
            if command.role != grant.role:
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_role_mismatch")
            if command.attempt_budget != grant.attempt_budget:
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_budget_mismatch")
            controlled_run_id = self._controlled_child_run_ids.get(command.execution_authority_hash)
            if controlled_run_id and controlled_run_id != command.run_id:
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_controlled_run_mismatch")
            cutoff = FactoryPhysicalAttemptCutoffViewV1(
                schema_version=FACTORY_PHYSICAL_ATTEMPT_CUTOFF_VIEW_SCHEMA,
                grant=grant,
                run_id=command.run_id,
                turn_id=command.turn_id,
                call_id=command.call_id,
                request_freeze_id=command.request_freeze_id,
                provider=command.provider,
                model=command.model,
                semantic_request_hash=command.semantic_request_hash,
                physical_wire_hash=command.physical_wire_hash,
            )
            cutoff_key = (command.execution_authority_hash, *_cutoff_key(cutoff))
            existing = self._registered_cutoffs.get(cutoff_key)
            if existing is None:
                self._coordinator.register_cutoff(cutoff)
                self._registered_cutoffs[cutoff_key] = cutoff
            elif existing != cutoff:
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_cutoff_view_mismatch")
            reservation = self._coordinator.reserve(command)
            self._controlled_child_run_ids.setdefault(command.execution_authority_hash, command.run_id)
            return reservation

    def begin_start(self, command: BeginFactoryPhysicalAttemptStartV1) -> FactoryPhysicalAttemptStartPermitV1:
        with self._authority_lock:
            return self._coordinator.begin_start(command)

    def commit_started(self, command: CommitFactoryPhysicalAttemptStartV1) -> FactoryPhysicalAttemptLeaseV1:
        return self._coordinator.commit_started(command)

    def abort_reservation(
        self,
        command: AbortFactoryPhysicalAttemptReservationV1,
    ) -> FactoryPhysicalAttemptBudgetStateV1:
        return self._coordinator.abort_reservation(command)

    def mark_start_ambiguous(
        self,
        command: MarkFactoryPhysicalAttemptStartAmbiguousV1,
    ) -> FactoryPhysicalAttemptBudgetStateV1:
        return self._coordinator.mark_start_ambiguous(command)

    def settle(self, command: SettleFactoryPhysicalAttemptV1) -> FactoryPhysicalAttemptBudgetStateV1:
        return self._coordinator.settle(command)

    def terminal_persistence_failed(
        self,
        command: FailFactoryPhysicalAttemptTerminalV1,
    ) -> FactoryPhysicalAttemptBudgetStateV1:
        return self._coordinator.terminal_persistence_failed(command)

    def budget_state(self, execution_authority_hash: str) -> FactoryPhysicalAttemptBudgetStateV1:
        return self._coordinator.budget_state(execution_authority_hash)

    def drain_snapshot(self) -> FactoryPhysicalAttemptRunDrainSnapshot:
        return self._coordinator.drain_snapshot()

    def snapshot(self) -> ProviderAttemptDrainResultV1:
        return self._coordinator.provider_drain_snapshot()

    async def wait_settled(
        self,
        *,
        verification_scope: str,
        scope_id: str,
        timeout_seconds: float | None = None,
    ) -> ProviderAttemptDrainResultV1:
        if verification_scope != self.verification_scope or scope_id != self.scope_id:
            raise ProviderAttemptDrainError(
                "provider attempt drain scope mismatch",
                code="provider_attempt_drain_scope_mismatch",
                result=self.snapshot(),
            )
        return await asyncio.to_thread(self._coordinator.wait_provider_settled, timeout_seconds)


__all__ = [
    "FactoryPhysicalAttemptControlError",
    "FactoryPhysicalAttemptCoordinator",
    "FactoryPhysicalAttemptLiveControlPort",
    "FactoryPhysicalAttemptRunDrainSnapshot",
]
