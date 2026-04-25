"""Tests for polaris.kernelone.locks.contracts."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from polaris.kernelone.contracts.technical import LockOptions
from polaris.kernelone.locks.contracts import FileLockAdapter, _LockEntry


class TestLockEntry:
    def test_to_dict(self) -> None:
        entry = _LockEntry("holder-1", 1000.0, 2000.0)
        d = entry.to_dict()
        assert d["holder_id"] == "holder-1"
        assert d["acquired_at"] == 1000.0
        assert d["expires_at"] == 2000.0

    def test_from_dict(self) -> None:
        d = {"holder_id": "h1", "acquired_at": 1000.0, "expires_at": 2000.0}
        entry = _LockEntry.from_dict(d)
        assert entry.holder_id == "h1"
        assert entry.acquired_at == 1000.0
        assert entry.expires_at == 2000.0

    def test_from_dict_defaults(self) -> None:
        entry = _LockEntry.from_dict({})
        assert entry.holder_id == ""
        assert entry.acquired_at == 0.0
        assert entry.expires_at == 0.0


class TestFileLockAdapter:
    def test_lock_path_sanitization(self, tmp_path: Path) -> None:
        adapter = FileLockAdapter(str(tmp_path))
        path = adapter._lock_path("/a/b/c")
        assert path.endswith("a_b_c.lock")
        assert os.path.isabs(path)

    def test_lock_path_empty(self, tmp_path: Path) -> None:
        adapter = FileLockAdapter(str(tmp_path))
        path = adapter._lock_path("")
        assert "default.lock" in path

    def test_acquire_and_release(self, tmp_path: Path) -> None:
        adapter = FileLockAdapter(str(tmp_path))
        result = asyncio.run(
            adapter.acquire("resource-1", "holder-1", LockOptions(timeout_seconds=1.0, ttl_seconds=10.0))
        )
        assert result.acquired is True
        assert result.holder_id == "holder-1"

        release_result = asyncio.run(adapter.release("resource-1", "holder-1"))
        assert release_result.released is True

    def test_acquire_non_blocking(self, tmp_path: Path) -> None:
        adapter = FileLockAdapter(str(tmp_path))
        result = asyncio.run(
            adapter.acquire("res", "h1", LockOptions(non_blocking=True, ttl_seconds=10.0))
        )
        assert result.acquired is True

    def test_acquire_timeout(self, tmp_path: Path) -> None:
        adapter1 = FileLockAdapter(str(tmp_path))
        adapter2 = FileLockAdapter(str(tmp_path))

        result1 = asyncio.run(
            adapter1.acquire("res", "h1", LockOptions(timeout_seconds=1.0, ttl_seconds=3600.0))
        )
        assert result1.acquired is True

        result2 = asyncio.run(
            adapter2.acquire(
                "res", "h2", LockOptions(timeout_seconds=0.1, ttl_seconds=10.0, retry_interval_seconds=0.05)
            )
        )
        assert result2.acquired is False
        assert result2.waited_ms >= 0

        asyncio.run(adapter1.release("res", "h1"))

    def test_release_wrong_holder(self, tmp_path: Path) -> None:
        adapter = FileLockAdapter(str(tmp_path))
        asyncio.run(
            adapter.acquire("res", "h1", LockOptions(timeout_seconds=1.0, ttl_seconds=10.0))
        )
        result = asyncio.run(adapter.release("res", "h2"))
        assert result.released is False

    def test_extend(self, tmp_path: Path) -> None:
        adapter = FileLockAdapter(str(tmp_path))
        asyncio.run(
            adapter.acquire("res", "h1", LockOptions(timeout_seconds=1.0, ttl_seconds=10.0))
        )
        extended = asyncio.run(adapter.extend("res", "h1", additional_seconds=10.0))
        assert extended is True

    def test_extend_wrong_holder(self, tmp_path: Path) -> None:
        adapter = FileLockAdapter(str(tmp_path))
        asyncio.run(
            adapter.acquire("res", "h1", LockOptions(timeout_seconds=1.0, ttl_seconds=10.0))
        )
        extended = asyncio.run(adapter.extend("res", "h2", additional_seconds=10.0))
        assert extended is False

    def test_is_held(self, tmp_path: Path) -> None:
        adapter = FileLockAdapter(str(tmp_path))
        held_before = asyncio.run(adapter.is_held("res"))
        assert held_before is False

        asyncio.run(
            adapter.acquire("res", "h1", LockOptions(timeout_seconds=1.0, ttl_seconds=10.0))
        )
        held_after = asyncio.run(adapter.is_held("res"))
        assert held_after is True

        asyncio.run(adapter.release("res", "h1"))
        # Use a fresh adapter to avoid Windows file-handle caching issues
        adapter2 = FileLockAdapter(str(tmp_path))
        held_released = asyncio.run(adapter2.is_held("res"))
        assert held_released is False

    def test_is_held_expired(self, tmp_path: Path) -> None:
        adapter = FileLockAdapter(str(tmp_path))
        asyncio.run(
            adapter.acquire("res", "h1", LockOptions(timeout_seconds=1.0, ttl_seconds=0.01))
        )
        import time

        time.sleep(0.05)
        held = asyncio.run(adapter.is_held("res"))
        assert held is False

    def test_close(self, tmp_path: Path) -> None:
        adapter = FileLockAdapter(str(tmp_path))
        asyncio.run(
            adapter.acquire("res", "h1", LockOptions(timeout_seconds=1.0, ttl_seconds=10.0))
        )
        asyncio.run(adapter.close())
        assert adapter._closed is True

    def test_acquire_after_close(self, tmp_path: Path) -> None:
        adapter = FileLockAdapter(str(tmp_path))
        asyncio.run(adapter.close())
        result = asyncio.run(
            adapter.acquire("res", "h1", LockOptions(timeout_seconds=1.0, ttl_seconds=10.0))
        )
        assert result.acquired is False

    def test_reacquire_same_holder(self, tmp_path: Path) -> None:
        adapter = FileLockAdapter(str(tmp_path))
        result1 = asyncio.run(
            adapter.acquire("res", "h1", LockOptions(timeout_seconds=1.0, ttl_seconds=10.0))
        )
        assert result1.acquired is True

        result2 = asyncio.run(
            adapter.acquire("res", "h1", LockOptions(timeout_seconds=1.0, ttl_seconds=20.0))
        )
        assert result2.acquired is True

        asyncio.run(adapter.release("res", "h1"))
