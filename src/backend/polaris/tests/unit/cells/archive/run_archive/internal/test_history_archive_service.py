"""Unit tests for polaris.cells.archive.run_archive.internal.history_archive_service."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from polaris.cells.archive.run_archive.internal.history_archive_service import (
    ArchiveManifest,
    HistoryArchiveService,
    HistoryRunIndex,
    create_history_archive_service,
)


class TestArchiveManifest:
    """Tests for ArchiveManifest dataclass."""

    def test_to_dict(self) -> None:
        manifest = ArchiveManifest(
            scope="run",
            id="r1",
            archive_timestamp=123.0,
            archive_datetime="2024-01-01",
            source_runtime_root="/tmp/runtime",
            source_paths=["events.jsonl"],
            target_path="runs/r1",
            total_size_bytes=100,
            file_count=5,
            content_hash="abc",
            reason="completed",
        )
        d = manifest.to_dict()
        assert d["scope"] == "run"
        assert d["id"] == "r1"
        assert d["file_count"] == 5

    def test_from_dict(self) -> None:
        data = {
            "scope": "run",
            "id": "r1",
            "archive_timestamp": 123.0,
            "archive_datetime": "2024-01-01",
            "source_runtime_root": "/tmp/runtime",
            "source_paths": ["events.jsonl"],
            "target_path": "runs/r1",
            "total_size_bytes": 100,
            "file_count": 5,
            "content_hash": "abc",
            "reason": "completed",
            "compressed": False,
            "compression_ratio": 1.0,
            "run_index_entry": {},
            "task_index_entry": None,
        }
        manifest = ArchiveManifest.from_dict(data)
        assert manifest.scope == "run"
        assert manifest.id == "r1"


class TestHistoryRunIndex:
    """Tests for HistoryRunIndex dataclass."""

    def test_to_dict(self) -> None:
        index = HistoryRunIndex(
            run_id="r1",
            archive_timestamp=123.0,
            archive_datetime="2024-01-01",
            reason="completed",
            target_path="runs/r1",
            total_size_bytes=100,
            file_count=5,
            content_hash="abc",
            status="success",
        )
        d = index.to_dict()
        assert d["run_id"] == "r1"
        assert d["status"] == "success"

    def test_from_dict(self) -> None:
        data = {
            "run_id": "r1",
            "archive_timestamp": 123.0,
            "archive_datetime": "2024-01-01",
            "reason": "completed",
            "target_path": "runs/r1",
            "total_size_bytes": 100,
            "file_count": 5,
            "content_hash": "abc",
            "status": "success",
        }
        index = HistoryRunIndex.from_dict(data)
        assert index.run_id == "r1"


class TestHistoryArchiveService:
    """Tests for HistoryArchiveService."""

    @pytest.fixture
    def service(self, tmp_path: Path) -> HistoryArchiveService:
        with patch(
            "polaris.cells.archive.run_archive.internal.history_archive_service.resolve_polaris_roots"
        ) as mock_roots:
            mock_roots.return_value = MagicMock(
                history_root=str(tmp_path / "history"),
                runtime_root=str(tmp_path / "runtime"),
                workspace_persistent_root=str(tmp_path),
            )
            return HistoryArchiveService(str(tmp_path))

    def test_init(self, service: HistoryArchiveService) -> None:
        assert service.history_root.exists()
        assert service.index_dir.exists()

    def test_create_empty_manifest(self, service: HistoryArchiveService) -> None:
        manifest = service._create_empty_manifest("run", "r1", "completed")
        assert manifest.scope == "run"
        assert manifest.id == "r1"
        assert manifest.total_size_bytes == 0
        assert manifest.file_count == 0

    def test_archive_run_missing_source(self, service: HistoryArchiveService) -> None:
        manifest = service.archive_run("nonexistent", reason="completed")
        assert manifest.total_size_bytes == 0
        assert manifest.file_count == 0

    def test_archive_task_snapshot_missing_source(self, service: HistoryArchiveService) -> None:
        manifest = service.archive_task_snapshot("nonexistent", reason="completed")
        assert manifest.total_size_bytes == 0
        assert manifest.file_count == 0

    def test_archive_factory_run_missing_source(self, service: HistoryArchiveService) -> None:
        manifest = service.archive_factory_run("nonexistent", reason="completed")
        assert manifest.total_size_bytes == 0
        assert manifest.file_count == 0

    def test_list_history_runs_empty(self, service: HistoryArchiveService) -> None:
        runs = service.list_history_runs()
        assert runs == []

    def test_get_manifest_not_found(self, service: HistoryArchiveService) -> None:
        manifest = service.get_manifest("run", "nonexistent")
        assert manifest is None

    def test_get_run_events_empty(self, service: HistoryArchiveService) -> None:
        events = service.get_run_events("nonexistent")
        assert events == []

    def test_collect_run_files(self, service: HistoryArchiveService, tmp_path: Path) -> None:
        run_dir = tmp_path / "test_run"
        run_dir.mkdir()
        (run_dir / "file1.txt").write_text("hello")
        (run_dir / "subdir").mkdir()
        (run_dir / "subdir" / "file2.txt").write_text("world")

        files = service._collect_run_files(run_dir)
        assert len(files) == 2
        assert "file1.txt" in files
        assert str(Path("subdir") / "file2.txt") in files

    def test_calculate_checksums(self, service: HistoryArchiveService, tmp_path: Path) -> None:
        run_dir = tmp_path / "test_run"
        run_dir.mkdir()
        (run_dir / "file1.txt").write_text("hello")

        total_size, file_count, content_hash = service._calculate_checksums(run_dir)
        assert total_size == 5
        assert file_count == 1
        assert len(content_hash) == 64  # SHA-256 hex

    def test_copy_directory(self, service: HistoryArchiveService, tmp_path: Path) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "file.txt").write_text("hello")
        target = tmp_path / "target"

        service._copy_directory(source, target)
        assert target.exists()
        assert (target / "file.txt").exists()
        assert (target / "file.txt").read_text() == "hello"

    def test_copy_directory_overwrites(self, service: HistoryArchiveService, tmp_path: Path) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "file.txt").write_text("new")
        target = tmp_path / "target"
        target.mkdir()
        (target / "file.txt").write_text("old")

        service._copy_directory(source, target)
        assert (target / "file.txt").read_text() == "new"


class TestCreateHistoryArchiveService:
    """Tests for create_history_archive_service factory."""

    def test_factory(self) -> None:
        with patch(
            "polaris.cells.archive.run_archive.internal.history_archive_service.resolve_polaris_roots"
        ) as mock_roots:
            mock_roots.return_value = MagicMock(
                history_root="/tmp/history",
                runtime_root="/tmp/runtime",
                workspace_persistent_root="/tmp",
            )
            service = create_history_archive_service("/tmp/ws")
            assert isinstance(service, HistoryArchiveService)
