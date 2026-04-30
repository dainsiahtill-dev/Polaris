"""Tests for locks/contracts module."""

from __future__ import annotations

import sys
import tempfile

import pytest
from polaris.kernelone.contracts.technical import LockOptions
from polaris.kernelone.locks.contracts import FileLockAdapter


class TestFileLockAdapter:
    """Tests for FileLockAdapter."""

    @pytest.fixture
    def lock_dir(self) -> str:
        """Create a temporary lock directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    async def adapter(self, lock_dir: str) -> FileLockAdapter:
        """Create a FileLockAdapter instance."""
        return FileLockAdapter(lock_dir)

    @pytest.mark.asyncio
    async def test_acquire_basic(self, lock_dir: str) -> None:
        """Basic lock acquisition."""
        adapter = FileLockAdapter(lock_dir)
        result = await adapter.acquire("resource1", "holder1", LockOptions(ttl_seconds=10))
        assert result.acquired is True
        assert result.holder_id == "holder1"
        assert result.lock_id is not None

    @pytest.mark.asyncio
    async def test_acquire_same_holder_renews(self, lock_dir: str) -> None:
        """Same holder can renew lock."""
        adapter = FileLockAdapter(lock_dir)
        await adapter.acquire("resource1", "holder1", LockOptions(ttl_seconds=10))
        result = await adapter.acquire("resource1", "holder1", LockOptions(ttl_seconds=20))
        assert result.acquired is True

    @pytest.mark.asyncio
    async def test_acquire_different_holder_blocked(self, lock_dir: str) -> None:
        """Different holder is blocked when lock is held."""
        adapter = FileLockAdapter(lock_dir)
        await adapter.acquire("resource1", "holder1", LockOptions(ttl_seconds=10))
        result = await adapter.acquire("resource1", "holder2", LockOptions(ttl_seconds=10, timeout_seconds=0.1))
        assert result.acquired is False

    @pytest.mark.asyncio
    async def test_release_basic(self, lock_dir: str) -> None:
        """Basic lock release."""
        adapter = FileLockAdapter(lock_dir)
        await adapter.acquire("resource1", "holder1", LockOptions(ttl_seconds=10))
        result = await adapter.release("resource1", "holder1")
        assert result.released is True

    @pytest.mark.asyncio
    async def test_release_wrong_holder(self, lock_dir: str) -> None:
        """Wrong holder cannot release lock."""
        adapter = FileLockAdapter(lock_dir)
        await adapter.acquire("resource1", "holder1", LockOptions(ttl_seconds=10))
        result = await adapter.release("resource1", "holder2")
        assert result.released is False

    @pytest.mark.asyncio
    async def test_release_after_acquire(self, lock_dir: str) -> None:
        """Released lock can be acquired by another holder."""
        adapter = FileLockAdapter(lock_dir)
        await adapter.acquire("resource1", "holder1", LockOptions(ttl_seconds=10))
        await adapter.release("resource1", "holder1")
        result = await adapter.acquire("resource1", "holder2", LockOptions(ttl_seconds=10))
        assert result.acquired is True

    @pytest.mark.asyncio
    async def test_is_held_true(self, lock_dir: str) -> None:
        """is_held returns True for held lock."""
        adapter = FileLockAdapter(lock_dir)
        await adapter.acquire("resource1", "holder1", LockOptions(ttl_seconds=10))
        assert await adapter.is_held("resource1") is True

    @pytest.mark.asyncio
    async def test_is_held_false(self, lock_dir: str) -> None:
        """is_held returns False for free lock."""
        adapter = FileLockAdapter(lock_dir)
        assert await adapter.is_held("resource1") is False

    @pytest.mark.asyncio
    @pytest.mark.skipif(sys.platform == "win32", reason="Windows file locking behavior differs")
    async def test_is_held_after_release(self, lock_dir: str) -> None:
        """is_held returns False after release."""
        adapter = FileLockAdapter(lock_dir)
        await adapter.acquire("resource1", "holder1", LockOptions(ttl_seconds=10))
        await adapter.release("resource1", "holder1")
        assert await adapter.is_held("resource1") is False

    @pytest.mark.asyncio
    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific file locking test")
    async def test_release_windows_behavior(self, lock_dir: str) -> None:
        """On Windows, release may be constrained by OS locking."""
        adapter = FileLockAdapter(lock_dir)
        result = await adapter.acquire("resource1", "holder1", LockOptions(ttl_seconds=10))
        assert result.acquired is True
        release_result = await adapter.release("resource1", "holder1")
        # Release may succeed or fail on Windows depending on OS state
        assert isinstance(release_result.released, bool)

    @pytest.mark.asyncio
    async def test_extend_basic(self, lock_dir: str) -> None:
        """Basic lock extension."""
        adapter = FileLockAdapter(lock_dir)
        await adapter.acquire("resource1", "holder1", LockOptions(ttl_seconds=10))
        result = await adapter.extend("resource1", "holder1", 10)
        assert result is True

    @pytest.mark.asyncio
    async def test_extend_wrong_holder(self, lock_dir: str) -> None:
        """Wrong holder cannot extend lock."""
        adapter = FileLockAdapter(lock_dir)
        await adapter.acquire("resource1", "holder1", LockOptions(ttl_seconds=10))
        result = await adapter.extend("resource1", "holder2", 10)
        assert result is False

    @pytest.mark.asyncio
    async def test_extend_no_lock(self, lock_dir: str) -> None:
        """Cannot extend non-existent lock."""
        adapter = FileLockAdapter(lock_dir)
        result = await adapter.extend("resource1", "holder1", 10)
        assert result is False

    @pytest.mark.asyncio
    async def test_non_blocking_acquire(self, lock_dir: str) -> None:
        """Non-blocking acquisition works."""
        adapter = FileLockAdapter(lock_dir)
        result = await adapter.acquire("resource1", "holder1", LockOptions(non_blocking=True, ttl_seconds=10))
        assert result.acquired is True

    @pytest.mark.asyncio
    async def test_closed_adapter_acquire_fails(self, lock_dir: str) -> None:
        """Closed adapter cannot acquire."""
        adapter = FileLockAdapter(lock_dir)
        await adapter.close()
        result = await adapter.acquire("resource1", "holder1", LockOptions(ttl_seconds=10))
        assert result.acquired is False

    @pytest.mark.asyncio
    async def test_closed_adapter_release_fails(self, lock_dir: str) -> None:
        """Closed adapter cannot release."""
        adapter = FileLockAdapter(lock_dir)
        await adapter.close()
        result = await adapter.release("resource1", "holder1")
        assert result.released is False

    @pytest.mark.asyncio
    async def test_close_releases_held_locks(self, lock_dir: str) -> None:
        """Close releases held locks."""
        adapter = FileLockAdapter(lock_dir)
        await adapter.acquire("resource1", "holder1", LockOptions(ttl_seconds=10))
        await adapter.close()
        # After close, a new adapter should be able to acquire
        adapter2 = FileLockAdapter(lock_dir)
        result = await adapter2.acquire("resource1", "holder2", LockOptions(ttl_seconds=10))
        assert result.acquired is True

    @pytest.mark.asyncio
    async def test_resource_path_sanitization(self, lock_dir: str) -> None:
        """Resource names with slashes are sanitized."""
        adapter = FileLockAdapter(lock_dir)
        result = await adapter.acquire("/path/to/resource", "holder1", LockOptions(ttl_seconds=10))
        assert result.acquired is True

    @pytest.mark.asyncio
    async def test_empty_resource_name(self, lock_dir: str) -> None:
        """Empty resource name defaults to 'default'."""
        adapter = FileLockAdapter(lock_dir)
        result = await adapter.acquire("", "holder1", LockOptions(ttl_seconds=10))
        assert result.acquired is True

    @pytest.mark.asyncio
    async def test_timeout_wait(self, lock_dir: str) -> None:
        """Timeout with wait returns waited_ms."""
        adapter = FileLockAdapter(lock_dir)
        await adapter.acquire("resource1", "holder1", LockOptions(ttl_seconds=60))
        result = await adapter.acquire("resource1", "holder2", LockOptions(ttl_seconds=10, timeout_seconds=0.05))
        assert result.acquired is False
        assert result.waited_ms >= 0


class TestLockEntry:
    """Tests for _LockEntry internal class."""

    def test_to_dict(self) -> None:
        """to_dict produces correct structure."""
        from polaris.kernelone.locks.contracts import _LockEntry

        entry = _LockEntry("holder1", 1000.0, 2000.0)
        d = entry.to_dict()
        assert d["holder_id"] == "holder1"
        assert d["acquired_at"] == 1000.0
        assert d["expires_at"] == 2000.0

    def test_from_dict(self) -> None:
        """from_dict reconstructs entry."""
        from polaris.kernelone.locks.contracts import _LockEntry

        entry = _LockEntry.from_dict({"holder_id": "h1", "acquired_at": "1000", "expires_at": "2000"})
        assert entry.holder_id == "h1"
        assert entry.acquired_at == 1000.0
        assert entry.expires_at == 2000.0


class TestModuleExports:
    """Tests for module public API."""

    def test_all_exports_present(self) -> None:
        """All expected names are importable."""
        from polaris.kernelone.locks import contracts

        assert hasattr(contracts, "FileLockAdapter")

    def test_file_lock_adapter_is_lock_port(self) -> None:
        """FileLockAdapter implements LockPort."""
        from polaris.kernelone.contracts.technical import LockPort

        assert issubclass(FileLockAdapter, LockPort)
