"""Unit tests for polaris.cells.archive.run_archive.internal.history_manifest_repository."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from polaris.cells.archive.run_archive.internal.history_manifest_repository import (
    FactoryIndexEntry,
    HistoryManifestRepository,
    IndexEntry,
    IndexType,
    RunIndexEntry,
    TaskIndexEntry,
    create_history_manifest_repository,
)


class TestIndexType:
    """Tests for IndexType enum."""

    def test_values(self) -> None:
        assert IndexType.RUNS.value == "runs"
        assert IndexType.TASKS.value == "tasks"
        assert IndexType.FACTORY.value == "factory"


class TestIndexEntry:
    """Tests for IndexEntry dataclass."""

    def test_to_dict(self) -> None:
        entry = IndexEntry(
            id="r1",
            archive_timestamp=123.0,
            archive_datetime="2024-01-01",
            reason="completed",
            target_path="runs/r1",
            total_size_bytes=100,
            file_count=5,
        )
        d = entry.to_dict()
        assert d["id"] == "r1"
        assert d["file_count"] == 5

    def test_from_dict(self) -> None:
        data = {
            "id": "r1",
            "archive_timestamp": 123.0,
            "archive_datetime": "2024-01-01",
            "reason": "completed",
            "target_path": "runs/r1",
            "total_size_bytes": 100,
            "file_count": 5,
        }
        entry = IndexEntry.from_dict(data)
        assert entry.id == "r1"
        assert entry.file_count == 5


class TestRunIndexEntry:
    """Tests for RunIndexEntry dataclass."""

    def test_defaults(self) -> None:
        entry = RunIndexEntry(
            id="r1",
            archive_timestamp=123.0,
            archive_datetime="2024-01-01",
            reason="completed",
            target_path="runs/r1",
            total_size_bytes=100,
            file_count=5,
        )
        assert entry.content_hash == ""
        assert entry.status == ""


class TestTaskIndexEntry:
    """Tests for TaskIndexEntry dataclass."""

    def test_defaults(self) -> None:
        entry = TaskIndexEntry(
            id="t1",
            archive_timestamp=123.0,
            archive_datetime="2024-01-01",
            reason="completed",
            target_path="tasks/t1",
            total_size_bytes=100,
            file_count=5,
        )
        assert entry.snapshot_id == ""


class TestFactoryIndexEntry:
    """Tests for FactoryIndexEntry dataclass."""

    def test_defaults(self) -> None:
        entry = FactoryIndexEntry(
            id="f1",
            archive_timestamp=123.0,
            archive_datetime="2024-01-01",
            reason="completed",
            target_path="factory/f1",
            total_size_bytes=100,
            file_count=5,
        )
        assert entry.factory_run_id == ""


class TestHistoryManifestRepository:
    """Tests for HistoryManifestRepository."""

    @pytest.fixture
    def repo(self, tmp_path: Path) -> HistoryManifestRepository:
        with patch(
            "polaris.cells.archive.run_archive.internal.history_manifest_repository.resolve_polaris_roots"
        ) as mock_roots:
            mock_roots.return_value = MagicMock(history_root=str(tmp_path / "history"))
            return HistoryManifestRepository(str(tmp_path))

    def test_init_creates_index_dir(self, repo: HistoryManifestRepository) -> None:
        assert repo.index_dir.exists()
        assert repo.index_dir.is_dir()

    def test_get_index_path(self, repo: HistoryManifestRepository) -> None:
        path = repo._get_index_path(IndexType.RUNS)
        assert path.name == "runs.index.jsonl"
        assert path.parent == repo.index_dir

    def test_append_and_read_run_entry(self, repo: HistoryManifestRepository) -> None:
        entry = RunIndexEntry(
            id="r1",
            archive_timestamp=123.0,
            archive_datetime="2024-01-01",
            reason="completed",
            target_path="runs/r1",
            total_size_bytes=100,
            file_count=5,
            content_hash="abc",
            status="success",
        )
        repo.append_run_entry(entry)

        entries = repo.read_runs_index()
        assert len(entries) == 1
        assert entries[0].id == "r1"
        assert entries[0].content_hash == "abc"

    def test_append_and_read_task_entry(self, repo: HistoryManifestRepository) -> None:
        entry = TaskIndexEntry(
            id="t1",
            archive_timestamp=123.0,
            archive_datetime="2024-01-01",
            reason="completed",
            target_path="tasks/t1",
            total_size_bytes=100,
            file_count=5,
            snapshot_id="snap-1",
        )
        repo.append_task_entry(entry)

        entries = repo.read_tasks_index()
        assert len(entries) == 1
        assert entries[0].snapshot_id == "snap-1"

    def test_append_and_read_factory_entry(self, repo: HistoryManifestRepository) -> None:
        entry = FactoryIndexEntry(
            id="f1",
            archive_timestamp=123.0,
            archive_datetime="2024-01-01",
            reason="completed",
            target_path="factory/f1",
            total_size_bytes=100,
            file_count=5,
            factory_run_id="fr-1",
        )
        repo.append_factory_entry(entry)

        entries = repo.read_factory_index()
        assert len(entries) == 1
        assert entries[0].factory_run_id == "fr-1"

    def test_read_runs_index_empty(self, repo: HistoryManifestRepository) -> None:
        entries = repo.read_runs_index()
        assert entries == []

    def test_read_runs_index_limit_offset(self, repo: HistoryManifestRepository) -> None:
        for i in range(5):
            entry = RunIndexEntry(
                id=f"r{i}",
                archive_timestamp=float(i),
                archive_datetime="2024-01-01",
                reason="completed",
                target_path=f"runs/r{i}",
                total_size_bytes=100,
                file_count=5,
            )
            repo.append_run_entry(entry)

        entries = repo.read_runs_index(limit=2, offset=1)
        assert len(entries) == 2
        # Sorted descending by timestamp
        assert entries[0].id == "r3"
        assert entries[1].id == "r2"

    def test_get_run_entry_found(self, repo: HistoryManifestRepository) -> None:
        entry = RunIndexEntry(
            id="r1",
            archive_timestamp=123.0,
            archive_datetime="2024-01-01",
            reason="completed",
            target_path="runs/r1",
            total_size_bytes=100,
            file_count=5,
        )
        repo.append_run_entry(entry)

        found = repo.get_run_entry("r1")
        assert found is not None
        assert found.id == "r1"

    def test_get_run_entry_not_found(self, repo: HistoryManifestRepository) -> None:
        found = repo.get_run_entry("nonexistent")
        assert found is None

    def test_get_task_entry_found(self, repo: HistoryManifestRepository) -> None:
        entry = TaskIndexEntry(
            id="t1",
            archive_timestamp=123.0,
            archive_datetime="2024-01-01",
            reason="completed",
            target_path="tasks/t1",
            total_size_bytes=100,
            file_count=5,
            snapshot_id="snap-1",
        )
        repo.append_task_entry(entry)

        found = repo.get_task_entry("snap-1")
        assert found is not None
        assert found.snapshot_id == "snap-1"

    def test_get_task_entry_not_found(self, repo: HistoryManifestRepository) -> None:
        found = repo.get_task_entry("nonexistent")
        assert found is None

    def test_get_factory_entry_found(self, repo: HistoryManifestRepository) -> None:
        entry = FactoryIndexEntry(
            id="f1",
            archive_timestamp=123.0,
            archive_datetime="2024-01-01",
            reason="completed",
            target_path="factory/f1",
            total_size_bytes=100,
            file_count=5,
            factory_run_id="fr-1",
        )
        repo.append_factory_entry(entry)

        found = repo.get_factory_entry("fr-1")
        assert found is not None
        assert found.factory_run_id == "fr-1"

    def test_get_factory_entry_not_found(self, repo: HistoryManifestRepository) -> None:
        found = repo.get_factory_entry("nonexistent")
        assert found is None

    def test_count_entries(self, repo: HistoryManifestRepository) -> None:
        assert repo.count_entries(IndexType.RUNS) == 0
        entry = RunIndexEntry(
            id="r1",
            archive_timestamp=123.0,
            archive_datetime="2024-01-01",
            reason="completed",
            target_path="runs/r1",
            total_size_bytes=100,
            file_count=5,
        )
        repo.append_run_entry(entry)
        assert repo.count_entries(IndexType.RUNS) == 1

    def test_clear_index(self, repo: HistoryManifestRepository) -> None:
        entry = RunIndexEntry(
            id="r1",
            archive_timestamp=123.0,
            archive_datetime="2024-01-01",
            reason="completed",
            target_path="runs/r1",
            total_size_bytes=100,
            file_count=5,
        )
        repo.append_run_entry(entry)
        assert repo.count_entries(IndexType.RUNS) == 1

        repo.clear_index(IndexType.RUNS)
        assert repo.count_entries(IndexType.RUNS) == 0

    def test_list_indices(self, repo: HistoryManifestRepository) -> None:
        indices = repo.list_indices()
        assert "runs" in indices
        assert "tasks" in indices
        assert "factory" in indices
        assert indices["runs"] == 0

    def test_read_index_skips_invalid_lines(self, repo: HistoryManifestRepository) -> None:
        index_path = repo._get_index_path(IndexType.RUNS)
        index_path.write_text("not json\n{}", encoding="utf-8")
        entries = repo.read_runs_index()
        assert entries == []


class TestCreateHistoryManifestRepository:
    """Tests for create_history_manifest_repository factory."""

    def test_factory(self) -> None:
        with patch(
            "polaris.cells.archive.run_archive.internal.history_manifest_repository.resolve_polaris_roots"
        ) as mock_roots:
            mock_roots.return_value = MagicMock(history_root="/tmp/history")
            repo = create_history_manifest_repository("/tmp/ws")
            assert isinstance(repo, HistoryManifestRepository)
