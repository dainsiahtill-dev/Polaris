"""Tests for `polaris.cells.archive.factory_archive.internal.factory_archive_service`."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from polaris.cells.archive.factory_archive.internal.factory_archive_service import (
    FactoryArchiveManifest,
    FactoryArchiveService,
)


class FakeStorageRoots:
    """Fake return value for resolve_storage_roots."""

    def __init__(self, tmp_path: Path) -> None:
        self.history_root = str(tmp_path / ".polaris" / "history")
        self.runtime_root = str(tmp_path / ".polaris" / "runtime")
        self.workspace_persistent_root = str(tmp_path / ".polaris")


def _make_service(tmp_path: Path) -> FactoryArchiveService:
    """Helper to create a FactoryArchiveService with mocked storage roots."""
    fake_roots = FakeStorageRoots(tmp_path)
    with patch(
        "polaris.kernelone.storage.resolve_storage_roots",
        return_value=fake_roots,
    ):
        return FactoryArchiveService(str(tmp_path))


class TestFactoryArchiveServiceInit:
    """Test suite for FactoryArchiveService initialization."""

    def test_init_requires_workspace(self, tmp_path: Path) -> None:
        """Service initializes with valid workspace path."""
        service = _make_service(tmp_path)
        assert service.workspace == tmp_path.resolve()

    def test_init_empty_workspace_raises(self) -> None:
        """Empty workspace string raises ValueError."""
        with pytest.raises(ValueError, match="workspace is required"):
            FactoryArchiveService("")

    def test_init_whitespace_workspace_raises(self) -> None:
        """Whitespace-only workspace string raises ValueError."""
        with pytest.raises(ValueError, match="workspace is required"):
            FactoryArchiveService("   ")

    def test_init_none_workspace_raises(self) -> None:
        """None workspace coerced to empty string raises ValueError."""
        with pytest.raises(ValueError, match="workspace is required"):
            FactoryArchiveService(None)  # type: ignore[arg-type]

    def test_init_creates_index_dir(self, tmp_path: Path) -> None:
        """Initialization creates history/index directory."""
        service = _make_service(tmp_path)
        assert service.index_dir.exists()
        assert service.index_dir.name == "index"

    def test_init_sets_paths_from_roots(self, tmp_path: Path) -> None:
        """Service sets history_root and workspace_persistent_root from roots."""
        service = _make_service(tmp_path)
        assert str(service.history_root) == FakeStorageRoots(tmp_path).history_root
        assert (
            str(service.workspace_persistent_root)
            == FakeStorageRoots(tmp_path).workspace_persistent_root
        )


class TestArchiveFactoryRun:
    """Test suite for archive_factory_run method."""

    @patch(
        "polaris.cells.archive.factory_archive.internal.factory_archive_service.HistoryManifestRepository"
    )
    def test_archive_factory_run_success(
        self,
        mock_repo_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Successful archive copies files and creates manifest."""
        service = _make_service(tmp_path)
        run_id = "factory-001"
        source_dir = tmp_path / ".polaris" / "factory" / run_id
        source_dir.mkdir(parents=True)
        (source_dir / "config.json").write_text('{"name": "test"}', encoding="utf-8")

        manifest = service.archive_factory_run(
            factory_run_id=run_id,
            source_factory_dir=str(source_dir),
            reason="completed",
        )

        assert manifest.id == run_id
        assert manifest.scope == "factory_run"
        assert manifest.reason == "completed"
        assert manifest.file_count == 1  # config.json only (manifest written after)
        assert manifest.total_size_bytes > 0
        assert manifest.content_hash != ""
        assert manifest.target_path == f"factory{os.sep}{run_id}"
        mock_repo_cls.return_value.append_factory_entry.assert_called_once()

    @patch(
        "polaris.cells.archive.factory_archive.internal.factory_archive_service.HistoryManifestRepository"
    )
    def test_archive_factory_run_missing_source_returns_empty_manifest(
        self,
        mock_repo_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Missing source directory returns empty manifest."""
        service = _make_service(tmp_path)
        run_id = "factory-missing"

        manifest = service.archive_factory_run(
            factory_run_id=run_id,
            source_factory_dir=str(tmp_path / "nonexistent"),
            reason="test",
        )

        assert manifest.id == run_id
        assert manifest.file_count == 0
        assert manifest.total_size_bytes == 0
        assert manifest.content_hash == ""
        assert manifest.reason == "test"
        mock_repo_cls.return_value.append_factory_entry.assert_not_called()

    def test_archive_factory_run_empty_id_raises(self, tmp_path: Path) -> None:
        """Empty factory_run_id raises ValueError."""
        service = _make_service(tmp_path)
        with pytest.raises(ValueError, match="factory_run_id is required"):
            service.archive_factory_run(factory_run_id="")

    def test_archive_factory_run_whitespace_id_raises(self, tmp_path: Path) -> None:
        """Whitespace-only factory_run_id raises ValueError."""
        service = _make_service(tmp_path)
        with pytest.raises(ValueError, match="factory_run_id is required"):
            service.archive_factory_run(factory_run_id="   ")

    @patch(
        "polaris.cells.archive.factory_archive.internal.factory_archive_service.HistoryManifestRepository"
    )
    def test_archive_factory_run_custom_reason(
        self,
        mock_repo_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Custom reason is preserved in manifest."""
        service = _make_service(tmp_path)
        run_id = "factory-002"
        source_dir = tmp_path / ".polaris" / "factory" / run_id
        source_dir.mkdir(parents=True)
        (source_dir / "file.txt").write_text("data", encoding="utf-8")

        manifest = service.archive_factory_run(
            factory_run_id=run_id,
            source_factory_dir=str(source_dir),
            reason="integration_test",
        )

        assert manifest.reason == "integration_test"

    @patch(
        "polaris.cells.archive.factory_archive.internal.factory_archive_service.HistoryManifestRepository"
    )
    def test_archive_factory_run_default_source_dir(
        self,
        mock_repo_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Default source dir uses workspace_persistent_root/factory/{run_id}."""
        service = _make_service(tmp_path)
        run_id = "factory-003"
        default_source = tmp_path / ".polaris" / "factory" / run_id
        default_source.mkdir(parents=True)
        (default_source / "data.json").write_text('{"x": 1}', encoding="utf-8")

        manifest = service.archive_factory_run(factory_run_id=run_id)

        assert manifest.id == run_id
        assert manifest.file_count == 1  # data.json only

    @patch(
        "polaris.cells.archive.factory_archive.internal.factory_archive_service.HistoryManifestRepository"
    )
    def test_archive_factory_run_overwrites_existing(
        self,
        mock_repo_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Re-archiving overwrites existing target directory."""
        service = _make_service(tmp_path)
        run_id = "factory-004"
        source_dir = tmp_path / ".polaris" / "factory" / run_id
        source_dir.mkdir(parents=True)
        (source_dir / "v1.txt").write_text("version1", encoding="utf-8")

        service.archive_factory_run(
            factory_run_id=run_id,
            source_factory_dir=str(source_dir),
        )

        # Update source and re-archive
        (source_dir / "v1.txt").write_text("version2", encoding="utf-8")
        manifest = service.archive_factory_run(
            factory_run_id=run_id,
            source_factory_dir=str(source_dir),
        )

        target_file = service.history_root / "factory" / run_id / "v1.txt"
        assert target_file.read_text(encoding="utf-8") == "version2"
        assert manifest.file_count == 1

    @patch(
        "polaris.cells.archive.factory_archive.internal.factory_archive_service.HistoryManifestRepository"
    )
    def test_archive_factory_run_calculates_checksums(
        self,
        mock_repo_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Archive calculates SHA256 checksum of all files."""
        service = _make_service(tmp_path)
        run_id = "factory-005"
        source_dir = tmp_path / ".polaris" / "factory" / run_id
        source_dir.mkdir(parents=True)
        (source_dir / "a.txt").write_text("alpha", encoding="utf-8")
        (source_dir / "b.txt").write_text("beta", encoding="utf-8")

        manifest = service.archive_factory_run(
            factory_run_id=run_id,
            source_factory_dir=str(source_dir),
        )

        assert manifest.content_hash != ""
        assert len(manifest.content_hash) == 64  # SHA256 hex length

    @patch(
        "polaris.cells.archive.factory_archive.internal.factory_archive_service.HistoryManifestRepository"
    )
    def test_archive_factory_run_manifest_persisted(
        self,
        mock_repo_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Manifest JSON is written to target directory."""
        service = _make_service(tmp_path)
        run_id = "factory-006"
        source_dir = tmp_path / ".polaris" / "factory" / run_id
        source_dir.mkdir(parents=True)
        (source_dir / "file.txt").write_text("data", encoding="utf-8")

        service.archive_factory_run(
            factory_run_id=run_id,
            source_factory_dir=str(source_dir),
        )

        manifest_path = service.history_root / "factory" / run_id / "manifest.json"
        assert manifest_path.exists()
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert payload["id"] == run_id
        assert payload["scope"] == "factory_run"

    @patch(
        "polaris.cells.archive.factory_archive.internal.factory_archive_service.HistoryManifestRepository"
    )
    def test_archive_factory_run_empty_reason_defaults_to_completed(
        self,
        mock_repo_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Empty reason defaults to 'completed'."""
        service = _make_service(tmp_path)
        run_id = "factory-007"
        source_dir = tmp_path / ".polaris" / "factory" / run_id
        source_dir.mkdir(parents=True)
        (source_dir / "file.txt").write_text("data", encoding="utf-8")

        manifest = service.archive_factory_run(
            factory_run_id=run_id,
            source_factory_dir=str(source_dir),
            reason="",
        )

        assert manifest.reason == "completed"


class TestGetManifest:
    """Test suite for get_manifest method."""

    def test_get_manifest_existing(self, tmp_path: Path) -> None:
        """Loads manifest from existing archive."""
        service = _make_service(tmp_path)
        run_id = "factory-008"
        target_dir = service.history_root / "factory" / run_id
        target_dir.mkdir(parents=True)
        manifest_data = {
            "scope": "factory_run",
            "id": run_id,
            "archive_timestamp": 1234567890.0,
            "archive_datetime": "2009-02-13T23:31:30+00:00",
            "source_runtime_root": str(service.workspace_persistent_root),
            "source_paths": ["factory/test"],
            "target_path": f"factory{os.sep}{run_id}",
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

        manifest = service.get_manifest(run_id)
        assert manifest is not None
        assert manifest.id == run_id
        assert manifest.total_size_bytes == 100

    def test_get_manifest_missing_returns_none(self, tmp_path: Path) -> None:
        """Returns None for non-existent manifest."""
        service = _make_service(tmp_path)
        result = service.get_manifest("nonexistent")
        assert result is None

    def test_get_manifest_empty_id_returns_none(self, tmp_path: Path) -> None:
        """Empty run_id returns None."""
        service = _make_service(tmp_path)
        result = service.get_manifest("")
        assert result is None

    def test_get_manifest_invalid_json_returns_none(
        self,
        tmp_path: Path,
    ) -> None:
        """Invalid JSON manifest returns None."""
        service = _make_service(tmp_path)
        run_id = "factory-bad"
        target_dir = service.history_root / "factory" / run_id
        target_dir.mkdir(parents=True)
        # Mock KernelFileSystem read to return invalid JSON directly
        with patch.object(
            service._kernel_fs,
            "workspace_read_text",
            return_value="not json",
        ):
            result = service.get_manifest(run_id)
        assert result is None

    def test_get_manifest_non_dict_payload_returns_none(
        self,
        tmp_path: Path,
    ) -> None:
        """Non-dict JSON payload returns None."""
        service = _make_service(tmp_path)
        run_id = "factory-list"
        target_dir = service.history_root / "factory" / run_id
        target_dir.mkdir(parents=True)
        (target_dir / "manifest.json").write_text("[1, 2, 3]", encoding="utf-8")

        result = service.get_manifest(run_id)
        assert result is None


class TestListFactoryRuns:
    """Test suite for list_factory_runs method."""

    @patch(
        "polaris.cells.archive.factory_archive.internal.factory_archive_service.HistoryManifestRepository"
    )
    def test_list_factory_runs_default(
        self,
        mock_repo_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Default limit and offset are applied."""
        service = _make_service(tmp_path)
        mock_repo = mock_repo_cls.return_value
        mock_repo.read_factory_index.return_value = []

        result = service.list_factory_runs()

        assert result == []
        mock_repo.read_factory_index.assert_called_once_with(limit=50, offset=0)

    @patch(
        "polaris.cells.archive.factory_archive.internal.factory_archive_service.HistoryManifestRepository"
    )
    def test_list_factory_runs_with_limit_offset(
        self,
        mock_repo_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Custom limit and offset are forwarded."""
        service = _make_service(tmp_path)
        mock_repo = mock_repo_cls.return_value
        mock_repo.read_factory_index.return_value = []

        service.list_factory_runs(limit=10, offset=5)

        mock_repo.read_factory_index.assert_called_once_with(limit=10, offset=5)

    @patch(
        "polaris.cells.archive.factory_archive.internal.factory_archive_service.HistoryManifestRepository"
    )
    def test_list_factory_runs_negative_values_clamped(
        self,
        mock_repo_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Negative limit and offset are clamped to zero."""
        service = _make_service(tmp_path)
        mock_repo = mock_repo_cls.return_value
        mock_repo.read_factory_index.return_value = []

        service.list_factory_runs(limit=-5, offset=-1)

        mock_repo.read_factory_index.assert_called_once_with(limit=0, offset=0)


class TestFactoryArchiveManifest:
    """Test suite for FactoryArchiveManifest dataclass."""

    def test_to_dict(self) -> None:
        """to_dict serializes all fields."""
        manifest = FactoryArchiveManifest(
            scope="factory_run",
            id="test-001",
            archive_timestamp=1234567890.0,
            archive_datetime="2009-02-13T23:31:30+00:00",
            source_runtime_root="/workspace",
            source_paths=["factory/test"],
            target_path="factory/test-001",
            total_size_bytes=100,
            file_count=1,
            content_hash="abcd",
            reason="completed",
        )
        d = manifest.to_dict()
        assert d["scope"] == "factory_run"
        assert d["id"] == "test-001"
        assert d["total_size_bytes"] == 100
        assert d["compressed"] is False

    def test_from_dict(self) -> None:
        """from_dict deserializes all fields."""
        payload: dict[str, Any] = {
            "scope": "factory_run",
            "id": "test-002",
            "archive_timestamp": 1234567890.0,
            "archive_datetime": "2009-02-13T23:31:30+00:00",
            "source_runtime_root": "/workspace",
            "source_paths": ["factory/test"],
            "target_path": "factory/test-002",
            "total_size_bytes": 200,
            "file_count": 2,
            "content_hash": "efgh",
            "reason": "test",
            "compressed": True,
            "compression_ratio": 0.5,
        }
        manifest = FactoryArchiveManifest.from_dict(payload)
        assert manifest.id == "test-002"
        assert manifest.compressed is True
        assert manifest.compression_ratio == 0.5

    def test_manifest_defaults(self) -> None:
        """Default values for compressed and compression_ratio."""
        manifest = FactoryArchiveManifest(
            scope="factory_run",
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
        path = Path("/workspace/history/run-001")
        root = Path("/workspace/history")
        result = FactoryArchiveService._safe_rel(path, root)
        assert "run-001" in result

    def test_safe_rel_outside_root(self) -> None:
        """_safe_rel returns absolute path when outside root."""
        path = Path("/other/path")
        root = Path("/workspace/history")
        result = FactoryArchiveService._safe_rel(path, root)
        assert result == str(path)

    def test_safe_rel_different_drives(self, tmp_path: Path) -> None:
        """_safe_rel handles different drives gracefully (Windows)."""
        path = Path("C:/other/path")
        root = Path("D:/workspace/history")
        result = FactoryArchiveService._safe_rel(path, root)
        assert result == str(path)


class TestModuleExports:
    """Test suite for module-level exports."""

    def test_all_exports_defined(self) -> None:
        """__all__ contains expected public symbols."""
        from polaris.cells.archive.factory_archive.internal import (
            factory_archive_service,
        )

        assert "FactoryArchiveManifest" in factory_archive_service.__all__
        assert "FactoryArchiveService" in factory_archive_service.__all__
