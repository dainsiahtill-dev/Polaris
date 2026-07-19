"""Unit tests for polaris.cells.factory.pipeline.internal.factory_store."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from polaris.cells.factory.pipeline.internal.factory_store import (
    FactoryRunSnapshotError,
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

    @pytest.mark.asyncio
    async def test_cancelled_waiter_does_not_leak_lock(self, tmp_path: Path) -> None:
        path = tmp_path / "run.json"
        path.write_text("{}", encoding="utf-8")

        async def _wait_for_lock() -> None:
            async with _acquire_file_lock(path, timeout=0.5):
                pass

        async with _acquire_file_lock(path, timeout=1.0):
            waiter = asyncio.create_task(_wait_for_lock())
            await asyncio.sleep(0.05)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter

        await asyncio.sleep(0.1)
        async with _acquire_file_lock(path, timeout=1.0):
            pass


class TestFactoryStore:
    """Tests for FactoryStore."""

    @pytest.fixture
    def tmp_store(self, tmp_path: Path) -> FactoryStore:
        return FactoryStore(tmp_path / "factory")

    def test_init_creates_dir(self, tmp_path: Path) -> None:
        base = tmp_path / "factory_new"
        FactoryStore(base)
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
    async def test_get_run_corrupt_json_returns_none(self, tmp_store: FactoryStore) -> None:
        run_dir = tmp_store.get_run_dir("run-corrupt")
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text("{not-json", encoding="utf-8")

        result = await tmp_store.get_run("run-corrupt")

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
    async def test_get_events_skips_corrupt_jsonl_records(self, tmp_store: FactoryStore) -> None:
        event_file = tmp_store.get_run_dir("run-corrupt-events") / "events" / "events.jsonl"
        event_file.parent.mkdir(parents=True)
        event_file.write_text(
            '{"type": "start"}\n{not-json\n["not", "an", "object"]\n{"type": "end"}\n',
            encoding="utf-8",
        )

        events = await tmp_store.get_events("run-corrupt-events")

        assert [event["type"] for event in events] == ["start", "end"]

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

        checkpoint_ref = await tmp_store.checkpoint(mock_run)
        checkpoint_dir = tmp_store.get_run_dir("run-001") / "checkpoints"
        assert checkpoint_dir.exists()
        files = list(checkpoint_dir.iterdir())
        assert len(files) == 1
        assert checkpoint_ref == "runtime/run-001/checkpoints/running_2024-01-01T00_00_00.json"

    @pytest.mark.asyncio
    async def test_strict_run_and_checkpoint_reread_exact_objects(self, tmp_store: FactoryStore) -> None:
        mock_run = MagicMock()
        mock_run.id = "run-strict"
        mock_run.updated_at = "2024-01-01T00:00:00"
        mock_run.created_at = "2024-01-01T00:00:00"
        mock_run.status = MagicMock()
        mock_run.status.value = "running"
        mock_run.to_dict.return_value = {"id": "run-strict", "status": "running"}

        run_ref = await tmp_store.save_run(mock_run)
        checkpoint_ref = await tmp_store.checkpoint(mock_run)

        assert run_ref == "runtime/run-strict/run.json"
        assert await tmp_store.read_strict_run_snapshot("run-strict") == mock_run.to_dict.return_value
        assert (
            await tmp_store.read_strict_checkpoint_snapshot("run-strict", checkpoint_ref)
            == mock_run.to_dict.return_value
        )

    @pytest.mark.asyncio
    async def test_checkpoint_ref_is_immutable_and_rejects_different_bytes(self, tmp_store: FactoryStore) -> None:
        mock_run = MagicMock()
        mock_run.id = "run-immutable"
        mock_run.updated_at = "2024-01-01T00:00:00"
        mock_run.created_at = "2024-01-01T00:00:00"
        mock_run.status = MagicMock()
        mock_run.status.value = "running"
        mock_run.to_dict.return_value = {"id": "run-immutable", "status": "running"}

        checkpoint_ref = await tmp_store.checkpoint(mock_run)
        assert await tmp_store.checkpoint(mock_run) == checkpoint_ref
        mock_run.to_dict.return_value = {"id": "run-immutable", "status": "changed"}
        with pytest.raises(FactoryRunSnapshotError) as captured:
            await tmp_store.checkpoint(mock_run)
        assert captured.value.code == "factory_checkpoint_immutable_collision"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "raw,code",
        [
            ('{"id":"run-bad","id":"other"}', "factory_run_snapshot_duplicate_key"),
            ('{"id":"run-bad","value":NaN}', "factory_run_snapshot_non_finite_number"),
            ('["not-an-object"]', "factory_run_snapshot_root_invalid"),
        ],
    )
    async def test_strict_run_snapshot_rejects_ambiguous_json(
        self,
        tmp_store: FactoryStore,
        raw: str,
        code: str,
    ) -> None:
        run_dir = tmp_store.get_run_dir("run-bad")
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(raw, encoding="utf-8")

        with pytest.raises(FactoryRunSnapshotError) as captured:
            await tmp_store.read_strict_run_snapshot("run-bad")
        assert captured.value.code == code

    @pytest.mark.asyncio
    async def test_strict_run_snapshot_rejects_symlink(self, tmp_store: FactoryStore) -> None:
        outside = tmp_store.base_dir / "outside.json"
        outside.write_text(json.dumps({"id": "run-link"}), encoding="utf-8")
        run_dir = tmp_store.get_run_dir("run-link")
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").symlink_to(outside)

        with pytest.raises(FactoryRunSnapshotError) as captured:
            await tmp_store.read_strict_run_snapshot("run-link")
        assert captured.value.code == "factory_run_snapshot_guard_failed"

    def test_list_runs(self, tmp_store: FactoryStore) -> None:
        # Only directories with a regular mutable snapshot are discoverable;
        # admission-first half-runs remain quarantined for strict audit.
        for run_id in ("run-a", "run-b"):
            run_dir = tmp_store.base_dir / run_id
            run_dir.mkdir()
            (run_dir / "run.json").write_text("{}", encoding="utf-8")
        (tmp_store.base_dir / "half-run").mkdir()
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
        # Create temp but make target directory read-only to force failure
        temp.write_text("hello")
        # On Windows, os.replace raises FileNotFoundError if temp doesn't exist;
        # on success it overwrites target. Test the failure path by removing temp first.
        temp.unlink()
        with pytest.raises(FileNotFoundError):
            await tmp_store._replace_with_retry(temp, target)
