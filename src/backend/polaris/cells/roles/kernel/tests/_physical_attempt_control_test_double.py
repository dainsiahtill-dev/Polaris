"""Public-contract-only physical-attempt control double for roles.kernel tests.

The production coordinator is owned by ``factory.pipeline``.  Role-kernel
unit tests must exercise their side of the public capability contract without
importing that Cell's private implementation or creating a reverse dependency.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from typing import Any

from polaris.cells.roles.kernel.public.physical_attempt_control import (
    FACTORY_PHYSICAL_ATTEMPT_BUDGET_STATE_SCHEMA,
    FACTORY_PHYSICAL_ATTEMPT_LEASE_SCHEMA,
    FACTORY_PHYSICAL_ATTEMPT_RESERVATION_SCHEMA,
    FACTORY_PHYSICAL_ATTEMPT_START_PERMIT_SCHEMA,
    AbortFactoryPhysicalAttemptReservationV1,
    BeginFactoryPhysicalAttemptStartV1,
    CommitFactoryPhysicalAttemptStartV1,
    FactoryPhysicalAttemptBudgetStateV1,
    FactoryPhysicalAttemptGrantViewV1,
    FactoryPhysicalAttemptLeaseV1,
    FactoryPhysicalAttemptReservationV1,
    FactoryPhysicalAttemptStartPermitV1,
    FailFactoryPhysicalAttemptTerminalV1,
    MarkFactoryPhysicalAttemptStartAmbiguousV1,
    ReserveFactoryPhysicalAttemptV1,
    SettleFactoryPhysicalAttemptV1,
)
from polaris.kernelone.llm.engine.contracts import (
    ProviderAttemptDrainError,
    ProviderAttemptDrainResultV1,
    ProviderAttemptTerminalFailureV1,
)


class FactoryPhysicalAttemptTestControlError(RuntimeError):
    """Stable test-double rejection matching the public control semantics."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class FactoryPhysicalAttemptTestControlPort:
    """Small in-memory implementation of the two public runtime protocols."""

    def __init__(
        self,
        *,
        factory_run_id: str,
        revalidate_active_stage_claim: Callable[[FactoryPhysicalAttemptGrantViewV1], None],
    ) -> None:
        self.factory_run_id = factory_run_id
        self._revalidate_active_stage_claim = revalidate_active_stage_claim
        self._grants: dict[str, FactoryPhysicalAttemptGrantViewV1] = {}
        self._attempts: dict[str, dict[str, object]] = {}
        self._aborted: dict[str, int] = {}
        self._ordinal: dict[str, int] = {}
        self._condition = asyncio.Condition()

    @property
    def verification_scope(self) -> str:
        return "factory"

    @property
    def scope_id(self) -> str:
        return self.factory_run_id

    @property
    def inflight_request_ids(self) -> tuple[str, ...]:
        return self.snapshot().inflight_request_ids

    def register_grant(
        self,
        grant_view: FactoryPhysicalAttemptGrantViewV1,
    ) -> FactoryPhysicalAttemptBudgetStateV1:
        if type(grant_view) is not FactoryPhysicalAttemptGrantViewV1:
            raise FactoryPhysicalAttemptTestControlError("factory_physical_attempt_grant_view_exact_type_required")
        self._revalidate_active_stage_claim(grant_view)
        if grant_view.factory_run_id != self.factory_run_id:
            raise FactoryPhysicalAttemptTestControlError("factory_physical_attempt_factory_run_mismatch")
        self._grants[grant_view.execution_authority_hash] = grant_view
        return self.budget_state(grant_view.execution_authority_hash)

    def reserve(
        self,
        command: ReserveFactoryPhysicalAttemptV1,
    ) -> FactoryPhysicalAttemptReservationV1:
        grant = self._grant(command.execution_authority_hash)
        self._validate_command(command, grant)
        state = self.budget_state(command.execution_authority_hash)
        if state.remaining_attempts <= 0:
            raise FactoryPhysicalAttemptTestControlError("factory_physical_attempt_budget_exhausted")
        ordinal = self._ordinal.get(command.execution_authority_hash, 0) + 1
        self._ordinal[command.execution_authority_hash] = ordinal
        composite_hash = hashlib.sha256(
            "|".join(
                (
                    command.semantic_request_hash,
                    command.physical_wire_hash,
                    str(ordinal),
                )
            ).encode("utf-8")
        ).hexdigest()
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
            composite_request_hash=composite_hash,
            reservation_id=f"reservation-{ordinal}",
            provider_request_id=f"provider-request-{ordinal}",
            authority_attempt_ordinal=ordinal,
        )
        self._attempts[reservation.reservation_id] = {
            "authority_hash": command.execution_authority_hash,
            "reservation": reservation,
            "status": "reserved",
        }
        return reservation

    def begin_start(
        self,
        command: BeginFactoryPhysicalAttemptStartV1,
    ) -> FactoryPhysicalAttemptStartPermitV1:
        record = self._reservation(command.reservation_id)
        self._require_status(record, "reserved")
        reservation = record["reservation"]
        if not isinstance(reservation, FactoryPhysicalAttemptReservationV1):
            raise FactoryPhysicalAttemptTestControlError("factory_physical_attempt_reservation_unknown")
        if self._reservation_identity(command) != self._reservation_identity(reservation):
            raise FactoryPhysicalAttemptTestControlError("factory_physical_attempt_reservation_state_conflict")
        permit = FactoryPhysicalAttemptStartPermitV1(
            schema_version=FACTORY_PHYSICAL_ATTEMPT_START_PERMIT_SCHEMA,
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
            composite_request_hash=command.composite_request_hash,
            reservation_id=command.reservation_id,
            provider_request_id=command.provider_request_id,
            authority_attempt_ordinal=command.authority_attempt_ordinal,
            start_permit_id=f"permit-{command.authority_attempt_ordinal}",
        )
        record["status"] = "start_persisting"
        record["permit"] = permit
        return permit

    def commit_started(
        self,
        command: CommitFactoryPhysicalAttemptStartV1,
    ) -> FactoryPhysicalAttemptLeaseV1:
        record = self._reservation(command.reservation_id)
        self._require_status(record, "start_persisting")
        permit = record.get("permit")
        if not isinstance(permit, FactoryPhysicalAttemptStartPermitV1) or (
            self._start_identity(command) != self._start_identity(permit)
        ):
            raise FactoryPhysicalAttemptTestControlError("factory_physical_attempt_start_permit_state_conflict")
        lease = FactoryPhysicalAttemptLeaseV1(
            schema_version=FACTORY_PHYSICAL_ATTEMPT_LEASE_SCHEMA,
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
            composite_request_hash=command.composite_request_hash,
            reservation_id=command.reservation_id,
            provider_request_id=command.provider_request_id,
            authority_attempt_ordinal=command.authority_attempt_ordinal,
            start_permit_id=command.start_permit_id,
            lease_id=f"lease-{command.authority_attempt_ordinal}",
            start_receipt=command.start_receipt,
        )
        record["status"] = "committed"
        record["lease"] = lease
        return lease

    def abort_reservation(
        self,
        command: AbortFactoryPhysicalAttemptReservationV1,
    ) -> FactoryPhysicalAttemptBudgetStateV1:
        record = self._reservation(command.reservation.reservation_id)
        self._require_status(record, "reserved", "start_persisting")
        record["status"] = "aborted"
        authority_hash = command.reservation.execution_authority_hash
        self._aborted[authority_hash] = self._aborted.get(authority_hash, 0) + 1
        return self.budget_state(authority_hash)

    def mark_start_ambiguous(
        self,
        command: MarkFactoryPhysicalAttemptStartAmbiguousV1,
    ) -> FactoryPhysicalAttemptBudgetStateV1:
        record = self._reservation(command.start_permit.reservation_id)
        self._require_status(record, "start_persisting")
        record["status"] = "ambiguous"
        return self.budget_state(command.start_permit.execution_authority_hash)

    def settle(
        self,
        command: SettleFactoryPhysicalAttemptV1,
    ) -> FactoryPhysicalAttemptBudgetStateV1:
        record = self._reservation(command.lease.reservation_id)
        self._require_status(record, "committed")
        record["status"] = "terminal"
        return self.budget_state(command.lease.execution_authority_hash)

    def terminal_persistence_failed(
        self,
        command: FailFactoryPhysicalAttemptTerminalV1,
    ) -> FactoryPhysicalAttemptBudgetStateV1:
        record = self._reservation(command.lease.reservation_id)
        self._require_status(record, "committed")
        record["status"] = "terminal_failure"
        record["terminal_failure"] = ProviderAttemptTerminalFailureV1(
            provider_request_id=command.lease.provider_request_id,
            error_type=command.error_type,
            error=command.error,
        )
        return self.budget_state(command.lease.execution_authority_hash)

    def budget_state(self, execution_authority_hash: str) -> FactoryPhysicalAttemptBudgetStateV1:
        grant = self._grant(execution_authority_hash)
        statuses = [
            str(record["status"])
            for record in self._attempts.values()
            if record["authority_hash"] == execution_authority_hash
        ]
        reserved = sum(status in {"reserved", "start_persisting"} for status in statuses)
        start_persisting = statuses.count("start_persisting")
        ambiguous = statuses.count("ambiguous")
        committed = sum(status in {"committed", "terminal", "terminal_failure"} for status in statuses)
        terminal = statuses.count("terminal")
        terminal_failure = statuses.count("terminal_failure")
        inflight = reserved + ambiguous + committed - terminal
        remaining = grant.attempt_budget - committed - reserved - ambiguous
        return FactoryPhysicalAttemptBudgetStateV1(
            schema_version=FACTORY_PHYSICAL_ATTEMPT_BUDGET_STATE_SCHEMA,
            factory_run_id=self.factory_run_id,
            execution_authority_hash=execution_authority_hash,
            attempt_budget=grant.attempt_budget,
            registered=True,
            revoked=False,
            closed=False,
            reserved_count=reserved,
            start_persisting_count=start_persisting,
            ambiguous_count=ambiguous,
            committed_count=committed,
            recovered_count=0,
            terminal_count=terminal,
            aborted_count=self._aborted.get(execution_authority_hash, 0),
            terminal_failure_count=terminal_failure,
            consumed_attempts=committed,
            remaining_attempts=remaining,
            inflight_count=inflight,
            settled=inflight == 0 and terminal_failure == 0,
        )

    def snapshot(self) -> ProviderAttemptDrainResultV1:
        inflight: list[str] = []
        failures: list[ProviderAttemptTerminalFailureV1] = []
        for record in self._attempts.values():
            status = str(record["status"])
            reservation = record["reservation"]
            if not isinstance(reservation, FactoryPhysicalAttemptReservationV1):
                continue
            if status not in {"aborted", "terminal"}:
                inflight.append(reservation.provider_request_id)
            failure = record.get("terminal_failure")
            if isinstance(failure, ProviderAttemptTerminalFailureV1):
                failures.append(failure)
        inflight_ids = tuple(sorted(inflight))
        terminal_failures = tuple(sorted(failures, key=lambda item: item.provider_request_id))
        return ProviderAttemptDrainResultV1(
            verification_scope="factory",
            scope_id=self.factory_run_id,
            settled=not inflight_ids and not terminal_failures,
            inflight_request_ids=inflight_ids,
            terminal_failures=terminal_failures,
        )

    async def wait_settled(
        self,
        *,
        verification_scope: str,
        scope_id: str,
        timeout_seconds: float | None = None,
    ) -> ProviderAttemptDrainResultV1:
        if verification_scope != self.verification_scope or scope_id != self.scope_id:
            result = self.snapshot()
            raise ProviderAttemptDrainError(
                "provider attempt drain scope mismatch",
                code="provider_attempt_drain_scope_mismatch",
                result=result,
            )
        deadline = None if timeout_seconds is None else asyncio.get_running_loop().time() + timeout_seconds
        while True:
            result = self.snapshot()
            if result.settled:
                return result
            if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                raise ProviderAttemptDrainError(
                    "provider attempt drain timed out",
                    code="provider_attempt_drain_timeout",
                    result=result,
                )
            await asyncio.sleep(0)

    def _grant(self, execution_authority_hash: str) -> FactoryPhysicalAttemptGrantViewV1:
        grant = self._grants.get(execution_authority_hash)
        if grant is None:
            raise FactoryPhysicalAttemptTestControlError("factory_physical_attempt_execution_authority_hash_mismatch")
        return grant

    def _validate_command(
        self,
        command: ReserveFactoryPhysicalAttemptV1,
        grant: FactoryPhysicalAttemptGrantViewV1,
    ) -> None:
        self._revalidate_active_stage_claim(grant)
        if command.factory_run_id != self.factory_run_id:
            raise FactoryPhysicalAttemptTestControlError("factory_physical_attempt_factory_run_mismatch")
        if command.role != grant.role:
            raise FactoryPhysicalAttemptTestControlError("factory_physical_attempt_role_mismatch")
        if command.attempt_budget != grant.attempt_budget:
            raise FactoryPhysicalAttemptTestControlError("factory_physical_attempt_budget_mismatch")

    def _reservation(self, reservation_id: str) -> dict[str, object]:
        record = self._attempts.get(reservation_id)
        if record is None:
            raise FactoryPhysicalAttemptTestControlError("factory_physical_attempt_reservation_unknown")
        return record

    @staticmethod
    def _require_status(record: dict[str, object], *expected: str) -> None:
        if record.get("status") not in expected:
            raise FactoryPhysicalAttemptTestControlError("factory_physical_attempt_reservation_state_conflict")

    @staticmethod
    def _candidate_identity(source: Any) -> dict[str, object]:
        return {
            name: getattr(source, name)
            for name in (
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
        }

    @classmethod
    def _reservation_identity(cls, source: Any) -> dict[str, object]:
        return {
            **cls._candidate_identity(source),
            **{
                name: getattr(source, name)
                for name in (
                    "composite_request_hash",
                    "reservation_id",
                    "provider_request_id",
                    "authority_attempt_ordinal",
                )
            },
        }

    @classmethod
    def _start_identity(cls, source: Any) -> dict[str, object]:
        return {
            **cls._reservation_identity(source),
            "start_permit_id": source.start_permit_id,
        }


__all__ = [
    "FactoryPhysicalAttemptTestControlError",
    "FactoryPhysicalAttemptTestControlPort",
]
