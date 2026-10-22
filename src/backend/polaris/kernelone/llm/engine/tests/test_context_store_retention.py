"""Tests for the ``runtime/contexts/`` retention policy.

Covers the three retention caps (TTL, max_files, max_total_bytes), the
cheap on-read gate, the atomic counter file, and the integration with
``AIExecutor._store_context_messages_sync`` (which must continue to
return the 24-char hash key after the retention gate is wired in).
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from polaris.kernelone.fs import LockedRegularFileSetV1
from polaris.kernelone.llm.engine.context_store_retention import (
    SWEEP_STATE_FILENAME,
    ContextSnapshotAuditPinError,
    ContextSnapshotAuditPinRepository,
    ContextStoreRetention,
    ContextStoreRetentionConfig,
    SweepReport,
    clear_retention_cache,
    get_retention,
)
from polaris.kernelone.llm.engine.executor import AIExecutor
from polaris.kernelone.storage import StorageLayout
from polaris.kernelone.storage.io_paths import build_cache_root, resolve_storage_roots


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_context_file(layout: StorageLayout, hash_key: str, content: str = "{}") -> Path:
    """Materialise a synthetic context file under ``runtime/contexts/``."""
    shard = hash_key[:2]
    file_path = layout.get_path("runtime", f"contexts/{shard}/{hash_key}")
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return Path(str(file_path))


def _backdate_file(path: Path, seconds_old: float) -> None:
    """Force a file's mtime to ``now - seconds_old``."""
    now = time.time()
    target = now - seconds_old
    os.utime(str(path), (target, target))


def _count_files(root: Path) -> int:
    """Count regular files under ``root`` (one level of sharding)."""
    if not root.is_dir():
        return 0
    total = 0
    for shard in root.iterdir():
        if not shard.is_dir():
            continue
        for child in shard.iterdir():
            if child.is_file() and child.name != SWEEP_STATE_FILENAME:
                total += 1
    return total


def _total_bytes(root: Path) -> int:
    """Sum the bytes of every regular file under ``root``."""
    if not root.is_dir():
        return 0
    total = 0
    for shard in root.iterdir():
        if not shard.is_dir():
            continue
        for child in shard.iterdir():
            if child.is_file() and child.name != SWEEP_STATE_FILENAME:
                total += child.stat().st_size
    return total


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestContextStoreRetentionPolicy:
    """Phase 2 retention policy — TTL / max_files / max_total_bytes."""

    def _build(self, workspace: str, config: ContextStoreRetentionConfig) -> ContextStoreRetention:
        return ContextStoreRetention(
            workspace=workspace,
            config=config,
            runtime_base=build_cache_root("", workspace),
        )

    def test_default_layout_uses_workspace_runtime_root_without_double_projects(self, tmp_path: Path) -> None:
        workspace = str(tmp_path)
        roots = resolve_storage_roots(workspace)

        retention = ContextStoreRetention(
            workspace=workspace,
            config=ContextStoreRetentionConfig(sweep_min_interval_seconds=0),
        )

        assert Path(retention.runtime_root) == Path(roots.runtime_root)
        assert Path(retention.contexts_root) == Path(roots.runtime_root) / "contexts"
        assert "/runtime/projects/" not in str(Path(retention.contexts_root).relative_to(roots.runtime_root))

    def test_ttl_drops_files_older_than_ttl(self, tmp_path: Path) -> None:
        """TTL phase: files older than ``ttl_seconds`` are removed first."""
        workspace = str(tmp_path)
        layout = StorageLayout(workspace=workspace, runtime_base=build_cache_root("", workspace))
        # Three files: one stale by 2*TTL, one stale by 0.5*TTL, one fresh.
        stale_a = _make_context_file(layout, "aaaaaaaa1111111111111111", content="old-a")
        stale_b = _make_context_file(layout, "bbbbbbbb2222222222222222", content="old-b")
        fresh = _make_context_file(layout, "cccccccc3333333333333333", content="fresh")
        _backdate_file(stale_a, seconds_old=2 * 86400)
        _backdate_file(stale_b, seconds_old=2 * 86400)

        config = ContextStoreRetentionConfig(
            ttl_seconds=86400,
            max_total_bytes=10_000_000,
            max_files=10_000,
            sweep_min_interval_seconds=0,
            enabled=True,
        )
        retention = self._build(workspace, config)
        report = retention.sweep(triggers=["ttl"])

        assert isinstance(report, SweepReport)
        assert report.removed_files == 2
        assert "ttl" in report.triggers
        assert not stale_a.exists()
        assert not stale_b.exists()
        assert fresh.exists()

    def test_max_files_cap_enforced_after_ttl(self, tmp_path: Path) -> None:
        """max_files cap is enforced after the TTL pass."""
        workspace = str(tmp_path)
        layout = StorageLayout(workspace=workspace, runtime_base=build_cache_root("", workspace))
        files = []
        for i in range(5):
            hash_key = f"{i:02d}dddddddddddddddddddddd"
            files.append(_make_context_file(layout, hash_key, content="x"))
        # Backdate all to be TTL-clean (within ttl).
        for f in files:
            _backdate_file(f, seconds_old=0)

        config = ContextStoreRetentionConfig(
            ttl_seconds=10**9,  # huge TTL: nothing drops on TTL
            max_total_bytes=10**12,  # huge byte cap: nothing drops on bytes
            max_files=3,
            sweep_min_interval_seconds=0,
        )
        retention = self._build(workspace, config)
        report = retention.sweep(triggers=["max_files"])

        assert report.removed_files == 2
        assert "max_files" in report.triggers
        # The two oldest (lowest mtime) are dropped.
        assert not files[0].exists()
        assert not files[1].exists()
        assert files[2].exists()
        assert files[3].exists()
        assert files[4].exists()

    def test_max_total_bytes_cap_enforced_after_file_count(self, tmp_path: Path) -> None:
        """max_total_bytes cap is enforced after the file-count cap."""
        workspace = str(tmp_path)
        layout = StorageLayout(workspace=workspace, runtime_base=build_cache_root("", workspace))
        # 4 files each 100 bytes = 400 total. Backdate each by a different
        # amount so the oldest-first ordering is deterministic.
        files = []
        for i in range(4):
            hash_key = f"{i:02d}eeeeeeeeeeeeeeeeeeeeee"
            f = _make_context_file(layout, hash_key, content="x" * 100)
            _backdate_file(f, seconds_old=100 - i)
            files.append(f)

        config = ContextStoreRetentionConfig(
            ttl_seconds=10**9,
            max_files=10_000,
            max_total_bytes=150,  # only 1 file fits
            sweep_min_interval_seconds=0,
        )
        retention = self._build(workspace, config)
        report = retention.sweep(triggers=["max_total_bytes"])

        assert "max_total_bytes" in report.triggers
        assert report.removed_files >= 3
        assert report.kept_files == 1
        # The single survivor is the most recent (highest mtime).
        assert files[-1].exists()
        for stale in files[:-1]:
            assert not stale.exists()

    def test_sweep_is_noop_under_all_caps(self, tmp_path: Path) -> None:
        """Sweep is a no-op when every cap is satisfied."""
        workspace = str(tmp_path)
        layout = StorageLayout(workspace=workspace, runtime_base=build_cache_root("", workspace))
        for i in range(3):
            _make_context_file(layout, f"{i:02d}ffffffffffffffffffffff", content="x")

        config = ContextStoreRetentionConfig(
            ttl_seconds=10**9,
            max_total_bytes=10**9,
            max_files=1000,
            sweep_min_interval_seconds=0,
        )
        retention = self._build(workspace, config)
        report = retention.sweep()

        assert report.removed_files == 0
        assert report.scanned_files == 3
        assert _count_files(Path(retention.contexts_root)) == 3

    def test_sweep_is_fail_closed_on_oserror(self, tmp_path: Path) -> None:
        """Sweep never raises even if individual file stat/remove fail."""
        workspace = str(tmp_path)
        layout = StorageLayout(workspace=workspace, runtime_base=build_cache_root("", workspace))
        for i in range(3):
            _make_context_file(layout, f"{i:02d}9999999999999999999999", content="x")

        config = ContextStoreRetentionConfig(
            ttl_seconds=0,  # TTL fires immediately: would try to delete everything
            max_total_bytes=10**9,
            max_files=1000,
            sweep_min_interval_seconds=0,
        )
        retention = self._build(workspace, config)

        def _broken_remove(path: str) -> None:
            raise OSError(13, "Permission denied")

        with patch(
            "polaris.kernelone.llm.engine.context_store_retention.guarded_compare_and_remove_regular_file",
            side_effect=OSError(13, "Permission denied"),
        ):
            # Must not raise.
            report = retention.sweep()

        assert report is not None
        # All three files still on disk because every remove was rejected.
        assert _count_files(Path(retention.contexts_root)) == 3

    def test_sweep_if_needed_skips_within_throttle(self, tmp_path: Path) -> None:
        """sweep_if_needed is a no-op when caps are clean and throttle is active."""
        workspace = str(tmp_path)
        layout = StorageLayout(workspace=workspace, runtime_base=build_cache_root("", workspace))
        for i in range(3):
            _make_context_file(layout, f"{i:02d}7777777777777777777777", content="x")

        config = ContextStoreRetentionConfig(
            ttl_seconds=10**9,
            max_total_bytes=10**9,
            max_files=1000,
            sweep_min_interval_seconds=600,  # 10 min throttle
        )
        retention = self._build(workspace, config)
        # Pre-write the counter file to within the throttle window.
        retention._write_sweep_state(
            last_sweep_at=time.time(),
            last_gate_state={"file_count": 3, "total_bytes": 3, "oldest_mtime": time.time()},
        )

        result = retention.sweep_if_needed()
        assert result is None

    def test_sweep_if_needed_triggers_on_total_bytes(self, tmp_path: Path) -> None:
        """sweep_if_needed fires when total_bytes > max_total_bytes."""
        workspace = str(tmp_path)
        layout = StorageLayout(workspace=workspace, runtime_base=build_cache_root("", workspace))
        for i in range(3):
            _make_context_file(layout, f"{i:02d}6666666666666666666666", content="x" * 100)

        config = ContextStoreRetentionConfig(
            ttl_seconds=10**9,
            max_total_bytes=150,  # exceeded
            max_files=10_000,
            sweep_min_interval_seconds=600,
        )
        retention = self._build(workspace, config)
        # Pre-write the counter to within the throttle window so only
        # the cap, not the throttle, triggers.
        retention._write_sweep_state(
            last_sweep_at=time.time(),
            last_gate_state={"file_count": 0, "total_bytes": 0, "oldest_mtime": time.time()},
        )

        result = retention.sweep_if_needed()
        assert result is not None
        assert "max_total_bytes" in result.triggers
        assert result.removed_files >= 1

    def test_sweep_if_needed_triggers_on_file_count(self, tmp_path: Path) -> None:
        """sweep_if_needed fires when file_count > max_files."""
        workspace = str(tmp_path)
        layout = StorageLayout(workspace=workspace, runtime_base=build_cache_root("", workspace))
        for i in range(5):
            _make_context_file(layout, f"{i:02d}5555555555555555555555", content="x")

        config = ContextStoreRetentionConfig(
            ttl_seconds=10**9,
            max_total_bytes=10**9,
            max_files=3,  # exceeded
            sweep_min_interval_seconds=600,
        )
        retention = self._build(workspace, config)
        retention._write_sweep_state(
            last_sweep_at=time.time(),
            last_gate_state={"file_count": 0, "total_bytes": 0, "oldest_mtime": time.time()},
        )

        result = retention.sweep_if_needed()
        assert result is not None
        assert "max_files" in result.triggers
        assert result.removed_files == 2

    def test_sweep_never_touches_files_outside_contexts_root(self, tmp_path: Path) -> None:
        """Sweep must not delete files outside runtime/contexts/."""
        workspace = str(tmp_path)
        # Plant a sibling file inside the runtime tree (but outside
        # contexts/) and a sibling file inside the workspace root.
        contexts_dir = Path(workspace) / "runtime" / "contexts"
        contexts_dir.mkdir(parents=True, exist_ok=True)
        sibling_in_runtime = contexts_dir.parent / "sibling_in_runtime.json"
        sibling_in_runtime.write_text("sibling-runtime", encoding="utf-8")
        sibling_in_workspace = Path(workspace) / "sibling_in_workspace.json"
        sibling_in_workspace.write_text("sibling-workspace", encoding="utf-8")
        # And a real context file to drive a sweep.
        layout = StorageLayout(workspace=workspace, runtime_base=build_cache_root("", workspace))
        target = _make_context_file(layout, "abcdef1234567890abcdef12", content="x")
        _backdate_file(target, seconds_old=2 * 86400)  # expire by TTL

        config = ContextStoreRetentionConfig(
            ttl_seconds=86400,
            max_total_bytes=0,  # tight
            max_files=0,  # tight
            sweep_min_interval_seconds=0,
        )
        retention = self._build(workspace, config)
        retention.sweep()

        # Sibling files must be preserved.
        assert sibling_in_runtime.exists()
        assert sibling_in_workspace.exists()
        assert not target.exists()

    def test_max_files_preserves_most_recent(self, tmp_path: Path) -> None:
        """After the max_files cap, the most recent files are kept."""
        workspace = str(tmp_path)
        layout = StorageLayout(workspace=workspace, runtime_base=build_cache_root("", workspace))
        files = []
        for i in range(6):
            f = _make_context_file(layout, f"{i:02d}4444444444444444444444", content="x")
            # Ensure monotonically increasing mtimes.
            _backdate_file(f, seconds_old=1000 - i)
            files.append(f)

        config = ContextStoreRetentionConfig(
            ttl_seconds=10**9,
            max_total_bytes=10**9,
            max_files=3,
            sweep_min_interval_seconds=0,
        )
        retention = self._build(workspace, config)
        retention.sweep()

        kept = [f for f in files if f.exists()]
        # Exactly 3 files kept.
        assert len(kept) == 3
        # The 3 newest (highest i) are kept; the 3 oldest (lowest i) are gone.
        assert files[-1].exists()
        assert files[-2].exists()
        assert files[-3].exists()
        assert not files[0].exists()
        assert not files[1].exists()
        assert not files[2].exists()

    def test_sweep_writes_sweep_state_json(self, tmp_path: Path) -> None:
        """The atomic counter file is written on every sweep."""
        workspace = str(tmp_path)
        layout = StorageLayout(workspace=workspace, runtime_base=build_cache_root("", workspace))
        for i in range(2):
            _make_context_file(layout, f"{i:02d}3333333333333333333333", content="x")

        config = ContextStoreRetentionConfig(
            ttl_seconds=10**9,
            max_total_bytes=10**9,
            max_files=1000,
            sweep_min_interval_seconds=0,
        )
        retention = self._build(workspace, config)
        retention.sweep()

        counter_path = Path(retention.sweep_state_path)
        assert counter_path.is_file()
        payload = json.loads(counter_path.read_text(encoding="utf-8"))
        assert "last_sweep_at" in payload
        assert "last_gate_state" in payload
        assert payload["last_gate_state"]["file_count"] == 2

    def test_gate_state_cheap_no_content_read(self, tmp_path: Path) -> None:
        """_gate_state must NOT read file contents — only stat."""
        workspace = str(tmp_path)
        layout = StorageLayout(workspace=workspace, runtime_base=build_cache_root("", workspace))
        path = _make_context_file(layout, "abcdef0000000000000000aa", content="x" * 1024)

        config = ContextStoreRetentionConfig()
        retention = self._build(workspace, config)
        with patch("builtins.open", side_effect=AssertionError("open() called during gate")):
            state = retention._gate_state()
        assert state["file_count"] == 1
        assert state["total_bytes"] == 1024
        assert state["oldest_mtime"] is not None
        assert path.exists()

    def test_sweep_outer_oserror_is_swallowed(self, tmp_path: Path) -> None:
        """Outer OSError during scan never raises to the caller."""
        workspace = str(tmp_path)
        config = ContextStoreRetentionConfig()
        retention = self._build(workspace, config)

        with patch("os.scandir", side_effect=OSError(5, "I/O error")):
            # Must not raise.
            report = retention.sweep()
        assert report.scanned_files == 0
        assert report.removed_files == 0


class TestStoreContextMessagesIntegration:
    """Wiring test: _store_context_messages_sync still returns the hash key
    after the retention gate is invoked."""

    def test_store_returns_hash_with_retention_wired(self, tmp_path: Path) -> None:
        """The store call returns a 24-char hash and the on-disk file exists
        after the retention gate is invoked."""
        clear_retention_cache()
        try:
            messages = [{"role": "user", "content": "hello"}]
            hash_key = AIExecutor._store_context_messages_sync(
                workspace=str(tmp_path),
                messages=messages,
                trace_id="trace-abc",
                call_id="call-123",
            )
            assert isinstance(hash_key, str)
            assert len(hash_key) == 24
            # File exists.
            shard = hash_key[:2]
            runtime_root = Path(resolve_storage_roots(str(tmp_path)).runtime_root)
            file_path = runtime_root / "contexts" / shard / hash_key
            assert file_path.is_file()
            # Note: counter file is only written when a sweep actually runs.
            # Without caps exceeded, sweep_if_needed returns None and
            # no counter is written — that's the cheap-gate contract.
        finally:
            clear_retention_cache()

    def test_n_writes_trigger_at_least_one_sweep_when_caps_tight(self, tmp_path: Path) -> None:
        """When the max_files cap is small, N writes trigger a sweep."""
        clear_retention_cache()
        try:
            workspace = str(tmp_path)
            config = ContextStoreRetentionConfig(
                ttl_seconds=10**9,
                max_total_bytes=10**12,
                max_files=3,
                sweep_min_interval_seconds=0,
            )
            # Pre-seed the retention singleton with a tight cap.
            get_retention(workspace).__init__(  # type: ignore[misc]
                workspace=workspace,
                config=config,
                runtime_base=build_cache_root("", workspace),
            )
            # Now write 5 files. On the 4th write, max_files is exceeded and
            # the gate should sweep, dropping the oldest until under cap.
            for i in range(5):
                AIExecutor._store_context_messages_sync(
                    workspace=workspace,
                    messages=[{"role": "user", "content": f"msg-{i}"}],
                    trace_id=f"trace-{i}",
                    call_id=f"call-{i}",
                )
            contexts_path = Path(get_retention(workspace).contexts_root)
            # After all sweeps, the file count is at or below max_files.
            assert _count_files(contexts_path) <= 3
        finally:
            clear_retention_cache()


class TestConfigDisableBlocklist:
    """The enabled flag uses the disable-as-blocklist convention."""

    def test_blocklist_disables(self) -> None:
        cfg = ContextStoreRetentionConfig(enabled=False)
        assert cfg.enabled is False

    def test_unset_keeps_enabled(self) -> None:
        cfg = ContextStoreRetentionConfig()
        assert cfg.enabled is True


class TestPinnedContextSnapshotRetention:
    def _repository(self, workspace: Path) -> ContextSnapshotAuditPinRepository:
        return ContextSnapshotAuditPinRepository(workspace=str(workspace))

    def _persist(self, repository: ContextSnapshotAuditPinRepository, *, provider_request_id: str = "req-1"):
        return repository.persist_snapshot_and_pin(
            snapshot={"schema_version": "llm.provider_request_snapshot.v2", "payload": "pinned"},
            factory_run_id="factory-run-1",
            role="director",
            verification_scope="factory",
            request_freeze_id="freeze-1",
            provider_request_id=provider_request_id,
            composite_request_hash="c" * 64,
            snapshot_source="roles.kernel.final_provider_attempt",
        )

    def test_producer_and_retention_share_one_registered_lock_identity(self, tmp_path: Path) -> None:
        producer = self._repository(tmp_path)
        retention = ContextStoreRetention(
            workspace=str(tmp_path),
            config=ContextStoreRetentionConfig(sweep_min_interval_seconds=0),
        )

        assert producer.lock_logical_path == retention.pin_repository.lock_logical_path
        assert producer.runtime_root == retention.pin_repository.runtime_root
        assert producer.storage_identity_token == retention.pin_repository.storage_identity_token

    def test_same_process_concurrent_pins_serialize_before_physical_lock(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Concurrent role calls must queue, not self-timeout on the audit flock."""

        active = 0
        active_guard = threading.Lock()
        start = threading.Barrier(2)
        errors: list[BaseException] = []

        class _Lease:
            def __enter__(self) -> _Lease:
                nonlocal active
                with active_guard:
                    active += 1
                    if active > 1:
                        raise AssertionError("same-process audit writers overlapped")
                # Keep the fake physical lease occupied long enough for the
                # other worker to contend when no process mutex exists.
                time.sleep(0.05)
                return self

            def __exit__(self, *_args: object) -> None:
                nonlocal active
                with active_guard:
                    active -= 1

        monkeypatch.setattr(
            LockedRegularFileSetV1,
            "provision_authority",
            classmethod(lambda _cls, **_kwargs: None),
        )
        monkeypatch.setattr(
            LockedRegularFileSetV1,
            "enroll_stream_lock_keys",
            classmethod(lambda _cls, **_kwargs: None),
        )
        monkeypatch.setattr(
            LockedRegularFileSetV1,
            "acquire",
            classmethod(lambda _cls, **_kwargs: _Lease()),
        )

        repositories = (self._repository(tmp_path), self._repository(tmp_path))

        def _worker(index: int) -> None:
            try:
                start.wait()
                self._persist(repositories[index], provider_request_id=f"req-{index}")
            except Exception as exc:  # noqa: BLE001 - preserve thread failure for assertion
                errors.append(exc)

        workers = [threading.Thread(target=_worker, args=(index,)) for index in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)

        assert not errors
        assert all(not worker.is_alive() for worker in workers)
        pins = repositories[0].query_snapshot_pins(self._persist(repositories[0]).context_snapshot_ref)
        assert {pin.provider_request_id for pin in pins} == {"req-0", "req-1"}

    def test_pinned_snapshot_survives_ttl_while_unpinned_snapshot_is_removed(self, tmp_path: Path) -> None:
        repository = self._repository(tmp_path)
        pin = self._persist(repository)
        pinned_path = Path(pin.snapshot_absolute_path)
        unpinned = Path(repository.contexts_root) / "ff" / ("f" * 24)
        unpinned.parent.mkdir(parents=True, exist_ok=True)
        unpinned.write_text("unpinned", encoding="utf-8")
        _backdate_file(pinned_path, seconds_old=3600)
        _backdate_file(unpinned, seconds_old=3600)

        retention = ContextStoreRetention(
            workspace=str(tmp_path),
            config=ContextStoreRetentionConfig(
                ttl_seconds=0,
                max_total_bytes=10**9,
                max_files=1000,
                sweep_min_interval_seconds=0,
            ),
        )
        report = retention.sweep()
        assert pinned_path.is_file()
        assert not unpinned.exists()
        assert report.removed_files == 1
        assert repository.query_snapshot_pins(pin.context_snapshot_ref) == (pin,)

    def test_exact_factory_run_pin_query_recovers_child_role_snapshots(self, tmp_path: Path) -> None:
        """Exact-run audit must not depend on parent journal correlation alone."""

        repository = self._repository(tmp_path)
        pm_pin = repository.persist_snapshot_and_pin(
            snapshot={"schema_version": "llm.provider_request_snapshot.v2", "role": "pm"},
            factory_run_id="factory-exact",
            role="pm",
            verification_scope="factory",
            request_freeze_id="freeze-pm",
            provider_request_id="req-pm",
            composite_request_hash="a" * 64,
            snapshot_source="roles.kernel.final_provider_attempt",
        )
        repository.persist_snapshot_and_pin(
            snapshot={"schema_version": "llm.provider_request_snapshot.v2", "role": "director"},
            factory_run_id="factory-foreign",
            role="director",
            verification_scope="factory",
            request_freeze_id="freeze-foreign",
            provider_request_id="req-foreign",
            composite_request_hash="b" * 64,
            snapshot_source="roles.kernel.final_provider_attempt",
        )

        pins = repository.query_factory_run_pins("factory-exact")

        assert pins == (pm_pin,)

    def test_pinned_snapshot_ref_refuses_non_identical_replacement(self, tmp_path: Path) -> None:
        repository = self._repository(tmp_path)
        pin = self._persist(repository)
        Path(pin.snapshot_absolute_path).write_text("corrupt replacement", encoding="utf-8")
        with pytest.raises(ContextSnapshotAuditPinError, match=r"immutable|content"):
            self._persist(repository)

    def test_cross_workspace_pin_copy_is_mismatch_and_retention_keeps_snapshot(self, tmp_path: Path) -> None:
        source_repository = self._repository(tmp_path / "one")
        target_repository = self._repository(tmp_path / "two")
        pin = self._persist(source_repository)
        target_ref = target_repository.persist_snapshot_bytes(b"canonical target writer fixture")
        assert Path(target_repository.snapshot_path(target_ref)).read_bytes() == b"canonical target writer fixture"
        source_pin_path = Path(source_repository.pin_path(pin.context_snapshot_ref, pin.provider_request_id))
        target_pin_path = Path(target_repository.pin_path(pin.context_snapshot_ref, pin.provider_request_id))
        target_pin_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_pin_path, target_pin_path)
        target_snapshot_path = Path(target_repository.snapshot_path(pin.context_snapshot_ref))
        target_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pin.snapshot_absolute_path, target_snapshot_path)
        _backdate_file(target_snapshot_path, seconds_old=3600)

        with pytest.raises(ContextSnapshotAuditPinError, match=r"workspace|storage"):
            target_repository.query_snapshot_pins(pin.context_snapshot_ref)
        retention = ContextStoreRetention(
            workspace=str(tmp_path / "two"),
            config=ContextStoreRetentionConfig(ttl_seconds=0, sweep_min_interval_seconds=0),
        )
        retention.sweep()
        assert target_snapshot_path.is_file()

    def test_sweep_rechecks_pin_under_delete_lock_to_defeat_toctou(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        repository = self._repository(tmp_path)
        snapshot = {"schema_version": "llm.provider_request_snapshot.v2", "payload": "pinned"}
        content, context_ref = repository.canonical_snapshot(snapshot)
        snapshot_path = Path(repository.snapshot_path(context_ref))
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(content, encoding="utf-8")
        _backdate_file(snapshot_path, seconds_old=3600)
        retention = ContextStoreRetention(
            workspace=str(tmp_path),
            config=ContextStoreRetentionConfig(ttl_seconds=0, sweep_min_interval_seconds=0),
        )
        # Native fault instrumentation: introduce a previously serialized valid pin
        # after initial inventory, without recursively acquiring the same authority.
        pin = self._persist(repository)
        pin_path = Path(repository.pin_path(context_ref, pin.provider_request_id))
        pin_bytes = pin_path.read_bytes()
        shutil.rmtree(pin_path.parent)
        original_remove = retention.pin_repository._remove_snapshot_locked
        raced = False

        def _pin_then_remove(expected, budget):
            nonlocal raced
            if not raced:
                raced = True
                pin_path.parent.mkdir(parents=True)
                pin_path.write_bytes(pin_bytes)
            return original_remove(expected, budget)

        monkeypatch.setattr(retention.pin_repository, "_remove_snapshot_locked", _pin_then_remove)
        report = retention.sweep()
        assert raced is True
        assert report.inventory_complete
        assert report.removed_files == 0
        assert snapshot_path.is_file()

    def test_corrupt_present_pin_state_fails_closed_and_keeps_snapshot(self, tmp_path: Path) -> None:
        repository = self._repository(tmp_path)
        content, context_ref = repository.canonical_snapshot(
            {"schema_version": "llm.provider_request_snapshot.v2", "payload": "corrupt-pin"}
        )
        snapshot_path = Path(repository.snapshot_path(context_ref))
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(content, encoding="utf-8")
        _backdate_file(snapshot_path, seconds_old=3600)
        corrupt_pin = Path(repository.pin_path(context_ref, "req-corrupt"))
        corrupt_pin.parent.mkdir(parents=True, exist_ok=True)
        corrupt_pin.write_text("{not-json", encoding="utf-8")

        retention = ContextStoreRetention(
            workspace=str(tmp_path),
            config=ContextStoreRetentionConfig(ttl_seconds=0, sweep_min_interval_seconds=0),
        )
        retention.sweep()
        assert snapshot_path.is_file()


class TestSettingsEnvWiring:
    """The env vars are mapped into Settings.runtime.context_store_retention."""

    def test_env_blocklist_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KERNELONE_CONTEXT_STORE_RETENTION_ENABLED", "0")
        from polaris.bootstrap.config import ContextStoreRetentionConfig as RuntimeRetentionConfig

        # Direct construction mirrors the field validator.
        cfg = RuntimeRetentionConfig(enabled="0")
        assert cfg.enabled is False

    def test_env_unset_keeps_enabled(self) -> None:
        from polaris.bootstrap.config import ContextStoreRetentionConfig as RuntimeRetentionConfig

        cfg = RuntimeRetentionConfig()
        assert cfg.enabled is True


class TestB3bPhysicalCounterexamples:
    def test_cold_query_does_not_provision(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        writer = ContextSnapshotAuditPinRepository(workspace=str(tmp_path))
        pin = TestPinnedContextSnapshotRetention()._persist(writer)
        calls: list[str] = []
        original = LockedRegularFileSetV1.provision_authority

        def observe(**kwargs):
            calls.append("provision")
            return original(**kwargs)

        monkeypatch.setattr(LockedRegularFileSetV1, "provision_authority", observe)
        from polaris.cells.context.engine.public.contracts import QueryFactoryRunContextSnapshotsV1
        from polaris.cells.context.engine.public.service import query_factory_run_context_snapshots

        result = query_factory_run_context_snapshots(
            QueryFactoryRunContextSnapshotsV1(workspace=str(tmp_path), factory_run_id=pin.factory_run_id)
        )
        assert result.ok
        assert calls == [], "cold readonly query physically provisioned authority"

    @pytest.mark.parametrize("mutation", ["replacement", "in_place"])
    def test_native_replacement_at_delete_survives(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
    ) -> None:
        layout = StorageLayout(workspace=str(tmp_path), runtime_base=build_cache_root("", str(tmp_path)))
        path = _make_context_file(layout, "a" * 24, "old selected bytes")
        retention = ContextStoreRetention(workspace=str(tmp_path), config=ContextStoreRetentionConfig(ttl_seconds=0))
        from polaris.kernelone.fs import guarded_regular_file_snapshot as native

        raced = False

        def replace_before_cas(_parent_fd, _leaf_name):
            nonlocal raced
            raced = True
            if mutation == "replacement":
                replacement = path.with_name("replacement")
                replacement.write_text("new unselected bytes", encoding="utf-8")
                os.replace(replacement, path)
            else:
                path.write_text("new unselected bytes", encoding="utf-8")

        monkeypatch.setattr(native, "_before_guarded_remove_revalidation", replace_before_cas)
        report = retention.sweep()
        assert raced
        assert path.exists(), "guarded CAS must retain native replacement"
        assert path.read_text(encoding="utf-8") == "new unselected bytes"
        assert report.removed_files == 0
        assert report.removed_bytes == 0
        assert not report.inventory_complete
        assert report.error_code == "guarded_remove_expected_mismatch"


class TestB3bInventoryAndRecovery:
    def test_cold_pin_query_and_stats_do_not_provision(self, tmp_path: Path) -> None:
        import subprocess
        import sys

        writer = ContextSnapshotAuditPinRepository(workspace=str(tmp_path))
        pin = TestPinnedContextSnapshotRetention()._persist(writer)
        script = r"""
import json,os,sys
writes=[]
def audit(event,args):
    if event == 'open' and isinstance(args[0],str) and isinstance(args[2],int) and args[2] & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC):
        writes.append(args[0])
    if event in {'os.mkdir','os.rename','os.remove'}:
        writes.append(str(args[0]))
sys.addaudithook(audit)
from polaris.kernelone.fs import KernelFileSystem,get_default_adapter,LockedRegularFileSetV1
from polaris.kernelone.llm.engine.context_store_retention import ContextSnapshotAuditPinRepository,ContextStoreRetention
from polaris.cells.context.engine.public import QueryFactoryRunContextSnapshotsV1,query_factory_run_context_snapshots
def denied(**kwargs):raise AssertionError('readonly provisioning attempted')
LockedRegularFileSetV1.provision_authority=denied
LockedRegularFileSetV1.enroll_stream_lock_keys=denied
from polaris.kernelone.llm.engine import context_store_retention as consumer
from polaris.kernelone.fs import guarded_regular_file_snapshot as native
def forbidden_sync(*args,**kwargs):raise AssertionError('readonly query attempted durability sync')
consumer.guarded_confirm_regular_file_durability=forbidden_sync
native.guarded_confirm_regular_file_durability=forbidden_sync
workspace=sys.argv[1]
repository=ContextSnapshotAuditPinRepository(workspace=workspace,read_only=True)
pins=repository.query_factory_run_pins('factory-run-1')
assert len(pins)==1
assert repository.query_snapshot_pins('f'*24)==()
stats=ContextStoreRetention(workspace=workspace,read_only=True).get_stats()
missing=query_factory_run_context_snapshots(QueryFactoryRunContextSnapshotsV1(workspace=workspace+'/absent',factory_run_id='none'))
assert missing.status=='unavailable'
print(json.dumps({'pin':pins[0].to_record(),'count':stats['file_count'],'writes':writes},ensure_ascii=False))
"""
        env = dict(os.environ)
        env.pop("PYTHONDONTWRITEBYTECODE", None)
        env["PYTHONPYCACHEPREFIX"] = str(tmp_path / "cold-bytecode")
        child = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path)],
            env=env,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=30,
            check=True,
        )
        result = json.loads(child.stdout)
        assert result["pin"] == pin.to_record()
        assert result["count"] == 1
        assert result["writes"]
        assert all(str(tmp_path / "cold-bytecode") in path for path in result["writes"]), result["writes"]
        assert not (tmp_path / "absent").exists()

    def test_readonly_mutation_denied_before_any_effect(self, tmp_path: Path) -> None:
        repository = ContextSnapshotAuditPinRepository(workspace=str(tmp_path), read_only=True)
        before = list(tmp_path.rglob("*"))
        with pytest.raises(ContextSnapshotAuditPinError, match="readonly"):
            repository.persist_snapshot_bytes(b"original")
        with pytest.raises(ContextSnapshotAuditPinError, match="readonly"):
            TestPinnedContextSnapshotRetention()._persist(repository)
        with pytest.raises(ContextSnapshotAuditPinError, match="readonly"):
            repository.remove_snapshot_if_unpinned(repository.snapshot_path("a" * 24))
        report = ContextStoreRetention(workspace=str(tmp_path), read_only=True).sweep()
        assert not report.inventory_complete
        assert list(tmp_path.rglob("*")) == before

    def test_inventory_requires_eof_and_later_valid_progress(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dataclasses import replace

        from polaris.kernelone.llm.engine import context_store_retention as module

        repository = ContextSnapshotAuditPinRepository(workspace=str(tmp_path))
        repository.persist_snapshot_bytes(b"original")
        retention = ContextStoreRetention(workspace=str(tmp_path), config=ContextStoreRetentionConfig(ttl_seconds=0))
        original = retention.pin_repository._fs.workspace_list_directory
        with monkeypatch.context() as failure:
            failure.setattr(
                retention.pin_repository._fs,
                "workspace_list_directory",
                lambda *args, **kwargs: replace(original(*args, **kwargs), complete=False),
            )
            failure.setattr(module, "_INVENTORY_ENTRY_BUDGET", 20)
            report = retention.sweep()
            assert not report.inventory_complete
            assert report.error_code == "context_inventory_budget_exhausted"
            assert report.removed_files == 0
            assert not Path(retention.sweep_state_path).exists()
        report = retention.sweep()
        assert report.inventory_complete
        assert report.removed_files == 1

    def test_inventory_above_initial_batch_and_budget_exhaustion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from polaris.kernelone.llm.engine import context_store_retention as module

        layout = StorageLayout(workspace=str(tmp_path), runtime_base=build_cache_root("", str(tmp_path)))
        for index in range(300):
            _make_context_file(layout, f"aa{index:022x}", "世")
        writer = ContextStoreRetention(workspace=str(tmp_path))
        writer.sweep()  # enroll same authority without deleting fresh files
        readonly = ContextStoreRetention(workspace=str(tmp_path), read_only=True)
        stats = readonly.get_stats()
        assert stats["file_count"] == 300
        assert stats["total_bytes"] == 900
        with monkeypatch.context() as failure:
            failure.setattr(module, "_INVENTORY_ENTRY_BUDGET", 260)
            from polaris.kernelone.llm.engine.context_store_retention import ContextStoreInventoryError

            with pytest.raises(ContextStoreInventoryError, match="budget"):
                readonly.get_stats()
        assert readonly.get_stats()["file_count"] == 300

    @pytest.mark.parametrize("fault", ["symlink", "hardlink", "failed_stat", "missing_root", "metadata_read_drift"])
    def test_inventory_unsafe_or_unavailable_never_publishes_throttle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
    ) -> None:
        from polaris.kernelone.fs import GuardedMetadataError

        layout = StorageLayout(workspace=str(tmp_path), runtime_base=build_cache_root("", str(tmp_path)))
        path = _make_context_file(layout, "a" * 24, "original")
        retention = ContextStoreRetention(workspace=str(tmp_path), config=ContextStoreRetentionConfig(ttl_seconds=0))
        if fault == "symlink":
            path.with_name("alias").symlink_to(path)
        elif fault == "hardlink":
            os.link(path, path.with_name("alias"))
        elif fault == "missing_root":
            shutil.rmtree(retention.contexts_root)
        elif fault == "failed_stat":
            monkeypatch.setattr(
                retention.pin_repository._fs,
                "workspace_list_directory",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    GuardedMetadataError("denied", code="guarded_metadata_io_error")
                ),
            )
        else:
            original = retention.pin_repository._read_snapshot

            def changed(candidate, expected=None):
                if str(candidate) == str(path):
                    path.write_text("changed in place", encoding="utf-8")
                return original(candidate, expected)

            monkeypatch.setattr(retention.pin_repository, "_read_snapshot", changed)
        report = retention.sweep()
        assert not report.inventory_complete
        assert report.error_code
        assert report.removed_files == 0
        assert not Path(retention.sweep_state_path).exists()
        if fault != "missing_root":
            assert path.exists()
        gate = retention.on_read_gate()
        assert gate["inventory_complete"] is False
        assert "file_count" not in gate

    @pytest.mark.parametrize("fault", ["empty", "corrupt", "unreadable", "incomplete", "symlink", "hardlink"])
    def test_pin_unavailable_is_never_empty_pins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
    ) -> None:
        from dataclasses import replace

        from polaris.kernelone.fs import GuardedMetadataError
        from polaris.kernelone.llm.engine import context_store_retention as module

        repository = ContextSnapshotAuditPinRepository(workspace=str(tmp_path))
        pin = TestPinnedContextSnapshotRetention()._persist(repository)
        pin_path = Path(repository.pin_path(pin.context_snapshot_ref, pin.provider_request_id))
        if fault == "empty":
            pin_path.unlink()
        elif fault == "corrupt":
            pin_path.write_text("{broken", encoding="utf-8")
        elif fault == "symlink":
            target = pin_path.with_name("target")
            pin_path.rename(target)
            pin_path.symlink_to(target)
        elif fault == "hardlink":
            os.link(pin_path, pin_path.with_name("other.json"))
        elif fault == "unreadable":
            monkeypatch.setattr(
                repository._fs,
                "workspace_list_directory",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    GuardedMetadataError("denied", code="guarded_metadata_io_error")
                ),
            )
        else:
            original = repository._fs.workspace_list_directory
            monkeypatch.setattr(
                repository._fs,
                "workspace_list_directory",
                lambda *args, **kwargs: replace(original(*args, **kwargs), complete=False),
            )
            monkeypatch.setattr(module, "_INVENTORY_ENTRY_BUDGET", 12)
        with pytest.raises((ContextSnapshotAuditPinError, OSError)):
            repository.query_snapshot_pins(pin.context_snapshot_ref)
        assert Path(pin.snapshot_absolute_path).exists()

    def test_single_authority_serializes_ordinary_writer_pin_and_sweep(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from contextlib import contextmanager

        repository = ContextSnapshotAuditPinRepository(workspace=str(tmp_path))
        repository.persist_snapshot_bytes(b"sweep initial")
        retention = ContextStoreRetention(workspace=str(tmp_path), config=ContextStoreRetentionConfig(ttl_seconds=0))
        assert retention.pin_repository.storage_identity_token == repository.storage_identity_token
        assert retention.pin_repository._platform_lock_root == repository._platform_lock_root
        assert retention.pin_repository.lock_logical_path == repository.lock_logical_path
        entered = threading.Event()
        release = threading.Event()
        attempted = {"ordinary": threading.Event(), "pin": threading.Event()}
        original_inventory = retention._inventory
        first = True

        def pause_inventory(*args, **kwargs):
            nonlocal first
            result = original_inventory(*args, **kwargs)
            if first:
                first = False
                entered.set()
                assert release.wait(5)
            return result

        monkeypatch.setattr(retention, "_inventory", pause_inventory)
        original_lock = ContextSnapshotAuditPinRepository._exclusive_lock

        @contextmanager
        def observed_lock(owner, **kwargs):
            name = threading.current_thread().name
            if name in attempted:
                attempted[name].set()
            with original_lock(owner, **kwargs):
                yield

        monkeypatch.setattr(ContextSnapshotAuditPinRepository, "_exclusive_lock", observed_lock)
        results: dict[str, object] = {}
        errors: list[Exception] = []

        def work(name, function):
            try:
                results[name] = function()
            except Exception as exc:  # noqa: BLE001 - preserve thread failure for assertion
                errors.append(exc)

        sweep = threading.Thread(name="sweep", target=work, args=("sweep", retention.sweep))
        ordinary = threading.Thread(
            name="ordinary",
            target=work,
            args=("ordinary", lambda: repository.persist_snapshot_bytes("中文原字节".encode())),
        )
        pin_thread = threading.Thread(
            name="pin", target=work, args=("pin", lambda: TestPinnedContextSnapshotRetention()._persist(repository))
        )
        sweep.start()
        assert entered.wait(5)
        try:
            ordinary.start()
            pin_thread.start()
            assert all(event.wait(5) for event in attempted.values())
            assert "ordinary" not in results and "pin" not in results
        finally:
            release.set()
            for thread in (sweep, ordinary, pin_thread):
                thread.join(10)
        assert all(not thread.is_alive() for thread in (sweep, ordinary, pin_thread))
        assert not errors
        sweep_result = results["sweep"]
        assert isinstance(sweep_result, SweepReport)
        assert sweep_result.inventory_complete
        assert Path(repository.snapshot_path(results["ordinary"])).read_bytes() == "中文原字节".encode()
        recovered = ContextSnapshotAuditPinRepository(workspace=str(tmp_path), read_only=True).query_factory_run_pins(
            "factory-run-1"
        )
        assert recovered == (results["pin"],)

    def test_partial_effect_preserved_and_cold_counter_recovery(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from polaris.kernelone.llm.engine.context_store_retention import ContextStoreInventoryError

        repository = ContextSnapshotAuditPinRepository(workspace=str(tmp_path))
        ref = repository.persist_snapshot_bytes("删除精确字节".encode())
        retention = ContextStoreRetention(workspace=str(tmp_path), config=ContextStoreRetentionConfig(ttl_seconds=0))
        original = retention._inventory
        calls = 0

        def final_failure(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ContextStoreInventoryError("synthetic final inventory failure")
            return original(*args, **kwargs)

        monkeypatch.setattr(retention, "_inventory", final_failure)
        report = retention.sweep()
        assert not report.inventory_complete
        assert report.removed_files == 1
        assert report.removed_bytes == len("删除精确字节".encode())
        assert not Path(repository.snapshot_path(ref)).exists()
        assert not Path(retention.sweep_state_path).exists()
        monkeypatch.setattr(retention, "_inventory", original)
        recovered = retention.sweep()
        assert recovered.inventory_complete and recovered.removed_files == 0
        cold = ContextStoreRetention(workspace=str(tmp_path), read_only=True)
        assert cold._read_sweep_state()["last_sweep_at"] > 0
        assert cold.get_stats()["file_count"] == 0

    def test_canonical_writer_and_explicit_readonly_history(self, tmp_path: Path) -> None:
        from polaris.kernelone.fs import guarded_compare_and_create_regular_file

        repository = ContextSnapshotAuditPinRepository(workspace=str(tmp_path))
        history_root = tmp_path / "history-runtime"
        before = list(tmp_path.rglob("*"))
        with pytest.raises(ContextSnapshotAuditPinError, match="canonical"):
            ContextSnapshotAuditPinRepository(workspace=str(tmp_path), runtime_root=str(history_root))
        assert list(tmp_path.rglob("*")) == before
        assert not history_root.exists()
        # Explicit native fixture: historical bytes and original enrolled authority.
        history_root.mkdir()
        historical = ContextSnapshotAuditPinRepository(
            workspace=str(tmp_path), runtime_root=str(history_root), read_only=True
        )
        LockedRegularFileSetV1.provision_authority(
            platform_lock_root=historical._platform_lock_root,
            storage_identity_token=historical.storage_identity_token,
            runtime_root=str(history_root),
        )
        LockedRegularFileSetV1.enroll_stream_lock_keys(
            platform_lock_root=historical._platform_lock_root,
            storage_identity_token=historical.storage_identity_token,
            runtime_root=str(history_root),
            logical_paths=(historical.lock_logical_path,),
        )
        content = b"historical exact bytes"
        import hashlib

        ref = hashlib.sha256(content).hexdigest()[:24]
        guarded_compare_and_create_regular_file(
            history_root, "contexts/" + ref[:2] + "/" + ref, content, max_bytes=len(content)
        )
        assert historical.query_snapshot_pins(ref) == ()
        # Recover an actual historical pin using only its original authority.
        import hashlib

        from polaris.kernelone.events.final_request_evidence import ContextSnapshotAuditPinV1

        pin = ContextSnapshotAuditPinV1.create(
            workspace_abs=historical.workspace_abs,
            runtime_root=historical.runtime_root,
            snapshot_logical_path=f"runtime/contexts/{ref[:2]}/{ref}",
            snapshot_absolute_path=historical.snapshot_path(ref),
            snapshot_source="synthetic.native.history.fixture",
            factory_run_id="history-exact",
            role="director",
            verification_scope="factory",
            request_freeze_id="history-freeze",
            provider_request_id="history-provider",
            context_snapshot_ref=ref,
            storage_identity_token=historical.storage_identity_token,
            snapshot_content_hash=hashlib.sha256(content).hexdigest(),
            composite_request_hash="a" * 64,
        )
        pin_bytes = json.dumps(pin.to_record(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        guarded_compare_and_create_regular_file(
            history_root, f"contexts/pins/{ref[:2]}/{ref}/history-provider.json", pin_bytes, max_bytes=len(pin_bytes)
        )
        assert historical.query_factory_run_pins("history-exact") == (pin,)
        assert historical.query_snapshot_pins(ref) == (pin,)
        assert historical._read_bytes(historical.snapshot_path(ref)) == content
        assert historical.storage_identity_token != repository.storage_identity_token
        with pytest.raises(ContextSnapshotAuditPinError, match="readonly"):
            historical.persist_snapshot_bytes(content)


def test_writer_capable_observation_methods_do_not_enroll(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = ContextSnapshotAuditPinRepository(workspace=str(tmp_path))
    pin = TestPinnedContextSnapshotRetention()._persist(repository)
    retention = ContextStoreRetention(workspace=str(tmp_path))

    def denied(**kwargs) -> None:
        raise AssertionError("observation attempted enrollment")

    monkeypatch.setattr(LockedRegularFileSetV1, "provision_authority", denied)
    monkeypatch.setattr(LockedRegularFileSetV1, "enroll_stream_lock_keys", denied)
    assert repository.query_snapshot_pins(pin.context_snapshot_ref) == (pin,)
    assert repository.query_factory_run_pins(pin.factory_run_id) == (pin,)
    assert retention.get_stats()["file_count"] == 1


@pytest.mark.parametrize("kind", ["snapshot", "pin"])
def test_existing_immutable_retry_requires_actual_sync_after_postrename_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    import errno
    import hashlib
    import stat

    from polaris.kernelone.fs import guarded_regular_file_snapshot as native

    repository = ContextSnapshotAuditPinRepository(workspace=str(tmp_path))
    if kind == "snapshot":
        content = "持久化不能由可读性代替".encode()
        ref = hashlib.sha256(content).hexdigest()[:24]
        target = Path(repository.snapshot_path(ref))
    else:
        snapshot = {"schema_version": "llm.provider_request_snapshot.v2", "payload": "pinned"}
        _content, ref = repository.canonical_snapshot(snapshot)
        target = Path(repository.pin_path(ref, "req-1"))

    def publish():
        if kind == "snapshot":
            return repository.persist_snapshot_bytes(content)
        return TestPinnedContextSnapshotRetention()._persist(repository)

    original_fsync = native.os.fsync
    attempts = 0
    active = True

    def fail_target_parent(fd: int) -> None:
        nonlocal attempts
        if stat.S_ISDIR(os.fstat(fd).st_mode) and os.readlink(f"/proc/self/fd/{fd}") == str(target.parent):
            attempts += 1
            if active:
                raise OSError(errno.EIO, "persistent actual parent fsync failure")
        original_fsync(fd)

    monkeypatch.setattr(native.os, "fsync", fail_target_parent)
    with pytest.raises((ContextSnapshotAuditPinError, OSError)):
        publish()
    assert attempts == 1
    assert target.is_file(), "actual rename must have committed before parent fsync failure"
    before = target.stat()
    exact_bytes = target.read_bytes()
    with pytest.raises(OSError, match="persistent actual parent fsync failure") as retry_error:
        publish()
    assert retry_error.value.errno == errno.EIO
    assert attempts == 2, "retry must actually sync existing committed target"
    assert target.read_bytes() == exact_bytes
    assert target.stat().st_ino == before.st_ino
    active = False
    result = publish()
    assert attempts == 3
    assert target.read_bytes() == exact_bytes
    assert target.stat().st_ino == before.st_ino
    if kind == "snapshot":
        assert result == ref
    else:
        assert result.context_snapshot_ref == ref
        assert repository.query_snapshot_pins(ref) == (result,)
