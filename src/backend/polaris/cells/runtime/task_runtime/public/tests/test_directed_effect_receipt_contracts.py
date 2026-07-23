from __future__ import annotations

import pytest
from polaris.cells.runtime.task_runtime.public.contracts import (
    DIRECTED_EFFECT_PARENT_BINDING_SCHEMA_V1,
    CommitDirectedEffectReceiptCommandV1,
    DeadLetterDirectedEffectOperationCommandV1,
    DirectedEffectParentBindingV1,
    DirectedEffectParentRegistryIdentityV1,
    MarkDirectedEffectRecoveryPendingCommandV1,
    ParentCorrelationV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)

_HASH = "a" * 64


def _identity() -> TaskRuntimeExecutionAttemptIdentityV1:
    return TaskRuntimeExecutionAttemptIdentityV1(
        workspace="/tmp/deo-3-contracts",
        task_id=31,
        external_task_id="DEO-31",
        session_id="session-31",
        attempt=2,
        role_id="director",
        worker_id="worker-31",
        run_id="run-31",
        lease_expires_at="2026-07-20T01:00:00+00:00",
    )


def _binding() -> DirectedEffectParentBindingV1:
    identity = _identity()
    return DirectedEffectParentBindingV1(
        schema_version=DIRECTED_EFFECT_PARENT_BINDING_SCHEMA_V1,
        registry_identity=DirectedEffectParentRegistryIdentityV1.from_execution_attempt(identity),
        registry_stream_token="task-runtime.deo-parent-registry.31",
        registry_version=1,
        parent_sequence=1,
        binding_id="binding-31",
        operation_stream_token="task-runtime.deo-operation.31",
        binding_hash="b" * 64,
        admission_idempotency_key="parent-31",
        correlation=ParentCorrelationV1(turn_id="turn-31", batch_id="batch-31"),
        actor="contract-test",
        source_event_id="event-31",
        source_event_seq=1,
    )


def _common() -> dict[str, object]:
    identity = _identity()
    return {
        "workspace": identity.workspace,
        "task_id": identity.task_id,
        "execution_attempt": identity,
        "parent_binding": _binding(),
        "tool_call_id": "call-31",
        "effect_id": "effect-31",
        "expected_version": 2,
        "expected_seq": 3,
        "actor": "roles.adapters",
        "intended_effect_fingerprint": "1" * 64,
        "policy_verdict_hash": "2" * 64,
        "expected_receipt_binding_hash": "3" * 64,
    }


def test_receipt_command_requires_exact_hash_bound_evidence() -> None:
    command = CommitDirectedEffectReceiptCommandV1(
        **_common(),
        receipt_ref="director-physical-effect-31",
        receipt_hash=_HASH,
        receipt_binding_hash="3" * 64,
        receipt_outcome="succeeded",
    )

    assert command.receipt_ref == "director-physical-effect-31"
    assert command.receipt_hash == _HASH
    assert command.receipt_binding_hash == command.expected_receipt_binding_hash
    assert command.receipt_outcome == "succeeded"

    with pytest.raises(ValueError, match="receipt_hash"):
        CommitDirectedEffectReceiptCommandV1(
            **_common(),
            receipt_ref="director-physical-effect-31",
            receipt_hash="not-a-sha256",
            receipt_binding_hash="3" * 64,
            receipt_outcome="succeeded",
        )
    with pytest.raises(ValueError, match="receipt_outcome"):
        CommitDirectedEffectReceiptCommandV1(
            **_common(),
            receipt_ref="director-physical-effect-31",
            receipt_hash=_HASH,
            receipt_binding_hash="3" * 64,
            receipt_outcome="unknown",  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("command_type", "ref_field", "hash_field"),
    (
        (MarkDirectedEffectRecoveryPendingCommandV1, "recovery_evidence_ref", "recovery_evidence_hash"),
        (DeadLetterDirectedEffectOperationCommandV1, "resolution_evidence_ref", "resolution_evidence_hash"),
    ),
)
def test_recovery_commands_require_reason_and_canonical_evidence(
    command_type: type[MarkDirectedEffectRecoveryPendingCommandV1] | type[DeadLetterDirectedEffectOperationCommandV1],
    ref_field: str,
    hash_field: str,
) -> None:
    kwargs = {
        **_common(),
        "reason": "physical receipt reconciliation required",
        ref_field: "evidence-31",
        hash_field: _HASH,
    }
    command = command_type(**kwargs)
    assert command.reason == "physical receipt reconciliation required"

    kwargs["reason"] = ""
    with pytest.raises(ValueError, match="reason"):
        command_type(**kwargs)
    kwargs["reason"] = "physical receipt reconciliation required"
    kwargs[hash_field] = "short"
    with pytest.raises(ValueError, match=hash_field):
        command_type(**kwargs)
