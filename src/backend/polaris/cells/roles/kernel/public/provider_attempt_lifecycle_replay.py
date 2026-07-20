"""Strict public replay projection for Factory provider-attempt lifecycle facts.

This boundary proves only what roles.kernel durably recorded at one captured
FactStream head.  It never reconstructs or authorizes Factory grants.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Literal, Protocol

from polaris.cells.events.fact_stream.public import (
    AppendSegmentedFactEventCommandV1,
    QuerySegmentedFactEventsV1,
    SegmentedFactLedgerHeadV1,
    SegmentedFactQueryResultV1,
    append_segmented_fact_event,
    query_segmented_fact_events,
)
from polaris.cells.roles.kernel.public.physical_attempt_control import (
    FACTORY_PHYSICAL_ATTEMPT_START_PERMIT_SCHEMA,
    PROVIDER_ATTEMPT_TERMINAL_RECEIPT_SCHEMA,
    FactoryPhysicalAttemptLeaseV1,
    FactoryPhysicalAttemptStartPermitV1,
    ProviderAttemptTerminalReceiptV1,
)
from polaris.kernelone.llm.engine.contracts import FrozenFinalProviderAttemptV1

QUERY_FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SCHEMA = (
    "roles.kernel.factory_provider_attempt_lifecycle_replay.query.v1"
)
FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_FACT_SCHEMA = "roles.kernel.factory_provider_attempt_lifecycle_replay.fact.v1"
FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SNAPSHOT_SCHEMA = (
    "roles.kernel.factory_provider_attempt_lifecycle_replay.snapshot.v1"
)
APPEND_FACTORY_PROVIDER_ATTEMPT_RECOVERY_TERMINAL_SCHEMA = (
    "roles.kernel.factory_provider_attempt_recovery_terminal.append.v1"
)

_START_EVENT_TYPE = "provider_attempt.started"
_TERMINAL_EVENT_TYPE = "provider_attempt.terminal"
_SOURCE = "roles.kernel"
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_HASH_CHARS = frozenset("0123456789abcdef")
_START_PAYLOAD_FIELDS = frozenset(
    {
        "provider_request_id",
        "request_freeze_id",
        "factory_run_id",
        "scope_id",
        "run_id",
        "turn_id",
        "call_id",
        "role",
        "provider",
        "model",
        "attempt_number",
        "verification_scope",
        "context_snapshot_ref",
        "semantic_request_hash",
        "physical_wire_hash",
        "composite_request_hash",
        "pin_hash",
        "execution_authority_hash",
        "attempt_budget",
        "authority_attempt_ordinal",
        "reservation_id",
        "start_permit_id",
    }
)
_TERMINAL_PAYLOAD_FIELDS = frozenset((*_START_PAYLOAD_FIELDS, "lease_id", "status", "error"))


def _text(field_name: str, value: object) -> str:
    if type(value) is not str:
        raise TypeError(f"{field_name}_type_invalid")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name}_missing")
    return normalized


def _hash64(field_name: str, value: object) -> str:
    normalized = _text(field_name, value)
    if len(normalized) != 64 or any(character not in _HASH_CHARS for character in normalized):
        raise ValueError(f"{field_name}_invalid")
    return normalized


def _positive_int(field_name: str, value: object) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name}_type_invalid")
    if value <= 0:
        raise ValueError(f"{field_name}_invalid")
    return value


def factory_provider_attempt_lifecycle_stream(factory_run_id: str) -> str:
    normalized = _text("factory_run_id", factory_run_id)
    run_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return f"roles.kernel.provider_attempts.factory.{run_hash}"


def factory_provider_attempt_recovery_lease_id(factory_run_id: str, provider_request_id: str) -> str:
    material = (
        f"recovery-lease|{_text('factory_run_id', factory_run_id)}|{_text('provider_request_id', provider_request_id)}"
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class QueryFactoryProviderAttemptLifecycleReplayV1:
    schema_version: str
    workspace: str
    factory_run_id: str

    def __post_init__(self) -> None:
        if self.schema_version != QUERY_FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SCHEMA:
            raise ValueError("factory_provider_attempt_lifecycle_replay_query_schema_mismatch")
        object.__setattr__(self, "workspace", _text("workspace", self.workspace))
        object.__setattr__(self, "factory_run_id", _text("factory_run_id", self.factory_run_id))


@dataclass(frozen=True, slots=True)
class FactoryProviderAttemptLifecycleReplayFactV1:
    schema_version: str
    phase: Literal["start", "terminal"]
    lifecycle_event_id: str
    logical_sequence: int
    event_hash: str
    factory_run_id: str
    scope_id: str
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
    composite_request_hash: str
    reservation_id: str
    provider_request_id: str
    authority_attempt_ordinal: int
    start_permit_id: str
    context_snapshot_ref: str
    pin_hash: str
    lease_id: str = ""
    terminal_status: str = ""
    error: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_FACT_SCHEMA:
            raise ValueError("factory_provider_attempt_lifecycle_replay_fact_schema_mismatch")
        if self.phase not in {"start", "terminal"}:
            raise ValueError("provider_attempt_lifecycle_phase_invalid")
        for field_name in (
            "lifecycle_event_id",
            "factory_run_id",
            "scope_id",
            "run_id",
            "role",
            "turn_id",
            "call_id",
            "request_freeze_id",
            "provider",
            "model",
            "reservation_id",
            "provider_request_id",
            "start_permit_id",
        ):
            object.__setattr__(self, field_name, _text(field_name, getattr(self, field_name)))
        object.__setattr__(self, "logical_sequence", _positive_int("logical_sequence", self.logical_sequence))
        object.__setattr__(
            self,
            "authority_attempt_ordinal",
            _positive_int("authority_attempt_ordinal", self.authority_attempt_ordinal),
        )
        object.__setattr__(self, "attempt_budget", _positive_int("attempt_budget", self.attempt_budget))
        for field_name in (
            "event_hash",
            "execution_authority_hash",
            "semantic_request_hash",
            "physical_wire_hash",
            "composite_request_hash",
            "pin_hash",
        ):
            object.__setattr__(self, field_name, _hash64(field_name, getattr(self, field_name)))
        context_ref = _text("context_snapshot_ref", self.context_snapshot_ref)
        if len(context_ref) != 24 or any(character not in _HASH_CHARS for character in context_ref):
            raise ValueError("context_snapshot_ref_invalid")
        object.__setattr__(self, "context_snapshot_ref", context_ref)
        if self.scope_id != self.factory_run_id:
            raise ValueError("provider_attempt_lifecycle_scope_mismatch")
        FactoryPhysicalAttemptStartPermitV1(
            schema_version=FACTORY_PHYSICAL_ATTEMPT_START_PERMIT_SCHEMA,
            verification_scope="factory",
            factory_run_id=self.factory_run_id,
            run_id=self.run_id,
            role=self.role,
            turn_id=self.turn_id,
            call_id=self.call_id,
            request_freeze_id=self.request_freeze_id,
            execution_authority_hash=self.execution_authority_hash,
            attempt_budget=self.attempt_budget,
            provider=self.provider,
            model=self.model,
            semantic_request_hash=self.semantic_request_hash,
            physical_wire_hash=self.physical_wire_hash,
            composite_request_hash=self.composite_request_hash,
            reservation_id=self.reservation_id,
            provider_request_id=self.provider_request_id,
            authority_attempt_ordinal=self.authority_attempt_ordinal,
            start_permit_id=self.start_permit_id,
        )
        lease_id = str(self.lease_id or "").strip()
        terminal_status = str(self.terminal_status or "").strip()
        error = str(self.error or "")
        if self.phase == "start":
            if lease_id or terminal_status or error:
                raise ValueError("provider_attempt_start_terminal_fields_forbidden")
        else:
            if not lease_id:
                raise ValueError("provider_attempt_terminal_lease_id_missing")
            if terminal_status not in _TERMINAL_STATUSES:
                raise ValueError("provider_attempt_terminal_status_invalid")
            if len(error) > 300:
                raise ValueError("provider_attempt_terminal_error_too_long")
        object.__setattr__(self, "lease_id", lease_id)
        object.__setattr__(self, "terminal_status", terminal_status)
        object.__setattr__(self, "error", error)


@dataclass(frozen=True, slots=True)
class FactoryProviderAttemptLifecycleReplaySnapshotV1:
    schema_version: str
    workspace: str
    factory_run_id: str
    logical_stream: str
    captured_head: SegmentedFactLedgerHeadV1
    facts: tuple[FactoryProviderAttemptLifecycleReplayFactV1, ...]

    def __post_init__(self) -> None:
        if self.schema_version != FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SNAPSHOT_SCHEMA:
            raise ValueError("factory_provider_attempt_lifecycle_replay_snapshot_schema_mismatch")
        object.__setattr__(self, "workspace", _text("workspace", self.workspace))
        object.__setattr__(self, "factory_run_id", _text("factory_run_id", self.factory_run_id))
        expected_stream = factory_provider_attempt_lifecycle_stream(self.factory_run_id)
        if self.logical_stream != expected_stream:
            raise ValueError("factory_provider_attempt_lifecycle_stream_mismatch")
        if type(self.captured_head) is not SegmentedFactLedgerHeadV1:
            raise TypeError("segmented_fact_ledger_head_exact_type_required")
        SegmentedFactLedgerHeadV1.__post_init__(self.captured_head)
        if (
            self.captured_head.workspace != self.workspace
            or self.captured_head.logical_stream != self.logical_stream
            or self.captured_head.total_count != len(self.facts)
        ):
            raise ValueError("factory_provider_attempt_lifecycle_snapshot_head_mismatch")
        if type(self.facts) is not tuple or any(
            type(fact) is not FactoryProviderAttemptLifecycleReplayFactV1 for fact in self.facts
        ):
            raise TypeError("provider_attempt_lifecycle_replay_facts_exact_tuple_required")
        for fact in self.facts:
            FactoryProviderAttemptLifecycleReplayFactV1.__post_init__(fact)
            if fact.factory_run_id != self.factory_run_id:
                raise ValueError("provider_attempt_lifecycle_replay_factory_run_mismatch")


@dataclass(frozen=True, slots=True)
class AppendFactoryProviderAttemptRecoveryTerminalV1:
    schema_version: str
    workspace: str
    attempt: FrozenFinalProviderAttemptV1
    lease: FactoryPhysicalAttemptLeaseV1
    context_snapshot_ref: str
    pin_hash: str
    expected_lifecycle_head_sequence: int
    expected_lifecycle_head_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != APPEND_FACTORY_PROVIDER_ATTEMPT_RECOVERY_TERMINAL_SCHEMA:
            raise ValueError("factory_provider_attempt_recovery_terminal_schema_mismatch")
        object.__setattr__(self, "workspace", _text("workspace", self.workspace))
        if type(self.attempt) is not FrozenFinalProviderAttemptV1:
            raise TypeError("frozen_final_provider_attempt_exact_type_required")
        FrozenFinalProviderAttemptV1.__post_init__(self.attempt)
        if type(self.lease) is not FactoryPhysicalAttemptLeaseV1:
            raise TypeError("factory_physical_attempt_lease_exact_type_required")
        FactoryPhysicalAttemptLeaseV1.__post_init__(self.lease)
        if self.attempt.verification_scope != "factory" or not self.attempt.factory_run_id:
            raise ValueError("factory_provider_attempt_recovery_terminal_factory_scope_required")
        identity_fields = (
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
            "composite_request_hash",
            "provider_request_id",
            "authority_attempt_ordinal",
        )
        if any(getattr(self.attempt, field_name) != getattr(self.lease, field_name) for field_name in identity_fields):
            raise ValueError("factory_provider_attempt_recovery_terminal_identity_mismatch")
        if self.lease.lease_id != factory_provider_attempt_recovery_lease_id(
            self.attempt.factory_run_id,
            self.attempt.provider_request_id,
        ):
            raise ValueError("factory_provider_attempt_recovery_lease_required")
        context_ref = _text("context_snapshot_ref", self.context_snapshot_ref)
        if len(context_ref) != 24 or any(character not in _HASH_CHARS for character in context_ref):
            raise ValueError("context_snapshot_ref_invalid")
        object.__setattr__(self, "context_snapshot_ref", context_ref)
        object.__setattr__(self, "pin_hash", _hash64("pin_hash", self.pin_hash))
        if type(self.expected_lifecycle_head_sequence) is not int or self.expected_lifecycle_head_sequence < 1:
            raise ValueError("expected_lifecycle_head_sequence_invalid")
        object.__setattr__(
            self,
            "expected_lifecycle_head_hash",
            _hash64("expected_lifecycle_head_hash", self.expected_lifecycle_head_hash),
        )


class FactoryProviderAttemptRecoveryFencePort(Protocol):
    """Ephemeral Factory-owned capability required for recovery mutation."""

    verification_scope: str
    factory_run_id: str

    def hold_recovery_terminal(
        self,
        command: AppendFactoryProviderAttemptRecoveryTerminalV1,
    ) -> AbstractContextManager[Callable[[], None]]: ...


def query_factory_provider_attempt_lifecycle_replay(
    query: QueryFactoryProviderAttemptLifecycleReplayV1,
) -> FactoryProviderAttemptLifecycleReplaySnapshotV1:
    if type(query) is not QueryFactoryProviderAttemptLifecycleReplayV1:
        raise TypeError("factory_provider_attempt_lifecycle_replay_query_exact_type_required")
    QueryFactoryProviderAttemptLifecycleReplayV1.__post_init__(query)
    logical_stream = factory_provider_attempt_lifecycle_stream(query.factory_run_id)
    events: list[dict[str, object]] = []
    captured_head: SegmentedFactLedgerHeadV1 | None = None
    continuation: str | None = None
    seen_continuations: set[str] = set()
    while True:
        page = query_segmented_fact_events(
            QuerySegmentedFactEventsV1(
                workspace=query.workspace,
                logical_stream=logical_stream,
                limit=511,
                continuation=continuation,
                strict_integrity=True,
            )
        )
        if type(page) is not SegmentedFactQueryResultV1:
            raise RuntimeError("provider_attempt_lifecycle_replay_page_exact_type_required")
        SegmentedFactQueryResultV1.__post_init__(page)
        if captured_head is None:
            captured_head = page.captured_head
        elif page.captured_head != captured_head:
            raise RuntimeError("provider_attempt_lifecycle_replay_head_drift")
        events.extend(page.events)
        continuation = page.continuation
        if continuation is None:
            break
        if continuation in seen_continuations:
            raise RuntimeError("provider_attempt_lifecycle_replay_continuation_cycle")
        seen_continuations.add(continuation)
    if captured_head is None or len(events) != captured_head.total_count:
        raise RuntimeError("provider_attempt_lifecycle_replay_incomplete")

    facts = tuple(
        _parse_lifecycle_event(
            event,
            expected_sequence=expected_sequence,
            logical_stream=logical_stream,
            factory_run_id=query.factory_run_id,
        )
        for expected_sequence, event in enumerate(events, start=1)
    )
    _validate_lifecycle_pairs(facts)
    return FactoryProviderAttemptLifecycleReplaySnapshotV1(
        schema_version=FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SNAPSHOT_SCHEMA,
        workspace=query.workspace,
        factory_run_id=query.factory_run_id,
        logical_stream=logical_stream,
        captured_head=captured_head,
        facts=facts,
    )


def append_factory_provider_attempt_recovery_terminal(
    command: AppendFactoryProviderAttemptRecoveryTerminalV1,
    *,
    recovery_fence: FactoryProviderAttemptRecoveryFencePort,
) -> ProviderAttemptTerminalReceiptV1:
    """CAS-append one cancelled terminal for a replay-proven unmatched start."""

    if type(command) is not AppendFactoryProviderAttemptRecoveryTerminalV1:
        raise TypeError("factory_provider_attempt_recovery_terminal_command_exact_type_required")
    AppendFactoryProviderAttemptRecoveryTerminalV1.__post_init__(command)
    if (
        recovery_fence.verification_scope != "factory"
        or recovery_fence.factory_run_id != command.attempt.factory_run_id
    ):
        raise RuntimeError("factory_provider_attempt_recovery_fence_scope_mismatch")
    with recovery_fence.hold_recovery_terminal(command) as revalidate:
        if not callable(revalidate):
            raise TypeError("factory_provider_attempt_recovery_revalidator_required")
        revalidate()
        replay = query_factory_provider_attempt_lifecycle_replay(
            QueryFactoryProviderAttemptLifecycleReplayV1(
                schema_version=QUERY_FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SCHEMA,
                workspace=command.workspace,
                factory_run_id=command.attempt.factory_run_id,
            )
        )
        existing_terminal = next(
            (
                fact
                for fact in replay.facts
                if fact.phase == "terminal" and fact.provider_request_id == command.attempt.provider_request_id
            ),
            None,
        )
        if existing_terminal is not None:
            _require_recovery_terminal_identity(existing_terminal, command)
            revalidate()
            return _terminal_receipt_from_replay(existing_terminal, command.lease)
        if (
            replay.captured_head.global_seq != command.expected_lifecycle_head_sequence
            or replay.captured_head.head_hash != command.expected_lifecycle_head_hash
        ):
            raise RuntimeError("factory_provider_attempt_recovery_terminal_head_drift")
        start = next(
            (
                fact
                for fact in replay.facts
                if fact.phase == "start" and fact.provider_request_id == command.attempt.provider_request_id
            ),
            None,
        )
        if start is None:
            raise RuntimeError("factory_provider_attempt_recovery_terminal_start_missing")
        _require_recovery_start_identity(start, command)
        payload = {
            "provider_request_id": command.attempt.provider_request_id,
            "request_freeze_id": command.attempt.request_freeze_id,
            "factory_run_id": command.attempt.factory_run_id,
            "scope_id": command.attempt.scope_id,
            "run_id": command.attempt.run_id,
            "turn_id": command.attempt.turn_id,
            "call_id": command.attempt.call_id,
            "role": command.attempt.role,
            "provider": command.attempt.provider,
            "model": command.attempt.model,
            "attempt_number": command.attempt.attempt_number,
            "verification_scope": "factory",
            "context_snapshot_ref": command.context_snapshot_ref,
            "semantic_request_hash": command.attempt.semantic_request_hash,
            "physical_wire_hash": command.attempt.physical_wire_hash,
            "composite_request_hash": command.attempt.composite_request_hash,
            "pin_hash": command.pin_hash,
            "execution_authority_hash": command.lease.execution_authority_hash,
            "attempt_budget": command.lease.attempt_budget,
            "authority_attempt_ordinal": command.lease.authority_attempt_ordinal,
            "reservation_id": command.lease.reservation_id,
            "start_permit_id": command.lease.start_permit_id,
            "lease_id": command.lease.lease_id,
            "status": "cancelled",
            "error": "recovered unmatched durable start; physical redispatch forbidden",
        }
        revalidate()
        appended = append_segmented_fact_event(
            AppendSegmentedFactEventCommandV1(
                workspace=command.workspace,
                logical_stream=replay.logical_stream,
                event_type=_TERMINAL_EVENT_TYPE,
                source=_SOURCE,
                payload=payload,
                idempotency_key=f"{command.attempt.provider_request_id}:terminal",
                expected_global_seq=command.expected_lifecycle_head_sequence + 1,
            )
        )
        revalidate()
        return ProviderAttemptTerminalReceiptV1(
            schema_version=PROVIDER_ATTEMPT_TERMINAL_RECEIPT_SCHEMA,
            verification_scope=command.lease.verification_scope,
            factory_run_id=command.lease.factory_run_id,
            run_id=command.lease.run_id,
            role=command.lease.role,
            turn_id=command.lease.turn_id,
            call_id=command.lease.call_id,
            request_freeze_id=command.lease.request_freeze_id,
            execution_authority_hash=command.lease.execution_authority_hash,
            attempt_budget=command.lease.attempt_budget,
            provider=command.lease.provider,
            model=command.lease.model,
            semantic_request_hash=command.lease.semantic_request_hash,
            physical_wire_hash=command.lease.physical_wire_hash,
            composite_request_hash=command.lease.composite_request_hash,
            reservation_id=command.lease.reservation_id,
            provider_request_id=command.lease.provider_request_id,
            authority_attempt_ordinal=command.lease.authority_attempt_ordinal,
            start_permit_id=command.lease.start_permit_id,
            lease_id=command.lease.lease_id,
            lifecycle_event_id=appended.event_id,
            logical_sequence=appended.global_seq,
            event_hash=appended.event_hash,
            phase="terminal",
            durability_acked=True,
            terminal_status="cancelled",
        )


def _require_recovery_start_identity(
    start: FactoryProviderAttemptLifecycleReplayFactV1,
    command: AppendFactoryProviderAttemptRecoveryTerminalV1,
) -> None:
    attempt = command.attempt
    lease = command.lease
    expected = {
        "factory_run_id": attempt.factory_run_id,
        "scope_id": attempt.scope_id,
        "run_id": attempt.run_id,
        "role": attempt.role,
        "turn_id": attempt.turn_id,
        "call_id": attempt.call_id,
        "request_freeze_id": attempt.request_freeze_id,
        "execution_authority_hash": attempt.execution_authority_hash,
        "attempt_budget": attempt.attempt_budget,
        "provider": attempt.provider,
        "model": attempt.model,
        "semantic_request_hash": attempt.semantic_request_hash,
        "physical_wire_hash": attempt.physical_wire_hash,
        "composite_request_hash": attempt.composite_request_hash,
        "reservation_id": lease.reservation_id,
        "provider_request_id": attempt.provider_request_id,
        "authority_attempt_ordinal": attempt.authority_attempt_ordinal,
        "start_permit_id": lease.start_permit_id,
        "context_snapshot_ref": command.context_snapshot_ref,
        "pin_hash": command.pin_hash,
    }
    if any(getattr(start, field_name) != value for field_name, value in expected.items()):
        raise RuntimeError("factory_provider_attempt_recovery_terminal_start_identity_mismatch")


def _require_recovery_terminal_identity(
    terminal: FactoryProviderAttemptLifecycleReplayFactV1,
    command: AppendFactoryProviderAttemptRecoveryTerminalV1,
) -> None:
    _require_recovery_start_identity(terminal, command)
    if terminal.lease_id != command.lease.lease_id or terminal.terminal_status != "cancelled":
        raise RuntimeError("factory_provider_attempt_recovery_terminal_identity_mismatch")


def _terminal_receipt_from_replay(
    terminal: FactoryProviderAttemptLifecycleReplayFactV1,
    lease: FactoryPhysicalAttemptLeaseV1,
) -> ProviderAttemptTerminalReceiptV1:
    return ProviderAttemptTerminalReceiptV1(
        schema_version=PROVIDER_ATTEMPT_TERMINAL_RECEIPT_SCHEMA,
        verification_scope=lease.verification_scope,
        factory_run_id=lease.factory_run_id,
        run_id=lease.run_id,
        role=lease.role,
        turn_id=lease.turn_id,
        call_id=lease.call_id,
        request_freeze_id=lease.request_freeze_id,
        execution_authority_hash=lease.execution_authority_hash,
        attempt_budget=lease.attempt_budget,
        provider=lease.provider,
        model=lease.model,
        semantic_request_hash=lease.semantic_request_hash,
        physical_wire_hash=lease.physical_wire_hash,
        composite_request_hash=lease.composite_request_hash,
        reservation_id=lease.reservation_id,
        provider_request_id=lease.provider_request_id,
        authority_attempt_ordinal=lease.authority_attempt_ordinal,
        start_permit_id=lease.start_permit_id,
        lease_id=lease.lease_id,
        lifecycle_event_id=terminal.lifecycle_event_id,
        logical_sequence=terminal.logical_sequence,
        event_hash=terminal.event_hash,
        phase="terminal",
        durability_acked=True,
        terminal_status=terminal.terminal_status,
    )


def _parse_lifecycle_event(
    event: object,
    *,
    expected_sequence: int,
    logical_stream: str,
    factory_run_id: str,
) -> FactoryProviderAttemptLifecycleReplayFactV1:
    if type(event) is not dict:
        raise RuntimeError("provider_attempt_lifecycle_replay_event_invalid")
    event_type = event.get("event_type")
    if event_type not in {_START_EVENT_TYPE, _TERMINAL_EVENT_TYPE}:
        raise RuntimeError("provider_attempt_lifecycle_replay_event_type_invalid")
    if event.get("logical_stream") != logical_stream or event.get("source") != _SOURCE:
        raise RuntimeError("provider_attempt_lifecycle_replay_event_authority_mismatch")
    if event.get("global_seq") != expected_sequence:
        raise RuntimeError("provider_attempt_lifecycle_replay_sequence_mismatch")
    payload = event.get("payload")
    if type(payload) is not dict:
        raise RuntimeError("provider_attempt_lifecycle_replay_payload_invalid")
    expected_fields = _START_PAYLOAD_FIELDS if event_type == _START_EVENT_TYPE else _TERMINAL_PAYLOAD_FIELDS
    if frozenset(payload) != expected_fields:
        raise RuntimeError("provider_attempt_lifecycle_replay_payload_fields_mismatch")
    if payload.get("factory_run_id") != factory_run_id or payload.get("verification_scope") != "factory":
        raise RuntimeError("provider_attempt_lifecycle_replay_factory_authority_mismatch")
    ordinal = payload.get("authority_attempt_ordinal")
    if payload.get("attempt_number") != ordinal:
        raise RuntimeError("provider_attempt_lifecycle_replay_ordinal_mismatch")
    phase: Literal["start", "terminal"] = "start" if event_type == _START_EVENT_TYPE else "terminal"
    return FactoryProviderAttemptLifecycleReplayFactV1(
        schema_version=FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_FACT_SCHEMA,
        phase=phase,
        lifecycle_event_id=event.get("event_id"),  # type: ignore[arg-type]
        logical_sequence=event.get("global_seq"),  # type: ignore[arg-type]
        event_hash=event.get("event_hash"),  # type: ignore[arg-type]
        factory_run_id=payload.get("factory_run_id"),  # type: ignore[arg-type]
        scope_id=payload.get("scope_id"),  # type: ignore[arg-type]
        run_id=payload.get("run_id"),  # type: ignore[arg-type]
        role=payload.get("role"),  # type: ignore[arg-type]
        turn_id=payload.get("turn_id"),  # type: ignore[arg-type]
        call_id=payload.get("call_id"),  # type: ignore[arg-type]
        request_freeze_id=payload.get("request_freeze_id"),  # type: ignore[arg-type]
        execution_authority_hash=payload.get("execution_authority_hash"),  # type: ignore[arg-type]
        attempt_budget=payload.get("attempt_budget"),  # type: ignore[arg-type]
        provider=payload.get("provider"),  # type: ignore[arg-type]
        model=payload.get("model"),  # type: ignore[arg-type]
        semantic_request_hash=payload.get("semantic_request_hash"),  # type: ignore[arg-type]
        physical_wire_hash=payload.get("physical_wire_hash"),  # type: ignore[arg-type]
        composite_request_hash=payload.get("composite_request_hash"),  # type: ignore[arg-type]
        reservation_id=payload.get("reservation_id"),  # type: ignore[arg-type]
        provider_request_id=payload.get("provider_request_id"),  # type: ignore[arg-type]
        authority_attempt_ordinal=ordinal,  # type: ignore[arg-type]
        start_permit_id=payload.get("start_permit_id"),  # type: ignore[arg-type]
        context_snapshot_ref=payload.get("context_snapshot_ref"),  # type: ignore[arg-type]
        pin_hash=payload.get("pin_hash"),  # type: ignore[arg-type]
        lease_id=payload.get("lease_id", ""),  # type: ignore[arg-type]
        terminal_status=payload.get("status", ""),  # type: ignore[arg-type]
        error=payload.get("error", ""),  # type: ignore[arg-type]
    )


def _validate_lifecycle_pairs(facts: tuple[FactoryProviderAttemptLifecycleReplayFactV1, ...]) -> None:
    starts: dict[str, FactoryProviderAttemptLifecycleReplayFactV1] = {}
    terminals: set[str] = set()
    lifecycle_ids: set[str] = set()
    lifecycle_hashes: set[str] = set()
    for fact in facts:
        if fact.lifecycle_event_id in lifecycle_ids or fact.event_hash in lifecycle_hashes:
            raise RuntimeError("provider_attempt_lifecycle_replay_duplicate_identity")
        lifecycle_ids.add(fact.lifecycle_event_id)
        lifecycle_hashes.add(fact.event_hash)
        if fact.phase == "start":
            if fact.provider_request_id in starts:
                raise RuntimeError("provider_attempt_lifecycle_replay_duplicate_start")
            starts[fact.provider_request_id] = fact
            continue
        start = starts.get(fact.provider_request_id)
        if start is None or fact.provider_request_id in terminals:
            raise RuntimeError("provider_attempt_lifecycle_replay_terminal_without_exact_start")
        identity_fields = (
            "factory_run_id",
            "scope_id",
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
            "composite_request_hash",
            "reservation_id",
            "provider_request_id",
            "authority_attempt_ordinal",
            "start_permit_id",
            "context_snapshot_ref",
            "pin_hash",
        )
        if any(getattr(start, field_name) != getattr(fact, field_name) for field_name in identity_fields):
            raise RuntimeError("provider_attempt_lifecycle_replay_terminal_identity_mismatch")
        if fact.logical_sequence <= start.logical_sequence:
            raise RuntimeError("provider_attempt_lifecycle_replay_terminal_sequence_invalid")
        terminals.add(fact.provider_request_id)


__all__ = [
    "APPEND_FACTORY_PROVIDER_ATTEMPT_RECOVERY_TERMINAL_SCHEMA",
    "FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_FACT_SCHEMA",
    "FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SNAPSHOT_SCHEMA",
    "QUERY_FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SCHEMA",
    "AppendFactoryProviderAttemptRecoveryTerminalV1",
    "FactoryProviderAttemptLifecycleReplayFactV1",
    "FactoryProviderAttemptLifecycleReplaySnapshotV1",
    "FactoryProviderAttemptRecoveryFencePort",
    "QueryFactoryProviderAttemptLifecycleReplayV1",
    "append_factory_provider_attempt_recovery_terminal",
    "factory_provider_attempt_lifecycle_stream",
    "factory_provider_attempt_recovery_lease_id",
    "query_factory_provider_attempt_lifecycle_replay",
]
