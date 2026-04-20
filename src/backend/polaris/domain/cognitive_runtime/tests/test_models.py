from __future__ import annotations

from polaris.domain.cognitive_runtime.models import ContextHandoffPack, TurnEnvelope


def test_context_handoff_pack_roundtrip_with_new_fields() -> None:
    envelope = TurnEnvelope(
        turn_id="turn-1",
        projection_version="v1",
        lease_id="lease-1",
        validation_id="val-1",
        receipt_ids=("r1", "r2"),
        session_id="session-1",
        run_id="run-1",
        role="director",
        task_id="task-1",
        state_version=1,
    )

    original = ContextHandoffPack(
        handoff_id="handoff-1",
        workspace="/workspace",
        created_at="2026-04-16T00:00:00Z",
        session_id="session-1",
        reason="test",
        run_id="run-1",
        current_goal="achieve_test",
        hard_constraints=("c1", "c2"),
        open_loops=("l1",),
        run_card={"key": "value"},
        context_slice_plan={"plan": "slice"},
        decision_log=({"d1": "v1"},),
        artifact_refs=("a1",),
        episode_refs=("e1",),
        receipt_refs=("r1",),
        source_spans=("s1",),
        state_snapshot={"state": "snap"},
        turn_envelope=envelope,
        checkpoint_state={"ckpt": "state"},
        pending_receipt_refs=("pr1", "pr2"),
        suggestion_rankings=({"rank": 1}, {"rank": 2}),
        lease_token="token-123",
    )

    payload = original.to_dict()
    restored = ContextHandoffPack.from_mapping(payload)

    assert restored is not None
    assert restored.handoff_id == original.handoff_id
    assert restored.workspace == original.workspace
    assert restored.created_at == original.created_at
    assert restored.session_id == original.session_id
    assert restored.reason == original.reason
    assert restored.run_id == original.run_id
    assert restored.current_goal == original.current_goal
    assert restored.hard_constraints == original.hard_constraints
    assert restored.open_loops == original.open_loops
    assert restored.run_card == original.run_card
    assert restored.context_slice_plan == original.context_slice_plan
    assert restored.decision_log == original.decision_log
    assert restored.artifact_refs == original.artifact_refs
    assert restored.episode_refs == original.episode_refs
    assert restored.receipt_refs == original.receipt_refs
    assert restored.source_spans == original.source_spans
    assert restored.state_snapshot == original.state_snapshot
    assert restored.checkpoint_state == original.checkpoint_state
    assert restored.pending_receipt_refs == original.pending_receipt_refs
    assert restored.suggestion_rankings == original.suggestion_rankings
    assert restored.lease_token == original.lease_token

    assert restored.turn_envelope is not None
    assert restored.turn_envelope.turn_id == envelope.turn_id
    assert restored.turn_envelope.projection_version == envelope.projection_version
    assert restored.turn_envelope.lease_id == envelope.lease_id
    assert restored.turn_envelope.validation_id == envelope.validation_id
    assert restored.turn_envelope.receipt_ids == envelope.receipt_ids


def test_context_handoff_pack_backward_compat_without_new_fields() -> None:
    """Old payloads without the new fields should deserialize with defaults."""
    payload = {
        "handoff_id": "handoff-old",
        "workspace": "/workspace",
        "created_at": "2026-04-16T00:00:00Z",
        "session_id": "session-old",
    }

    restored = ContextHandoffPack.from_mapping(payload)
    assert restored is not None
    assert restored.handoff_id == "handoff-old"
    assert restored.checkpoint_state == {}
    assert restored.pending_receipt_refs == ()
    assert restored.suggestion_rankings == ()
    assert restored.lease_token is None
