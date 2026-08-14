from __future__ import annotations

import hashlib
import json

import pytest
from polaris.cells.factory.pipeline.internal.factory_run_models import FactoryRunStatus
from polaris.cells.factory.pipeline.internal.factory_stage_persistence import (
    FactoryLastStageCommitV1,
    FactoryStagePersistenceCommittedV1,
    FactoryStagePersistenceError,
    FactoryStagePersistenceIntentV1,
    build_stage_persistence_intent,
    canonical_checkpoint_sha256,
    canonical_run_snapshot_sha256,
    canonical_stage_result_sha256,
    reduce_factory_stage_persistence,
    validate_current_stage_commit_pointer,
)

RUN_ID = "factory_run_123"
STAGE = "pm_planning"
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def _frozen_document_hash(domain: str, document: object) -> str:
    raw = json.dumps(
        {"domain": domain, "document": document},
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _legacy_typed_field_hash(domain: str, field: str, document: object) -> str:
    raw = json.dumps(
        {"domain": domain, field: document},
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _intent() -> FactoryStagePersistenceIntentV1:
    return build_stage_persistence_intent(
        factory_run_id=RUN_ID,
        stage=STAGE,
        stage_result={"stage": STAGE, "status": "success", "output": "ok"},
        checkpoint_ref="runtime/factory_run_123/checkpoints/running_2026-07-19T01_02_03Z.json",
    )


def _stage_event(intent: FactoryStagePersistenceIntentV1) -> dict[str, object]:
    return {
        "type": "stage_completed",
        "run_id": RUN_ID,
        "event_id": "evt_stage",
        "timestamp": "2026-07-19T01:02:03Z",
        "chain_sequence": 2,
        "chain_event_hash": HASH_A,
        "chain_previous_hash": HASH_B,
        "stage": STAGE,
        "result": {"stage": STAGE, "status": "success", "output": "ok"},
        "persistence_intent": intent.to_record(),
    }


def _marker(intent: FactoryStagePersistenceIntentV1) -> dict[str, object]:
    return {
        "type": "factory_stage_persistence_committed",
        "schema_version": "factory.stage_persistence_committed.v1",
        "run_id": RUN_ID,
        "event_id": "evt_marker",
        "timestamp": "2026-07-19T01:02:04Z",
        "chain_sequence": 3,
        "chain_event_hash": HASH_B,
        "chain_previous_hash": HASH_A,
        "factory_run_id": RUN_ID,
        "stage": STAGE,
        "stage_completed_event_id": "evt_stage",
        "stage_completed_chain_sequence": 2,
        "stage_completed_chain_event_hash": HASH_A,
        "persistence_intent_sha256": intent.persistence_intent_sha256,
        "run_snapshot_canonical_sha256": HASH_C,
        "checkpoint_ref": intent.checkpoint_ref,
        "checkpoint_canonical_sha256": HASH_D,
    }


def test_intent_round_trip_is_exact_and_hash_bound() -> None:
    intent = _intent()

    assert FactoryStagePersistenceIntentV1.from_record(intent.to_record()) == intent
    assert intent.stage_result_canonical_sha256 != HASH_A
    assert len(intent.persistence_intent_sha256) == 64

    tampered = intent.to_record()
    tampered["stage"] = "qa_review"
    with pytest.raises(FactoryStagePersistenceError, match="intent_hash_mismatch"):
        FactoryStagePersistenceIntentV1.from_record(tampered)


def test_all_document_hashes_use_the_one_frozen_domain_document_shape() -> None:
    document = {"z": [1, "two"], "a": {"ok": True}}

    assert canonical_stage_result_sha256(document) == _frozen_document_hash("polaris.factory.stage_result.v1", document)
    assert canonical_run_snapshot_sha256(document) == _frozen_document_hash("polaris.factory.run_snapshot.v1", document)
    assert canonical_checkpoint_sha256(document) == _frozen_document_hash("polaris.factory.run_checkpoint.v1", document)
    assert canonical_stage_result_sha256(document) != _legacy_typed_field_hash(
        "polaris.factory.stage_result.v1", "stage_result", document
    )
    assert canonical_run_snapshot_sha256(document) != _legacy_typed_field_hash(
        "polaris.factory.run_snapshot.v1", "run_snapshot", document
    )
    assert canonical_checkpoint_sha256(document) != _legacy_typed_field_hash(
        "polaris.factory.run_checkpoint.v1", "checkpoint", document
    )


@pytest.mark.parametrize(
    "checkpoint_ref",
    [
        "runtime/other_run/checkpoints/running.json",
        f"runtime/{RUN_ID}/checkpoints/arbitrary.json",
        f"runtime/{RUN_ID}/checkpoints/running_arbitrary.json",
        f"runtime/{RUN_ID}/checkpoints/banana_2026-07-19T01_02_03Z.json",
        f"runtime/{RUN_ID}/checkpoints/../running.json",
        f"runtime/{RUN_ID}/checkpoints/nested/running.json",
        f"runtime/{RUN_ID}/checkpoints/running.JSON",
        f"runtime/{RUN_ID}/checkpoints/e\u0301.json",
        f"runtime/{RUN_ID}/checkpoints/running\n.json",
        f"runtime/{RUN_ID}/checkpoints/{'x' * 1100}.json",
    ],
)
def test_checkpoint_ref_is_exact_canonical_current_run_identity(checkpoint_ref: str) -> None:
    with pytest.raises(FactoryStagePersistenceError, match="checkpoint_ref_invalid"):
        FactoryStagePersistenceIntentV1.create(
            factory_run_id=RUN_ID,
            stage=STAGE,
            stage_result_canonical_sha256=HASH_A,
            checkpoint_ref=checkpoint_ref,
        )


@pytest.mark.parametrize("status", tuple(FactoryRunStatus))
def test_checkpoint_ref_accepts_every_internal_factory_run_status(status: FactoryRunStatus) -> None:
    checkpoint_ref = f"runtime/{RUN_ID}/checkpoints/{status.value}_2026-07-19T01_02_03Z.json"
    intent = FactoryStagePersistenceIntentV1.create(
        factory_run_id=RUN_ID,
        stage=STAGE,
        stage_result_canonical_sha256=HASH_A,
        checkpoint_ref=checkpoint_ref,
    )
    assert intent.checkpoint_ref == checkpoint_ref


def test_marker_and_pointer_reject_cross_run_checkpoint_refs() -> None:
    intent = _intent()
    marker = _marker(intent)
    marker["checkpoint_ref"] = "runtime/other_run/checkpoints/running.json"
    with pytest.raises(FactoryStagePersistenceError, match="checkpoint_ref_invalid"):
        FactoryStagePersistenceCommittedV1.from_record(marker)

    pointer = FactoryLastStageCommitV1.from_commit(FactoryStagePersistenceCommittedV1.from_record(_marker(intent)))
    pointer_record = pointer.to_record()
    pointer_record["checkpoint_ref"] = "runtime/other_run/checkpoints/running.json"
    with pytest.raises(FactoryStagePersistenceError, match="checkpoint_ref_invalid"):
        FactoryLastStageCommitV1.from_record(pointer_record, factory_run_id=RUN_ID)


def test_intent_rejects_extra_fields_fail_closed() -> None:
    record = _intent().to_record()
    record["unexpected"] = True
    with pytest.raises(FactoryStagePersistenceError, match="intent_fields_invalid"):
        FactoryStagePersistenceIntentV1.from_record(record)


def test_unmatched_stage_completed_is_pending_quarantine() -> None:
    intent = _intent()
    reduced = reduce_factory_stage_persistence([_stage_event(intent)], factory_run_id=RUN_ID)

    assert reduced.pending_stage_event_id == "evt_stage"
    assert reduced.commits == ()
    assert reduced.is_quarantined is True


def test_marker_ack_closes_exact_pending_intent() -> None:
    intent = _intent()
    reduced = reduce_factory_stage_persistence(
        [_stage_event(intent), _marker(intent)],
        factory_run_id=RUN_ID,
    )

    assert reduced.pending_stage_event_id is None
    assert len(reduced.commits) == 1
    assert reduced.commits[0].stage_completed_event_id == "evt_stage"
    assert reduced.is_quarantined is False


def test_marker_rejects_mismatched_event_identity() -> None:
    intent = _intent()
    marker = _marker(intent)
    marker["stage_completed_chain_event_hash"] = HASH_D

    with pytest.raises(FactoryStagePersistenceError, match="marker_stage_event_mismatch"):
        reduce_factory_stage_persistence([_stage_event(intent), marker], factory_run_id=RUN_ID)


def test_marker_dto_rejects_bool_chain_sequence() -> None:
    intent = _intent()
    marker = _marker(intent)
    marker["stage_completed_chain_sequence"] = True
    with pytest.raises(FactoryStagePersistenceError, match="marker_field_invalid"):
        FactoryStagePersistenceCommittedV1.from_record(marker)


def test_explicit_quarantine_remains_fail_closed_after_commit() -> None:
    intent = _intent()
    quarantine = {
        "type": "factory_run_quarantined",
        "schema_version": "factory.run_quarantined.v1",
        "run_id": RUN_ID,
        "event_id": "evt_quarantine",
        "timestamp": "2026-07-19T01:02:05Z",
        "chain_sequence": 4,
        "chain_event_hash": HASH_C,
        "chain_previous_hash": HASH_B,
        "factory_run_id": RUN_ID,
        "stage": STAGE,
        "failed_step": "commit_marker",
        "stage_completed_event_id": "evt_stage",
        "stage_completed_chain_sequence": 2,
        "stage_completed_chain_event_hash": HASH_A,
        "persistence_intent_sha256": intent.persistence_intent_sha256,
        "error_type": "OSError",
        "error_message": "disk failure",
    }
    reduced = reduce_factory_stage_persistence(
        [_stage_event(intent), _marker(intent), quarantine],
        factory_run_id=RUN_ID,
    )
    assert reduced.is_quarantined is True
    assert reduced.quarantine_event_id == "evt_quarantine"


def test_matching_checkpoint_quarantine_can_be_closed_only_by_exact_later_marker() -> None:
    intent = _intent()
    quarantine = {
        "type": "factory_run_quarantined",
        "schema_version": "factory.run_quarantined.v1",
        "run_id": RUN_ID,
        "event_id": "evt_quarantine",
        "timestamp": "2026-07-19T01:02:05Z",
        "chain_sequence": 3,
        "chain_event_hash": HASH_C,
        "chain_previous_hash": HASH_B,
        "factory_run_id": RUN_ID,
        "stage": STAGE,
        "failed_step": "checkpoint",
        "stage_completed_event_id": "evt_stage",
        "stage_completed_chain_sequence": 2,
        "stage_completed_chain_event_hash": HASH_A,
        "persistence_intent_sha256": intent.persistence_intent_sha256,
        "error_type": "FactoryRunSnapshotError",
        "error_message": "guarded snapshot leaf changed during the read",
    }

    quarantined = reduce_factory_stage_persistence(
        [_stage_event(intent), quarantine],
        factory_run_id=RUN_ID,
    )
    assert quarantined.is_quarantined is True
    assert quarantined.recoverable_stage_event_id == "evt_stage"

    recovered = reduce_factory_stage_persistence(
        [_stage_event(intent), quarantine, _marker(intent)],
        factory_run_id=RUN_ID,
    )
    assert recovered.is_quarantined is False
    assert recovered.recoverable_stage_event_id is None


def test_current_pointer_must_match_latest_commit() -> None:
    intent = _intent()
    commit = FactoryStagePersistenceCommittedV1.from_record(_marker(intent))
    pointer = FactoryLastStageCommitV1.from_commit(commit)

    validate_current_stage_commit_pointer(pointer.to_record(), commit)

    tampered = pointer.to_record()
    tampered["checkpoint_ref"] = "runtime/factory_run_123/checkpoints/running_2000-01-01T00_00_00Z.json"
    with pytest.raises(FactoryStagePersistenceError, match="current_pointer_mismatch"):
        validate_current_stage_commit_pointer(tampered, commit)


def test_pointer_is_forbidden_without_any_committed_stage() -> None:
    with pytest.raises(FactoryStagePersistenceError, match="current_pointer_without_commit"):
        validate_current_stage_commit_pointer(
            {
                "schema_version": "factory.last_stage_commit.v1",
                "stage": STAGE,
                "stage_completed_event_id": "evt_stage",
                "stage_completed_chain_sequence": 2,
                "stage_completed_chain_event_hash": HASH_A,
                "persistence_intent_sha256": HASH_B,
                "checkpoint_ref": "runtime/factory_run_123/checkpoints/one.json",
            },
            None,
        )
