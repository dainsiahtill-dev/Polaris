"""Tests for polaris.cells.audit.evidence.bundle_service.

Covers EvidenceBundleService with mocked filesystem and git operations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from polaris.cells.audit.evidence.bundle_service import (
    EvidenceBundleService,
    _get_bundle_index_path,
    _get_bundle_storage_path,
    _workspace_fs,
    create_evidence_bundle_service,
    get_evidence_bundle_service,
)
from polaris.domain.entities.evidence_bundle import ChangeType, EvidenceBundle, FileChange, SourceType


class TestBundlePathHelpers:
    """Pure path helper functions."""

    def test_get_bundle_storage_path(self) -> None:
        path = _get_bundle_storage_path("/ws", "b1")
        assert "bundles" in str(path)
        assert "b1" in str(path)

    def test_get_bundle_index_path(self) -> None:
        path = _get_bundle_index_path("/ws")
        assert "evidence_index.jsonl" in str(path)

    def test_workspace_fs(self) -> None:
        fs = _workspace_fs("/ws")
        assert fs is not None


class TestEvidenceBundleService:
    """EvidenceBundleService tests with mocked dependencies."""

    @pytest.fixture
    def service(self) -> EvidenceBundleService:
        return EvidenceBundleService()

    def test_create_from_working_tree_mocked(self, service: EvidenceBundleService, tmp_path: Path) -> None:
        workspace = str(tmp_path)

        with (
            patch.object(service, "_collect_working_tree_changes", return_value=[]) as mock_collect,
            patch.object(service, "_save_bundle") as mock_save,
            patch.object(service, "_update_index") as mock_update,
            patch.object(service, "_get_current_commit", return_value="abc123"),
        ):
            bundle = service.create_from_working_tree(
                workspace=workspace,
                base_sha="abc123",
                source_type=SourceType.DIRECTOR_RUN,
            )

            assert isinstance(bundle, EvidenceBundle)
            assert bundle.workspace == str(tmp_path.resolve())
            assert bundle.base_sha == "abc123"
            assert bundle.source_type == SourceType.DIRECTOR_RUN
            mock_collect.assert_called_once()
            mock_save.assert_called_once()
            mock_update.assert_called_once()

    def test_create_from_director_run_mocked(self, service: EvidenceBundleService, tmp_path: Path) -> None:
        workspace = str(tmp_path)

        with (
            patch.object(service, "_collect_changes_from_tasks", return_value=[]) as mock_collect,
            patch.object(service, "_save_bundle") as mock_save,
            patch.object(service, "_update_index") as mock_update,
            patch.object(service, "_get_current_commit", return_value="def456"),
        ):
            bundle = service.create_from_director_run(
                workspace=workspace,
                run_id="run-1",
                task_results=[],
            )

            assert isinstance(bundle, EvidenceBundle)
            assert bundle.source_run_id == "run-1"
            assert bundle.source_type == SourceType.DIRECTOR_RUN
            mock_collect.assert_called_once()
            mock_save.assert_called_once()
            mock_update.assert_called_once()

    def test_get_bundle_not_found(self, service: EvidenceBundleService, tmp_path: Path) -> None:
        workspace = str(tmp_path)
        result = service.get_bundle(workspace, "nonexistent")
        assert result is None

    def test_list_bundles_no_index(self, service: EvidenceBundleService, tmp_path: Path) -> None:
        workspace = str(tmp_path)
        with patch("polaris.cells.audit.evidence.bundle_service._workspace_fs") as mock_fs:
            mock_fs.return_value.workspace_exists.return_value = False
            result = service.list_bundles(workspace)
        assert result == []

    def test_list_bundles_with_entries(self, service: EvidenceBundleService, tmp_path: Path) -> None:
        workspace = str(tmp_path)
        entries = [
            {"bundle_id": "b1", "source_type": "director_run", "source_run_id": "r1"},
            {"bundle_id": "b2", "source_type": "manual", "source_run_id": None},
        ]
        with patch("polaris.cells.audit.evidence.bundle_service._workspace_fs") as mock_fs:
            mock_fs.return_value.workspace_exists.return_value = True
            mock_fs.return_value.workspace_is_file.return_value = True
            mock_fs.return_value.workspace_read_text.return_value = (
                "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n"
            )
            result = service.list_bundles(workspace)
        assert len(result) == 2
        assert result[0]["bundle_id"] == "b1"

    def test_list_bundles_filtered_by_source_type(self, service: EvidenceBundleService, tmp_path: Path) -> None:
        workspace = str(tmp_path)
        entries = [
            {"bundle_id": "b1", "source_type": "director_run", "source_run_id": "r1"},
            {"bundle_id": "b2", "source_type": "manual", "source_run_id": None},
        ]
        with patch("polaris.cells.audit.evidence.bundle_service._workspace_fs") as mock_fs:
            mock_fs.return_value.workspace_exists.return_value = True
            mock_fs.return_value.workspace_is_file.return_value = True
            mock_fs.return_value.workspace_read_text.return_value = (
                "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n"
            )
            result = service.list_bundles(workspace, source_type=SourceType.DIRECTOR_RUN)
        assert len(result) == 1
        assert result[0]["bundle_id"] == "b1"

    def test_list_bundles_filtered_by_run_id(self, service: EvidenceBundleService, tmp_path: Path) -> None:
        workspace = str(tmp_path)
        entries = [
            {"bundle_id": "b1", "source_type": "director_run", "source_run_id": "r1"},
            {"bundle_id": "b2", "source_type": "director_run", "source_run_id": "r2"},
        ]
        with patch("polaris.cells.audit.evidence.bundle_service._workspace_fs") as mock_fs:
            mock_fs.return_value.workspace_exists.return_value = True
            mock_fs.return_value.workspace_is_file.return_value = True
            mock_fs.return_value.workspace_read_text.return_value = (
                "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n"
            )
            result = service.list_bundles(workspace, source_run_id="r1")
        assert len(result) == 1
        assert result[0]["bundle_id"] == "b1"

    def test_list_bundles_limit(self, service: EvidenceBundleService, tmp_path: Path) -> None:
        workspace = str(tmp_path)
        entries = [{"bundle_id": f"b{i}", "source_type": "director_run"} for i in range(10)]
        with patch("polaris.cells.audit.evidence.bundle_service._workspace_fs") as mock_fs:
            mock_fs.return_value.workspace_exists.return_value = True
            mock_fs.return_value.workspace_is_file.return_value = True
            mock_fs.return_value.workspace_read_text.return_value = (
                "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n"
            )
            result = service.list_bundles(workspace, limit=3)
        assert len(result) == 3

    def test_list_bundles_skips_bad_json(self, service: EvidenceBundleService, tmp_path: Path) -> None:
        workspace = str(tmp_path)
        with patch("polaris.cells.audit.evidence.bundle_service._workspace_fs") as mock_fs:
            mock_fs.return_value.workspace_exists.return_value = True
            mock_fs.return_value.workspace_is_file.return_value = True
            mock_fs.return_value.workspace_read_text.return_value = (
                json.dumps({"bundle_id": "b1", "source_type": "director_run"}) + "\nnot json\n"
            )
            result = service.list_bundles(workspace)
        assert len(result) == 1

    def test_collect_changes_from_tasks(self, service: EvidenceBundleService) -> None:
        task_results = [
            {
                "file_changes": [
                    {"path": "a.py", "change_type": "added", "lines_added": 10, "lines_deleted": 0},
                    {"path": "b.py", "change_type": "modified", "lines_added": 5, "lines_deleted": 2},
                ]
            },
            {
                "file_changes": [
                    {"path": "a.py", "change_type": "deleted"},  # Duplicate path skipped
                ]
            },
        ]

        changes = service._collect_changes_from_tasks("/ws", task_results)
        assert len(changes) == 2
        assert changes[0].path == "a.py"
        assert changes[0].change_type == ChangeType.ADDED
        assert changes[1].path == "b.py"

    def test_get_current_commit_success(self, service: EvidenceBundleService, tmp_path: Path) -> None:
        # Initialize a git repo
        import subprocess

        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=False)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True, check=False)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True, check=False)
        (tmp_path / "file.txt").write_text("hello", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, check=False)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True, check=False)

        sha = service._get_current_commit(str(tmp_path))
        assert len(sha) == 40  # Full SHA

    def test_get_current_commit_failure(self, service: EvidenceBundleService, tmp_path: Path) -> None:
        # Not a git repo
        sha = service._get_current_commit(str(tmp_path))
        assert sha == "sha-unknown"

    def test_save_bundle(self, service: EvidenceBundleService, tmp_path: Path) -> None:
        bundle = EvidenceBundle(
            bundle_id="b1",
            workspace=str(tmp_path),
            base_sha="abc",
            head_sha=None,
            working_tree_dirty=True,
            change_set=[],
            source_type=SourceType.DIRECTOR_RUN,
        )
        storage_path = tmp_path / "bundles" / "b1"
        storage_path.mkdir(parents=True)

        service._save_bundle(bundle, storage_path)
        bundle_file = storage_path / "bundle.json"
        assert bundle_file.exists()
        data = json.loads(bundle_file.read_text(encoding="utf-8"))
        assert data["bundle_id"] == "b1"

    def test_update_index(self, service: EvidenceBundleService, tmp_path: Path) -> None:
        bundle = EvidenceBundle(
            bundle_id="b1",
            workspace=str(tmp_path),
            base_sha="abc",
            head_sha=None,
            working_tree_dirty=True,
            change_set=[FileChange(path="a.py", change_type=ChangeType.ADDED)],
            source_type=SourceType.DIRECTOR_RUN,
            source_run_id="r1",
        )

        # Mock _workspace_fs so index path resolution stays inside workspace boundary
        mock_fs = MagicMock()
        captured_text = ""

        def capture_append(rel_path: str, text: str, **kwargs: Any) -> None:
            nonlocal captured_text
            captured_text += text

        mock_fs.workspace_append_text.side_effect = capture_append
        with patch("polaris.cells.audit.evidence.bundle_service._workspace_fs", return_value=mock_fs):
            service._update_index(str(tmp_path), bundle)

        assert captured_text != ""
        lines = captured_text.strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["bundle_id"] == "b1"
        assert entry["source_run_id"] == "r1"
        assert "a.py" in entry["affected_files"]


class TestFactoryFunctions:
    """Factory and alias tests."""

    def test_create_evidence_bundle_service(self) -> None:
        service = create_evidence_bundle_service()
        assert isinstance(service, EvidenceBundleService)

    def test_get_evidence_bundle_service_alias(self) -> None:
        service = get_evidence_bundle_service()
        assert isinstance(service, EvidenceBundleService)
