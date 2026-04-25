"""Unit tests for polaris.cells.factory.pipeline.internal.factory_store."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from polaris.cells.factory.pipeline.internal.factory_store import (
    FactoryStore,
    FileLockTimeoutError,
    _acquire_file_lock,
    _acquire_lock_with_timeout,
    _get_run_file_lock,
)


class TestFileLockTimeoutError:
    """Tests for FileLockTimeoutError."""

    def test_exception_attributes(self) -> None:
        path = Path("/tmp/test.json")
        exc = FileLockTimeoutError(path, 5.0)
        assert exc.file_path == path
        assert exc.timeout == 5.0
        assert "Failed to acquire file lock" in str(exc)


class TestGetRunFileLock:
    """Tests for _get_run_file_lock."""

    def test_same_path_same_lock(self) -> None:
        path = Path("/tmp/run.json")
        lock1 = _get_run_file_lock(path)
        lock2 = _get_run_file_lock(path)
        assert lock1 is lock2

    def test_different_path_different_lock(self) -> None:
        lock1 = _get_run_file_lock(Path("/tmp/a.json"))
        lock2 = _get_run_file_lock(Path("/tmp/b.json"))
        assert lock1 is not lock2


class TestAcquireLockWithTimeout:
    """Tests for _acquire_lock_with_timeout."""

    def test_acquire_success(self) -> None:
        import threading

        lock = threading.Lock()
        result = _acquire_lock_with_timeout(lock, 1.0)
        assert result is True
        lock.release()

    def test_acquire_timeout_raises(self) -> None:
        import threading

        lock = threading.Lock()
        lock.acquire()
        with pytest.raises(FileLockTimeoutError):
            _acquire_lock_with_timeout(lock, 0.01)
        lock.release()


class TestAcquireFileLock:
    """Tests for _acquire_file_lock async context manager."""

    @pytest.mark.asyncio
    async def test_acquire_and_release(self) -> None:
        path = Path("/tmp/test_lock.json")
        async with _acquire_file_lock(path, timeout=1.0):
            pass  # Should not raise


class TestFactoryStore:
    """Tests for FactoryStore."""

    @pytest.fixture
    def tmp_store(self, tmp_path: Path) -> FactoryStore:
        return FactoryStore(tmp_path / "factory")

    def test_init_creates_dir(self, tmp_path: Path) -> None:
        base = tmp_path / "factory_new"
        store = FactoryStore(base)
        assert base.exists()
        assert base.is_dir()

    def test_get_run_dir(self, tmp_store: FactoryStore) -> None:
        run_dir = tmp_store.get_run_dir("run-001")
        assert str(run_dir).endswith("run-001")

    @pytest.mark.asyncio
    async def test_save_and_get_run(self, tmp_store: FactoryStore) -> None:
        mock_run = MagicMock()
        mock_run.id = "run-001"
        mock_run.to_dict.return_value = {"id": "run-001", "status": "pending"}

        await tmp_store.save_run(mock_run)
        run_file = tmp_store.get_run_dir("run-001") / "run.json"
        assert run_file.exists()

    @pytest.mark.asyncio
    async def test_get_run_not_found(self, tmp_store: FactoryStore) -> None:
        result = await tmp_store.get_run("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_append_and_get_events(self, tmp_store: FactoryStore) -> None:
        await tmp_store.append_event("run-001", {"type": "start", "msg": "hello"})
        await tmp_store.append_event("run-001", {"type": "end", "msg": "bye"})

        events = await tmp_store.get_events("run-001")
        assert len(events) == 2
        assert events[0]["type"] == "start"
        assert events[1]["type"] == "end"

    @pytest.mark.asyncio
    async def test_get_events_empty(self, tmp_store: FactoryStore) -> None:
        events = await tmp_store.get_events("run-no-events")
        assert events == []

    @pytest.mark.asyncio
    async def test_checkpoint(self, tmp_store: FactoryStore) -> None:
        mock_run = MagicMock()
        mock_run.id = "run-001"
        mock_run.updated_at = "2024-01-01T00:00:00"
        mock_run.created_at = "2024-01-01T00:00:00"
        mock_run.status = MagicMock()
        mock_run.status.value = "running"
        mock_run.to_dict.return_value = {"id": "run-001", "status": "running"}

        await tmp_store.checkpoint(mock_run)
        checkpoint_dir = tmp_store.get_run_dir("run-001") / "checkpoints"
        assert checkpoint_dir.exists()
        files = list(checkpoint_dir.iterdir())
        assert len(files) == 1

    def test_list_runs(self, tmp_store: FactoryStore) -> None:
        # Create some run directories manually
        (tmp_store.base_dir / "run-a").mkdir()
        (tmp_store.base_dir / "run-b").mkdir()
        (tmp_store.base_dir / "not_a_dir.txt").write_text("x")

        runs = tmp_store.list_runs()
        assert sorted(runs) == ["run-a", "run-b"]

    def test_list_runs_empty_base(self, tmp_path: Path) -> None:
        store = FactoryStore(tmp_path / "empty")
        assert store.list_runs() == []

    @pytest.mark.asyncio
    async def test_replace_with_retry_success(self, tmp_store: FactoryStore) -> None:
        temp = tmp_store.base_dir / "temp.txt"
        target = tmp_store.base_dir / "target.txt"
        temp.write_text("hello")
        await tmp_store._replace_with_retry(temp, target)
        assert target.exists()
        assert target.read_text() == "hello"

    @pytest.mark.asyncio
    async def test_replace_with_retry_cleans_temp(self, tmp_store: FactoryStore) -> None:
        temp = tmp_store.base_dir / "temp.txt"
        target = tmp_store.base_dir / "target.txt"
        # Don't create temp - force failure
        with pytest.raises(PermissionError):
            await tmp_store._replace_with_retry(temp, target)
