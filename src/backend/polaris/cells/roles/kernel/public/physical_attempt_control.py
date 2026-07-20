"""Runtime-only B3.4 physical-attempt admission contracts.

These immutable DTOs bind Factory-owned grant/cutoff facts to one physical
provider attempt.  They do not authorize provider transport by themselves.
The protocol is a runtime capability and must never be serialized into a
provider request, runtime event, or evidence payload.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from polaris.kernelone.events.final_request_evidence import role_final_request_policy

RESERVE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA = "factory.physical_attempt.reserve.v1"
BEGIN_FACTORY_PHYSICAL_ATTEMPT_START_SCHEMA = "factory.physical_attempt.begin_start.v1"
COMMIT_FACTORY_PHYSICAL_ATTEMPT_START_SCHEMA = "factory.physical_attempt.commit_start.v1"
ABORT_FACTORY_PHYSICAL_ATTEMPT_RESERVATION_SCHEMA = "factory.physical_attempt.abort_reservation.v1"
MARK_FACTORY_PHYSICAL_ATTEMPT_START_AMBIGUOUS_SCHEMA = "factory.physical_attempt.start_ambiguous.v1"
SETTLE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA = "factory.physical_attempt.settle.v1"
FAIL_FACTORY_PHYSICAL_ATTEMPT_TERMINAL_SCHEMA = "factory.physical_attempt.terminal_failure.v1"

FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA = "factory.physical_attempt.grant_view.v1"
FACTORY_PHYSICAL_ATTEMPT_CUTOFF_VIEW_SCHEMA = "factory.physical_attempt.cutoff_view.v1"
FACTORY_PHYSICAL_ATTEMPT_DEFINITE_START_NOT_PERSISTED_PROOF_SCHEMA = (
    "factory.physical_attempt.definite_start_not_persisted_proof.v1"
)
FACTORY_PHYSICAL_ATTEMPT_RESERVATION_SCHEMA = "factory.physical_attempt.reservation.v1"
FACTORY_PHYSICAL_ATTEMPT_START_PERMIT_SCHEMA = "factory.physical_attempt.start_permit.v1"
FACTORY_PHYSICAL_ATTEMPT_LEASE_SCHEMA = "factory.physical_attempt.lease.v1"
FACTORY_PHYSICAL_ATTEMPT_BUDGET_STATE_SCHEMA = "factory.physical_attempt_budget_state.v1"
PROVIDER_ATTEMPT_START_RECEIPT_SCHEMA = "provider.attempt_start_receipt.v1"
PROVIDER_ATTEMPT_TERMINAL_RECEIPT_SCHEMA = "provider.attempt_terminal_receipt.v1"

_HASH_LENGTH = 64
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_CANDIDATE_IDENTITY_FIELDS = (
    "verification_scope",
    "factory_run_id",
    "run_id",
    "role",
    "turn_id",
    "call_id",
    "request_freeze_id",
    "execution_authority_hash",
    "attempt_budget",
    "provider",
    "model",
    "semantic_request_hash",
    "physical_wire_hash",
)
_RESERVATION_IDENTITY_FIELDS = (
    *_CANDIDATE_IDENTITY_FIELDS,
    "composite_request_hash",
    "reservation_id",
    "provider_request_id",
    "authority_attempt_ordinal",
)
_START_PERMIT_IDENTITY_FIELDS = (*_RESERVATION_IDENTITY_FIELDS, "start_permit_id")
_LEASE_IDENTITY_FIELDS = (*_START_PERMIT_IDENTITY_FIELDS, "lease_id")


def _schema(value: object, expected: str, code: str) -> None:
    if type(value) is not str:
        raise TypeError("schema_version_type_invalid")
    if value != expected:
        raise ValueError(code)


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


def _positive_int(field_name: str, value: object) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name}_type_invalid")
    if value <= 0:
        raise ValueError(f"{field_name}_invalid")
    return value


def _non_negative_int(field_name: str, value: object) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name}_type_invalid")
    if value < 0:
        raise ValueError(f"{field_name}_invalid")
    return value


def _exact_bool(field_name: str, value: object) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{field_name}_bool_required")
    return value


def _canonical_role(value: object) -> str:
    role = _identifier("role", value)
    policy = role_final_request_policy(role)
    if policy.role != role:
        raise ValueError("role_not_canonical")
    return role


def _identity_equal(left: object, right: object, names: tuple[str, ...]) -> bool:
    return all(getattr(left, name, None) == getattr(right, name, None) for name in names)


@dataclass(frozen=True, slots=True)
class FactoryPhysicalAttemptGrantViewV1:
    """Exact Factory-owned execution grant facts; never caller-assembled strings."""

    schema_version: str
    verification_scope: str
    factory_run_id: str
    role: str
    stage: str
    workspace_fencing_token: int
    stage_claim_attempt: int
    stage_claim_nonce: str
    execution_authority_hash: str
    attempt_budget: int

    def __post_init__(self) -> None:
        _schema(
            self.schema_version,
            FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
            "factory_physical_attempt_grant_view_schema_mismatch",
        )
        if type(self.verification_scope) is not str:
            raise TypeError("verification_scope_type_invalid")
        if self.verification_scope != "factory":
            raise ValueError("verification_scope_mismatch")
        object.__setattr__(self, "factory_run_id", _identifier("factory_run_id", self.factory_run_id))
        object.__setattr__(self, "role", _canonical_role(self.role))
        object.__setattr__(self, "stage", _identifier("stage", self.stage))
        object.__setattr__(
            self,
            "workspace_fencing_token",
            _positive_int("workspace_fencing_token", self.workspace_fencing_token),
        )
        object.__setattr__(
            self,
            "stage_claim_attempt",
            _positive_int("stage_claim_attempt", self.stage_claim_attempt),
        )
        object.__setattr__(self, "stage_claim_nonce", _identifier("stage_claim_nonce", self.stage_claim_nonce))
        _hash64("execution_authority_hash", self.execution_authority_hash)
        object.__setattr__(self, "attempt_budget", _positive_int("attempt_budget", self.attempt_budget))


@dataclass(frozen=True, slots=True)
class FactoryPhysicalAttemptCutoffViewV1:
    """Exact Factory-owned final-request cutoff bound to its execution grant."""

    schema_version: str
    grant: FactoryPhysicalAttemptGrantViewV1
    run_id: str
    turn_id: str
    call_id: str
    request_freeze_id: str
    provider: str
    model: str
    semantic_request_hash: str
    physical_wire_hash: str

    def __post_init__(self) -> None:
        _schema(
            self.schema_version,
            FACTORY_PHYSICAL_ATTEMPT_CUTOFF_VIEW_SCHEMA,
            "factory_physical_attempt_cutoff_view_schema_mismatch",
        )
        if type(self.grant) is not FactoryPhysicalAttemptGrantViewV1:
            raise TypeError("factory_physical_attempt_grant_view_exact_type_required")
        FactoryPhysicalAttemptGrantViewV1.__post_init__(self.grant)
        for field_name in ("run_id", "turn_id", "call_id", "request_freeze_id", "provider", "model"):
            object.__setattr__(self, field_name, _identifier(field_name, getattr(self, field_name)))
        _hash64("semantic_request_hash", self.semantic_request_hash)
        _hash64("physical_wire_hash", self.physical_wire_hash)


@dataclass(frozen=True, slots=True)
class _FactoryPhysicalAttemptCandidateIdentityV1:
    schema_version: str
    verification_scope: str
    factory_run_id: str
    run_id: str
    role: str
    turn_id: str
    call_id: str
    request_freeze_id: str
    execution_authority_hash: str
    attempt_budget: int
    provider: str
    model: str
    semantic_request_hash: str
    physical_wire_hash: str

    def _validate_candidate(self, expected_schema: str, schema_code: str) -> None:
        _schema(self.schema_version, expected_schema, schema_code)
        if type(self.verification_scope) is not str:
            raise TypeError("verification_scope_type_invalid")
        if self.verification_scope != "factory":
            raise ValueError("verification_scope_mismatch")
        object.__setattr__(self, "factory_run_id", _identifier("factory_run_id", self.factory_run_id))
        object.__setattr__(self, "run_id", _identifier("run_id", self.run_id))
        object.__setattr__(self, "role", _canonical_role(self.role))
        object.__setattr__(self, "turn_id", _identifier("turn_id", self.turn_id))
        object.__setattr__(self, "call_id", _identifier("call_id", self.call_id))
        object.__setattr__(self, "request_freeze_id", _identifier("request_freeze_id", self.request_freeze_id))
        _hash64("execution_authority_hash", self.execution_authority_hash)
        object.__setattr__(self, "attempt_budget", _positive_int("attempt_budget", self.attempt_budget))
        object.__setattr__(self, "provider", _identifier("provider", self.provider))
        object.__setattr__(self, "model", _identifier("model", self.model))
        _hash64("semantic_request_hash", self.semantic_request_hash)
        _hash64("physical_wire_hash", self.physical_wire_hash)


@dataclass(frozen=True, slots=True)
class ReserveFactoryPhysicalAttemptV1(_FactoryPhysicalAttemptCandidateIdentityV1):
    """Reserve capacity for one exact Factory-owned cutoff."""

    def __post_init__(self) -> None:
        self._validate_candidate(
            RESERVE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA,
            "reserve_factory_physical_attempt_schema_mismatch",
        )


@dataclass(frozen=True, slots=True)
class _FactoryPhysicalAttemptReservationIdentityV1(_FactoryPhysicalAttemptCandidateIdentityV1):
    composite_request_hash: str
    reservation_id: str
    provider_request_id: str
    authority_attempt_ordinal: int

    def _validate_reservation(self, expected_schema: str, schema_code: str) -> None:
        self._validate_candidate(expected_schema, schema_code)
        _hash64("composite_request_hash", self.composite_request_hash)
        object.__setattr__(self, "reservation_id", _identifier("reservation_id", self.reservation_id))
        object.__setattr__(self, "provider_request_id", _identifier("provider_request_id", self.provider_request_id))
        object.__setattr__(
            self,
            "authority_attempt_ordinal",
            _positive_int("authority_attempt_ordinal", self.authority_attempt_ordinal),
        )


@dataclass(frozen=True, slots=True)
class FactoryPhysicalAttemptReservationV1(_FactoryPhysicalAttemptReservationIdentityV1):
    """Drain-visible reservation; it does not authorize transport."""

    def __post_init__(self) -> None:
        self._validate_reservation(
            FACTORY_PHYSICAL_ATTEMPT_RESERVATION_SCHEMA,
            "factory_physical_attempt_reservation_schema_mismatch",
        )


@dataclass(frozen=True, slots=True)
class BeginFactoryPhysicalAttemptStartV1(_FactoryPhysicalAttemptReservationIdentityV1):
    """Move one exact reservation into start persistence."""

    def __post_init__(self) -> None:
        self._validate_reservation(
            BEGIN_FACTORY_PHYSICAL_ATTEMPT_START_SCHEMA,
            "begin_factory_physical_attempt_start_schema_mismatch",
        )


@dataclass(frozen=True, slots=True)
class _FactoryPhysicalAttemptStartPermitIdentityV1(_FactoryPhysicalAttemptReservationIdentityV1):
    start_permit_id: str

    def _validate_start_permit(self, expected_schema: str, schema_code: str) -> None:
        self._validate_reservation(expected_schema, schema_code)
        object.__setattr__(self, "start_permit_id", _identifier("start_permit_id", self.start_permit_id))


@dataclass(frozen=True, slots=True)
class FactoryPhysicalAttemptStartPermitV1(_FactoryPhysicalAttemptStartPermitIdentityV1):
    """One exact permission to persist a durable start outside coordinator locks."""

    def __post_init__(self) -> None:
        self._validate_start_permit(
            FACTORY_PHYSICAL_ATTEMPT_START_PERMIT_SCHEMA,
            "factory_physical_attempt_start_permit_schema_mismatch",
        )


@dataclass(frozen=True, slots=True)
class FactoryPhysicalAttemptDefiniteStartNotPersistedProofV1:
    """Durable exact proof that one permitted start was definitely not persisted."""

    schema_version: str
    start_permit: FactoryPhysicalAttemptStartPermitV1
    proof_id: str
    lifecycle_head_sequence: int
    lifecycle_head_hash: str
    proof_kind: str
    durability_acked: bool

    def __post_init__(self) -> None:
        _schema(
            self.schema_version,
            FACTORY_PHYSICAL_ATTEMPT_DEFINITE_START_NOT_PERSISTED_PROOF_SCHEMA,
            "factory_physical_attempt_definite_start_not_persisted_proof_schema_mismatch",
        )
        if type(self.start_permit) is not FactoryPhysicalAttemptStartPermitV1:
            raise TypeError("factory_physical_attempt_start_permit_exact_type_required")
        FactoryPhysicalAttemptStartPermitV1.__post_init__(self.start_permit)
        object.__setattr__(self, "proof_id", _identifier("proof_id", self.proof_id))
        object.__setattr__(
            self,
            "lifecycle_head_sequence",
            _non_negative_int("lifecycle_head_sequence", self.lifecycle_head_sequence),
        )
        _hash64("lifecycle_head_hash", self.lifecycle_head_hash)
        if type(self.proof_kind) is not str:
            raise TypeError("proof_kind_type_invalid")
        if self.proof_kind != "definite_start_not_persisted":
            raise ValueError("proof_kind_invalid")
        if _exact_bool("durability_acked", self.durability_acked) is not True:
            raise ValueError("durability_ack_required")


@dataclass(frozen=True, slots=True)
class ProviderAttemptStartReceiptV1(_FactoryPhysicalAttemptStartPermitIdentityV1):
    """Exact durability ACK returned by the strict lifecycle store."""

    lifecycle_event_id: str
    logical_sequence: int
    event_hash: str
    phase: str
    durability_acked: bool

    def __post_init__(self) -> None:
        self._validate_start_permit(
            PROVIDER_ATTEMPT_START_RECEIPT_SCHEMA,
            "provider_attempt_start_receipt_schema_mismatch",
        )
        object.__setattr__(self, "lifecycle_event_id", _identifier("lifecycle_event_id", self.lifecycle_event_id))
        object.__setattr__(self, "logical_sequence", _positive_int("logical_sequence", self.logical_sequence))
        _hash64("event_hash", self.event_hash)
        if type(self.phase) is not str:
            raise TypeError("phase_type_invalid")
        if self.phase != "start":
            raise ValueError("provider_attempt_start_receipt_phase_mismatch")
        if _exact_bool("durability_acked", self.durability_acked) is not True:
            raise ValueError("durability_ack_required")


@dataclass(frozen=True, slots=True)
class CommitFactoryPhysicalAttemptStartV1(_FactoryPhysicalAttemptStartPermitIdentityV1):
    """Commit one exact durable start and request its physical lease."""

    start_receipt: ProviderAttemptStartReceiptV1

    def __post_init__(self) -> None:
        self._validate_start_permit(
            COMMIT_FACTORY_PHYSICAL_ATTEMPT_START_SCHEMA,
            "commit_factory_physical_attempt_start_schema_mismatch",
        )
        if type(self.start_receipt) is not ProviderAttemptStartReceiptV1:
            raise TypeError("provider_attempt_start_receipt_exact_type_required")
        ProviderAttemptStartReceiptV1.__post_init__(self.start_receipt)
        if not _identity_equal(self, self.start_receipt, _START_PERMIT_IDENTITY_FIELDS):
            raise ValueError("provider_attempt_start_receipt_identity_mismatch")


@dataclass(frozen=True, slots=True)
class FactoryPhysicalAttemptLeaseV1(_FactoryPhysicalAttemptStartPermitIdentityV1):
    """One-shot transport lease minted only from an exact durable start ACK."""

    lease_id: str
    start_receipt: ProviderAttemptStartReceiptV1

    def __post_init__(self) -> None:
        self._validate_start_permit(
            FACTORY_PHYSICAL_ATTEMPT_LEASE_SCHEMA,
            "factory_physical_attempt_lease_schema_mismatch",
        )
        object.__setattr__(self, "lease_id", _identifier("lease_id", self.lease_id))
        if type(self.start_receipt) is not ProviderAttemptStartReceiptV1:
            raise TypeError("provider_attempt_start_receipt_exact_type_required")
        ProviderAttemptStartReceiptV1.__post_init__(self.start_receipt)
        if not _identity_equal(self, self.start_receipt, _START_PERMIT_IDENTITY_FIELDS):
            raise ValueError("factory_physical_attempt_lease_start_receipt_identity_mismatch")


@dataclass(frozen=True, slots=True)
class ProviderAttemptTerminalReceiptV1(_FactoryPhysicalAttemptStartPermitIdentityV1):
    """Exact durability ACK for one terminal lifecycle fact."""

    lease_id: str
    lifecycle_event_id: str
    logical_sequence: int
    event_hash: str
    phase: str
    durability_acked: bool
    terminal_status: str

    def __post_init__(self) -> None:
        self._validate_start_permit(
            PROVIDER_ATTEMPT_TERMINAL_RECEIPT_SCHEMA,
            "provider_attempt_terminal_receipt_schema_mismatch",
        )
        object.__setattr__(self, "lease_id", _identifier("lease_id", self.lease_id))
        object.__setattr__(self, "lifecycle_event_id", _identifier("lifecycle_event_id", self.lifecycle_event_id))
        object.__setattr__(self, "logical_sequence", _positive_int("logical_sequence", self.logical_sequence))
        _hash64("event_hash", self.event_hash)
        if type(self.phase) is not str:
            raise TypeError("phase_type_invalid")
        if self.phase != "terminal":
            raise ValueError("provider_attempt_terminal_receipt_phase_mismatch")
        if _exact_bool("durability_acked", self.durability_acked) is not True:
            raise ValueError("durability_ack_required")
        if type(self.terminal_status) is not str:
            raise TypeError("terminal_status_type_invalid")
        if self.terminal_status not in _TERMINAL_STATUSES:
            raise ValueError("terminal_status_invalid")


@dataclass(frozen=True, slots=True)
class AbortFactoryPhysicalAttemptReservationV1:
    schema_version: str
    reservation: FactoryPhysicalAttemptReservationV1
    start_permit: FactoryPhysicalAttemptStartPermitV1 | None
    definite_start_not_persisted_proof: FactoryPhysicalAttemptDefiniteStartNotPersistedProofV1 | None

    def __post_init__(self) -> None:
        _schema(
            self.schema_version,
            ABORT_FACTORY_PHYSICAL_ATTEMPT_RESERVATION_SCHEMA,
            "abort_factory_physical_attempt_reservation_schema_mismatch",
        )
        if type(self.reservation) is not FactoryPhysicalAttemptReservationV1:
            raise TypeError("factory_physical_attempt_reservation_exact_type_required")
        FactoryPhysicalAttemptReservationV1.__post_init__(self.reservation)
        if self.start_permit is None and self.definite_start_not_persisted_proof is None:
            return
        if type(self.start_permit) is not FactoryPhysicalAttemptStartPermitV1:
            raise TypeError("factory_physical_attempt_start_permit_exact_type_required")
        FactoryPhysicalAttemptStartPermitV1.__post_init__(self.start_permit)
        if type(self.definite_start_not_persisted_proof) is not FactoryPhysicalAttemptDefiniteStartNotPersistedProofV1:
            raise TypeError("factory_physical_attempt_definite_start_proof_exact_type_required")
        FactoryPhysicalAttemptDefiniteStartNotPersistedProofV1.__post_init__(self.definite_start_not_persisted_proof)
        if not _identity_equal(self.reservation, self.start_permit, _RESERVATION_IDENTITY_FIELDS):
            raise ValueError("abort_factory_physical_attempt_start_permit_identity_mismatch")
        if not _identity_equal(
            self.start_permit,
            self.definite_start_not_persisted_proof.start_permit,
            _START_PERMIT_IDENTITY_FIELDS,
        ):
            raise ValueError("abort_factory_physical_attempt_definite_start_proof_identity_mismatch")


@dataclass(frozen=True, slots=True)
class MarkFactoryPhysicalAttemptStartAmbiguousV1:
    schema_version: str
    start_permit: FactoryPhysicalAttemptStartPermitV1
    reason_code: str

    def __post_init__(self) -> None:
        _schema(
            self.schema_version,
            MARK_FACTORY_PHYSICAL_ATTEMPT_START_AMBIGUOUS_SCHEMA,
            "mark_factory_physical_attempt_start_ambiguous_schema_mismatch",
        )
        if type(self.start_permit) is not FactoryPhysicalAttemptStartPermitV1:
            raise TypeError("factory_physical_attempt_start_permit_exact_type_required")
        FactoryPhysicalAttemptStartPermitV1.__post_init__(self.start_permit)
        object.__setattr__(self, "reason_code", _identifier("reason_code", self.reason_code))


@dataclass(frozen=True, slots=True)
class SettleFactoryPhysicalAttemptV1:
    schema_version: str
    lease: FactoryPhysicalAttemptLeaseV1
    terminal_receipt: ProviderAttemptTerminalReceiptV1

    def __post_init__(self) -> None:
        _schema(
            self.schema_version,
            SETTLE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA,
            "settle_factory_physical_attempt_schema_mismatch",
        )
        if type(self.lease) is not FactoryPhysicalAttemptLeaseV1:
            raise TypeError("factory_physical_attempt_lease_exact_type_required")
        FactoryPhysicalAttemptLeaseV1.__post_init__(self.lease)
        if type(self.terminal_receipt) is not ProviderAttemptTerminalReceiptV1:
            raise TypeError("provider_attempt_terminal_receipt_exact_type_required")
        ProviderAttemptTerminalReceiptV1.__post_init__(self.terminal_receipt)
        if not _identity_equal(self.lease, self.terminal_receipt, _LEASE_IDENTITY_FIELDS):
            raise ValueError("settle_factory_physical_attempt_identity_mismatch")


@dataclass(frozen=True, slots=True)
class FailFactoryPhysicalAttemptTerminalV1:
    schema_version: str
    lease: FactoryPhysicalAttemptLeaseV1
    failure_code: str
    error_type: str
    error: str

    def __post_init__(self) -> None:
        _schema(
            self.schema_version,
            FAIL_FACTORY_PHYSICAL_ATTEMPT_TERMINAL_SCHEMA,
            "fail_factory_physical_attempt_terminal_schema_mismatch",
        )
        if type(self.lease) is not FactoryPhysicalAttemptLeaseV1:
            raise TypeError("factory_physical_attempt_lease_exact_type_required")
        FactoryPhysicalAttemptLeaseV1.__post_init__(self.lease)
        object.__setattr__(self, "failure_code", _identifier("failure_code", self.failure_code))
        object.__setattr__(self, "error_type", _identifier("error_type", self.error_type))
        if type(self.error) is not str:
            raise TypeError("error_type_invalid")
        error = self.error.strip()
        if not error:
            raise ValueError("error_missing")
        if len(error) > 500:
            raise ValueError("error_too_long")
        object.__setattr__(self, "error", error)


@dataclass(frozen=True, slots=True)
class FactoryPhysicalAttemptBudgetStateV1:
    """State-derived budget projection for one exact execution authority."""

    schema_version: str
    factory_run_id: str
    execution_authority_hash: str
    attempt_budget: int
    registered: bool
    revoked: bool
    closed: bool
    reserved_count: int
    start_persisting_count: int
    ambiguous_count: int
    committed_count: int
    recovered_count: int
    terminal_count: int
    aborted_count: int
    terminal_failure_count: int
    consumed_attempts: int
    remaining_attempts: int
    inflight_count: int
    settled: bool

    def __post_init__(self) -> None:
        _schema(
            self.schema_version,
            FACTORY_PHYSICAL_ATTEMPT_BUDGET_STATE_SCHEMA,
            "factory_physical_attempt_budget_state_schema_mismatch",
        )
        object.__setattr__(self, "factory_run_id", _identifier("factory_run_id", self.factory_run_id))
        _hash64("execution_authority_hash", self.execution_authority_hash)
        budget = _positive_int("attempt_budget", self.attempt_budget)
        for field_name in ("registered", "revoked", "closed", "settled"):
            _exact_bool(field_name, getattr(self, field_name))
        counts = {
            field_name: _non_negative_int(field_name, getattr(self, field_name))
            for field_name in (
                "reserved_count",
                "start_persisting_count",
                "ambiguous_count",
                "committed_count",
                "recovered_count",
                "terminal_count",
                "aborted_count",
                "terminal_failure_count",
                "consumed_attempts",
                "remaining_attempts",
                "inflight_count",
            )
        }
        if counts["start_persisting_count"] > counts["reserved_count"]:
            raise ValueError("start_persisting_count_inconsistent")
        if counts["recovered_count"] > counts["committed_count"]:
            raise ValueError("recovered_count_inconsistent")
        if counts["terminal_count"] > counts["committed_count"]:
            raise ValueError("terminal_count_inconsistent")
        if counts["terminal_failure_count"] > counts["committed_count"]:
            raise ValueError("terminal_failure_count_inconsistent")
        if counts["terminal_count"] + counts["terminal_failure_count"] > counts["committed_count"]:
            raise ValueError("terminal_outcome_count_inconsistent")
        if counts["consumed_attempts"] != counts["committed_count"]:
            raise ValueError("consumed_attempts_inconsistent")
        raw_remaining = budget - counts["committed_count"] - counts["reserved_count"] - counts["ambiguous_count"]
        if raw_remaining < 0:
            raise ValueError("budget_state_overcommitted")
        if counts["remaining_attempts"] != raw_remaining:
            raise ValueError("remaining_attempts_inconsistent")
        expected_inflight = (
            counts["reserved_count"] + counts["ambiguous_count"] + counts["committed_count"] - counts["terminal_count"]
        )
        if counts["inflight_count"] != expected_inflight:
            raise ValueError("inflight_count_inconsistent")
        expected_settled = expected_inflight == 0 and counts["terminal_failure_count"] == 0
        if self.settled is not expected_settled:
            raise ValueError("settled_state_inconsistent")


@runtime_checkable
class FactoryPhysicalAttemptControlPort(Protocol):
    """Exact synchronous admission protocol; implementations perform no I/O under locks."""

    def reserve(self, command: ReserveFactoryPhysicalAttemptV1) -> FactoryPhysicalAttemptReservationV1: ...

    def begin_start(self, command: BeginFactoryPhysicalAttemptStartV1) -> FactoryPhysicalAttemptStartPermitV1: ...

    def commit_started(self, command: CommitFactoryPhysicalAttemptStartV1) -> FactoryPhysicalAttemptLeaseV1: ...

    def abort_reservation(
        self,
        command: AbortFactoryPhysicalAttemptReservationV1,
    ) -> FactoryPhysicalAttemptBudgetStateV1: ...

    def mark_start_ambiguous(
        self,
        command: MarkFactoryPhysicalAttemptStartAmbiguousV1,
    ) -> FactoryPhysicalAttemptBudgetStateV1: ...

    def settle(self, command: SettleFactoryPhysicalAttemptV1) -> FactoryPhysicalAttemptBudgetStateV1: ...

    def terminal_persistence_failed(
        self,
        command: FailFactoryPhysicalAttemptTerminalV1,
    ) -> FactoryPhysicalAttemptBudgetStateV1: ...


__all__ = [
    "ABORT_FACTORY_PHYSICAL_ATTEMPT_RESERVATION_SCHEMA",
    "BEGIN_FACTORY_PHYSICAL_ATTEMPT_START_SCHEMA",
    "COMMIT_FACTORY_PHYSICAL_ATTEMPT_START_SCHEMA",
    "FACTORY_PHYSICAL_ATTEMPT_BUDGET_STATE_SCHEMA",
    "FACTORY_PHYSICAL_ATTEMPT_CUTOFF_VIEW_SCHEMA",
    "FACTORY_PHYSICAL_ATTEMPT_DEFINITE_START_NOT_PERSISTED_PROOF_SCHEMA",
    "FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA",
    "FACTORY_PHYSICAL_ATTEMPT_LEASE_SCHEMA",
    "FACTORY_PHYSICAL_ATTEMPT_RESERVATION_SCHEMA",
    "FACTORY_PHYSICAL_ATTEMPT_START_PERMIT_SCHEMA",
    "FAIL_FACTORY_PHYSICAL_ATTEMPT_TERMINAL_SCHEMA",
    "MARK_FACTORY_PHYSICAL_ATTEMPT_START_AMBIGUOUS_SCHEMA",
    "PROVIDER_ATTEMPT_START_RECEIPT_SCHEMA",
    "PROVIDER_ATTEMPT_TERMINAL_RECEIPT_SCHEMA",
    "RESERVE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA",
    "SETTLE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA",
    "AbortFactoryPhysicalAttemptReservationV1",
    "BeginFactoryPhysicalAttemptStartV1",
    "CommitFactoryPhysicalAttemptStartV1",
    "FactoryPhysicalAttemptBudgetStateV1",
    "FactoryPhysicalAttemptControlPort",
    "FactoryPhysicalAttemptCutoffViewV1",
    "FactoryPhysicalAttemptDefiniteStartNotPersistedProofV1",
    "FactoryPhysicalAttemptGrantViewV1",
    "FactoryPhysicalAttemptLeaseV1",
    "FactoryPhysicalAttemptReservationV1",
    "FactoryPhysicalAttemptStartPermitV1",
    "FailFactoryPhysicalAttemptTerminalV1",
    "MarkFactoryPhysicalAttemptStartAmbiguousV1",
    "ProviderAttemptStartReceiptV1",
    "ProviderAttemptTerminalReceiptV1",
    "ReserveFactoryPhysicalAttemptV1",
    "SettleFactoryPhysicalAttemptV1",
]
