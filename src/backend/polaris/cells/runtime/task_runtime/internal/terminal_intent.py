"""Pure terminal-intent binding validation shared by owner and read-only observation."""

from __future__ import annotations

import hashlib
import json
from typing import Literal, Mapping, cast

from polaris.cells.runtime.task_runtime.public.contracts import (
    TaskRuntimeExecutionAttemptIdentityV1,
)

from .directed_effect_operation import DirectedEffectSettlementPreBarrierVerdictV1
from .execution_session import TaskExecutionSession


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def validate_fulfilled_terminal_intent(
    session: TaskExecutionSession,
    current_identity: TaskRuntimeExecutionAttemptIdentityV1,
) -> TaskRuntimeExecutionAttemptIdentityV1 | DirectedEffectSettlementPreBarrierVerdictV1:
    """Validate the persisted owner intent without querying, executing, or writing."""

    pending_raw = session.metadata.get("pending_terminal_intent")
    pending_intent = pending_raw if isinstance(pending_raw, Mapping) else None
    proof_raw = session.metadata.get("terminal_settlement_proof")
    proof = proof_raw if isinstance(proof_raw, Mapping) else None

    def conflict(reason: str) -> DirectedEffectSettlementPreBarrierVerdictV1:
        return DirectedEffectSettlementPreBarrierVerdictV1(
            allowed=False,
            code="settlement_terminal_intent_conflict",
            evidence={
                "reason": reason,
                "pending_terminal_intent": dict(pending_intent or {}),
                "pending_terminal_intent_valid": pending_intent is not None,
                "terminal_settlement_proof": dict(proof or {}),
                "terminal_settlement_proof_valid": proof is not None,
            },
        )

    required_keys = {
        "schema_version",
        "identity_hash",
        "outcome",
        "summary_hash",
        "metadata_hash",
        "terminal_transition_id",
        "terminal_intent_hash",
    }
    if pending_intent is None or set(pending_intent) != required_keys:
        return conflict("pending_terminal_intent_invalid")
    body = {key: pending_intent[key] for key in required_keys - {"terminal_intent_hash"}}
    terminal_intent_hash = str(pending_intent.get("terminal_intent_hash") or "").strip()
    outcome = cast(
        Literal["completed", "failed", "suspended"],
        str(pending_intent.get("outcome") or "").strip(),
    )
    settlement_lease_expires_at = str(session.metadata.get("settlement_identity_lease_expires_at") or "").strip()
    if not settlement_lease_expires_at:
        return conflict("settlement_identity_lease_missing")
    settlement_identity_record = current_identity.to_record()
    settlement_identity_record["lease_expires_at"] = settlement_lease_expires_at
    try:
        settlement_identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(settlement_identity_record)
    except (TypeError, ValueError):
        return conflict("settlement_identity_invalid")
    terminal_transition_id = str(session.terminal_transition_id or "").strip()
    if (
        pending_intent.get("schema_version") != "task-runtime.pending-terminal-intent/1"
        or outcome not in {"completed", "failed", "suspended"}
        or terminal_intent_hash != _canonical_sha256(body)
        or pending_intent.get("identity_hash") != _canonical_sha256(settlement_identity.to_record())
        or not terminal_transition_id
        or pending_intent.get("terminal_transition_id") != terminal_transition_id
    ):
        return conflict("pending_terminal_intent_binding_invalid")
    for hash_field in ("identity_hash", "summary_hash", "metadata_hash", "terminal_intent_hash"):
        value = str(pending_intent.get(hash_field) or "")
        if (
            len(value) != 64
            or value != value.lower()
            or any(character not in "0123456789abcdef" for character in value)
        ):
            return conflict(f"pending_terminal_intent_{hash_field}_invalid")
    if proof is None:
        return conflict("terminal_settlement_proof_missing")
    if (
        str(proof.get("terminal_intent_hash") or "").strip() != terminal_intent_hash
        or str(proof.get("settlement_outcome") or "").strip() != outcome
        or not str(proof.get("registry_state") or "").strip()
    ):
        return conflict("terminal_settlement_proof_binding_invalid")
    expected_status = outcome
    if session.status != expected_status:
        return conflict("terminal_session_outcome_mismatch")

    return settlement_identity
