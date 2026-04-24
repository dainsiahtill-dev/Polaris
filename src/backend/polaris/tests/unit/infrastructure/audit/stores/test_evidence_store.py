"""Tests for polaris.infrastructure.audit.stores.evidence_store."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from polaris.infrastructure.audit.stores.evidence_store import (
    EvidenceNotFoundError,
    EvidenceStore,
)


class TestEvidenceStore:
    def test_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EvidenceStore(tmpdir)
            assert store.runtime_root == Path(tmpdir).resolve()

    def test_get_task_evidence_dir_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EvidenceStore(tmpdir)
            evidence_dir = store._get_task_evidence_dir("task-123")
            assert evidence_dir.exists()
            assert "task-123" in str(evidence_dir)

    def test_get_task_evidence_dir_traversal_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EvidenceStore(tmpdir)
            with pytest.raises(ValueError, match="Invalid task_id"):
                store._get_task_evidence_dir("../etc/passwd")

    def test_get_task_evidence_dir_slash_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EvidenceStore(tmpdir)
            with pytest.raises(ValueError, match="Invalid task_id"):
                store._get_task_evidence_dir("foo/bar")

    def test_get_task_evidence_dir_backslash_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EvidenceStore(tmpdir)
            with pytest.raises(ValueError, match="Invalid task_id"):
                store._get_task_evidence_dir("foo\\bar")

    def test_save_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EvidenceStore(tmpdir)
            package = {
                "task_id": "task-save",
                "iteration": 0,
                "summary": "test evidence",
            }

            result = store.save_evidence(package)

            assert "evidence_path" in result
            assert result["task_id"] == "task-save"
            assert result["iteration"] == 0
            assert Path(result["evidence_path"]).exists()

    def test_save_evidence_with_ducktyping(self) -> None:
        """Test save_evidence accepts objects with to_dict method."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EvidenceStore(tmpdir)

            class MockPackage:
                def to_dict(self) -> dict:
                    return {
                        "task_id": "task-obj",
                        "iteration": 1,
                        "summary": "object evidence",
                    }

            result = store.save_evidence(MockPackage())
            assert result["task_id"] == "task-obj"

    def test_load_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EvidenceStore(tmpdir)
            package = {
                "task_id": "task-load",
                "iteration": 0,
                "summary": "load test",
            }
            store.save_evidence(package)

            loaded = store.load_evidence("task-load", 0)
            assert loaded["task_id"] == "task-load"

    def test_load_evidence_not_found_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EvidenceStore(tmpdir)
            with pytest.raises(EvidenceNotFoundError):
                store.load_evidence("nonexistent-task", 0)

    def test_load_latest_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EvidenceStore(tmpdir)

            # Save multiple iterations
            for i in range(3):
                store.save_evidence({"task_id": "task-latest", "iteration": i})

            latest = store.load_latest_evidence("task-latest")
            assert latest["iteration"] == 2

    def test_list_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EvidenceStore(tmpdir)

            for i in range(3):
                store.save_evidence({"task_id": "task-list", "iteration": i})

            evidence = store.list_evidence("task-list")
            assert len(evidence) == 3
            assert all(e["task_id"] == "task-list" for e in evidence)

    def test_append_to_evidence_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EvidenceStore(tmpdir)
            entry = {"action": "test", "timestamp": "2026-04-24"}

            log_path = store.append_to_evidence_log("task-log", entry)

            assert Path(log_path).exists()
            assert Path(log_path).read_text().strip()

    def test_read_evidence_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EvidenceStore(tmpdir)

            store.append_to_evidence_log("task-read-log", {"action": "step1"})
            store.append_to_evidence_log("task-read-log", {"action": "step2"})

            entries = store.read_evidence_log("task-read-log")
            assert len(entries) == 2

    def test_read_evidence_log_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EvidenceStore(tmpdir)
            entries = store.read_evidence_log("task-empty")
            assert entries == []

    def test_export_for_role_agent_qa(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EvidenceStore(tmpdir)
            store.save_evidence(
                {
                    "task_id": "task-export",
                    "iteration": 0,
                    "acceptance": "complete",
                }
            )

            export_path = store.export_for_role_agent("task-export", "qa")
            assert Path(export_path).exists()

            exported = json.loads(Path(export_path).read_text())
            assert exported["task_id"] == "task-export"

    def test_export_for_role_agent_pm(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EvidenceStore(tmpdir)
            store.save_evidence(
                {
                    "task_id": "task-export-pm",
                    "iteration": 0,
                }
            )

            export_path = store.export_for_role_agent("task-export-pm", "pm")
            exported = json.loads(Path(export_path).read_text())
            assert "iteration" in exported

    def test_create_qa_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EvidenceStore(tmpdir)
            evidence = {
                "task_id": "task-qa",
                "acceptance": "pass",
                "file_changes": ["a.txt"],
                "verification_results": [{"passed": True}],
            }

            summary = store._create_qa_summary(evidence)
            assert summary["task_id"] == "task-qa"
            assert summary["acceptance"] == "pass"

    def test_create_pm_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EvidenceStore(tmpdir)
            evidence = {
                "task_id": "task-pm",
                "iteration": 3,
                "file_changes": ["a.txt", "b.txt"],
            }

            summary = store._create_pm_summary(evidence)
            assert summary["iteration"] == 3
            assert summary["file_changes"] == 2  # Count, not list

    def test_create_director_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EvidenceStore(tmpdir)
            evidence = {
                "task_id": "task-director",
                "iteration": 1,
                "file_changes": ["x.py"],
                "tool_outputs": ["output1"],
                "llm_interactions": [],
                "audit_entries": [],
            }

            summary = store._create_director_summary(evidence)
            assert summary["task_id"] == "task-director"
            assert summary["file_changes"] == ["x.py"]
