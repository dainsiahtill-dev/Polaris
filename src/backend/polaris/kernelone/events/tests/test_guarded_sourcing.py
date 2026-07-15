"""KernelOne race and durability evidence for guarded event sourcing."""

from __future__ import annotations

import ast
import multiprocessing
import os
from pathlib import Path

import pytest
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.events.sourcing import (
    AppendIfGuardedSnapshotCommandV1,
    EventSourcingError,
    GuardedFactEventV1,
    JsonlEventStore,
    ReadGuardedFactSnapshotCommandV1,
    append_if_guarded_snapshot,
    guarded,
    read_guarded_fact_snapshot,
)
from polaris.kernelone.fs import locked_regular_file, set_default_adapter
from polaris.kernelone.fs.locked_regular_file import (
    LockedRegularFileSetV1,
    StreamLeaseV1,
    default_platform_lock_root,
)


def _provision_store(store: JsonlEventStore, paths: tuple[str, ...]) -> None:
    identity = store.storage_identity
    LockedRegularFileSetV1.provision_authority(
        platform_lock_root=default_platform_lock_root(),
        storage_identity_token=identity.token,
        runtime_root=identity.runtime_root,
    )
    LockedRegularFileSetV1.enroll_stream_lock_keys(
        platform_lock_root=default_platform_lock_root(),
        storage_identity_token=identity.token,
        runtime_root=identity.runtime_root,
        logical_paths=paths,
    )


def _prepared_store(workspace: Path, *streams: str) -> JsonlEventStore:
    workspace.mkdir(parents=True, exist_ok=True)
    store = JsonlEventStore(str(workspace))
    _provision_store(store, tuple(store.stream_logical_path(stream) for stream in streams))
    return store


@pytest.fixture(autouse=True)
def _inject_local_adapter() -> None:
    set_default_adapter(LocalFileSystemAdapter())


def _prepared_command(workspace: Path, key: str = "key-1") -> AppendIfGuardedSnapshotCommandV1:
    snapshot = read_guarded_fact_snapshot(
        ReadGuardedFactSnapshotCommandV1(
            workspace=str(workspace),
            target_stream="target",
            guard_stream="guard",
        )
    )
    return AppendIfGuardedSnapshotCommandV1(
        snapshot_proof=snapshot.proof,
        event=GuardedFactEventV1(
            event_type="recorded",
            source="test.guarded",
            payload={"id": key},
            metadata={},
        ),
        idempotency_key=key,
    )


def test_guarded_snapshot_projects_torn_tail_as_strict_corruption(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "target", "guard")
    target_path = store._kernel_fs.resolve_path(store.stream_logical_path("target"))
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text('{"schema_version":1', encoding="utf-8")

    with pytest.raises(EventSourcingError) as exc_info:
        read_guarded_fact_snapshot(
            ReadGuardedFactSnapshotCommandV1(
                workspace=str(workspace),
                target_stream="target",
                guard_stream="guard",
            )
        )

    assert exc_info.value.code == "strict_stream_corruption"
    assert exc_info.value.details["strict_reason"] == "torn_tail"
    assert exc_info.value.details["reason_code"] == "torn_tail"
    assert exc_info.value.details["strict_failure_code"] == "torn_tail"


def test_guarded_snapshot_unknown_runtime_error_propagates_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    _prepared_store(workspace, "target", "guard")
    failure = RuntimeError("injected internal failure")

    def fail_resolution(**_kwargs: object) -> object:
        raise failure

    monkeypatch.setattr(guarded, "_resolve_distinct_streams", fail_resolution)
    with pytest.raises(RuntimeError) as exc_info:
        read_guarded_fact_snapshot(
            ReadGuardedFactSnapshotCommandV1(
                workspace=str(workspace),
                target_stream="target",
                guard_stream="guard",
            )
        )

    assert exc_info.value is failure


def test_guarded_append_unknown_runtime_error_propagates_without_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "target", "guard")
    command = _prepared_command(workspace)
    failure = RuntimeError("injected proof-resolution failure")

    def fail_resolution(**_kwargs: object) -> object:
        raise failure

    monkeypatch.setattr(guarded, "_resolve_distinct_streams", fail_resolution)
    with pytest.raises(RuntimeError) as exc_info:
        append_if_guarded_snapshot(command)

    assert exc_info.value is failure
    assert store.query(stream="target", strict_integrity=True).total == 0


def test_guarded_append_os_error_maps_to_capability_failure_without_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "target", "guard")
    command = _prepared_command(workspace)
    failure = OSError("injected filesystem failure")

    def fail_resolution(**_kwargs: object) -> object:
        raise failure

    monkeypatch.setattr(guarded, "_resolve_distinct_streams", fail_resolution)
    with pytest.raises(EventSourcingError) as exc_info:
        append_if_guarded_snapshot(command)

    assert exc_info.value.code == "guarded_fs_capability_unavailable"
    assert exc_info.value.__cause__ is failure
    assert store.query(stream="target", strict_integrity=True).total == 0


def test_guarded_append_value_error_maps_to_invalid_proof_without_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "target", "guard")
    command = _prepared_command(workspace)
    failure = ValueError("injected invalid proof binding")

    def fail_resolution(**_kwargs: object) -> object:
        raise failure

    monkeypatch.setattr(guarded, "_resolve_distinct_streams", fail_resolution)
    with pytest.raises(EventSourcingError) as exc_info:
        append_if_guarded_snapshot(command)

    assert exc_info.value.code == "snapshot_proof_invalid"
    assert exc_info.value.__cause__ is failure
    assert store.query(stream="target", strict_integrity=True).total == 0


def test_guarded_append_projects_strict_corruption_after_prepare(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "target", "guard")
    command = _prepared_command(workspace)
    target_path = store._kernel_fs.resolve_path(store.stream_logical_path("target"))
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text("{not-json}\n", encoding="utf-8")

    with pytest.raises(EventSourcingError) as exc_info:
        append_if_guarded_snapshot(command)

    assert exc_info.value.code == "strict_stream_corruption"
    assert exc_info.value.details["strict_reason"] == "middle_corruption"
    assert exc_info.value.details["reason_code"] == "stream_corruption"


def _prepare_many_in_process(
    workspace: str,
    target_stream: str,
    guard_stream: str,
    result_queue: multiprocessing.Queue[tuple[str, str]],
) -> None:
    """Exercise reversed two-lock order repeatedly in a spawned process."""

    set_default_adapter(LocalFileSystemAdapter())
    store = JsonlEventStore(workspace)
    _provision_store(store, (store.stream_logical_path(target_stream), store.stream_logical_path(guard_stream)))
    try:
        for _ in range(20):
            snapshot = read_guarded_fact_snapshot(
                ReadGuardedFactSnapshotCommandV1(
                    workspace=workspace,
                    target_stream=target_stream,
                    guard_stream=guard_stream,
                )
            )
            result_queue.put(("ok", snapshot.proof.continuity_digest))
    except EventSourcingError as exc:
        result_queue.put(("error", exc.code))


def _guarded_commit_in_process(
    command: AppendIfGuardedSnapshotCommandV1,
    result_queue: multiprocessing.Queue[tuple[str, str]],
) -> None:
    """Commit one prepared command in a real competing process."""

    set_default_adapter(LocalFileSystemAdapter())
    try:
        receipt = append_if_guarded_snapshot(command)
        result_queue.put(("ok", receipt.event_id))
    except EventSourcingError as exc:
        result_queue.put(("error", exc.code))


def _legacy_append_in_process(workspace: str, result_queue: multiprocessing.Queue[tuple[str, str]]) -> None:
    """Use the legacy stream append against a guarded commit's target stream."""

    set_default_adapter(LocalFileSystemAdapter())
    try:
        event = JsonlEventStore(workspace).append(
            stream="target",
            event_type="legacy",
            source="test.legacy",
            payload={"kind": "legacy"},
            strict_integrity=True,
            durability="fsync",
            idempotency_key="legacy-key",
        )
        result_queue.put(("ok", event.event_id))
    except EventSourcingError as exc:
        result_queue.put(("error", exc.code))


def test_reversed_stream_inputs_have_same_canonical_lock_order_and_no_process_deadlock(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # The backend suite is loaded through pytest's source-root package alias;
    # fork keeps this a real cross-process lock test without requiring that
    # alias to be importable by a fresh interpreter.
    context = multiprocessing.get_context("fork")
    result_queue: multiprocessing.Queue[tuple[str, str]] = context.Queue()
    first = context.Process(
        target=_prepare_many_in_process,
        args=(str(workspace), "alpha", "omega", result_queue),
    )
    second = context.Process(
        target=_prepare_many_in_process,
        args=(str(workspace), "omega", "alpha", result_queue),
    )
    first.start()
    second.start()
    first.join(timeout=15)
    second.join(timeout=15)

    assert not first.is_alive()
    assert not second.is_alive()
    assert first.exitcode == 0
    assert second.exitcode == 0
    results = [result_queue.get(timeout=2) for _ in range(40)]
    assert all(status == "ok" for status, _ in results)


def test_reversed_stream_inputs_acquire_the_same_canonical_lock_order(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "alpha", "omega")
    forward_paths = (
        store.stream_logical_path("alpha"),
        store.stream_logical_path("omega"),
    )
    reverse_paths = tuple(reversed(forward_paths))
    with LockedRegularFileSetV1.acquire(
        runtime_root=store.storage_identity.runtime_root,
        storage_identity_token=store.storage_identity.token,
        logical_paths=forward_paths,
    ) as forward_lock_set:
        forward = tuple(os.path.basename(os.readlink(f"/proc/self/fd/{fd}")) for fd in forward_lock_set._lock_fds)
    with LockedRegularFileSetV1.acquire(
        runtime_root=store.storage_identity.runtime_root,
        storage_identity_token=store.storage_identity.token,
        logical_paths=reverse_paths,
    ) as reverse_lock_set:
        reverse = tuple(os.path.basename(os.readlink(f"/proc/self/fd/{fd}")) for fd in reverse_lock_set._lock_fds)

    assert forward == reverse
    assert forward == tuple(sorted(forward))


def test_reversed_guarded_commits_do_not_deadlock_and_one_observes_guard_drift(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _prepared_store(workspace, "alpha", "omega")
    first_snapshot = read_guarded_fact_snapshot(
        ReadGuardedFactSnapshotCommandV1(workspace=str(workspace), target_stream="alpha", guard_stream="omega")
    )
    second_snapshot = read_guarded_fact_snapshot(
        ReadGuardedFactSnapshotCommandV1(workspace=str(workspace), target_stream="omega", guard_stream="alpha")
    )
    first = AppendIfGuardedSnapshotCommandV1(
        snapshot_proof=first_snapshot.proof,
        event=GuardedFactEventV1(event_type="recorded", source="test", payload={"id": "alpha"}),
        idempotency_key="alpha-key",
    )
    second = AppendIfGuardedSnapshotCommandV1(
        snapshot_proof=second_snapshot.proof,
        event=GuardedFactEventV1(event_type="recorded", source="test", payload={"id": "omega"}),
        idempotency_key="omega-key",
    )
    context = multiprocessing.get_context("fork")
    results: multiprocessing.Queue[tuple[str, str]] = context.Queue()
    processes = [
        context.Process(target=_guarded_commit_in_process, args=(first, results)),
        context.Process(target=_guarded_commit_in_process, args=(second, results)),
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)

    assert all(not process.is_alive() and process.exitcode == 0 for process in processes)
    outcomes = [results.get(timeout=2) for _ in processes]
    assert sorted(status for status, _ in outcomes) == ["error", "ok"]
    assert next(value for status, value in outcomes if status == "error") == "guard_snapshot_drift"


def test_legacy_append_serializes_with_guarded_commit_without_corrupting_stream(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "target", "guard")
    command = _prepared_command(workspace, key="guarded-key")
    context = multiprocessing.get_context("fork")
    results: multiprocessing.Queue[tuple[str, str]] = context.Queue()
    guarded_process = context.Process(target=_guarded_commit_in_process, args=(command, results))
    legacy_process = context.Process(target=_legacy_append_in_process, args=(str(workspace), results))
    guarded_process.start()
    legacy_process.start()
    guarded_process.join(timeout=15)
    legacy_process.join(timeout=15)

    assert guarded_process.exitcode == 0
    assert legacy_process.exitcode == 0
    outcomes = [results.get(timeout=2) for _ in range(2)]
    assert all(status in {"ok", "error"} for status, _ in outcomes)
    records = store.query(stream="target", strict_integrity=True).events
    assert [record.seq for record in records] == list(range(1, len(records) + 1))
    assert len(records) in {1, 2}


def test_lock_timeout_releases_already_acquired_lock(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "alpha", "omega")
    alpha_path = store.stream_logical_path("alpha")
    with (
        LockedRegularFileSetV1.acquire(
            runtime_root=store.storage_identity.runtime_root,
            storage_identity_token=store.storage_identity.token,
            logical_paths=(alpha_path,),
        ),
        pytest.raises(EventSourcingError) as exc_info,
    ):
        read_guarded_fact_snapshot(
            ReadGuardedFactSnapshotCommandV1(
                workspace=str(workspace), target_stream="alpha", guard_stream="omega"
            )
        )

    assert exc_info.value.code == "lock_acquisition_timeout"
    read_guarded_fact_snapshot(
        ReadGuardedFactSnapshotCommandV1(workspace=str(workspace), target_stream="alpha", guard_stream="omega")
    )


def test_fsync_failure_emits_no_receipt_and_replay_reconciles_a_durable_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "target", "guard")
    command = _prepared_command(workspace)
    target_path = store._kernel_fs.resolve_path(store.stream_logical_path("target"))
    for stream in ("target", "guard"):
        stream_path = store._kernel_fs.resolve_path(store.stream_logical_path(stream))
        stream_path.parent.mkdir(parents=True, exist_ok=True)
        stream_path.touch()
    original_fsync = locked_regular_file.os.fsync

    def fail_fsync(fd: int) -> None:
        if os.path.realpath(os.readlink(f"/proc/self/fd/{fd}")) == str(target_path):
            raise OSError("injected fsync failure")
        original_fsync(fd)

    monkeypatch.setattr(locked_regular_file.os, "fsync", fail_fsync)
    with pytest.raises(EventSourcingError) as exc_info:
        append_if_guarded_snapshot(command)
    assert exc_info.value.code == "post_fsync_authority_reconciliation_required"

    monkeypatch.setattr(locked_regular_file.os, "fsync", original_fsync)
    replay = append_if_guarded_snapshot(command)
    assert replay.appended_seq == 1
    assert store.query(stream="target", strict_integrity=True).total == 1


def test_write_failure_does_not_create_a_strictly_replayable_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "target", "guard")
    command = _prepared_command(workspace)

    def fail_before_write(self: StreamLeaseV1, _payload: bytes, **_kwargs: object) -> None:
        raise OSError("injected write failure")

    monkeypatch.setattr(StreamLeaseV1, "append_bytes", fail_before_write)
    with pytest.raises(EventSourcingError) as exc_info:
        append_if_guarded_snapshot(command)
    assert exc_info.value.code == "append_write_failed"
    assert store.query(stream="target", strict_integrity=True).total == 0


def test_flush_failure_emits_no_receipt_and_replay_reconciles_the_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "target", "guard")
    command = _prepared_command(workspace, key="flush-key")
    target_path = store._kernel_fs.resolve_path(store.stream_logical_path("target"))
    for stream in ("target", "guard"):
        stream_path = store._kernel_fs.resolve_path(store.stream_logical_path(stream))
        stream_path.parent.mkdir(parents=True, exist_ok=True)
        stream_path.touch()
    original_flush = locked_regular_file.os.fsync

    def fail_flush(fd: int) -> None:
        if os.path.realpath(os.readlink(f"/proc/self/fd/{fd}")) == str(target_path):
            raise OSError("injected flush failure")
        original_flush(fd)

    monkeypatch.setattr(locked_regular_file.os, "fsync", fail_flush)
    with pytest.raises(EventSourcingError) as exc_info:
        append_if_guarded_snapshot(command)
    assert exc_info.value.code == "post_fsync_authority_reconciliation_required"

    monkeypatch.setattr(locked_regular_file.os, "fsync", original_flush)
    assert append_if_guarded_snapshot(command).appended_seq == 1


def test_kernelone_sourcing_has_no_taskruntime_import() -> None:
    sourcing_root = Path(__file__).parents[1] / "sourcing"
    imported_modules: set[str] = set()
    for source_path in sourcing_root.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_modules.update(node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom))
        imported_modules.update(
            alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
        )
    assert not any("task_runtime" in module for module in imported_modules)


def test_sourcing_and_factstream_do_not_restore_the_legacy_sequence_lock() -> None:
    """Keep the cutover independent of io_events JSONL's separate lock contract."""

    polaris_root = Path(__file__).parents[3]
    source_roots = (
        polaris_root / "kernelone" / "events" / "sourcing",
        polaris_root / "cells" / "events" / "fact_stream" / "public",
    )
    for source_root in source_roots:
        for source_path in source_root.glob("*.py"):
            assert ".seq.lock" not in source_path.read_text(encoding="utf-8")
