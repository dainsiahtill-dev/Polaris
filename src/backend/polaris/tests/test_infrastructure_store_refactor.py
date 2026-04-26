"""Tests for evidence_store and state_store with Dict payloads (no Domain imports)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from polaris.infrastructure.audit.stores.evidence_store import (
    EvidenceNotFoundError,
    EvidenceStore,
)
from polaris.infrastructure.persistence.state_store import (
    StateNotFoundError,
    StateStore,
)


class TestEvidenceStoreWithDictPayload:
    """EvidenceStore accepts Dict[str, Any] — no Domain entity imports."""

    def test_save_and_load_evidence_with_minimal_dict(self, tmp_path):
        store = EvidenceStore(runtime_root=str(tmp_path))
        package = {
            "task_id": "task-123",
            "iteration": 0,
            "summary": "test evidence",
            "acceptance": True,
        }

        result = store.save_evidence(package, run_id="run-abc", stage="execution")

        assert result["task_id"] == "task-123"
        assert result["iteration"] == 0
        assert "evidence_path" in result
        assert Path(result["evidence_path"]).exists()

        loaded = store.load_evidence("task-123", iteration=0)
        assert loaded["task_id"] == "task-123"
        assert loaded["summary"] == "test evidence"
        assert loaded["acceptance"] is True
        # Metadata should be added
        assert "_metadata" in loaded
        assert loaded["_metadata"]["stage"] == "execution"
        assert loaded["_metadata"]["run_id"] == "run-abc"

    def test_save_evidence_does_not_mutate_callers_dict(self, tmp_path):
        store = EvidenceStore(runtime_root=str(tmp_path))
        package = {
            "task_id": "task-456",
            "iteration": 1,
        }
        original_keys = set(package.keys())

        store.save_evidence(package)

        # Original dict should not have _metadata
        assert set(package.keys()) == original_keys

    def test_load_latest_evidence(self, tmp_path):
        store = EvidenceStore(runtime_root=str(tmp_path))
        for i in range(3):
            store.save_evidence(
                {
                    "task_id": "task-latest",
                    "iteration": i,
                }
            )

        loaded = store.load_latest_evidence("task-latest")
        assert loaded["iteration"] == 2

    def test_load_evidence_not_found_raises(self, tmp_path):
        store = EvidenceStore(runtime_root=str(tmp_path))
        with pytest.raises(EvidenceNotFoundError):
            store.load_evidence("nonexistent", iteration=0)

    def test_list_evidence(self, tmp_path):
        store = EvidenceStore(runtime_root=str(tmp_path))
        store.save_evidence({"task_id": "task-list", "iteration": 0})
        store.save_evidence({"task_id": "task-list", "iteration": 1})

        results = store.list_evidence("task-list")
        assert len(results) == 2
        assert [r["iteration"] for r in results] == [0, 1]

    def test_append_to_evidence_log(self, tmp_path):
        store = EvidenceStore(runtime_root=str(tmp_path))
        store.append_to_evidence_log("task-log", {"event": "test", "value": 42})

        entries = store.read_evidence_log("task-log")
        assert len(entries) == 1
        assert entries[0]["event"] == "test"
        assert entries[0]["value"] == 42

    def test_export_for_role_agent(self, tmp_path):
        store = EvidenceStore(runtime_root=str(tmp_path))
        store.save_evidence(
            {
                "task_id": "task-export",
                "iteration": 0,
                "file_changes": [{"path": "a.py"}],
                "verification_results": [{"passed": True}],
                "policy_violations": [],
                "summary": "ok",
                "acceptance": True,
            }
        )

        export_path = store.export_for_role_agent("task-export", "qa")
        with open(export_path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["task_id"] == "task-export"
        assert data["acceptance"] is True
        assert len(data["file_changes"]) == 1


class TestStateStoreWithDictPayload:
    """StateStore accepts Dict[str, Any] — no Domain entity imports."""

    def test_save_and_load_state_with_minimal_dict(self, tmp_path):
        store = StateStore(runtime_root=str(tmp_path))
        payload = {
            "task_id": "state-task-1",
            "current_phase": "PLANNING",
            "context": {
                "workspace": str(tmp_path),
                "build_round": 0,
                "stall_count": 0,
                "changed_files": [],
            },
            "is_terminal": False,
            "trajectory": [],
        }

        result = store.save_state(payload, run_id="run-1", phase="planning", status="ok")

        assert result["task_id"] == "state-task-1"
        assert result["phase"] == "PLANNING"
        assert "state_path" in result

        loaded = store.load_state("state-task-1")
        assert loaded["task_id"] == "state-task-1"
        assert loaded["current_phase"] == "PLANNING"

    def test_save_state_does_not_mutate_callers_dict(self, tmp_path):
        store = StateStore(runtime_root=str(tmp_path))
        payload = {
            "task_id": "state-task-2",
            "current_phase": "EXECUTION",
            "context": {
                "workspace": str(tmp_path),
                "build_round": 1,
                "stall_count": 0,
                "changed_files": [],
            },
            "is_terminal": False,
            "trajectory": [],
        }
        original_keys = set(payload.keys())

        store.save_state(payload)

        assert set(payload.keys()) == original_keys

    def test_load_state_not_found_raises(self, tmp_path):
        store = StateStore(runtime_root=str(tmp_path))
        with pytest.raises(StateNotFoundError):
            store.load_state("nonexistent")

    def test_list_tasks(self, tmp_path):
        store = StateStore(runtime_root=str(tmp_path))
        for i in range(2):
            store.save_state(
                {
                    "task_id": f"state-task-list-{i}",
                    "current_phase": "COMPLETED",
                    "context": {
                        "workspace": str(tmp_path),
                        "build_round": 0,
                        "stall_count": 0,
                        "changed_files": [],
                    },
                    "is_terminal": True,
                    "trajectory": [],
                }
            )

        results = store.list_tasks()
        task_ids = [r["task_id"] for r in results]
        assert "state-task-list-0" in task_ids
        assert "state-task-list-1" in task_ids

    def test_load_lifecycle(self, tmp_path):
        store = StateStore(runtime_root=str(tmp_path))
        store.save_state(
            {
                "task_id": "state-task-lifecycle",
                "current_phase": "VERIFICATION",
                "context": {
                    "workspace": str(tmp_path),
                    "build_round": 0,
                    "stall_count": 0,
                    "changed_files": [],
                },
                "is_terminal": False,
                "trajectory": [],
            },
            run_id="run-lc",
            phase="verification",
            status="running",
        )

        lifecycle = store.load_lifecycle("state-task-lifecycle")
        assert lifecycle["run_id"] == "run-lc"
        assert lifecycle["phase"] == "verification"
        assert lifecycle["status"] == "running"

    def test_load_trajectory(self, tmp_path):
        store = StateStore(runtime_root=str(tmp_path))
        store.save_state(
            {
                "task_id": "state-task-traj",
                "current_phase": "EXECUTION",
                "context": {
                    "workspace": str(tmp_path),
                    "build_round": 0,
                    "stall_count": 0,
                    "changed_files": [],
                },
                "is_terminal": False,
                "trajectory": [
                    {"phase": "PLANNING", "action": "plan"},
                    {"phase": "EXECUTION", "action": "exec"},
                ],
            }
        )

        trajectory = store.load_trajectory("state-task-traj")
        assert len(trajectory) == 2
        assert trajectory[0]["phase"] == "PLANNING"

    def test_get_latest_by_run(self, tmp_path):
        store = StateStore(runtime_root=str(tmp_path))
        for i in range(3):
            store.save_state(
                {
                    "task_id": f"state-task-run-{i}",
                    "current_phase": "COMPLETED",
                    "context": {
                        "workspace": str(tmp_path),
                        "build_round": 0,
                        "stall_count": 0,
                        "changed_files": [],
                    },
                    "is_terminal": True,
                    "trajectory": [],
                },
                run_id="run-latest",
                phase="completed",
            )

        latest = store.get_latest_by_run("run-latest")
        assert latest is not None
        assert latest["task_id"] == "state-task-run-2"

    def test_get_latest_by_run_not_found(self, tmp_path):
        store = StateStore(runtime_root=str(tmp_path))
        result = store.get_latest_by_run("nonexistent-run")
        assert result is None
