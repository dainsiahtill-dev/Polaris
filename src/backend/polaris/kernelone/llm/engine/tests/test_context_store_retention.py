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

        with patch("os.remove", side_effect=_broken_remove):
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
        original_remove = retention.pin_repository.remove_snapshot_if_unpinned
        raced = False

        def _pin_then_remove(path: str) -> bool:
            nonlocal raced
            if not raced:
                raced = True
                self._persist(repository)
            return original_remove(path)

        monkeypatch.setattr(retention.pin_repository, "remove_snapshot_if_unpinned", _pin_then_remove)
        retention.sweep()
        assert raced is True
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
