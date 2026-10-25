"""Real CAS/pin effects with synthetic host identities; no native acceptance."""

from __future__ import annotations

import hashlib
import multiprocessing
import os
import time
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.events.final_request_evidence import ContextSnapshotReferenceOwnerV1
from polaris.kernelone.llm.engine.context_store_retention import (
    ContextSnapshotAuditPinError,
    ContextSnapshotAuditPinRepository,
    ContextStoreRetention,
    ContextStoreRetentionConfig,
)


def owner(workspace: Path, reference: str = "synthetic-reference") -> ContextSnapshotReferenceOwnerV1:
    return ContextSnapshotReferenceOwnerV1("synthetic", "host-run", reference, str(workspace.resolve()))


def test_original_bytes_pinned_before_pressure_and_budget_reported(tmp_path: Path) -> None:
    repository = ContextSnapshotAuditPinRepository(workspace=str(tmp_path))
    original = b'{ "synthetic": "' + b"x" * 74000 + b'" }\n'
    ref = repository.persist_snapshot_bytes(original, reference_owner=owner(tmp_path))
    garbage = repository.persist_snapshot_bytes(b"synthetic disposable garbage")
    report = ContextStoreRetention(
        str(tmp_path), ContextStoreRetentionConfig(ttl_seconds=0, max_total_bytes=65536, max_files=0)
    ).sweep()
    assert Path(repository.snapshot_path(ref)).read_bytes() == original
    assert hashlib.sha256(original).hexdigest()[:24] == ref
    assert not Path(repository.snapshot_path(garbage)).exists()
    assert report.inventory_complete and not report.budget_satisfied
    assert report.protected_bytes == report.unreclaimable_bytes == len(original)
    assert repository.query_snapshot_pins(ref) == (), "V1 query must retain its existing type/semantics"
    assert repository.query_reference_pins(ref)[0].owner == owner(tmp_path)
    stats = ContextStoreRetention(str(tmp_path), ContextStoreRetentionConfig(max_total_bytes=65536)).get_stats()
    assert stats["protected_bytes"] == len(original) and not stats["budget_satisfied"]


def test_foreign_owner_cannot_publish_and_corrupt_pin_never_means_unpinned(tmp_path: Path) -> None:
    repository = ContextSnapshotAuditPinRepository(workspace=str(tmp_path))
    foreign = tmp_path / "foreign"
    with pytest.raises((ContextSnapshotAuditPinError, ValueError), match="workspace"):
        repository.persist_snapshot_bytes(b"foreign", reference_owner=owner(foreign))
    ref = repository.persist_snapshot_bytes(b"protected", reference_owner=owner(tmp_path))
    pin = next((Path(repository.contexts_root) / "pins" / ref[:2] / ref).iterdir())
    pin.write_text("{}", encoding="utf-8")
    report = ContextStoreRetention(str(tmp_path), ContextStoreRetentionConfig(ttl_seconds=0)).sweep()
    assert not report.inventory_complete
    assert Path(repository.snapshot_path(ref)).read_bytes() == b"protected"


def test_pin_failure_never_returns_available_and_pin_only_orphan_remains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = ContextSnapshotAuditPinRepository(workspace=str(tmp_path))
    original = repository._write_immutable

    def fail_pin(path: str, content: bytes, *, kind: str) -> None:
        if kind == "pin":
            raise OSError("synthetic disk failure")
        original(path, content, kind=kind)

    monkeypatch.setattr(repository, "_write_immutable", fail_pin)
    with pytest.raises(OSError, match="disk failure"):
        repository.persist_snapshot_bytes(b"before-pin", reference_owner=owner(tmp_path))
    monkeypatch.setattr(repository, "_write_immutable", original)
    ref = repository.persist_snapshot_bytes(b"pin-before-event-orphan", reference_owner=owner(tmp_path))
    # There is deliberately no event. A cold sweeper cannot infer deletion authority.
    report = ContextStoreRetention(str(tmp_path), ContextStoreRetentionConfig(ttl_seconds=0)).sweep()
    assert report.inventory_complete
    assert Path(repository.snapshot_path(ref)).read_bytes() == b"pin-before-event-orphan"


def test_legacy_v1_and_generic_v2_share_bytes_keep_query_types(tmp_path: Path) -> None:
    repository = ContextSnapshotAuditPinRepository(workspace=str(tmp_path))
    legacy = repository.persist_snapshot_and_pin(
        snapshot={"synthetic": "legacy"},
        factory_run_id="legacy-control",
        role="director",
        verification_scope="synthetic",
        request_freeze_id="freeze",
        provider_request_id="provider",
        composite_request_hash="a" * 64,
        snapshot_source="synthetic-control",
    )
    original = Path(repository.snapshot_path(legacy.context_snapshot_ref)).read_bytes()
    ref = repository.persist_snapshot_bytes(original, reference_owner=owner(tmp_path))
    assert ref == legacy.context_snapshot_ref
    assert repository.query_factory_run_pins("legacy-control") == (legacy,)
    assert repository.query_snapshot_pins(ref) == (legacy,)
    assert len(repository.query_reference_pins(ref)) == 1
    assert ContextStoreRetention(str(tmp_path), ContextStoreRetentionConfig(ttl_seconds=0)).sweep().removed_files == 0


def test_symlink_pin_cannot_authorize_or_enable_deletion(tmp_path: Path) -> None:
    repository = ContextSnapshotAuditPinRepository(workspace=str(tmp_path))
    ref = repository.persist_snapshot_bytes(b"protected-symlink", reference_owner=owner(tmp_path))
    path = Path(repository.reference_pin_path(ref, owner(tmp_path)))
    saved = tmp_path / "saved-pin.json"
    saved.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(saved)
    report = ContextStoreRetention(str(tmp_path), ContextStoreRetentionConfig(ttl_seconds=0)).sweep()
    assert not report.inventory_complete
    assert Path(repository.snapshot_path(ref)).read_bytes() == b"protected-symlink"
    with pytest.raises(ContextSnapshotAuditPinError):
        repository.query_reference_pins(ref)


def _writer(workspace: str, entered: Any, release: Any, output: Any) -> None:
    repository = ContextSnapshotAuditPinRepository(workspace=workspace)
    write = repository._write_immutable

    def pause(path: str, content: bytes, *, kind: str) -> None:
        write(path, content, kind=kind)
        if kind == "snapshot":
            entered.set()
            if not release.wait(5):
                raise RuntimeError("synthetic lock fixture timed out")

    repository._write_immutable = pause
    ref = repository.persist_snapshot_bytes(b"locked-original", reference_owner=owner(Path(workspace)))
    output.put(ref)


def _sweeper(workspace: str, started: Any, output: Any) -> None:
    started.set()
    output.put(ContextStoreRetention(workspace, ContextStoreRetentionConfig(ttl_seconds=0)).sweep().removed_files)


def test_real_process_sweeper_waits_for_original_bytes_and_pin(tmp_path: Path) -> None:
    # A separate OS process must not delete the between-write-and-pin snapshot.
    context = multiprocessing.get_context("fork")
    entered, release, started = context.Event(), context.Event(), context.Event()
    writer_output, sweep_output = context.Queue(), context.Queue()
    writer = context.Process(target=_writer, args=(str(tmp_path), entered, release, writer_output))
    sweeper = context.Process(target=_sweeper, args=(str(tmp_path), started, sweep_output))
    writer.start()
    try:
        assert entered.wait(5)
        sweeper.start()
        assert started.wait(5)
        time.sleep(0.05)
        assert sweeper.is_alive(), "sweeper bypassed the shared physical CAS lock"
        release.set()
        writer.join(5)
        sweeper.join(5)
        assert writer.exitcode == sweeper.exitcode == 0
        ref = writer_output.get(timeout=1)
        assert sweep_output.get(timeout=1) == 0
        cold = ContextSnapshotAuditPinRepository(workspace=str(tmp_path), read_only=True)
        assert Path(cold.snapshot_path(ref)).read_bytes() == b"locked-original"
        assert len(cold.query_reference_pins(ref)) == 1
    finally:
        release.set()
        for child in (writer, sweeper):
            if child.is_alive():
                child.terminate()
                child.join(5)


def _crash_writer(workspace: str, stage: str) -> None:
    repository = ContextSnapshotAuditPinRepository(workspace=workspace)
    write = repository._write_immutable

    def crash(path: str, content: bytes, *, kind: str) -> None:
        write(path, content, kind=kind)
        if kind == stage:
            os._exit(71)

    repository._write_immutable = crash
    repository.persist_snapshot_bytes(b"synthetic-crash-boundary", reference_owner=owner(Path(workspace)))


@pytest.mark.parametrize("stage", ["snapshot", "pin"])
def test_process_death_before_pin_or_before_event_preserves_exact_effect(tmp_path: Path, stage: str) -> None:
    child = multiprocessing.get_context("fork").Process(target=_crash_writer, args=(str(tmp_path), stage))
    child.start()
    child.join(5)
    assert child.exitcode == 71
    ref = hashlib.sha256(b"synthetic-crash-boundary").hexdigest()[:24]
    repository = ContextSnapshotAuditPinRepository(workspace=str(tmp_path))
    report = repository.reconcile_references((), validate_snapshot=lambda *_: None)
    assert report["orphan_pins_retained"] == (1 if stage == "pin" else 0)
    swept = ContextStoreRetention(str(tmp_path), ContextStoreRetentionConfig(ttl_seconds=0)).sweep()
    assert swept.inventory_complete
    assert Path(repository.snapshot_path(ref)).exists() is (stage == "pin")
