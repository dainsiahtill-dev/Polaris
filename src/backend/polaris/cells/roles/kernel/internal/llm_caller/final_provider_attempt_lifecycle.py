"""Durable physical provider-attempt lifecycle over segmented FactStream."""

from __future__ import annotations

import hashlib
import re
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, cast

from polaris.cells.events.fact_stream.public import (
    AppendSegmentedFactEventCommandV1,
    EnsureSegmentedFactLedgerCommandV1,
    FactStreamError,
    QuerySegmentedFactEventsV1,
    append_segmented_fact_event,
    ensure_segmented_fact_ledger,
    query_segmented_fact_events,
)
from polaris.cells.roles.kernel.public.physical_attempt_control import (
    FACTORY_PHYSICAL_ATTEMPT_DEFINITE_START_NOT_PERSISTED_PROOF_SCHEMA,
    PROVIDER_ATTEMPT_START_RECEIPT_SCHEMA,
    PROVIDER_ATTEMPT_TERMINAL_RECEIPT_SCHEMA,
    FactoryPhysicalAttemptDefiniteStartNotPersistedProofV1,
    FactoryPhysicalAttemptLeaseV1,
    FactoryPhysicalAttemptStartPermitV1,
    ProviderAttemptStartReceiptV1,
    ProviderAttemptTerminalReceiptV1,
)
from polaris.kernelone.llm.engine.contracts import FrozenFinalProviderAttemptV1
from polaris.kernelone.llm.engine.internal.context_hash import validate_context_hash
from polaris.kernelone.storage import resolve_storage_roots

_PIN_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


class StrictProviderAttemptLifecycleStore:
    """Exactly-paired start/terminal facts keyed by physical request id."""

    _validation_cache_lock = threading.Lock()
    _validated_ledgers: OrderedDict[tuple[str, str, int, int, str], None] = OrderedDict()
    _validation_cache_max_size = 512

    def __init__(self, *, workspace: str, logical_stream: str, verification_scope: str, scope_id: str) -> None:
        self.workspace = str(workspace)
        self.logical_stream = str(logical_stream)
        self.verification_scope = str(verification_scope)
        self.scope_id = str(scope_id)
        if self.verification_scope not in {"factory", "role_session"} or not self.scope_id:
            raise ValueError("provider attempt lifecycle requires an explicit Factory run or role session scope")
        self._ensure_ledger_once_per_runtime_identity()

    def _ensure_ledger_once_per_runtime_identity(self) -> None:
        """Validate once per physical runtime identity and logical stream.

        Factory startup already enrolls lifecycle ledgers. Re-running the
        full segmented-ledger scan for every provider request needlessly holds
        the shared filesystem authority lock and can starve later Director
        tasks. The cache is process-local and includes the runtime root's
        device/inode identity: restart, workspace recreation, or stream change
        therefore performs a fresh fail-closed validation.
        """

        roots = resolve_storage_roots(self.workspace)
        workspace_abs = str(Path(roots.workspace_abs).resolve())
        runtime_root = Path(roots.runtime_root)
        with self._validation_cache_lock:
            cache_key = self._physical_cache_key(
                workspace_abs=workspace_abs,
                runtime_root=runtime_root,
            )
            if cache_key is not None and cache_key in self._validated_ledgers:
                self._validated_ledgers.move_to_end(cache_key)
                return

            ensure_segmented_fact_ledger(
                EnsureSegmentedFactLedgerCommandV1(
                    workspace=self.workspace,
                    logical_stream=self.logical_stream,
                    maintenance_reason="provider_attempt_lifecycle_open",
                )
            )
            cache_key = self._physical_cache_key(
                workspace_abs=workspace_abs,
                runtime_root=runtime_root,
            )
            if cache_key is None:
                raise RuntimeError("provider attempt lifecycle runtime identity is unavailable after ledger ensure")
            self._validated_ledgers[cache_key] = None
            self._validated_ledgers.move_to_end(cache_key)
            while len(self._validated_ledgers) > self._validation_cache_max_size:
                self._validated_ledgers.popitem(last=False)

    def _physical_cache_key(
        self,
        *,
        workspace_abs: str,
        runtime_root: Path,
    ) -> tuple[str, str, int, int, str] | None:
        try:
            stat = runtime_root.stat()
        except FileNotFoundError:
            return None
        return (
            workspace_abs,
            str(runtime_root.resolve()),
            int(stat.st_dev),
            int(stat.st_ino),
            self.logical_stream,
        )

    @classmethod
    def for_factory_run(cls, *, workspace: str, factory_run_id: str) -> StrictProviderAttemptLifecycleStore:
        run_hash = hashlib.sha256(str(factory_run_id).encode("utf-8")).hexdigest()[:24]
        return cls(
            workspace=workspace,
            logical_stream=f"roles.kernel.provider_attempts.factory.{run_hash}",
            verification_scope="factory",
            scope_id=factory_run_id,
        )

    @classmethod
    def for_role_session(cls, *, workspace: str, role_session_id: str) -> StrictProviderAttemptLifecycleStore:
        session_hash = hashlib.sha256(str(role_session_id).encode("utf-8")).hexdigest()[:24]
        return cls(
            workspace=workspace,
            logical_stream=f"roles.kernel.provider_attempts.session.{session_hash}",
            verification_scope="role_session",
            scope_id=role_session_id,
        )

    def append_start(
        self,
        attempt: FrozenFinalProviderAttemptV1,
        *,
        start_permit: FactoryPhysicalAttemptStartPermitV1 | None = None,
        context_snapshot_ref: str,
        pin_hash: str,
    ) -> ProviderAttemptStartReceiptV1 | None:
        self._validate_attempt_scope(attempt)
        self._validate_evidence(context_snapshot_ref=context_snapshot_ref, pin_hash=pin_hash)
        if self.verification_scope == "factory":
            if type(start_permit) is not FactoryPhysicalAttemptStartPermitV1:
                raise TypeError("factory_physical_attempt_start_permit_exact_type_required")
            start_permit = cast(FactoryPhysicalAttemptStartPermitV1, start_permit)
            start_permit.__post_init__()
            self._validate_start_permit_identity(attempt, start_permit)
        elif start_permit is not None:
            raise TypeError("role_session_start_permit_forbidden")
        payload = self._base_payload(
            attempt,
            context_snapshot_ref=context_snapshot_ref,
            pin_hash=pin_hash,
        )
        if start_permit is not None:
            payload.update(self._factory_identity_payload(start_permit))
        appended = append_segmented_fact_event(
            AppendSegmentedFactEventCommandV1(
                workspace=self.workspace,
                logical_stream=self.logical_stream,
                event_type="provider_attempt.started",
                source="roles.kernel",
                payload=payload,
                idempotency_key=f"{attempt.provider_request_id}:start",
            )
        )
        if start_permit is None:
            return None
        return ProviderAttemptStartReceiptV1(
            schema_version=PROVIDER_ATTEMPT_START_RECEIPT_SCHEMA,
            verification_scope=start_permit.verification_scope,
            factory_run_id=start_permit.factory_run_id,
            run_id=start_permit.run_id,
            role=start_permit.role,
            turn_id=start_permit.turn_id,
            call_id=start_permit.call_id,
            request_freeze_id=start_permit.request_freeze_id,
            execution_authority_hash=start_permit.execution_authority_hash,
            attempt_budget=start_permit.attempt_budget,
            provider=start_permit.provider,
            model=start_permit.model,
            semantic_request_hash=start_permit.semantic_request_hash,
            physical_wire_hash=start_permit.physical_wire_hash,
            composite_request_hash=start_permit.composite_request_hash,
            reservation_id=start_permit.reservation_id,
            provider_request_id=start_permit.provider_request_id,
            authority_attempt_ordinal=start_permit.authority_attempt_ordinal,
            start_permit_id=start_permit.start_permit_id,
            lifecycle_event_id=appended.event_id,
            logical_sequence=appended.global_seq,
            event_hash=appended.event_hash,
            phase="start",
            durability_acked=True,
        )

    def append_terminal(
        self,
        attempt: FrozenFinalProviderAttemptV1,
        *,
        lease: FactoryPhysicalAttemptLeaseV1 | None = None,
        context_snapshot_ref: str,
        pin_hash: str,
        status: str,
        error: str | None = None,
    ) -> ProviderAttemptTerminalReceiptV1 | None:
        self._validate_attempt_scope(attempt)
        self._validate_evidence(context_snapshot_ref=context_snapshot_ref, pin_hash=pin_hash)
        if self.verification_scope == "factory":
            if type(lease) is not FactoryPhysicalAttemptLeaseV1:
                raise TypeError("factory_physical_attempt_lease_exact_type_required")
            lease = cast(FactoryPhysicalAttemptLeaseV1, lease)
            lease.__post_init__()
            self._validate_lease_identity(attempt, lease)
        elif lease is not None:
            raise TypeError("role_session_physical_attempt_lease_forbidden")
        status_token = str(status or "").strip()
        if status_token not in _TERMINAL_STATUSES:
            raise ValueError("provider attempt terminal status is invalid")
        start_payload = self._base_payload(
            attempt,
            context_snapshot_ref=context_snapshot_ref,
            pin_hash=pin_hash,
        )
        if lease is not None:
            start_payload.update(self._factory_identity_payload(lease, include_lease_id=False))
        try:
            append_segmented_fact_event(
                AppendSegmentedFactEventCommandV1(
                    workspace=self.workspace,
                    logical_stream=self.logical_stream,
                    event_type="provider_attempt.started",
                    source="roles.kernel",
                    payload=start_payload,
                    idempotency_key=f"{attempt.provider_request_id}:start",
                    require_idempotency_replay=True,
                )
            )
        except FactStreamError as exc:
            raise RuntimeError("authoritative provider attempt start is missing or ambiguous") from exc
        payload = self._base_payload(attempt, context_snapshot_ref=context_snapshot_ref, pin_hash=pin_hash)
        if lease is not None:
            payload.update(self._factory_identity_payload(lease))
        payload.update({"status": status_token, "error": str(error or "")[:300]})
        appended = append_segmented_fact_event(
            AppendSegmentedFactEventCommandV1(
                workspace=self.workspace,
                logical_stream=self.logical_stream,
                event_type="provider_attempt.terminal",
                source="roles.kernel",
                payload=payload,
                idempotency_key=f"{attempt.provider_request_id}:terminal",
            )
        )
        if lease is None:
            return None
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
            lifecycle_event_id=appended.event_id,
            logical_sequence=appended.global_seq,
            event_hash=appended.event_hash,
            phase="terminal",
            durability_acked=True,
            terminal_status=status_token,
        )

    def query_strict(self) -> tuple[dict[str, Any], ...]:
        facts: list[dict[str, Any]] = []
        continuation: str | None = None
        while True:
            result = query_segmented_fact_events(
                QuerySegmentedFactEventsV1(
                    workspace=self.workspace,
                    logical_stream=self.logical_stream,
                    limit=100,
                    continuation=continuation,
                )
            )
            facts.extend(result.events)
            continuation = result.continuation
            if continuation is None:
                return tuple(facts)

    def prove_start_not_persisted(
        self,
        start_permit: FactoryPhysicalAttemptStartPermitV1,
    ) -> FactoryPhysicalAttemptDefiniteStartNotPersistedProofV1 | None:
        """Return a captured-head proof only when the exact start fact is absent.

        A persisted start with a lost durability ACK returns ``None``.  Query,
        integrity, pagination, or identity ambiguity raises instead of being
        projected as absence.
        """

        if self.verification_scope != "factory":
            raise RuntimeError("definite start absence proof requires Factory scope")
        if type(start_permit) is not FactoryPhysicalAttemptStartPermitV1:
            raise TypeError("factory_physical_attempt_start_permit_exact_type_required")
        FactoryPhysicalAttemptStartPermitV1.__post_init__(start_permit)
        if start_permit.factory_run_id != self.scope_id:
            raise RuntimeError("provider attempt Factory run mismatch")

        facts: list[dict[str, Any]] = []
        captured_head: Any | None = None
        continuation: str | None = None
        while True:
            page = query_segmented_fact_events(
                QuerySegmentedFactEventsV1(
                    workspace=self.workspace,
                    logical_stream=self.logical_stream,
                    limit=100,
                    continuation=continuation,
                )
            )
            if captured_head is None:
                captured_head = page.captured_head
            elif page.captured_head != captured_head:
                raise RuntimeError("provider attempt lifecycle captured head drift")
            facts.extend(page.events)
            continuation = page.continuation
            if continuation is None:
                break

        if captured_head is None or len(facts) != captured_head.total_count:
            raise RuntimeError("provider attempt lifecycle strict query incomplete")
        for fact in facts:
            if fact.get("event_type") != "provider_attempt.started":
                continue
            payload = fact.get("payload")
            if type(payload) is not dict:
                raise RuntimeError("provider attempt lifecycle start payload invalid")
            if payload.get("provider_request_id") != start_permit.provider_request_id:
                continue
            if not self._start_payload_matches_permit(payload, start_permit):
                raise RuntimeError("provider attempt lifecycle start identity conflict")
            return None

        proof_material = "|".join(
            (
                start_permit.factory_run_id,
                start_permit.provider_request_id,
                start_permit.reservation_id,
                start_permit.start_permit_id,
                str(captured_head.global_seq),
                captured_head.head_hash,
            )
        )
        return FactoryPhysicalAttemptDefiniteStartNotPersistedProofV1(
            schema_version=FACTORY_PHYSICAL_ATTEMPT_DEFINITE_START_NOT_PERSISTED_PROOF_SCHEMA,
            start_permit=start_permit,
            proof_id=hashlib.sha256(proof_material.encode("utf-8")).hexdigest(),
            lifecycle_head_sequence=captured_head.global_seq,
            lifecycle_head_hash=captured_head.head_hash,
            proof_kind="definite_start_not_persisted",
            durability_acked=True,
        )

    @staticmethod
    def _base_payload(
        attempt: FrozenFinalProviderAttemptV1,
        *,
        context_snapshot_ref: str,
        pin_hash: str,
    ) -> dict[str, Any]:
        return {
            "provider_request_id": attempt.provider_request_id,
            "request_freeze_id": attempt.request_freeze_id,
            "factory_run_id": attempt.factory_run_id,
            "scope_id": attempt.scope_id,
            "run_id": attempt.run_id,
            "turn_id": attempt.turn_id,
            "call_id": attempt.call_id,
            "role": attempt.role,
            "provider": attempt.provider,
            "model": attempt.model,
            "attempt_number": attempt.attempt_number,
            "verification_scope": attempt.verification_scope,
            "context_snapshot_ref": context_snapshot_ref,
            "semantic_candidate_hash": attempt.semantic_candidate_hash,
            "semantic_request_hash": attempt.semantic_request_hash,
            "physical_wire_hash": attempt.physical_wire_hash,
            "composite_request_hash": attempt.composite_request_hash,
            "pin_hash": pin_hash,
        }

    def _validate_attempt_scope(self, attempt: FrozenFinalProviderAttemptV1) -> None:
        if attempt.verification_scope != self.verification_scope or attempt.scope_id != self.scope_id:
            raise RuntimeError("provider attempt verification scope mismatch")
        if self.verification_scope == "factory" and attempt.factory_run_id != self.scope_id:
            raise RuntimeError("provider attempt Factory run mismatch")
        if self.verification_scope == "role_session" and attempt.factory_run_id:
            raise RuntimeError("role-session provider attempt cannot claim Factory scope")

    @staticmethod
    def _validate_start_permit_identity(
        attempt: FrozenFinalProviderAttemptV1,
        permit: FactoryPhysicalAttemptStartPermitV1 | FactoryPhysicalAttemptLeaseV1,
    ) -> None:
        expected = (
            attempt.factory_run_id,
            attempt.run_id,
            attempt.role,
            attempt.turn_id,
            attempt.call_id,
            attempt.request_freeze_id,
            attempt.provider,
            attempt.model,
            attempt.provider_request_id,
            attempt.attempt_number,
            attempt.semantic_request_hash,
            attempt.physical_wire_hash,
            attempt.composite_request_hash,
        )
        actual = (
            permit.factory_run_id,
            permit.run_id,
            permit.role,
            permit.turn_id,
            permit.call_id,
            permit.request_freeze_id,
            permit.provider,
            permit.model,
            permit.provider_request_id,
            permit.authority_attempt_ordinal,
            permit.semantic_request_hash,
            permit.physical_wire_hash,
            permit.composite_request_hash,
        )
        if actual != expected:
            raise RuntimeError("provider_attempt_start_permit_identity_mismatch")

    @classmethod
    def _validate_lease_identity(
        cls,
        attempt: FrozenFinalProviderAttemptV1,
        lease: FactoryPhysicalAttemptLeaseV1,
    ) -> None:
        cls._validate_start_permit_identity(attempt, lease)

    @staticmethod
    def _factory_identity_payload(
        value: FactoryPhysicalAttemptStartPermitV1 | FactoryPhysicalAttemptLeaseV1,
        *,
        include_lease_id: bool = True,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "execution_authority_hash": value.execution_authority_hash,
            "attempt_budget": value.attempt_budget,
            "authority_attempt_ordinal": value.authority_attempt_ordinal,
            "reservation_id": value.reservation_id,
            "start_permit_id": value.start_permit_id,
        }
        if include_lease_id and type(value) is FactoryPhysicalAttemptLeaseV1:
            payload["lease_id"] = value.lease_id
        return payload

    @staticmethod
    def _start_payload_matches_permit(
        payload: dict[str, Any],
        permit: FactoryPhysicalAttemptStartPermitV1,
    ) -> bool:
        expected = {
            "verification_scope": permit.verification_scope,
            "factory_run_id": permit.factory_run_id,
            "run_id": permit.run_id,
            "role": permit.role,
            "turn_id": permit.turn_id,
            "call_id": permit.call_id,
            "request_freeze_id": permit.request_freeze_id,
            "execution_authority_hash": permit.execution_authority_hash,
            "attempt_budget": permit.attempt_budget,
            "provider": permit.provider,
            "model": permit.model,
            "semantic_request_hash": permit.semantic_request_hash,
            "physical_wire_hash": permit.physical_wire_hash,
            "composite_request_hash": permit.composite_request_hash,
            "reservation_id": permit.reservation_id,
            "provider_request_id": permit.provider_request_id,
            "authority_attempt_ordinal": permit.authority_attempt_ordinal,
            "start_permit_id": permit.start_permit_id,
        }
        return all(payload.get(field_name) == value for field_name, value in expected.items())

    @staticmethod
    def _validate_evidence(*, context_snapshot_ref: str, pin_hash: str) -> None:
        try:
            validate_context_hash(str(context_snapshot_ref or ""))
        except ValueError as exc:
            raise ValueError("context_snapshot_ref must be exactly 24 lowercase hex") from exc

        if not _PIN_HASH_RE.fullmatch(str(pin_hash or "")):
            raise ValueError("pin_hash must be exactly 64 lowercase hex")
