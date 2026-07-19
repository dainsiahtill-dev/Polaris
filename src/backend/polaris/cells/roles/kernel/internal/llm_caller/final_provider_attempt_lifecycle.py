"""Durable physical provider-attempt lifecycle over segmented FactStream."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from polaris.cells.events.fact_stream.public import (
    AppendSegmentedFactEventCommandV1,
    EnsureSegmentedFactLedgerCommandV1,
    FactStreamError,
    QuerySegmentedFactEventsV1,
    append_segmented_fact_event,
    ensure_segmented_fact_ledger,
    query_segmented_fact_events,
)
from polaris.kernelone.llm.engine.contracts import FrozenFinalProviderAttemptV1

_CONTEXT_REF_RE = re.compile(r"^[0-9a-f]{24}$")
_PIN_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


class StrictProviderAttemptLifecycleStore:
    """Exactly-paired start/terminal facts keyed by physical request id."""

    def __init__(self, *, workspace: str, logical_stream: str, verification_scope: str, scope_id: str) -> None:
        self.workspace = str(workspace)
        self.logical_stream = str(logical_stream)
        self.verification_scope = str(verification_scope)
        self.scope_id = str(scope_id)
        if self.verification_scope not in {"factory", "role_session"} or not self.scope_id:
            raise ValueError("provider attempt lifecycle requires an explicit Factory run or role session scope")
        ensure_segmented_fact_ledger(
            EnsureSegmentedFactLedgerCommandV1(
                workspace=self.workspace,
                logical_stream=self.logical_stream,
                maintenance_reason="provider_attempt_lifecycle_open",
            )
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
        context_snapshot_ref: str,
        pin_hash: str,
    ) -> None:
        self._validate_attempt_scope(attempt)
        self._validate_evidence(context_snapshot_ref=context_snapshot_ref, pin_hash=pin_hash)
        payload = self._base_payload(
            attempt,
            context_snapshot_ref=context_snapshot_ref,
            pin_hash=pin_hash,
        )
        append_segmented_fact_event(
            AppendSegmentedFactEventCommandV1(
                workspace=self.workspace,
                logical_stream=self.logical_stream,
                event_type="provider_attempt.started",
                source="roles.kernel",
                payload=payload,
                idempotency_key=f"{attempt.provider_request_id}:start",
            )
        )

    def append_terminal(
        self,
        attempt: FrozenFinalProviderAttemptV1,
        *,
        context_snapshot_ref: str,
        pin_hash: str,
        status: str,
        error: str | None = None,
    ) -> None:
        self._validate_attempt_scope(attempt)
        self._validate_evidence(context_snapshot_ref=context_snapshot_ref, pin_hash=pin_hash)
        status_token = str(status or "").strip()
        if status_token not in _TERMINAL_STATUSES:
            raise ValueError("provider attempt terminal status is invalid")
        try:
            append_segmented_fact_event(
                AppendSegmentedFactEventCommandV1(
                    workspace=self.workspace,
                    logical_stream=self.logical_stream,
                    event_type="provider_attempt.started",
                    source="roles.kernel",
                    payload=self._base_payload(
                        attempt,
                        context_snapshot_ref=context_snapshot_ref,
                        pin_hash=pin_hash,
                    ),
                    idempotency_key=f"{attempt.provider_request_id}:start",
                    require_idempotency_replay=True,
                )
            )
        except FactStreamError as exc:
            raise RuntimeError("authoritative provider attempt start is missing or ambiguous") from exc
        payload = self._base_payload(attempt, context_snapshot_ref=context_snapshot_ref, pin_hash=pin_hash)
        payload.update({"status": status_token, "error": str(error or "")[:300]})
        append_segmented_fact_event(
            AppendSegmentedFactEventCommandV1(
                workspace=self.workspace,
                logical_stream=self.logical_stream,
                event_type="provider_attempt.terminal",
                source="roles.kernel",
                payload=payload,
                idempotency_key=f"{attempt.provider_request_id}:terminal",
            )
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
    def _validate_evidence(*, context_snapshot_ref: str, pin_hash: str) -> None:
        if not _CONTEXT_REF_RE.fullmatch(str(context_snapshot_ref or "")):
            raise ValueError("context_snapshot_ref must be exactly 24 lowercase hex")
        if not _PIN_HASH_RE.fullmatch(str(pin_hash or "")):
            raise ValueError("pin_hash must be exactly 64 lowercase hex")
