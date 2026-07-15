"""Public contract evidence for guarded FactStream append v1."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from polaris.cells.events.fact_stream.public import (
    AppendIfGuardedSnapshotCommandV1,
    FactStreamError,
    GuardedFactEventV1,
    ProvisionFactStreamLockAuthorityCommandV1,
    ReadGuardedFactSnapshotCommandV1,
    append_if_guarded_snapshot,
    provision_fact_stream_lock_authority,
    read_guarded_fact_snapshot,
)
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.events.sourcing import JsonlEventStore
from polaris.kernelone.fs import set_default_adapter
from polaris.kernelone.fs.locked_regular_file import LockedRegularFileError, LockedRegularFileSetV1


@pytest.fixture(autouse=True)
def _inject_local_adapter() -> None:
    set_default_adapter(LocalFileSystemAdapter())


@pytest.fixture
def guarded_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provision_fact_stream_lock_authority(
        ProvisionFactStreamLockAuthorityCommandV1(
            workspace=str(workspace),
            streams=("operation", "registry"),
            maintenance_reason="test fixture bootstrap",
        )
    )
    return workspace


def _prepare(workspace: Path):
    return read_guarded_fact_snapshot(
        ReadGuardedFactSnapshotCommandV1(
            workspace=str(workspace),
            target_stream="operation",
            guard_stream="registry",
        )
    )


def _command(snapshot, *, key: str = "operation-1", value: str = "created"):
    return AppendIfGuardedSnapshotCommandV1(
        snapshot_proof=snapshot.proof,
        event=GuardedFactEventV1(
            event_type="operation_recorded",
            source="test.guarded",
            payload={"state": value, "domain_recorded_at": "2026-07-14T00:00:00Z"},
            metadata={"recorded_at": "caller-domain-value"},
        ),
        idempotency_key=key,
    )


def _append_intervening(workspace: Path, stream: str, key: str) -> None:
    JsonlEventStore(str(workspace)).append(
        stream=stream,
        event_type="intervening",
        source="test.guarded",
        payload={"key": key},
        metadata={"idempotency_key": key},
        idempotency_key=key,
        strict_integrity=True,
        durability="fsync",
    )


def test_public_authority_provision_is_explicit_and_idempotent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    authority_root = tmp_path / "platform-authority"
    command = ProvisionFactStreamLockAuthorityCommandV1(
        workspace=str(workspace),
        streams=("operation", "registry"),
        maintenance_reason="test bootstrap",
        platform_lock_root=str(authority_root),
    )

    first_receipt = provision_fact_stream_lock_authority(command)
    second_receipt = provision_fact_stream_lock_authority(command)
    assert tuple(proof.operation for proof in first_receipt.proofs) == (
        "provision_authority",
        "enroll_stream_lock_keys",
    )
    assert tuple(proof.operation for proof in second_receipt.proofs) == (
        "provision_authority",
        "enroll_stream_lock_keys",
    )
    assert all(proof.final_validation is True for proof in first_receipt.proofs)

    store = JsonlEventStore(str(workspace))
    anchor = authority_root / store.storage_identity.token / "anchor.lock"
    assert anchor.is_file()


def test_guarded_snapshot_is_deeply_immutable_and_reduction_is_detached(
    guarded_workspace: Path,
) -> None:
    workspace = guarded_workspace
    _append_intervening(workspace, "operation", "seed")
    snapshot = _prepare(workspace)

    assert snapshot.target_facts_digest == snapshot.proof.target_facts_digest
    with pytest.raises(TypeError):
        snapshot.target_facts[0]["payload"]["key"] = "mutated"  # type: ignore[index]
    detached = snapshot.target_records()
    detached[0]["payload"]["key"] = "mutated"
    assert snapshot.target_records()[0]["payload"]["key"] == "seed"
    assert snapshot.target_facts_digest == snapshot.proof.target_facts_digest


@pytest.mark.parametrize(
    "field_name,replacement",
    [
        ("workspace", "/tmp/other-workspace"),
        ("target_stream", "other-target"),
        ("guard_stream", "other-guard"),
        ("target_storage_path", "runtime/events/other-target.jsonl"),
        ("guard_storage_path", "runtime/events/other-guard.jsonl"),
        ("strict_format_revision", "polaris.strict-event-jsonl.v0"),
        ("target_head_seq", 1),
        ("guard_head_seq", 1),
        ("target_facts_digest", "0" * 64),
        ("guard_facts_digest", "1" * 64),
        ("continuity_digest", "2" * 64),
    ],
)
def test_guarded_proof_tampering_is_rejected_before_append(
    guarded_workspace: Path,
    field_name: str,
    replacement: object,
) -> None:
    workspace = guarded_workspace
    snapshot = _prepare(workspace)
    tampered = replace(snapshot.proof, **{field_name: replacement})

    with pytest.raises(FactStreamError) as exc_info:
        append_if_guarded_snapshot(replace(_command(snapshot), snapshot_proof=tampered))

    assert exc_info.value.code in {"snapshot_proof_invalid", "snapshot_proof_tampered"}
    assert JsonlEventStore(str(workspace)).query(stream="operation", strict_integrity=True).total == 0


def test_guarded_target_and_guard_drift_fail_independently(guarded_workspace: Path) -> None:
    workspace = guarded_workspace

    target_snapshot = _prepare(workspace)
    _append_intervening(workspace, "operation", "target-drift")
    with pytest.raises(FactStreamError) as target_exc:
        append_if_guarded_snapshot(_command(target_snapshot))
    assert target_exc.value.code == "target_snapshot_drift"

    guard_snapshot = _prepare(workspace)
    _append_intervening(workspace, "registry", "guard-drift")
    with pytest.raises(FactStreamError) as guard_exc:
        append_if_guarded_snapshot(_command(guard_snapshot, key="operation-2"))
    assert guard_exc.value.code == "guard_snapshot_drift"


def test_guarded_exact_replay_wins_over_guard_drift_and_conflict_is_typed(
    guarded_workspace: Path,
) -> None:
    workspace = guarded_workspace
    snapshot = _prepare(workspace)
    command = _command(snapshot)
    committed = append_if_guarded_snapshot(command)
    _append_intervening(workspace, "registry", "guard-drift")

    replay = append_if_guarded_snapshot(command)
    assert replay == committed
    fresh_replay = append_if_guarded_snapshot(_command(_prepare(workspace)))
    assert fresh_replay == committed
    stored = JsonlEventStore(str(workspace)).query(stream="operation", strict_integrity=True).events[0]
    assert stored.metadata["idempotency_key"] == "operation-1"

    with pytest.raises(FactStreamError) as conflict:
        append_if_guarded_snapshot(_command(snapshot, value="different"))
    assert conflict.value.code == "idempotency_semantic_conflict"
    assert JsonlEventStore(str(workspace)).query(stream="operation", strict_integrity=True).total == 1


def test_guarded_metadata_idempotency_key_mismatch_is_typed(guarded_workspace: Path) -> None:
    workspace = guarded_workspace
    snapshot = _prepare(workspace)
    command = AppendIfGuardedSnapshotCommandV1(
        snapshot_proof=snapshot.proof,
        event=GuardedFactEventV1(
            event_type="operation_recorded",
            source="test.guarded",
            payload={"state": "created"},
            metadata={"idempotency_key": "wrong-key"},
        ),
        idempotency_key="command-key",
    )

    with pytest.raises(FactStreamError) as exc_info:
        append_if_guarded_snapshot(command)

    assert exc_info.value.code == "idempotency_semantic_conflict"


def test_tampered_binding_is_rejected_before_existing_idempotent_replay(
    guarded_workspace: Path,
) -> None:
    workspace = guarded_workspace
    snapshot = _prepare(workspace)
    command = _command(snapshot)
    append_if_guarded_snapshot(command)
    tampered = replace(snapshot.proof, target_stream="other-target")

    with pytest.raises(FactStreamError) as exc_info:
        append_if_guarded_snapshot(replace(command, snapshot_proof=tampered))

    assert exc_info.value.code == "snapshot_proof_tampered"
    assert JsonlEventStore(str(workspace)).query(stream="operation", strict_integrity=True).total == 1


def test_guarded_prepare_rejects_same_stream_path_aliases(guarded_workspace: Path) -> None:
    workspace = guarded_workspace

    with pytest.raises(FactStreamError) as exc_info:
        read_guarded_fact_snapshot(
            ReadGuardedFactSnapshotCommandV1(
                workspace=str(workspace),
                target_stream=" operation ",
                guard_stream="operation",
            )
        )

    assert exc_info.value.code == "same_target_and_guard_stream"


def test_guarded_prepare_reports_typed_lock_acquisition_failure(
    guarded_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = guarded_workspace

    def fail_lock(*_args: object, **_kwargs: object) -> int:
        raise LockedRegularFileError("injected", code="lock_acquisition_failed")

    monkeypatch.setattr(LockedRegularFileSetV1, "_acquire_lock", fail_lock)

    with pytest.raises(FactStreamError) as exc_info:
        _prepare(workspace)

    assert exc_info.value.code == "lock_acquisition_failed"


def test_guarded_commit_appends_only_target_and_preserves_guard(guarded_workspace: Path) -> None:
    workspace = guarded_workspace
    _append_intervening(workspace, "registry", "guard-seed")
    snapshot = _prepare(workspace)
    receipt = append_if_guarded_snapshot(_command(snapshot))
    store = JsonlEventStore(str(workspace))

    assert receipt.appended_seq == 1
    assert store.query(stream="operation", strict_integrity=True).total == 1
    assert store.query(stream="registry", strict_integrity=True).total == 1


def test_guarded_strict_torn_tail_denies_prepare_and_commit(guarded_workspace: Path) -> None:
    workspace = guarded_workspace
    snapshot = _prepare(workspace)
    store = JsonlEventStore(str(workspace))
    target_path = Path(store._resolve_runtime_stream_path(store.stream_logical_path("operation")))
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text('{"torn":', encoding="utf-8")

    with pytest.raises(FactStreamError) as prepare_error:
        _prepare(workspace)
    assert prepare_error.value.code == "strict_stream_corruption"

    with pytest.raises(FactStreamError) as commit_error:
        append_if_guarded_snapshot(_command(snapshot))
    assert commit_error.value.code == "strict_stream_corruption"


def test_guarded_symlink_swap_cannot_write_outside_runtime_root(
    guarded_workspace: Path,
    tmp_path: Path,
) -> None:
    workspace = guarded_workspace
    snapshot = _prepare(workspace)
    store = JsonlEventStore(str(workspace))
    target_path = Path(store._resolve_runtime_stream_path(store.stream_logical_path("operation")))
    outside_path = tmp_path / "outside.jsonl"
    outside_path.write_text("outside-evidence\n", encoding="utf-8")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.symlink_to(outside_path)

    with pytest.raises(FactStreamError) as exc_info:
        append_if_guarded_snapshot(_command(snapshot))

    assert exc_info.value.code == "unsafe_stream_object"
    assert outside_path.read_text(encoding="utf-8") == "outside-evidence\n"


def test_guarded_events_directory_replacement_cannot_write_outside_root(
    guarded_workspace: Path,
    tmp_path: Path,
) -> None:
    workspace = guarded_workspace
    store = JsonlEventStore(str(workspace))
    target_path = Path(store._resolve_runtime_stream_path(store.stream_logical_path("operation")))
    events_path = target_path.parent
    events_path.mkdir(parents=True, exist_ok=True)
    snapshot = _prepare(workspace)
    outside_events = tmp_path / "outside-events"
    outside_events.mkdir()
    outside_target = outside_events / target_path.name
    outside_target.write_text("outside-evidence\n", encoding="utf-8")
    events_path.rename(events_path.with_name("events-original"))
    events_path.symlink_to(outside_events, target_is_directory=True)

    with pytest.raises(FactStreamError) as exc_info:
        append_if_guarded_snapshot(_command(snapshot))

    assert exc_info.value.code == "stream_identity_drift"
    assert outside_target.read_text(encoding="utf-8") == "outside-evidence\n"


def test_guard_corruption_does_not_create_an_absent_target(guarded_workspace: Path) -> None:
    workspace = guarded_workspace
    snapshot = _prepare(workspace)
    store = JsonlEventStore(str(workspace))
    target_path = Path(store._resolve_runtime_stream_path(store.stream_logical_path("operation")))
    guard_path = Path(store._resolve_runtime_stream_path(store.stream_logical_path("registry")))
    guard_path.parent.mkdir(parents=True, exist_ok=True)
    guard_path.write_text('{"torn":', encoding="utf-8")

    with pytest.raises(FactStreamError) as exc_info:
        append_if_guarded_snapshot(_command(snapshot))

    assert exc_info.value.code == "strict_stream_corruption"
    assert not target_path.exists()


def test_strict_valid_json_without_final_newline_is_torn_tail(guarded_workspace: Path) -> None:
    workspace = guarded_workspace
    _append_intervening(workspace, "operation", "seed")
    store = JsonlEventStore(str(workspace))
    target_path = Path(store._resolve_runtime_stream_path(store.stream_logical_path("operation")))
    target_path.write_bytes(target_path.read_bytes().rstrip(b"\n"))

    with pytest.raises(FactStreamError) as exc_info:
        _prepare(workspace)

    assert exc_info.value.code == "strict_stream_corruption"
    assert exc_info.value.details["strict_failure_code"] == "torn_tail"
