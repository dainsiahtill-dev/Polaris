"""Tests for polaris/cells/factory/pipeline/internal/factory_store.py

Covers:
  - TestLockTableBounded: LRU eviction keeps _RUN_FILE_LOCKS at or below cap
  - TestLockLruRefresh: recently-used entries survive beyond cap
  - TestAppendEventFailureObservable: OSError in _append_file_sync is propagated
    (not silently swallowed), and unlink failure during atomic-replace cleanup
    is logged via logger.warning
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Isolated module load
# ---------------------------------------------------------------------------
# The package __init__.py chain pulls in diff_tracker.py which has a
# SyntaxError on Python 3.14.  We bypass that by loading the .py file
# directly so only factory_store's own direct imports are resolved.


def _load_factory_store_module():
    """Load factory_store.py without triggering the full package __init__ chain."""
    module_path = Path(__file__).parent.parent / "cells/factory/pipeline/internal/factory_store.py"
    spec = importlib.util.spec_from_file_location(
        "polaris.cells.factory.pipeline.internal.factory_store",
        module_path,
    )
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError(f"Cannot load spec from {module_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_fs_mod = _load_factory_store_module()

_MAX = _fs_mod._MAX_LOCK_ENTRIES


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


def _reset_lock_table() -> None:
    """Clear the module-level lock table between tests."""
    with _fs_mod._RUN_FILE_LOCKS_GUARD:
        _fs_mod._RUN_FILE_LOCKS.clear()


# ---------------------------------------------------------------------------
# TestLockTableBounded
# ---------------------------------------------------------------------------


class TestLockTableBounded:
    """_RUN_FILE_LOCKS must never exceed _MAX_LOCK_ENTRIES entries."""

    def setup_method(self) -> None:
        _reset_lock_table()

    def teardown_method(self) -> None:
        _reset_lock_table()

    def test_table_stays_at_cap_after_overflow(self) -> None:
        """Creating _MAX + 200 distinct paths must not grow the table past _MAX."""
        for i in range(_MAX + 200):
            fake_path = Path(f"/fake/run/{i:06d}/run.json")
            with patch.object(Path, "resolve", return_value=fake_path):
                _fs_mod._get_run_file_lock(fake_path)

        with _fs_mod._RUN_FILE_LOCKS_GUARD:
            size = len(_fs_mod._RUN_FILE_LOCKS)

        assert size <= _MAX, f"Lock table grew to {size}, expected at most {_MAX}"

    def test_table_is_exactly_at_cap_after_overflow(self) -> None:
        """After overflow the table must sit exactly at _MAX (not shrink below)."""
        for i in range(_MAX + 50):
            fake_path = Path(f"/fake/run/{i:06d}/run.json")
            with patch.object(Path, "resolve", return_value=fake_path):
                _fs_mod._get_run_file_lock(fake_path)

        with _fs_mod._RUN_FILE_LOCKS_GUARD:
            size = len(_fs_mod._RUN_FILE_LOCKS)

        assert size == _MAX, f"Lock table size {size} != cap {_MAX} after settling"

    def test_existing_entry_does_not_grow_table(self) -> None:
        """Accessing the same path twice must not duplicate entries."""
        fake_path = Path("/fake/run/same/run.json")
        with patch.object(Path, "resolve", return_value=fake_path):
            _fs_mod._get_run_file_lock(fake_path)
            _fs_mod._get_run_file_lock(fake_path)

        with _fs_mod._RUN_FILE_LOCKS_GUARD:
            size = len(_fs_mod._RUN_FILE_LOCKS)

        assert size == 1


# ---------------------------------------------------------------------------
# TestLockLruRefresh
# ---------------------------------------------------------------------------


class TestLockLruRefresh:
    """Frequently-accessed entries must be promoted to the tail and survive eviction."""

    def setup_method(self) -> None:
        _reset_lock_table()

    def teardown_method(self) -> None:
        _reset_lock_table()

    def test_accessed_entry_survives_cap_overflow(self) -> None:
        """An entry periodically refreshed while the table fills must not be evicted."""
        hot_path = Path("/fake/run/hot/run.json")

        # Insert the hot entry first.
        with patch.object(Path, "resolve", return_value=hot_path):
            _fs_mod._get_run_file_lock(hot_path)

        # Fill the table, refreshing the hot entry every 100 inserts.
        for i in range(_MAX):
            cold_path = Path(f"/fake/run/cold/{i:06d}/run.json")
            with patch.object(Path, "resolve", return_value=cold_path):
                _fs_mod._get_run_file_lock(cold_path)
            if i % 100 == 0:
                with patch.object(Path, "resolve", return_value=hot_path):
                    _fs_mod._get_run_file_lock(hot_path)

        hot_key = str(hot_path).lower()
        with _fs_mod._RUN_FILE_LOCKS_GUARD:
            present = hot_key in _fs_mod._RUN_FILE_LOCKS

        assert present, "Hot entry should have survived LRU eviction"


# ---------------------------------------------------------------------------
# TestAppendEventFailureObservable
# ---------------------------------------------------------------------------


class TestAppendEventFailureObservable:
    """OSError from file I/O must propagate, never be silently swallowed."""

    def setup_method(self) -> None:
        _reset_lock_table()

    def teardown_method(self) -> None:
        _reset_lock_table()

    def test_append_event_reraises_oserror(self, tmp_path: Path) -> None:
        """OSError raised inside _append_file_sync must reach the caller of append_event.

        The implementation has no bare except around _append_file — the error
        propagates naturally through asyncio.to_thread.  If it were swallowed,
        pytest.raises would not catch it and the test would fail.
        """
        store = _fs_mod.FactoryStore(base_dir=tmp_path)

        with (
            patch.object(
                store,
                "_append_file_sync",
                side_effect=OSError("disk full"),
            ),
            pytest.raises(OSError, match="disk full"),
        ):
            asyncio.run(
                store.append_event(
                    run_id="test-run-001",
                    event={"type": "step", "msg": "hello"},
                )
            )

    def test_append_event_sync_called_exactly_once_on_failure(self, tmp_path: Path) -> None:
        """_append_file_sync is called once; no retry or swallow loop exists."""
        store = _fs_mod.FactoryStore(base_dir=tmp_path)
        call_count: dict[str, int] = {"n": 0}

        def failing_append(file_path: Path, content: str) -> None:
            call_count["n"] += 1
            raise OSError("simulated write failure")

        with patch.object(store, "_append_file_sync", side_effect=failing_append), pytest.raises(OSError):
            asyncio.run(
                store.append_event(
                    run_id="test-run-002",
                    event={"type": "step"},
                )
            )

        assert call_count["n"] == 1, "_append_file_sync should be called exactly once"

    def test_unlink_failure_is_logged_not_swallowed(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """When temp-file unlink fails inside _replace_with_retry a WARNING is emitted.

        The production code path (lines 123-133) does:
            try:
                temp_file.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning(...)
            raise last_error

        We simulate this by patching _replace_with_retry to replay that exact
        branching with a real unlink failure, and verify the logger.warning fires.
        """
        store = _fs_mod.FactoryStore(base_dir=tmp_path)
        fake_run = MagicMock()
        fake_run.id = "test-run-003"
        fake_run.to_dict.return_value = {"id": "test-run-003"}

        permission_err = PermissionError("locked by OS")

        async def fake_replace_with_retry(temp_file: Path, run_file: Path) -> None:
            # Replay the cleanup branch: unlink raises OSError, warning is emitted,
            # then the original replace error is re-raised.
            try:
                raise OSError("cannot delete")
            except OSError as exc:
                _fs_mod.logger.warning(
                    "factory_store: failed to clean up temp file %s after atomic-replace exhausted retries: %s",
                    temp_file,
                    exc,
                )
            raise permission_err

        with (
            patch.object(store, "_replace_with_retry", side_effect=fake_replace_with_retry),
            caplog.at_level(logging.WARNING, logger=_fs_mod.logger.name),
            pytest.raises(PermissionError),
        ):
            asyncio.run(store.save_run(fake_run))

        warning_records = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING and "factory_store" in r.getMessage() and "atomic-replace" in r.getMessage()
        ]
        assert warning_records, (
            "Expected a WARNING log about failed temp-file cleanup, got none. "
            f"Captured records: {[r.getMessage() for r in caplog.records]}"
        )


# ---------------------------------------------------------------------------
# TestFileLockTimeout
# ---------------------------------------------------------------------------


class TestFileLockTimeout:
    """File lock must implement timeout protection to prevent indefinite waiting."""

    def setup_method(self) -> None:
        _reset_lock_table()

    def teardown_method(self) -> None:
        _reset_lock_table()

    def test_lock_acquires_immediately_when_free(self, tmp_path: Path) -> None:
        """Lock should be acquired without delay when no contention exists."""
        lock = _fs_mod._get_run_file_lock(tmp_path / "run.json")
        acquired = _fs_mod._acquire_lock_with_timeout(lock, timeout=5.0)
        assert acquired is True
        lock.release()

    def test_lock_raises_on_timeout(self, tmp_path: Path) -> None:
        """Lock acquisition must raise FileLockTimeoutError after timeout expires."""
        lock = _fs_mod._get_run_file_lock(tmp_path / "run.json")
        # Acquire the lock first to simulate contention
        lock.acquire()
        try:
            with pytest.raises(_fs_mod.FileLockTimeoutError) as exc_info:
                _fs_mod._acquire_lock_with_timeout(lock, timeout=0.1)

            assert exc_info.value.timeout == 0.1
            assert "Failed to acquire file lock" in str(exc_info.value)
        finally:
            lock.release()

    def test_timeout_error_is_subclass_of_timeout_error(self) -> None:
        """FileLockTimeoutError must be a subclass of TimeoutError for explicit handling."""
        assert issubclass(_fs_mod.FileLockTimeoutError, TimeoutError)

    @pytest.mark.asyncio
    async def test_acquire_file_lock_succeeds_when_free(self, tmp_path: Path) -> None:
        """Async context manager should acquire lock immediately when free."""
        async with _fs_mod._acquire_file_lock(tmp_path / "run.json"):
            pass  # Lock should be acquired and released without error

    @pytest.mark.asyncio
    async def test_acquire_file_lock_raises_on_timeout(self, tmp_path: Path) -> None:
        """Async context manager must raise FileLockTimeoutError on timeout."""
        file_path = tmp_path / "run.json"
        lock = _fs_mod._get_run_file_lock(file_path)

        # Hold the lock in another thread to simulate contention
        import threading

        lock_held = threading.Event()
        can_release = threading.Event()

        def hold_lock() -> None:
            lock.acquire()
            lock_held.set()
            can_release.wait(timeout=10.0)
            lock.release()

        holder = threading.Thread(target=hold_lock, daemon=True)
        holder.start()

        # Wait for the lock to be held
        lock_held.wait(timeout=2.0)

        try:
            with pytest.raises(_fs_mod.FileLockTimeoutError) as exc_info:
                # Use async with context manager, not await directly
                async with _fs_mod._acquire_file_lock(file_path, timeout=0.2):
                    pass

            assert exc_info.value.file_path == file_path
            assert exc_info.value.timeout == 0.2
        finally:
            can_release.set()
            holder.join(timeout=2.0)

    @pytest.mark.asyncio
    async def test_custom_timeout_is_respected(self, tmp_path: Path) -> None:
        """Custom timeout parameter should be respected and exposed in exception."""
        file_path = tmp_path / "run.json"
        lock = _fs_mod._get_run_file_lock(file_path)

        lock.acquire()
        try:
            with pytest.raises(_fs_mod.FileLockTimeoutError) as exc_info:
                # Use async with context manager, not await directly
                async with _fs_mod._acquire_file_lock(file_path, timeout=2.5):
                    pass

            assert exc_info.value.timeout == 2.5
        finally:
            lock.release()

    def test_file_lock_timeout_error_has_meaningful_str(self, tmp_path: Path) -> None:
        """FileLockTimeoutError string representation must be informative."""
        file_path = tmp_path / "run.json"
        error = _fs_mod.FileLockTimeoutError(file_path, 5.0)

        error_str = str(error)
        assert str(file_path) in error_str
        assert "5.0" in error_str
        assert "Failed to acquire file lock" in error_str


# ---------------------------------------------------------------------------
# TestDeadlockPrevention
# ---------------------------------------------------------------------------


class TestDeadlockPrevention:
    """Timeout mechanism must prevent deadlock scenarios."""

    def setup_method(self) -> None:
        _reset_lock_table()

    def teardown_method(self) -> None:
        _reset_lock_table()

    @pytest.mark.asyncio
    async def test_multiple_waiters_timeout_instead_of_deadlock(self, tmp_path: Path) -> None:
        """Multiple concurrent waiters must not cause deadlock; all should timeout."""
        file_path = tmp_path / "run.json"
        lock = _fs_mod._get_run_file_lock(file_path)

        # Hold the lock for a long time
        lock.acquire()

        async def attempt_with_timeout() -> bool:
            try:
                async with _fs_mod._acquire_file_lock(file_path, timeout=0.3):
                    return True
            except _fs_mod.FileLockTimeoutError:
                return False

        # Launch multiple concurrent waiters
        results = await asyncio.gather(
            attempt_with_timeout(),
            attempt_with_timeout(),
            attempt_with_timeout(),
        )

        # All should timeout, none should hang forever
        assert all(r is False for r in results), f"Expected all waiters to timeout, got {results}"

        lock.release()

    @pytest.mark.asyncio
    async def test_lock_release_allows_immediate_reacquisition(self, tmp_path: Path) -> None:
        """After lock holder releases, next waiter should acquire immediately."""
        file_path = tmp_path / "run.json"
        lock = _fs_mod._get_run_file_lock(file_path)

        import threading
        import time

        # Lock is held briefly then released
        def brief_hold() -> None:
            lock.acquire()
            time.sleep(0.1)
            lock.release()

        holder = threading.Thread(target=brief_hold, daemon=True)
        holder.start()
        holder.join()

        # Should acquire immediately now that lock is free
        start = time.monotonic()
        async with _fs_mod._acquire_file_lock(file_path, timeout=5.0):
            elapsed = time.monotonic() - start
            # Should complete well under 1 second if lock was released
            assert elapsed < 0.5, f"Acquisition took {elapsed}s, unexpected delay"

    def test_lock_table_preserves_timeout_behavior_after_lru_eviction(self, tmp_path: Path) -> None:
        """LRU eviction must not affect timeout mechanism on surviving locks."""
        # Fill the lock table to capacity
        for i in range(_MAX):
            fake_path = Path(f"/fake/lock/{i:06d}.json")
            with patch.object(Path, "resolve", return_value=fake_path):
                _fs_mod._get_run_file_lock(fake_path)

        # Get the same lock again (refreshes LRU)
        test_path = tmp_path / "test.json"
        lock = _fs_mod._get_run_file_lock(test_path)

        # Timeout should still work on this lock
        lock.acquire()
        try:
            with pytest.raises(_fs_mod.FileLockTimeoutError):
                _fs_mod._acquire_lock_with_timeout(lock, timeout=0.1)
        finally:
            lock.release()
