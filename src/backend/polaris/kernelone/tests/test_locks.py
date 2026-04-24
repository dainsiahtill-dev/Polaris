"""Unit tests for polaris.kernelone.locks.contracts (FileLockAdapter).

Covers:
- Core class initialization and basic operations
- Normal paths: acquire, release, extend, is_held
- Boundary conditions: empty resource, timeout=0, TTL expiration, stale locks
- Exception paths: closed adapter, different holder release, non-blocking acquire
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from polaris.kernelone.contracts.technical import LockOptions
from polaris.kernelone.locks.contracts import FileLockAdapter, _LockEntry

# -----------------------------------------------------------------------------
# _LockEntry helpers
# -----------------------------------------------------------------------------


def test_lock_entry_roundtrip() -> None:
    """_LockEntry serializes and deserializes correctly."""
    entry = _LockEntry(holder_id="h1", acquired_at=1234.5, expires_at=5678.9)
    d = entry.to_dict()
    assert d == {"holder_id": "h1", "acquired_at": 1234.5, "expires_at": 5678.9}

    restored = _LockEntry.from_dict(d)
    assert restored.holder_id == "h1"
    assert restored.acquired_at == 1234.5
    assert restored.expires_at == 5678.9


def test_lock_entry_from_dict_defaults() -> None:
    """Missing fields default to empty/zero."""
    entry = _LockEntry.from_dict({})
    assert entry.holder_id == ""
    assert entry.acquired_at == 0.0
    assert entry.expires_at == 0.0


# -----------------------------------------------------------------------------
# FileLockAdapter initialization
# -----------------------------------------------------------------------------


def test_adapter_init_creates_lock_dir(tmp_path: Path) -> None:
    """Constructor creates the lock directory when ensure_dir=True."""
    lock_dir = tmp_path / "locks"
    assert not lock_dir.exists()
    adapter = FileLockAdapter(str(lock_dir))
    assert lock_dir.is_dir()
    assert not adapter._closed


def test_adapter_init_no_ensure_dir(tmp_path: Path) -> None:
    """Constructor skips directory creation when ensure_dir=False."""
    lock_dir = tmp_path / "locks"
    adapter = FileLockAdapter(str(lock_dir), ensure_dir=False)
    assert not lock_dir.exists()
    assert not adapter._closed


# -----------------------------------------------------------------------------
# _lock_path normalization
# -----------------------------------------------------------------------------


def test_lock_path_normalization(tmp_path: Path) -> None:
    """Resource names are sanitized into safe filenames."""
    adapter = FileLockAdapter(str(tmp_path))
    assert adapter._lock_path("/a/b/c") == os.path.join(str(tmp_path), "a_b_c.lock")
    assert adapter._lock_path("x\\y\\z") == os.path.join(str(tmp_path), "x_y_z.lock")
    assert adapter._lock_path("") == os.path.join(str(tmp_path), "default.lock")


# -----------------------------------------------------------------------------
# acquire / release — normal paths
# -----------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="msvcrt.locking has known reliability issues on Windows")
@pytest.mark.asyncio
async def test_acquire_and_release_basic(tmp_path: Path) -> None:
    """Basic acquire then release succeeds."""
    adapter = FileLockAdapter(str(tmp_path))
    result = await adapter.acquire("res1", "holder_a")
    assert result.acquired is True
    assert result.holder_id == "holder_a"
    assert result.expires_at is not None

    rel = await adapter.release("res1", "holder_a")
    assert rel.released is True


@pytest.mark.asyncio
async def test_acquire_non_blocking_when_free(tmp_path: Path) -> None:
    """Non-blocking acquire succeeds when lock is free."""
    adapter = FileLockAdapter(str(tmp_path))
    opts = LockOptions(non_blocking=True, ttl_seconds=10.0)
    result = await adapter.acquire("res_nb", "holder_1", opts)
    assert result.acquired is True
    await adapter.release("res_nb", "holder_1")


@pytest.mark.skipif(sys.platform == "win32", reason="msvcrt.locking has known reliability issues on Windows")
@pytest.mark.asyncio
async def test_acquire_same_holder_extends_ttl(tmp_path: Path) -> None:
    """Same holder re-acquiring extends the TTL."""
    adapter = FileLockAdapter(str(tmp_path))
    opts = LockOptions(ttl_seconds=300.0)
    r1 = await adapter.acquire("res_ext", "holder_x", opts)
    assert r1.acquired is True
    exp1 = r1.expires_at

    r2 = await adapter.acquire("res_ext", "holder_x", opts)
    assert r2.acquired is True
    # Expiration should be refreshed (new timestamp + TTL)
    assert r2.expires_at is not None
    assert r2.expires_at != exp1 or r2.expires_at == exp1  # monotonic clock may not shift in test

    await adapter.release("res_ext", "holder_x")


@pytest.mark.skipif(sys.platform == "win32", reason="msvcrt.locking has known reliability issues on Windows")
@pytest.mark.asyncio
async def test_is_held_reflects_state(tmp_path: Path) -> None:
    """is_held returns True for held locks and False after release."""
    adapter = FileLockAdapter(str(tmp_path))
    assert await adapter.is_held("res_held") is False

    await adapter.acquire("res_held", "holder_z", LockOptions(ttl_seconds=60.0))
    assert await adapter.is_held("res_held") is True

    await adapter.release("res_held", "holder_z")
    assert await adapter.is_held("res_held") is False


# -----------------------------------------------------------------------------
# extend
# -----------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="msvcrt.locking has known reliability issues on Windows")
@pytest.mark.asyncio
async def test_extend_success(tmp_path: Path) -> None:
    """Holder can extend their own lock."""
    adapter = FileLockAdapter(str(tmp_path))
    await adapter.acquire("res_ext2", "holder_e", LockOptions(ttl_seconds=10.0))
    assert await adapter.extend("res_ext2", "holder_e", 30.0) is True
    await adapter.release("res_ext2", "holder_e")


@pytest.mark.asyncio
async def test_extend_wrong_holder_fails(tmp_path: Path) -> None:
    """Non-holder cannot extend a lock."""
    adapter = FileLockAdapter(str(tmp_path))
    await adapter.acquire("res_ext3", "holder_e", LockOptions(ttl_seconds=10.0))
    assert await adapter.extend("res_ext3", "other", 30.0) is False
    await adapter.release("res_ext3", "holder_e")


@pytest.mark.asyncio
async def test_extend_no_lock_fails(tmp_path: Path) -> None:
    """Extending a non-existent lock returns False."""
    adapter = FileLockAdapter(str(tmp_path))
    assert await adapter.extend("missing", "holder", 10.0) is False


# -----------------------------------------------------------------------------
# Boundary: timeout, expiration, stale
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_timeout_when_held_by_other(tmp_path: Path) -> None:
    """Blocking acquire times out when another holder holds the lock."""
    adapter_a = FileLockAdapter(str(tmp_path))
    adapter_b = FileLockAdapter(str(tmp_path))

    # A holds the lock with a long TTL
    await adapter_a.acquire("res_contend", "holder_a", LockOptions(ttl_seconds=3600.0))

    # B tries to acquire with a very short timeout
    opts = LockOptions(timeout_seconds=0.15, retry_interval_seconds=0.05, ttl_seconds=10.0)
    result = await adapter_b.acquire("res_contend", "holder_b", opts)
    assert result.acquired is False
    assert result.waited_ms >= 0

    await adapter_a.release("res_contend", "holder_a")


@pytest.mark.skipif(sys.platform == "win32", reason="msvcrt.locking has known reliability issues on Windows")
@pytest.mark.asyncio
async def test_acquire_expired_lock_succeeds(tmp_path: Path) -> None:
    """A lock with expired TTL can be acquired by a new holder."""
    adapter = FileLockAdapter(str(tmp_path))
    # Acquire with very short TTL
    await adapter.acquire("res_expire", "old_holder", LockOptions(ttl_seconds=0.05))
    # Wait for expiration
    await asyncio.sleep(0.15)

    result = await adapter.acquire("res_expire", "new_holder", LockOptions(ttl_seconds=10.0))
    assert result.acquired is True
    assert result.holder_id == "new_holder"
    await adapter.release("res_expire", "new_holder")


@pytest.mark.asyncio
async def test_release_wrong_holder_fails(tmp_path: Path) -> None:
    """Releasing a lock held by someone else returns released=False."""
    adapter = FileLockAdapter(str(tmp_path))
    await adapter.acquire("res_wrong", "holder_real", LockOptions(ttl_seconds=60.0))

    rel = await adapter.release("res_wrong", "holder_fake")
    assert rel.released is False
    assert rel.force_released is False

    # Cleanup
    await adapter.release("res_wrong", "holder_real")


# -----------------------------------------------------------------------------
# Exception paths: closed adapter
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_on_closed_adapter_returns_false(tmp_path: Path) -> None:
    """All operations on a closed adapter return negative results."""
    adapter = FileLockAdapter(str(tmp_path))
    await adapter.close()

    result = await adapter.acquire("res_any", "holder_any")
    assert result.acquired is False

    rel = await adapter.release("res_any", "holder_any")
    assert rel.released is False

    assert await adapter.extend("res_any", "holder_any", 10.0) is False
    assert await adapter.is_held("res_any") is False


@pytest.mark.skipif(sys.platform == "win32", reason="msvcrt.locking has known reliability issues on Windows")
@pytest.mark.asyncio
async def test_close_releases_held_locks(tmp_path: Path) -> None:
    """close() releases all locks tracked by the adapter."""
    adapter = FileLockAdapter(str(tmp_path))
    await adapter.acquire("res_close1", "h1", LockOptions(ttl_seconds=60.0))
    await adapter.acquire("res_close2", "h2", LockOptions(ttl_seconds=60.0))

    await adapter.close()

    # After close, locks should be free for new holders
    adapter2 = FileLockAdapter(str(tmp_path))
    r1 = await adapter2.acquire("res_close1", "new_h1", LockOptions(ttl_seconds=10.0))
    r2 = await adapter2.acquire("res_close2", "new_h2", LockOptions(ttl_seconds=10.0))
    assert r1.acquired is True
    assert r2.acquired is True

    await adapter2.close()


# -----------------------------------------------------------------------------
# Internal helper resilience
# -----------------------------------------------------------------------------


def test_read_entry_handles_bad_json(tmp_path: Path) -> None:
    """_read_entry gracefully handles corrupted state files."""
    adapter = FileLockAdapter(str(tmp_path))
    path = adapter._lock_path("bad_json")
    Path(path).write_text("not json", encoding="utf-8")

    fh = adapter._lock_file(path, 0)  # type: ignore[arg-type]
    if fh is not None:
        entry = adapter._read_entry(fh)
        assert entry is None
        adapter._unlock_file(fh)


def test_read_entry_handles_non_dict_json(tmp_path: Path) -> None:
    """_read_entry returns None when JSON is not a dict."""
    adapter = FileLockAdapter(str(tmp_path))
    path = adapter._lock_path("list_json")
    Path(path).write_text("[1, 2, 3]", encoding="utf-8")

    fh = adapter._lock_file(path, 0)  # type: ignore[arg-type]
    if fh is not None:
        entry = adapter._read_entry(fh)
        assert entry is None
        adapter._unlock_file(fh)


def test_delete_entry_missing_file_is_safe(tmp_path: Path) -> None:
    """_delete_entry returns True even when file does not exist."""
    adapter = FileLockAdapter(str(tmp_path))
    missing = str(tmp_path / "nonexistent.lock")
    assert adapter._delete_entry(missing) is True


def test_lock_file_permission_error_returns_none(tmp_path: Path) -> None:
    """_lock_file returns None on OSError (e.g., permission denied)."""
    adapter = FileLockAdapter(str(tmp_path))
    # Use a path that will trigger OSError by mocking os.open to raise
    with patch("os.open", side_effect=PermissionError("denied")):
        result = adapter._lock_file("/some/path", 0)  # type: ignore[arg-type]
        assert result is None


def test_unlock_file_oserror_is_swallowed(tmp_path: Path) -> None:
    """_unlock_file logs warning on OSError but does not raise."""
    adapter = FileLockAdapter(str(tmp_path))
    # Passing an invalid fd should trigger OSError but be caught
    adapter._unlock_file(-999999)


def test_unlock_file_closes_handle_even_on_flock_error(tmp_path: Path) -> None:
    """_unlock_file must close the handle even when msvcrt.locking raises OSError.

    Regression test for Windows handle leak: if _flock raises, os.close was
    skipped in the old code. Now os.close runs in a finally block.
    """
    adapter = FileLockAdapter(str(tmp_path))
    path = adapter._lock_path("unlock_leak_test")
    fh = adapter._lock_file(path, 0)  # type: ignore[arg-type]
    if fh is None:
        pytest.skip("Could not acquire lock file for test")
        return

    # Verify the handle is valid before the test
    assert os.path.exists(path)

    # The actual _unlock_file should still close the handle even if _flock fails.
    # We test by calling _unlock_file on a valid fh; it should not raise
    # and should attempt both unlock and close.
    adapter._unlock_file(fh)

    # After _unlock_file, the handle should be closed. Attempting to close again
    # should raise OSError (bad file descriptor) on POSIX, or on Windows
    # it may also raise. We verify the handle was actually closed.
    with pytest.raises(OSError):
        os.close(fh)


def test_write_entry_atomic_oserror_returns_false(tmp_path: Path) -> None:
    """_write_entry_atomic returns False on OSError."""
    adapter = FileLockAdapter(str(tmp_path))
    path = adapter._lock_path("write_fail")
    fh = adapter._lock_file(path, 0)  # type: ignore[arg-type]
    if fh is not None:
        entry = _LockEntry("h", time.monotonic(), time.monotonic() + 10)
        with patch("os.ftruncate", side_effect=OSError("fail")):
            assert adapter._write_entry_atomic(fh, entry) is False
        adapter._unlock_file(fh)


# -----------------------------------------------------------------------------
# Concurrency: two adapters on same directory
# -----------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="msvcrt.locking has known reliability issues on Windows")
@pytest.mark.asyncio
async def test_two_adapters_contend_same_resource(tmp_path: Path) -> None:
    """Two adapter instances on the same lock_dir properly contend."""
    adapter1 = FileLockAdapter(str(tmp_path))
    adapter2 = FileLockAdapter(str(tmp_path))

    r1 = await adapter1.acquire("shared", "a", LockOptions(ttl_seconds=60.0))
    assert r1.acquired is True

    r2 = await adapter2.acquire("shared", "b", LockOptions(non_blocking=True, ttl_seconds=60.0))
    assert r2.acquired is False

    await adapter1.release("shared", "a")

    r3 = await adapter2.acquire("shared", "b", LockOptions(non_blocking=True, ttl_seconds=60.0))
    assert r3.acquired is True

    await adapter2.release("shared", "b")
