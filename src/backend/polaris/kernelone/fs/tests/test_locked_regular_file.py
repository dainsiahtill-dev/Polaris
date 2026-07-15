"""Regression coverage for persistent descriptor-bound regular-file leases."""

from __future__ import annotations

import errno
import json
import multiprocessing
import os
import subprocess
import sys
import threading
import time
from collections.abc import Buffer
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from polaris.kernelone.fs import locked_regular_file
from polaris.kernelone.fs.locked_regular_file import (
    LockedRegularFileError,
    LockedRegularFileSetV1,
    LockMaintenanceProofV1,
)

_ENROLLMENT_SUBPROCESS_PROBE = r"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from polaris.kernelone.fs.locked_regular_file import LockedRegularFileSetV1


def identity_record(identity: object) -> list[int]:
    return [int(getattr(identity, "device")), int(getattr(identity, "inode"))]


runtime_root, authority_root, ready_file, start_file = sys.argv[1:5]
ready_path = Path(ready_file)
start_path = Path(start_file)
ready_path.write_text("ready\n", encoding="utf-8")

barrier_deadline = time.monotonic() + 120.0
while not start_path.exists():
    if time.monotonic() >= barrier_deadline:
        raise TimeoutError("subprocess enrollment start barrier timed out")
    time.sleep(0.005)

proof = LockedRegularFileSetV1.enroll_stream_lock_keys(
    platform_lock_root=authority_root,
    storage_identity_token="storage-token",
    runtime_root=runtime_root,
    logical_paths=("runtime/events/process-stress.jsonl",),
)
key = proof.lock_keys[0]
result = {
    "anchor_identity": identity_record(proof.anchor_identity),
    "final_validation": proof.final_validation,
    "key_identity": identity_record(key.identity),
    "key_verdict": key.verdict,
    "lock_key": key.lock_key,
    "realm_identity": identity_record(proof.realm_identity),
    "root_identity": identity_record(proof.root_identity),
    "status": "ok",
    "verdict": proof.verdict,
}
sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
sys.stdout.flush()
"""


def _reap_probe_processes(processes: list[subprocess.Popen[str]]) -> list[tuple[int, str, str]]:
    """Terminate, kill if needed, and fully collect every child pipe."""

    for process in processes:
        if process.poll() is None:
            process.terminate()

    terminate_deadline = time.monotonic() + 5.0
    while any(process.poll() is None for process in processes) and time.monotonic() < terminate_deadline:
        time.sleep(0.01)

    for process in processes:
        if process.poll() is None:
            process.kill()

    outputs: list[tuple[int, str, str]] = []
    for process in processes:
        try:
            stdout, stderr = process.communicate(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate(timeout=5.0)
        returncode = process.returncode
        if returncode is None:
            raise AssertionError(f"subprocess {process.pid} was not reaped")
        outputs.append((returncode, stdout, stderr))
    return outputs


def _probe_diagnostics(outputs: list[tuple[int, str, str]]) -> str:
    """Render complete per-process output for assertion failures."""

    return "\n".join(
        f"process[{index}] returncode={returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}"
        for index, (returncode, stdout, stderr) in enumerate(outputs)
    )


def _probe_identity(record: dict[str, object], field_name: str) -> tuple[int, int]:
    """Validate and project one structured physical identity."""

    value = record.get(field_name)
    assert isinstance(value, list)
    assert len(value) == 2
    device, inode = value
    assert isinstance(device, int)
    assert isinstance(inode, int)
    return device, inode


def _leases(root: Path, *logical_paths: str) -> LockedRegularFileSetV1:
    authority_root = root.parent / "authority"
    LockedRegularFileSetV1.provision_authority(
        platform_lock_root=str(authority_root),
        storage_identity_token="storage-token",
        runtime_root=str(root),
    )
    LockedRegularFileSetV1.enroll_stream_lock_keys(
        platform_lock_root=str(authority_root),
        storage_identity_token="storage-token",
        runtime_root=str(root),
        logical_paths=logical_paths,
    )
    return LockedRegularFileSetV1.acquire(
        runtime_root=str(root),
        storage_identity_token="storage-token",
        logical_paths=logical_paths,
        platform_lock_root=str(authority_root),
    )


def _hold_realm_then_append(
    root: str,
    ready: multiprocessing.synchronize.Event,
    proceed: multiprocessing.synchronize.Event,
    outcomes: multiprocessing.Queue[str],
) -> None:
    """Hold a live lease until the parent deterministically replaces its realm."""

    with _leases(Path(root), "runtime/events/target.jsonl") as leases:
        lease = leases.lease("runtime/events/target.jsonl")
        assert lease.open_existing(writable=True)
        original_write = locked_regular_file.os.write

        def pause_after_precheck(fd: int, payload: Buffer) -> int:
            ready.set()
            assert proceed.wait(timeout=5)
            return original_write(fd, payload)

        locked_regular_file.os.write = pause_after_precheck
        try:
            lease.append_bytes(b"first\n", fsync_file=True, fsync_parent_on_create=False)
        except LockedRegularFileError as exc:
            outcomes.put(exc.code)
        else:
            outcomes.put("unexpected_success")
        finally:
            locked_regular_file.os.write = original_write


def test_persistent_lock_survives_events_directory_replacement_and_detects_drift(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    events = root / "events"
    events.mkdir(parents=True)
    stream = events / "target.jsonl"
    stream.write_bytes(b"seed\n")

    with _leases(root, "runtime/events/target.jsonl") as leases:
        lease = leases.lease("runtime/events/target.jsonl")
        assert lease.open_existing()
        original_locks = tuple((root.parent / "authority" / "storage-token" / "realm").iterdir())
        events.rename(root / "events-old")
        events.mkdir()
        with pytest.raises(LockedRegularFileError) as caught:
            lease.read_bytes()

    assert caught.value.code == "stream_identity_drift"
    assert all(path.exists() and path.is_file() for path in original_locks)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO coverage requires POSIX mkfifo")
def test_fifo_and_hardlink_leaves_fail_without_blocking(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    events = root / "events"
    events.mkdir(parents=True)
    fifo_path = events / "fifo.jsonl"
    os.mkfifo(fifo_path)
    started = time.monotonic()
    with _leases(root, "runtime/events/fifo.jsonl") as leases, pytest.raises(LockedRegularFileError) as fifo_error:
        leases.lease("runtime/events/fifo.jsonl").open_existing()
    assert fifo_error.value.code == "unsafe_stream_object"
    assert time.monotonic() - started < 1.0

    regular = events / "regular.jsonl"
    regular.write_bytes(b"record\n")
    os.link(regular, events / "regular-copy.jsonl")
    with (
        _leases(root, "runtime/events/regular.jsonl") as leases,
        pytest.raises(LockedRegularFileError) as hardlink_error,
    ):
        leases.lease("runtime/events/regular.jsonl").open_existing()
    assert hardlink_error.value.code == "hard_link_rejected"


def test_first_create_fsyncs_file_created_ancestor_and_root_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    _leases(root, "runtime/events/new.jsonl").close()
    fsync_calls: list[int] = []
    original_fsync = locked_regular_file.os.fsync

    def capture_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        original_fsync(fd)

    monkeypatch.setattr(locked_regular_file.os, "fsync", capture_fsync)
    with LockedRegularFileSetV1.acquire(
        runtime_root=str(root),
        storage_identity_token="storage-token",
        logical_paths=("runtime/events/new.jsonl",),
        platform_lock_root=str(root.parent / "authority"),
    ) as leases:
        lease = leases.lease("runtime/events/new.jsonl")
        assert not lease.open_existing(writable=True)
        lease.append_bytes(b"record\n", fsync_file=True, fsync_parent_on_create=True)

    assert (root / "events" / "new.jsonl").read_bytes() == b"record\n"
    assert len(fsync_calls) == 3
    assert len(set(fsync_calls)) == 3


def test_windows_without_reparse_safe_backend_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(locked_regular_file, "_platform_name", lambda: "nt")
    with pytest.raises(LockedRegularFileError) as caught:
        _leases(tmp_path / "runtime", "runtime/events/target.jsonl")
    assert caught.value.code == "guarded_fs_capability_unavailable"


def test_root_replacement_and_displaced_lock_file_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    events = root / "events"
    events.mkdir(parents=True)
    target = events / "target.jsonl"
    target.write_bytes(b"record\n")
    with _leases(root, "runtime/events/target.jsonl") as leases:
        lease = leases.lease("runtime/events/target.jsonl")
        assert lease.open_existing()
        root.rename(tmp_path / "runtime-old")
        root.mkdir()
        with pytest.raises(LockedRegularFileError) as root_error:
            lease.read_bytes()
    assert root_error.value.code == "stream_identity_drift"

    stable_root = tmp_path / "stable" / "runtime"
    with _leases(stable_root, "runtime/events/target.jsonl"):
        pass
    lock_path = next((stable_root.parent / "authority" / "storage-token" / "realm").iterdir())
    lock_path.unlink()
    lock_path.symlink_to(tmp_path / "outside-lock")
    with pytest.raises(LockedRegularFileError) as lock_error:
        LockedRegularFileSetV1.acquire(
            runtime_root=str(stable_root),
            storage_identity_token="storage-token",
            logical_paths=("runtime/events/target.jsonl",),
            platform_lock_root=str(stable_root.parent / "authority"),
        )
    assert lock_error.value.code == "stream_lock_invalid"


def test_live_realm_replacement_fences_the_old_lease_in_a_second_process() -> None:
    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("requires POSIX fork multiprocessing")
    with TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory) / "runtime"
        events = root / "events"
        events.mkdir(parents=True)
        target = events / "target.jsonl"
        target.write_bytes(b"seed\n")
        context = multiprocessing.get_context("fork")
        ready = context.Event()
        proceed = context.Event()
        outcomes: multiprocessing.Queue[str] = context.Queue()
        process = context.Process(target=_hold_realm_then_append, args=(str(root), ready, proceed, outcomes))
        process.start()
        assert ready.wait(timeout=5)
        realm = root.parent / "authority" / "storage-token" / "realm"
        realm.rename(realm.with_name("v1-original"))
        realm.mkdir()
        with pytest.raises(LockedRegularFileError) as replacement_error:
            LockedRegularFileSetV1.acquire(
                runtime_root=str(root),
                storage_identity_token="storage-token",
                logical_paths=("runtime/events/target.jsonl",),
                platform_lock_root=str(root.parent / "authority"),
            )
        assert replacement_error.value.code == "lock_realm_binding_mismatch"
        proceed.set()
        process.join(timeout=5)
        assert process.exitcode == 0
        assert outcomes.get(timeout=2) == "post_fsync_authority_reconciliation_required"
        assert target.read_bytes() == b"seed\nfirst\n"


def test_ancestor_symlink_swap_and_direct_append_offset_are_fenced(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    events = root / "events"
    events.mkdir(parents=True)
    target = events / "target.jsonl"
    target.write_bytes(b"seed\n")
    with _leases(root, "runtime/events/target.jsonl") as leases:
        lease = leases.lease("runtime/events/target.jsonl")
        assert lease.open_existing(writable=True)
        assert lease._file_fd is not None
        os.lseek(lease._file_fd, 0, os.SEEK_SET)
        lease.append_bytes(b"append\n", fsync_file=False, fsync_parent_on_create=False)
    assert target.read_bytes() == b"seed\nappend\n"

    outside = tmp_path / "outside"
    outside.mkdir()
    outside_target = outside / "target.jsonl"
    outside_target.write_bytes(b"outside\n")
    with _leases(root, "runtime/events/target.jsonl") as leases:
        lease = leases.lease("runtime/events/target.jsonl")
        assert lease.open_existing(writable=True)
        events.rename(root / "events-original")
        events.symlink_to(outside, target_is_directory=True)
        with pytest.raises(LockedRegularFileError) as swap_error:
            lease.append_bytes(b"blocked\n", fsync_file=False, fsync_parent_on_create=False)
    assert swap_error.value.code == "stream_identity_drift"
    assert outside_target.read_bytes() == b"outside\n"


def test_parent_directory_fsync_failure_requires_reconciliation_with_exact_cause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    _leases(root, "runtime/events/new.jsonl").close()
    original_fsync = locked_regular_file.os.fsync
    calls = 0

    def fail_parent_directory_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("injected parent directory fsync failure")
        original_fsync(fd)

    monkeypatch.setattr(locked_regular_file.os, "fsync", fail_parent_directory_fsync)
    with (
        LockedRegularFileSetV1.acquire(
            runtime_root=str(root),
            storage_identity_token="storage-token",
            logical_paths=("runtime/events/new.jsonl",),
            platform_lock_root=str(root.parent / "authority"),
        ) as leases,
        pytest.raises(LockedRegularFileError) as caught,
    ):
        leases.lease("runtime/events/new.jsonl").append_bytes(
            b"record\n",
            fsync_file=True,
            fsync_parent_on_create=True,
        )
    assert caught.value.code == "post_fsync_authority_reconciliation_required"
    assert caught.value.details["cause_code"] == "stream_parent_directory_fsync_failed"
    assert caught.value.details["completed_fsync_order"] == ["runtime/events/new.jsonl", "runtime/events"]
    assert (root / "events" / "new.jsonl").read_bytes() == b"record\n"


def test_first_create_fsyncs_every_created_ancestor_and_parent_descendant_to_ancestor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    logical_path = "runtime/events/nested/new.jsonl"
    _leases(root, logical_path).close()
    original_fsync = locked_regular_file.os.fsync
    fsync_paths: list[str] = []

    def capture_fsync(fd: int) -> None:
        identities = {
            (path.stat().st_dev, path.stat().st_ino): logical
            for path, logical in (
                (root / "events" / "nested" / "new.jsonl", logical_path),
                (root / "events" / "nested", "runtime/events/nested"),
                (root / "events", "runtime/events"),
                (root, "runtime"),
            )
        }
        identity = os.fstat(fd)
        fsync_paths.append(identities[(identity.st_dev, identity.st_ino)])
        original_fsync(fd)

    monkeypatch.setattr(locked_regular_file.os, "fsync", capture_fsync)
    with LockedRegularFileSetV1.acquire(
        runtime_root=str(root),
        storage_identity_token="storage-token",
        logical_paths=(logical_path,),
        platform_lock_root=str(root.parent / "authority"),
    ) as leases:
        leases.lease(logical_path).append_bytes(
            b"record\n",
            fsync_file=True,
            fsync_parent_on_create=True,
        )

    assert fsync_paths == [
        logical_path,
        "runtime/events/nested",
        "runtime/events",
        "runtime",
    ]


@pytest.mark.parametrize(
    ("target", "expected_cause_code", "expected_completed_order"),
    (
        ("file", "file_fsync_reconciliation_required", []),
        ("nested", "stream_directory_fsync_failed", ["runtime/events/nested/target.jsonl"]),
        (
            "root",
            "stream_parent_directory_fsync_failed",
            ["runtime/events/nested/target.jsonl", "runtime/events/nested", "runtime/events"],
        ),
    ),
)
def test_first_create_durability_boundaries_require_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    expected_cause_code: str,
    expected_completed_order: list[str],
) -> None:
    root = tmp_path / "runtime"
    logical_path = "runtime/events/nested/target.jsonl"
    _leases(root, logical_path).close()
    original_fsync = locked_regular_file.os.fsync

    def fail_target_fsync(fd: int) -> None:
        target_path = {
            "file": root / "events" / "nested" / "target.jsonl",
            "nested": root / "events" / "nested",
            "root": root,
        }[target]
        identity = os.fstat(fd)
        target_identity = target_path.stat()
        if (identity.st_dev, identity.st_ino) == (target_identity.st_dev, target_identity.st_ino):
            raise OSError("injected stream durability failure")
        original_fsync(fd)

    monkeypatch.setattr(locked_regular_file.os, "fsync", fail_target_fsync)
    with (
        LockedRegularFileSetV1.acquire(
            runtime_root=str(root),
            storage_identity_token="storage-token",
            logical_paths=(logical_path,),
            platform_lock_root=str(root.parent / "authority"),
        ) as leases,
        pytest.raises(LockedRegularFileError) as caught,
    ):
        leases.lease(logical_path).append_bytes(
            b"record\n",
            fsync_file=True,
            fsync_parent_on_create=True,
        )

    assert caught.value.code == "post_fsync_authority_reconciliation_required"
    assert caught.value.details["cause_code"] == expected_cause_code
    assert caught.value.details["completed_fsync_order"] == expected_completed_order
    assert [item["path"] for item in caught.value.details["created_directories"]] == [
        "runtime/events",
        "runtime/events/nested",
    ]
    assert (root / "events" / "nested" / "target.jsonl").read_bytes() == b"record\n"


def test_first_create_release_failure_after_durability_requires_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    logical_path = "runtime/events/release.jsonl"
    _leases(root, logical_path).close()
    leases = LockedRegularFileSetV1.acquire(
        runtime_root=str(root),
        storage_identity_token="storage-token",
        logical_paths=(logical_path,),
        platform_lock_root=str(root.parent / "authority"),
    )
    lease = leases.lease(logical_path)
    lease.append_bytes(b"record\n", fsync_file=True, fsync_parent_on_create=True)
    created_fd = lease._directory_durability._created[0].fd
    original_close = locked_regular_file.os.close
    failed = False

    def fail_retained_descriptor_release(fd: int) -> None:
        nonlocal failed
        if fd == created_fd and not failed:
            failed = True
            raise OSError(errno.EIO, "injected retained descriptor release failure")
        original_close(fd)

    monkeypatch.setattr(locked_regular_file.os, "close", fail_retained_descriptor_release)
    with pytest.raises(LockedRegularFileError) as caught:
        leases.close()

    assert failed
    assert caught.value.code == "post_fsync_authority_reconciliation_required"
    assert caught.value.details["cause_code"] == "stream_lease_close_failed"
    evidence = caught.value.details["stream_durability"]
    assert isinstance(evidence, list)
    assert evidence[0]["completed_fsync_order"] == [
        logical_path,
        "runtime/events",
        "runtime",
    ]
    original_close(created_fd)


def test_first_create_file_exists_race_opens_and_verifies_the_elected_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    logical_path = "runtime/events/race.jsonl"
    _leases(root, logical_path).close()
    original_create = locked_regular_file._open_stream_exclusive
    creator_ready = threading.Event()
    release_competitor = threading.Event()
    create_attempts = 0

    def elect_winner_then_force_competitor_fallback(parent_fd: int, name: str) -> int:
        nonlocal create_attempts
        create_attempts += 1
        winner_fd = original_create(parent_fd, name)
        creator_ready.set()
        assert release_competitor.wait(timeout=2)
        os.close(winner_fd)
        raise FileExistsError(errno.EEXIST, "controlled stream create race", name)

    monkeypatch.setattr(locked_regular_file, "_open_stream_exclusive", elect_winner_then_force_competitor_fallback)
    with LockedRegularFileSetV1.acquire(
        runtime_root=str(root),
        storage_identity_token="storage-token",
        logical_paths=(logical_path,),
        platform_lock_root=str(root.parent / "authority"),
    ) as leases:
        lease = leases.lease(logical_path)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                lease.append_bytes,
                b"record\n",
                fsync_file=True,
                fsync_parent_on_create=True,
            )
            assert creator_ready.wait(timeout=2)
            assert (root / "events" / "race.jsonl").is_file()
            release_competitor.set()
            future.result(timeout=2)
            assert lease.exists

    assert create_attempts == 1
    assert (root / "events" / "race.jsonl").read_bytes() == b"record\n"


@pytest.mark.parametrize(
    ("drift", "cause_code"),
    (
        ("root", "stream_identity_drift"),
        ("ancestor", "stream_identity_drift"),
        ("parent", "stream_identity_drift"),
        ("leaf", "hard_link_rejected"),
        ("anchor", "lock_anchor_invalid"),
        ("realm", "lock_realm_binding_mismatch"),
    ),
)
def test_every_final_drift_after_file_fsync_requires_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
    cause_code: str,
) -> None:
    root = tmp_path / "runtime"
    target = root / "events" / "nested" / "target.jsonl"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"seed\n")
    authority = root.parent / "authority" / "storage-token"

    with _leases(root, "runtime/events/nested/target.jsonl") as leases:
        lease = leases.lease("runtime/events/nested/target.jsonl")
        assert lease.open_existing(writable=True)
        assert lease._file_fd is not None
        file_fd = lease._file_fd
        original_fsync = locked_regular_file.os.fsync
        drifted = False

        def drift_after_file_fsync(fd: int) -> None:
            nonlocal drifted
            original_fsync(fd)
            if drifted or fd != file_fd:
                return
            drifted = True
            if drift == "root":
                root.rename(tmp_path / "runtime-old")
                root.mkdir()
            elif drift == "ancestor":
                events = root / "events"
                events.rename(root / "events-old")
                events.mkdir()
            elif drift == "parent":
                nested = root / "events" / "nested"
                nested.rename(root / "events" / "nested-old")
                nested.mkdir()
            elif drift == "leaf":
                target.unlink()
                target.write_bytes(b"replacement\n")
            elif drift == "anchor":
                anchor = authority / "anchor.lock"
                anchor.rename(authority / "anchor-old.lock")
                anchor.write_bytes(b"replacement")
            else:
                realm = authority / "realm"
                realm.rename(authority / "realm-old")
                realm.mkdir()

        monkeypatch.setattr(locked_regular_file.os, "fsync", drift_after_file_fsync)
        with pytest.raises(LockedRegularFileError) as caught:
            lease.append_bytes(b"record\n", fsync_file=True, fsync_parent_on_create=False)

    assert caught.value.code == "post_fsync_authority_reconciliation_required"
    assert caught.value.details["cause_code"] == cause_code
    assert isinstance(caught.value.details["cause_details"], dict)


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        ("anchor_hardlink", "hard_link_rejected"),
        ("anchor_replacement", "lock_anchor_invalid"),
        ("realm_replacement", "lock_realm_binding_mismatch"),
    ),
)
def test_initial_provision_revalidates_anchor_and_realm_after_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    expected_code: str,
) -> None:
    root = tmp_path / "runtime"
    authority = root.parent / "authority" / "storage-token"
    original_fsync = locked_regular_file.os.fsync
    mutated = False

    def mutate_authority_after_fsync(fd: int) -> None:
        nonlocal mutated
        original_fsync(fd)
        if mutated:
            return
        mutated = True
        if mutation == "anchor_hardlink":
            os.link(authority / "anchor.lock", authority / "anchor-copy.lock")
        elif mutation == "anchor_replacement":
            anchor = authority / "anchor.lock"
            anchor.rename(authority / "anchor-original.lock")
            anchor.write_bytes(b"replacement")
        else:
            realm = authority / "realm"
            realm.rename(authority / "realm-original")
            realm.mkdir()

    monkeypatch.setattr(locked_regular_file.os, "fsync", mutate_authority_after_fsync)
    with pytest.raises(LockedRegularFileError) as caught:
        LockedRegularFileSetV1.provision_authority(
            platform_lock_root=str(root.parent / "authority"),
            storage_identity_token="storage-token",
            runtime_root=str(root),
        )

    assert caught.value.code == "post_fsync_authority_reconciliation_required"
    assert caught.value.details["cause_code"] == expected_code
    assert isinstance(caught.value.details["cause_details"], dict)


def test_authority_provision_fsyncs_created_directories_and_parents_in_descendant_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    authority_root = tmp_path / "locks" / "platform"
    original_fsync = locked_regular_file.os.fsync
    fsync_paths: list[str] = []

    def capture_fsync(fd: int) -> None:
        identities = {
            (path.stat().st_dev, path.stat().st_ino): str(path)
            for path in (
                authority_root / "storage-token" / "anchor.lock",
                authority_root / "storage-token" / "realm",
                authority_root / "storage-token",
                authority_root,
                tmp_path / "locks",
                tmp_path,
                root,
            )
            if path.exists()
        }
        identity = os.fstat(fd)
        fsync_paths.append(identities[(identity.st_dev, identity.st_ino)])
        original_fsync(fd)

    monkeypatch.setattr(locked_regular_file.os, "fsync", capture_fsync)
    proof = LockedRegularFileSetV1.provision_authority(
        platform_lock_root=str(authority_root),
        storage_identity_token="storage-token",
        runtime_root=str(root),
    )

    expected_created = (
        str(tmp_path / "locks"),
        str(authority_root),
        str(authority_root / "storage-token"),
        str(root),
        str(authority_root / "storage-token" / "realm"),
    )
    assert proof.created_directories == expected_created
    assert proof.completed_fsync_order == tuple(fsync_paths)
    assert fsync_paths[0] == str(authority_root / "storage-token" / "anchor.lock")
    assert fsync_paths[1:] == [
        str(authority_root / "storage-token" / "realm"),
        str(root),
        str(authority_root / "storage-token"),
        str(authority_root),
        str(tmp_path / "locks"),
        str(tmp_path),
    ]


def test_existing_authority_has_no_created_directory_durability_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    authority_root = root.parent / "authority"
    LockedRegularFileSetV1.provision_authority(
        platform_lock_root=str(authority_root),
        storage_identity_token="storage-token",
        runtime_root=str(root),
    )
    original_fsync = locked_regular_file.os.fsync
    fsync_calls: list[int] = []

    def capture_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        original_fsync(fd)

    monkeypatch.setattr(locked_regular_file.os, "fsync", capture_fsync)
    proof = LockedRegularFileSetV1.provision_authority(
        platform_lock_root=str(authority_root),
        storage_identity_token="storage-token",
        runtime_root=str(root),
    )

    assert proof.verdict == "already_present"
    assert proof.created_directories == ()
    assert proof.completed_fsync_order == ()
    assert fsync_calls == []


@pytest.mark.parametrize(
    ("target", "expected_cause_code"),
    (
        ("anchor", "anchor_fsync_failed"),
        ("realm", "directory_fsync_failed"),
        ("parent", "parent_directory_fsync_failed"),
    ),
)
def test_authority_provision_fsync_failures_require_reconciliation_without_success_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    expected_cause_code: str,
) -> None:
    root = tmp_path / "runtime"
    authority_root = tmp_path / "authority"
    authority = authority_root / "storage-token"
    original_fsync = locked_regular_file.os.fsync

    def fail_target_fsync(fd: int) -> None:
        identity = os.fstat(fd)
        if target == "anchor":
            target_identity = (authority / "anchor.lock").stat()
        elif target == "realm":
            target_identity = (authority / "realm").stat()
        else:
            target_identity = tmp_path.stat()
        if (identity.st_dev, identity.st_ino) == (target_identity.st_dev, target_identity.st_ino):
            raise OSError("injected authority durability failure")
        original_fsync(fd)

    monkeypatch.setattr(locked_regular_file.os, "fsync", fail_target_fsync)
    proof: LockMaintenanceProofV1 | None = None
    with pytest.raises(LockedRegularFileError) as caught:
        proof = LockedRegularFileSetV1.provision_authority(
            platform_lock_root=str(authority_root),
            storage_identity_token="storage-token",
            runtime_root=str(root),
        )

    assert proof is None
    assert caught.value.code == "post_fsync_authority_reconciliation_required"
    assert caught.value.details["cause_code"] == expected_cause_code
    assert isinstance(caught.value.details["cause_details"], dict)
    assert caught.value.details["created_directories"]
    assert isinstance(caught.value.details["completed_fsync_order"], list)


def test_authority_provision_revalidates_after_later_directory_durability_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    authority_root = tmp_path / "authority"
    authority = authority_root / "storage-token"
    realm = authority / "realm"
    original_fsync = locked_regular_file.os.fsync
    replaced = False

    def replace_realm_after_authority_fsync(fd: int) -> None:
        nonlocal replaced
        original_fsync(fd)
        if replaced or not authority.exists():
            return
        identity = os.fstat(fd)
        authority_identity = authority.stat()
        if (identity.st_dev, identity.st_ino) != (authority_identity.st_dev, authority_identity.st_ino):
            return
        replaced = True
        realm.rename(authority / "realm-before-revalidation")
        realm.mkdir()

    monkeypatch.setattr(locked_regular_file.os, "fsync", replace_realm_after_authority_fsync)
    with pytest.raises(LockedRegularFileError) as caught:
        LockedRegularFileSetV1.provision_authority(
            platform_lock_root=str(authority_root),
            storage_identity_token="storage-token",
            runtime_root=str(root),
        )

    assert replaced
    assert caught.value.code == "post_fsync_authority_reconciliation_required"
    assert caught.value.details["cause_code"] == "lock_realm_binding_mismatch"


def test_anchor_and_realm_symlinks_preserve_exact_failure_taxonomy(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    authority = root.parent / "authority" / "storage-token"
    authority.mkdir(parents=True)
    (authority / "anchor.lock").symlink_to(tmp_path / "outside-anchor")

    with pytest.raises(LockedRegularFileError) as anchor_error:
        LockedRegularFileSetV1.provision_authority(
            platform_lock_root=str(root.parent / "authority"),
            storage_identity_token="storage-token",
            runtime_root=str(root),
        )
    assert anchor_error.value.code == "lock_anchor_invalid"

    (authority / "anchor.lock").unlink()
    LockedRegularFileSetV1.provision_authority(
        platform_lock_root=str(root.parent / "authority"),
        storage_identity_token="storage-token",
        runtime_root=str(root),
    )
    realm = authority / "realm"
    realm.rename(authority / "realm-original")
    realm.symlink_to(tmp_path / "outside-realm", target_is_directory=True)

    with pytest.raises(LockedRegularFileError) as realm_error:
        LockedRegularFileSetV1.acquire(
            runtime_root=str(root),
            storage_identity_token="storage-token",
            logical_paths=(),
            platform_lock_root=str(root.parent / "authority"),
        )
    assert realm_error.value.code == "lock_realm_binding_mismatch"


def test_initial_provision_crash_point_does_not_self_heal_partial_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    authority = root.parent / "authority" / "storage-token"
    (authority / "realm").mkdir(parents=True)
    moments = iter((0.0, 3.0))
    monkeypatch.setattr(locked_regular_file.time, "monotonic", lambda: next(moments))

    with pytest.raises(LockedRegularFileError) as caught:
        LockedRegularFileSetV1.provision_authority(
            platform_lock_root=str(root.parent / "authority"),
            storage_identity_token="storage-token",
            runtime_root=str(root),
        )

    assert caught.value.code == "lock_authority_provision_conflict"
    assert not (authority / "anchor.lock").exists()


def test_acquire_never_provisions_a_missing_authority(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    authority_root = root.parent / "authority"

    with pytest.raises(LockedRegularFileError) as caught:
        LockedRegularFileSetV1.acquire(
            runtime_root=str(root),
            storage_identity_token="storage-token",
            logical_paths=("runtime/events/target.jsonl",),
            platform_lock_root=str(authority_root),
        )

    assert caught.value.code == "lock_authority_missing"
    assert not authority_root.exists()


def test_maintenance_successes_return_stable_typed_physical_proof(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    authority_root = root.parent / "authority"
    authority = authority_root / "storage-token"

    created_authority = LockedRegularFileSetV1.provision_authority(
        platform_lock_root=str(authority_root),
        storage_identity_token="storage-token",
        runtime_root=str(root),
    )
    existing_authority = LockedRegularFileSetV1.provision_authority(
        platform_lock_root=str(authority_root),
        storage_identity_token="storage-token",
        runtime_root=str(root),
    )

    assert isinstance(created_authority, LockMaintenanceProofV1)
    assert created_authority.operation == "provision_authority"
    assert created_authority.verdict == "created"
    assert existing_authority.verdict == "already_present"
    assert created_authority.final_validation is True
    assert created_authority.lock_keys == ()
    assert created_authority.format_revision == locked_regular_file.LOCK_AUTHORITY_FORMAT_REVISION
    assert created_authority.root_identity.device == root.stat().st_dev
    assert created_authority.root_identity.inode == root.stat().st_ino
    assert created_authority.anchor_identity.inode == (authority / "anchor.lock").stat().st_ino
    assert created_authority.realm_identity.inode == (authority / "realm").stat().st_ino
    assert existing_authority.root_identity == created_authority.root_identity
    assert existing_authority.anchor_identity == created_authority.anchor_identity
    assert existing_authority.realm_identity == created_authority.realm_identity
    assert created_authority.to_record() == created_authority.to_record()

    logical_paths = (
        "runtime/events/zeta.jsonl",
        "runtime/events/alpha.jsonl",
        "runtime/events/zeta.jsonl",
    )
    created_keys = LockedRegularFileSetV1.enroll_stream_lock_keys(
        platform_lock_root=str(authority_root),
        storage_identity_token="storage-token",
        runtime_root=str(root),
        logical_paths=logical_paths,
    )
    existing_keys = LockedRegularFileSetV1.enroll_stream_lock_keys(
        platform_lock_root=str(authority_root),
        storage_identity_token="storage-token",
        runtime_root=str(root),
        logical_paths=reversed(logical_paths),
    )

    assert created_keys.operation == "enroll_stream_lock_keys"
    assert created_keys.verdict == "created"
    assert created_keys.final_validation is True
    assert len(created_keys.lock_keys) == 2
    assert [item.lock_key for item in created_keys.lock_keys] == sorted(
        item.lock_key for item in created_keys.lock_keys
    )
    assert {item.logical_path for item in created_keys.lock_keys} == {
        "runtime/events/alpha.jsonl",
        "runtime/events/zeta.jsonl",
    }
    assert all(item.verdict == "created" for item in created_keys.lock_keys)
    assert all(item.identity.inode == (authority / "realm" / item.lock_key).stat().st_ino for item in created_keys.lock_keys)
    assert existing_keys.verdict == "already_present"
    assert all(item.verdict == "already_present" for item in existing_keys.lock_keys)
    assert [item.lock_key for item in existing_keys.lock_keys] == [
        item.lock_key for item in created_keys.lock_keys
    ]
    assert existing_keys.root_identity == created_keys.root_identity
    assert existing_keys.anchor_identity == created_keys.anchor_identity
    assert existing_keys.realm_identity == created_keys.realm_identity
    assert [item.identity for item in existing_keys.lock_keys] == [item.identity for item in created_keys.lock_keys]


@pytest.mark.parametrize(
    ("operation", "expected_code"),
    (
        ("provision", "lock_anchor_binding_mismatch"),
        ("enroll", "stream_identity_drift"),
        ("acquire", "stream_identity_drift"),
    ),
)
def test_same_path_runtime_root_inode_replacement_fails_closed(
    tmp_path: Path,
    operation: str,
    expected_code: str,
) -> None:
    root = tmp_path / "runtime"
    authority_root = root.parent / "authority"
    LockedRegularFileSetV1.provision_authority(
        platform_lock_root=str(authority_root),
        storage_identity_token="storage-token",
        runtime_root=str(root),
    )
    realm = authority_root / "storage-token" / "realm"
    original_realm_entries = tuple(realm.iterdir())
    root.rename(tmp_path / "runtime-original")
    root.mkdir()

    with pytest.raises(LockedRegularFileError) as caught:
        if operation == "provision":
            LockedRegularFileSetV1.provision_authority(
                platform_lock_root=str(authority_root),
                storage_identity_token="storage-token",
                runtime_root=str(root),
            )
        elif operation == "enroll":
            LockedRegularFileSetV1.enroll_stream_lock_keys(
                platform_lock_root=str(authority_root),
                storage_identity_token="storage-token",
                runtime_root=str(root),
                logical_paths=("runtime/events/target.jsonl",),
            )
        else:
            LockedRegularFileSetV1.acquire(
                runtime_root=str(root),
                storage_identity_token="storage-token",
                logical_paths=(),
                platform_lock_root=str(authority_root),
            )

    assert caught.value.code == expected_code
    assert tuple(realm.iterdir()) == original_realm_entries


@pytest.mark.parametrize(
    ("drift", "cause_code"),
    (
        ("root", "stream_identity_drift"),
        ("anchor", "lock_anchor_invalid"),
        ("realm", "lock_realm_binding_mismatch"),
    ),
)
def test_enrollment_final_drift_after_realm_fsync_returns_no_success_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
    cause_code: str,
) -> None:
    root = tmp_path / "runtime"
    authority_root = root.parent / "authority"
    authority = authority_root / "storage-token"
    LockedRegularFileSetV1.provision_authority(
        platform_lock_root=str(authority_root),
        storage_identity_token="storage-token",
        runtime_root=str(root),
    )
    original_fsync = locked_regular_file.os.fsync
    drifted = False

    def drift_after_realm_fsync(fd: int) -> None:
        nonlocal drifted
        original_fsync(fd)
        if drifted:
            return
        drifted = True
        if drift == "root":
            root.rename(tmp_path / "runtime-original")
            root.mkdir()
        elif drift == "anchor":
            anchor = authority / "anchor.lock"
            anchor.rename(authority / "anchor-original.lock")
            anchor.write_bytes(b"replacement")
        else:
            realm = authority / "realm"
            realm.rename(authority / "realm-original")
            realm.mkdir()

    monkeypatch.setattr(locked_regular_file.os, "fsync", drift_after_realm_fsync)
    with pytest.raises(LockedRegularFileError) as caught:
        LockedRegularFileSetV1.enroll_stream_lock_keys(
            platform_lock_root=str(authority_root),
            storage_identity_token="storage-token",
            runtime_root=str(root),
            logical_paths=("runtime/events/target.jsonl",),
        )

    assert caught.value.code == "post_fsync_authority_reconciliation_required"
    assert caught.value.details["cause_code"] == cause_code
    assert isinstance(caught.value.details["cause_details"], dict)


def test_enrollment_rejects_key_replaced_after_realm_fsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "runtime"
    authority_root = root.parent / "authority"
    authority = authority_root / "storage-token"
    realm = authority / "realm"
    logical_path = "runtime/events/replaced-after-fsync.jsonl"
    lock_key = locked_regular_file._key("storage-token", logical_path)
    LockedRegularFileSetV1.provision_authority(
        platform_lock_root=str(authority_root),
        storage_identity_token="storage-token",
        runtime_root=str(root),
    )
    original_fsync = locked_regular_file.os.fsync
    replaced = False

    def replace_key_after_realm_fsync(fd: int) -> None:
        nonlocal replaced
        original_fsync(fd)
        if replaced or os.fstat(fd).st_ino != realm.stat().st_ino:
            return
        replaced = True
        key_path = realm / lock_key
        key_path.rename(realm / f"{lock_key}.pre-fsync")
        key_path.write_bytes(b"replacement")

    monkeypatch.setattr(locked_regular_file.os, "fsync", replace_key_after_realm_fsync)
    with pytest.raises(LockedRegularFileError) as caught:
        LockedRegularFileSetV1.enroll_stream_lock_keys(
            platform_lock_root=str(authority_root),
            storage_identity_token="storage-token",
            runtime_root=str(root),
            logical_paths=(logical_path,),
        )

    assert replaced
    assert caught.value.code == "post_fsync_authority_reconciliation_required"
    assert caught.value.details["cause_code"] == "stream_lock_invalid"
    cause_details = caught.value.details["cause_details"]
    assert isinstance(cause_details, dict)
    assert cause_details["name"] == lock_key


def test_64_concurrent_thread_enrollments_return_one_created_and_stable_final_proofs(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    authority_root = root.parent / "authority"
    LockedRegularFileSetV1.provision_authority(
        platform_lock_root=str(authority_root),
        storage_identity_token="storage-token",
        runtime_root=str(root),
    )

    start = threading.Barrier(65)

    def enroll_once() -> LockMaintenanceProofV1:
        start.wait(timeout=30)
        return LockedRegularFileSetV1.enroll_stream_lock_keys(
            platform_lock_root=str(authority_root),
            storage_identity_token="storage-token",
            runtime_root=str(root),
            logical_paths=("runtime/events/stress.jsonl",),
        )

    with ThreadPoolExecutor(max_workers=64) as executor:
        futures = [executor.submit(enroll_once) for _ in range(64)]
        start.wait(timeout=30)
        proofs = [future.result(timeout=30) for future in futures]

    assert sum(proof.verdict == "created" for proof in proofs) == 1
    assert sum(proof.verdict == "already_present" for proof in proofs) == 63
    assert all(proof.final_validation for proof in proofs)
    assert len({proof.root_identity for proof in proofs}) == 1
    assert len({proof.anchor_identity for proof in proofs}) == 1
    assert len({proof.realm_identity for proof in proofs}) == 1
    assert len({proof.lock_keys[0].lock_key for proof in proofs}) == 1
    assert len({proof.lock_keys[0].identity for proof in proofs}) == 1
    assert sum(proof.lock_keys[0].verdict == "created" for proof in proofs) == 1


@pytest.mark.skipif(
    os.name == "nt",
    reason="process stress requires POSIX descriptor locking",
)
def test_64_concurrent_process_enrollments_return_one_created_and_stable_final_proofs(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    authority_root = root.parent / "authority"
    LockedRegularFileSetV1.provision_authority(
        platform_lock_root=str(authority_root),
        storage_identity_token="storage-token",
        runtime_root=str(root),
    )
    backend_root = Path(__file__).resolve().parents[4]
    child_cwd = tmp_path / "child-cwd"
    ready_root = tmp_path / "ready"
    start_file = tmp_path / "start"
    child_cwd.mkdir()
    ready_root.mkdir()
    child_env = os.environ.copy()
    inherited_pythonpath = child_env.get("PYTHONPATH", "")
    child_env["PYTHONPATH"] = os.pathsep.join(
        path for path in (str(backend_root), inherited_pythonpath) if path
    )
    child_env["PYTHONUTF8"] = "1"
    child_env["PYTHONIOENCODING"] = "utf-8"

    processes: list[subprocess.Popen[str]] = []
    outputs: list[tuple[int, str, str]] = []
    execution_failure: str | None = None
    try:
        for index in range(64):
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        _ENROLLMENT_SUBPROCESS_PROBE,
                        str(root),
                        str(authority_root),
                        str(ready_root / f"{index:02d}.ready"),
                        str(start_file),
                    ],
                    cwd=child_cwd,
                    env=child_env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="strict",
                )
            )

        ready_deadline = time.monotonic() + 60.0
        while len(tuple(ready_root.iterdir())) != len(processes):
            exited_early = [process.pid for process in processes if process.poll() is not None]
            if exited_early:
                execution_failure = f"subprocesses exited before start barrier: {exited_early}"
                break
            if time.monotonic() >= ready_deadline:
                execution_failure = "subprocess ready barrier timed out"
                break
            time.sleep(0.01)

        if execution_failure is None:
            start_file.write_text("start\n", encoding="utf-8")
            completion_deadline = time.monotonic() + 90.0
            while any(process.poll() is None for process in processes):
                if time.monotonic() >= completion_deadline:
                    execution_failure = "subprocess enrollment completion timed out"
                    break
                time.sleep(0.01)
    finally:
        outputs = _reap_probe_processes(processes)

    diagnostics = _probe_diagnostics(outputs)
    assert execution_failure is None, f"{execution_failure}\n{diagnostics}"
    assert len(outputs) == 64
    assert all(returncode == 0 for returncode, _, _ in outputs), diagnostics
    assert all(not stderr for _, _, stderr in outputs), diagnostics

    results: list[dict[str, object]] = []
    for returncode, stdout, _stderr in outputs:
        assert returncode == 0
        parsed: object = json.loads(stdout)
        assert isinstance(parsed, dict)
        results.append(parsed)

    assert sum(result.get("verdict") == "created" for result in results) == 1
    assert sum(result.get("verdict") == "already_present" for result in results) == 63
    assert sum(result.get("key_verdict") == "created" for result in results) == 1
    assert sum(result.get("key_verdict") == "already_present" for result in results) == 63
    assert all(result.get("final_validation") is True for result in results)
    assert len({result.get("lock_key") for result in results}) == 1
    for field_name in ("root_identity", "anchor_identity", "realm_identity", "key_identity"):
        assert len({_probe_identity(result, field_name) for result in results}) == 1


def test_close_serializes_with_io_and_cannot_close_a_reused_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    target = root / "events" / "target.jsonl"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"seed\n")
    leases = _leases(root, "runtime/events/target.jsonl")
    lease = leases.lease("runtime/events/target.jsonl")
    assert lease.open_existing(writable=True)
    assert lease._file_fd is not None
    held_file_fd = lease._file_fd
    original_close = locked_regular_file.os.close
    file_closed = threading.Event()
    finish_close = threading.Event()
    intercepted = False

    def pause_after_file_close(fd: int) -> None:
        nonlocal intercepted
        if fd == held_file_fd and not intercepted:
            intercepted = True
            original_close(fd)
            file_closed.set()
            assert finish_close.wait(timeout=5)
            return
        original_close(fd)

    monkeypatch.setattr(locked_regular_file.os, "close", pause_after_file_close)
    closer = threading.Thread(target=leases.close)
    closer.start()
    assert file_closed.wait(timeout=5)

    with pytest.raises(LockedRegularFileError) as closing_error:
        lease.read_bytes()
    assert closing_error.value.code == "stream_lease_closing"

    reused_fd = os.open(target, os.O_RDONLY)
    assert reused_fd == held_file_fd
    duplicate_closer = threading.Thread(target=leases.close)
    duplicate_closer.start()
    assert duplicate_closer.is_alive()
    finish_close.set()
    closer.join(timeout=5)
    duplicate_closer.join(timeout=5)
    assert not closer.is_alive()
    assert not duplicate_closer.is_alive()
    assert os.fstat(reused_fd).st_ino == target.stat().st_ino
    leases.close()
    assert os.fstat(reused_fd).st_ino == target.stat().st_ino
    os.close(reused_fd)
