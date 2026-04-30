"""Tests for `polaris.cells.archive.task_snapshot_archive.internal.task_snapshot_archive_service`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from polaris.cells.archive.task_snapshot_archive.internal.task_snapshot_archive_service import (
    TaskSnapshotArchiveManifest,
    TaskSnapshotArchiveService,
)


class FakeStorageRoots:
    """Fake return value for resolve_storage_roots."""

    def __init__(self, tmp_path: Path) -> None:
        self.history_root = str(tmp_path / ".polaris" / "history")
        self.runtime_root = str(tmp_path / ".polaris" / "runtime")
        self.workspace_persistent_root = str(tmp_path / ".polaris")


def _make_service(tmp_path: Path) -> TaskSnapshotArchiveService:
    """Helper to create a TaskSnapshotArchiveService with mocked storage roots."""
    fake_roots = FakeStorageRoots(tmp_path)
    with patch(
        "polaris.kernelone.storage.resolve_storage_roots",
        return_value=fake_roots,
    ):
        return TaskSnapshotArchiveService(str(tmp_path))


class TestTaskSnapshotArchiveServiceInit:
    """Test suite for TaskSnapshotArchiveService initialization."""

    def test_init_requires_workspace(self, tmp_path: Path) -> None:
        """Service initializes with valid workspace path."""
        service = _make_service(tmp_path)
        assert service.workspace == tmp_path.resolve()

    def test_init_empty_workspace_raises(self) -> None:
        """Empty workspace string raises ValueError."""
        with pytest.raises(ValueError, match="workspace is required"):
            TaskSnapshotArchiveService("")

    def test_init_whitespace_workspace_raises(self) -> None:
        """Whitespace-only workspace string raises ValueError."""
        with pytest.raises(ValueError, match="workspace is required"):
            TaskSnapshotArchiveService("   ")

    def test_init_none_workspace_raises(self) -> None:
        """None workspace coerced to empty string raises ValueError."""
        with pytest.raises(ValueError, match="workspace is required"):
            TaskSnapshotArchiveService(None)  # type: ignore[arg-type]

    def test_init_creates_index_dir(self, tmp_path: Path) -> None:
        """Initialization creates history/index directory."""
        service = _make_service(tmp_path)
        assert service.index_dir.exists()
        assert service.index_dir.name == "index"

    def test_init_sets_paths_from_roots(self, tmp_path: Path) -> None:
        """Service sets runtime_root and history_root from roots."""
        service = _make_service(tmp_path)
        assert str(service.history_root) == FakeStorageRoots(tmp_path).history_root
        assert str(service.runtime_root) == FakeStorageRoots(tmp_path).runtime_root


class TestArchiveTaskSnapshot:
    """Test suite for archive_task_snapshot method."""

    @patch(
        "polaris.cells.archive.run_archive.internal.history_manifest_repository.HistoryManifestRepository"
    )
    def test_archive_task_snapshot_success(
        self,
        mock_repo_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Successful archive copies files and creates manifest."""
        service = _make_service(tmp_path)
        snapshot_id = "snap-001"
        source_dir = tmp_path / "source" / "tasks"
        source_dir.mkdir(parents=True)
        (source_dir / "task_1.json").write_text('{"id": "t1"}', encoding="utf-8")

        manifest = service.archive_task_snapshot(
            snapshot_id=snapshot_id,
            source_tasks_dir=str(source_dir),
            reason="completed",
        )

        assert manifest.id == snapshot_id
        assert manifest.scope == "task_snapshot"
        assert manifest.reason == "completed"
        assert manifest.file_count == 2  # task_1.json + manifest.json
        assert manifest.total_size_bytes > 0
        assert manifest.content_hash != ""
        assert manifest.target_path == f"tasks/{snapshot_id}"
        mock_repo_cls.return_value.append_task_entry.assert_called_once()

    @patch(
        "polaris.cells.archive.run_archive.internal.history_manifest_repository.HistoryManifestRepository"
    )
    def test_archive_task_snapshot_missing_source_returns_empty_manifest(
        self,
        mock_repo_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Missing source directory returns empty manifest."""
        service = _make_service(tmp_path)
        snapshot_id = "snap-missing"

        manifest = service.archive_task_snapshot(
            snapshot_id=snapshot_id,
            source_tasks_dir=str(tmp_path / "nonexistent"),
            reason="test",
        )

        assert manifest.id == snapshot_id
        assert manifest.file_count == 0
        assert manifest.total_size_bytes == 0
        assert manifest.content_hash == ""
        assert manifest.reason == "test"
        mock_repo_cls.return_value.append_task_entry.assert_not_called()

    def test_archive_task_snapshot_empty_id_raises(self, tmp_path: Path) -> None:
        """Empty snapshot_id raises ValueError."""
        service = _make_service(tmp_path)
        with pytest.raises(ValueError, match="snapshot_id is required"):
            service.archive_task_snapshot(snapshot_id="")

    def test_archive_task_snapshot_whitespace_id_raises(self, tmp_path: Path) -> None:
        """Whitespace-only snapshot_id raises ValueError."""
        service = _make_service(tmp_path)
        with pytest.raises(ValueError, match="snapshot_id is required"):
            service.archive_task_snapshot(snapshot_id="   ")

    @patch(
        "polaris.cells.archive.run_archive.internal.history_manifest_repository.HistoryManifestRepository"
    )
    def test_archive_task_snapshot_with_plan_file(
        self,
        mock_repo_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Plan file is copied to target directory when provided."""
        service = _make_service(tmp_path)
        snapshot_id = "snap-002"
        source_dir = tmp_path / "source" / "tasks"
        source_dir.mkdir(parents=True)
        (source_dir / "task_1.json").write_text('{"id": "t1"}', encoding="utf-8")
        plan_path = tmp_path / "source" / "plan.json"
        plan_path.write_text('{"tasks": []}', encoding="utf-8")

        manifest = service.archive_task_snapshot(
            snapshot_id=snapshot_id,
            source_tasks_dir=str(source_dir),
            source_plan_path=str(plan_path),
            reason="completed",
        )

        assert manifest.id == snapshot_id
        target_plan = service.history_root / "tasks" / snapshot_id / "plan.json"
        assert target_plan.exists()
        assert json.loads(target_plan.read_text(encoding="utf-8")) == {"tasks": []}

    @patch(
        "polaris.cells.archive.run_archive.internal.history_manifest_repository.HistoryManifestRepository"
    )
    def test_archive_task_snapshot_missing_plan_ignored(
        self,
        mock_repo_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Missing plan file is silently ignored."""
        service = _make_service(tmp_path)
        snapshot_id = "snap-003"
        source_dir = tmp_path / "source" / "tasks"
        source_dir.mkdir(parents=True)
        (source_dir / "task_1.json").write_text('{"id": "t1"}', encoding="utf-8")

        manifest = service.archive_task_snapshot(
            snapshot_id=snapshot_id,
            source_tasks_dir=str(source_dir),
            source_plan_path=str(tmp_path / "nonexistent" / "plan.json"),
            reason="completed",
        )

        assert manifest.id == snapshot_id
        target_plan = service.history_root / "tasks" / snapshot_id / "plan.json"
        assert not target_plan.exists()

    @patch(
        "polaris.cells.archive.run_archive.internal.history_manifest_repository.HistoryManifestRepository"
    )
    def test_archive_task_snapshot_plan_is_directory_ignored(
        self,
        mock_repo_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Plan path pointing to directory is ignored."""
        service = _make_service(tmp_path)
        snapshot_id = "snap-004"
        source_dir = tmp_path / "source" / "tasks"
        source_dir.mkdir(parents=True)
        (source_dir / "task_1.json").write_text('{"id": "t1"}', encoding="utf-8")
        plan_dir = tmp_path / "source" / "plan.json"
        plan_dir.mkdir()  # Directory named plan.json

        manifest = service.archive_task_snapshot(
            snapshot_id=snapshot_id,
            source_tasks_dir=str(source_dir),
            source_plan_path=str(plan_dir),
            reason="completed",
        )

        assert manifest.id == snapshot_id
        target_plan = service.history_root / "tasks" / snapshot_id / "plan.json"
        assert not target_plan.exists()

    @patch(
        "polaris.cells.archive.run_archive.internal.history_manifest_repository.HistoryManifestRepository"
    )
    def test_archive_task_snapshot_custom_reason(
        self,
        mock_repo_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Custom reason is preserved in manifest."""
        service = _make_service(tmp_path)
        snapshot_id = "snap-005"
        source_dir = tmp_path / "source" / "tasks"
        source_dir.mkdir(parents=True)
        (source_dir / "file.txt").write_text("data", encoding="utf-8")

        manifest = service.archive_task_snapshot(
            snapshot_id=snapshot_id,
            source_tasks_dir=str(source_dir),
            reason="integration_test",
        )

        assert manifest.reason == "integration_test"

    @patch(
        "polaris.cells.archive.run_archive.internal.history_manifest_repository.HistoryManifestRepository"
    )
    def test_archive_task_snapshot_default_source_dir(
        self,
        mock_repo_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Default source dir uses runtime_root/tasks."""
        service = _make_service(tmp_path)
        snapshot_id = "snap-006"
        default_source = tmp_path / ".polaris" / "runtime" / "tasks"
        default_source.mkdir(parents=True)
        (default_source / "task.json").write_text('{"x": 1}', encoding="utf-8")

        manifest = service.archive_task_snapshot(snapshot_id=snapshot_id)

        assert manifest.id == snapshot_id
        assert manifest.file_count == 2  # task.json + manifest.json

    @patch(
        "polaris.cells.archive.run_archive.internal.history_manifest_repository.HistoryManifestRepository"
    )
    def test_archive_task_snapshot_overwrites_existing(
        self,
        mock_repo_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Re-archiving overwrites existing target directory."""
        service = _make_service(tmp_path)
        snapshot_id = "snap-007"
        source_dir = tmp_path / "source" / "tasks"
        source_dir.mkdir(parents=True)
        (source_dir / "v1.txt").write_text("version1", encoding="utf-8")

        service.archive_task_snapshot(
            snapshot_id=snapshot_id,
            source_tasks_dir=str(source_dir),
        )

        # Update source and re-archive
        (source_dir / "v1.txt").write_text("version2", encoding="utf-8")
        manifest = service.archive_task_snapshot(
            snapshot_id=snapshot_id,
            source_tasks_dir=str(source_dir),
        )

        target_file = service.history_root / "tasks" / snapshot_id / "v1.txt"
        assert target_file.read_text(encoding="utf-8") == "version2"
        assert manifest.file_count == 2

    @patch(
        "polaris.cells.archive.run_archive.internal.history_manifest_repository.HistoryManifestRepository"
    )
    def test_archive_task_snapshot_calculates_checksums(
        self,
        mock_repo_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Archive calculates SHA256 checksum of all files."""
        service = _make_service(tmp_path)
        snapshot_id = "snap-008"
        source_dir = tmp_path / "source" / "tasks"
        source_dir.mkdir(parents=True)
        (source_dir / "a.txt").write_text("alpha", encoding="utf-8")
        (source_dir / "b.txt").write_text("beta", encoding="utf-8")

        manifest = service.archive_task_snapshot(
            snapshot_id=snapshot_id,
            source_tasks_dir=str(source_dir),
        )

        assert manifest.content_hash != ""
        assert len(manifest.content_hash) == 64  # SHA256 hex length

    @patch(
        "polaris.cells.archive.run_archive.internal.history_manifest_repository.HistoryManifestRepository"
    )
    def test_archive_task_snapshot_manifest_persisted(
        self,
        mock_repo_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Manifest JSON is written to target directory."""
        service = _make_service(tmp_path)
        snapshot_id = "snap-009"
        source_dir = tmp_path / "source" / "tasks"
        source_dir.mkdir(parents=True)
        (source_dir / "file.txt").write_text("data", encoding="utf-8")

        service.archive_task_snapshot(
            snapshot_id=snapshot_id,
            source_tasks_dir=str(source_dir),
        )

        manifest_path = service.history_root / "tasks" / snapshot_id / "manifest.json"
        assert manifest_path.exists()
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert payload["id"] == snapshot_id
        assert payload["scope"] == "task_snapshot"

    @patch(
        "polaris.cells.archive.run_archive.internal.history_manifest_repository.HistoryManifestRepository"
    )
    def test_archive_task_snapshot_empty_reason_defaults_to_completed(
        self,
        mock_repo_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Empty reason defaults to 'completed'."""
        service = _make_service(tmp_path)
        snapshot_id = "snap-010"
        source_dir = tmp_path / "source" / "tasks"
        source_dir.mkdir(parents=True)
        (source_dir / "file.txt").write_text("data", encoding="utf-8")

        manifest = service.archive_task_snapshot(
            snapshot_id=snapshot_id,
            source_tasks_dir=str(source_dir),
            reason="",
        )

        assert manifest.reason == "completed"


class TestGetManifest:
    """Test suite for get_manifest method."""

    def test_get_manifest_existing(self, tmp_path: Path) -> None:
        """Loads manifest from existing archive."""
        service = _make_service(tmp_path)
        snapshot_id = "snap-011"
        target_dir = service.history_root / "tasks" / snapshot_id
        target_dir.mkdir(parents=True)
        manifest_data = {
            "scope": "task_snapshot",
            "id": snapshot_id,
            "archive_timestamp": 1234567890.0,
            "archive_datetime": "2009-02-13T23:31:30+00:00",
            "source_runtime_root": str(service.runtime_root),
            "source_paths": ["tasks"],
            "target_path": f"tasks/{snapshot_id}",
            "total_size_bytes": 100,
            "file_count": 1,
            "content_hash": "abcd1234",
            "reason": "completed",
            "compressed": False,
            "compression_ratio": 1.0,
        }
        (target_dir / "manifest.json").write_text(
            json.dumps(manifest_data, ensure_ascii=False),
            encoding="utf-8",
        )

        manifest = service.get_manifest(snapshot_id)
        assert manifest is not None
        assert manifest.id == snapshot_id
        assert manifest.total_size_bytes == 100

    def test_get_manifest_missing_returns_none(self, tmp_path: Path) -> None:
        """Returns None for non-existent manifest."""
        service = _make_service(tmp_path)
        result = service.get_manifest("nonexistent")
        assert result is None

    def test_get_manifest_empty_id_returns_none(self, tmp_path: Path) -> None:
        """Empty snapshot_id returns None."""
        service = _make_service(tmp_path)
        result = service.get_manifest("")
        assert result is None

    def test_get_manifest_invalid_json_returns_none(
        self,
        tmp_path: Path,
    ) -> None:
        """Invalid JSON manifest returns None."""
        service = _make_service(tmp_path)
        snapshot_id = "snap-bad"
        target_dir = service.history_root / "tasks" / snapshot_id
        target_dir.mkdir(parents=True)
        (target_dir / "manifest.json").write_text("not json", encoding="utf-8")

        result = service.get_manifest(snapshot_id)
        assert result is None

    def test_get_manifest_non_dict_payload_returns_none(
        self,
        tmp_path: Path,
    ) -> None:
        """Non-dict JSON payload returns None."""
        service = _make_service(tmp_path)
        snapshot_id = "snap-list"
        target_dir = service.history_root / "tasks" / snapshot_id
        target_dir.mkdir(parents=True)
        (target_dir / "manifest.json").write_text("[1, 2, 3]", encoding="utf-8")

        result = service.get_manifest(snapshot_id)
        assert result is None


class TestListTaskSnapshots:
    """Test suite for list_task_snapshots method."""

    @patch(
        "polaris.cells.archive.run_archive.internal.history_manifest_repository.HistoryManifestRepository"
    )
    def test_list_task_snapshots_default(
        self,
        mock_repo_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Default limit and offset are applied."""
        service = _make_service(tmp_path)
        mock_repo = mock_repo_cls.return_value
        mock_repo.read_tasks_index.return_value = []

        result = service.list_task_snapshots()

        assert result == []
        mock_repo.read_tasks_index.assert_called_once_with(limit=50, offset=0)

    @patch(
        "polaris.cells.archive.run_archive.internal.history_manifest_repository.HistoryManifestRepository"
    )
    def test_list_task_snapshots_with_limit_offset(
        self,
        mock_repo_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Custom limit and offset are forwarded."""
        service = _make_service(tmp_path)
        mock_repo = mock_repo_cls.return_value
        mock_repo.read_tasks_index.return_value = []

        service.list_task_snapshots(limit=10, offset=5)

        mock_repo.read_tasks_index.assert_called_once_with(limit=10, offset=5)

    @patch(
        "polaris.cells.archive.run_archive.internal.history_manifest_repository.HistoryManifestRepository"
    )
    def test_list_task_snapshots_negative_values_clamped(
        self,
        mock_repo_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Negative limit and offset are clamped to zero."""
        service = _make_service(tmp_path)
        mock_repo = mock_repo_cls.return_value
        mock_repo.read_tasks_index.return_value = []

        service.list_task_snapshots(limit=-5, offset=-1)

        mock_repo.read_tasks_index.assert_called_once_with(limit=0, offset=0)


class TestTaskSnapshotArchiveManifest:
    """Test suite for TaskSnapshotArchiveManifest dataclass."""

    def test_to_dict(self) -> None:
        """to_dict serializes all fields."""
        manifest = TaskSnapshotArchiveManifest(
            scope="task_snapshot",
            id="test-001",
            archive_timestamp=1234567890.0,
            archive_datetime="2009-02-13T23:31:30+00:00",
            source_runtime_root="/workspace",
            source_paths=["tasks"],
            target_path="tasks/test-001",
            total_size_bytes=100,
            file_count=1,
            content_hash="abcd",
            reason="completed",
        )
        d = manifest.to_dict()
        assert d["scope"] == "task_snapshot"
        assert d["id"] == "test-001"
        assert d["total_size_bytes"] == 100
        assert d["compressed"] is False

    def test_from_dict(self) -> None:
        """from_dict deserializes all fields."""
        payload: dict[str, Any] = {
            "scope": "task_snapshot",
            "id": "test-002",
            "archive_timestamp": 1234567890.0,
            "archive_datetime": "2009-02-13T23:31:30+00:00",
            "source_runtime_root": "/workspace",
            "source_paths": ["tasks"],
            "target_path": "tasks/test-002",
            "total_size_bytes": 200,
            "file_count": 2,
            "content_hash": "efgh",
            "reason": "test",
            "compressed": True,
            "compression_ratio": 0.5,
        }
        manifest = TaskSnapshotArchiveManifest.from_dict(payload)
        assert manifest.id == "test-002"
        assert manifest.compressed is True
        assert manifest.compression_ratio == 0.5

    def test_manifest_defaults(self) -> None:
        """Default values for compressed and compression_ratio."""
        manifest = TaskSnapshotArchiveManifest(
            scope="task_snapshot",
            id="test-003",
            archive_timestamp=0.0,
            archive_datetime="",
            source_runtime_root="",
            source_paths=[],
            target_path="",
            total_size_bytes=0,
            file_count=0,
            content_hash="",
            reason="",
        )
        assert manifest.compressed is False
        assert manifest.compression_ratio == 1.0


class TestHelperMethods:
    """Test suite for internal helper methods."""

    def test_safe_rel_inside_root(self) -> None:
        """_safe_rel returns relative path when inside root."""
        path = Path("/workspace/history/tasks/snap-001")
        root = Path("/workspace/history")
        result = TaskSnapshotArchiveService._safe_rel(path, root)
        assert result == "tasks/snap-001"

    def test_safe_rel_outside_root(self) -> None:
        """_safe_rel returns absolute path when outside root."""
        path = Path("/other/path")
        root = Path("/workspace/history")
        result = TaskSnapshotArchiveService._safe_rel(path, root)
        assert result == str(path)

    def test_safe_rel_with_oserror(self, tmp_path: Path) -> None:
        """_safe_rel handles OSError gracefully."""
        path = Path("C:/other/path")
        root = Path("D:/workspace/history")
        result = TaskSnapshotArchiveService._safe_rel(path, root)
        assert result == str(path)


class TestModuleExports:
    """Test suite for module-level exports."""

    def test_all_exports_defined(self) -> None:
        """__all__ contains expected public symbols."""
        from polaris.cells.archive.task_snapshot_archive.internal import (
            task_snapshot_archive_service,
        )

        assert "TaskSnapshotArchiveManifest" in task_snapshot_archive_service.__all__
        assert "TaskSnapshotArchiveService" in task_snapshot_archive_service.__all__
