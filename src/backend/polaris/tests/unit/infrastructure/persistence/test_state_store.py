"""Tests for polaris.infrastructure.persistence.state_store."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from polaris.infrastructure.persistence.state_store import (
    StateNotFoundError,
    StateStore,
)


class TestStateStore:
    def test_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = StateStore(tmpdir)
            assert store.runtime_root == Path(tmpdir).resolve()

    def test_get_task_state_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = StateStore(tmpdir)
            task_dir = store._get_task_state_dir("task-123")
            assert task_dir.exists()
            assert "task-123" in str(task_dir)

    def test_write_json_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.json"
            data = {"key": "value"}

            StateStore._write_json_atomic(path, data)
            loaded = json.loads(path.read_text())
            assert loaded == data

    def test_write_text_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.txt"
            text = "Hello, World!"

            StateStore._write_text_atomic(path, text)
            assert path.read_text() == text

    def test_save_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = StateStore(tmpdir)
            payload = {
                "task_id": "task-abc",
                "current_phase": "PLANNING",
                "context": {"workspace": ".", "build_round": 1},
                "is_terminal": False,
            }

            result = store.save_state(payload)

            assert "state_path" in result
            assert result["task_id"] == "task-abc"
            assert Path(result["state_path"]).exists()

    def test_save_state_with_dict(self) -> None:
        """Test save_state accepts plain dict (not just objects with to_dict)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = StateStore(tmpdir)
            payload = {
                "task_id": "task-dict",
                "current_phase": "EXECUTION",
                "context": {"workspace": ".", "build_round": 2},
                "is_terminal": False,
            }

            result = store.save_state(payload)
            assert result["task_id"] == "task-dict"

    def test_save_state_with_object_interface(self) -> None:
        """Test save_state duck-types objects with to_dict method."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = StateStore(tmpdir)

            class MockStateMachine:
                def to_dict(self) -> dict:
                    return {
                        "task_id": "task-obj",
                        "current_phase": "VERIFICATION",
                        "context": {"workspace": ".", "build_round": 3},
                        "is_terminal": True,
                    }

            result = store.save_state(MockStateMachine())
            assert result["task_id"] == "task-obj"

    def test_load_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = StateStore(tmpdir)
            payload = {
                "task_id": "task-load",
                "current_phase": "PLANNING",
                "context": {"workspace": ".", "build_round": 1},
                "is_terminal": False,
            }
            store.save_state(payload)

            loaded = store.load_state("task-load")
            assert loaded["task_id"] == "task-load"

    def test_load_state_not_found_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = StateStore(tmpdir)
            with pytest.raises(StateNotFoundError):
                store.load_state("nonexistent-task")

    def test_load_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = StateStore(tmpdir)
            payload = {
                "task_id": "task-lifecycle",
                "current_phase": "PLANNING",
                "context": {"workspace": ".", "build_round": 1},
                "is_terminal": False,
            }
            store.save_state(payload, phase="planning", status="active")

            lifecycle = store.load_lifecycle("task-lifecycle")
            assert lifecycle.get("task_id") == "task-lifecycle"

    def test_load_trajectory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = StateStore(tmpdir)
            payload = {
                "task_id": "task-traj",
                "current_phase": "EXECUTION",
                "context": {"workspace": ".", "build_round": 1},
                "is_terminal": False,
                "trajectory": [
                    {"phase": "planning", "action": "done"},
                    {"phase": "execution", "action": "working"},
                ],
            }
            store.save_state(payload)

            trajectory = store.load_trajectory("task-traj")
            assert len(trajectory) == 2

    def test_list_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = StateStore(tmpdir)

            for i in range(3):
                payload = {
                    "task_id": f"task-{i}",
                    "current_phase": "PLANNING",
                    "context": {"workspace": ".", "build_round": 1},
                    "is_terminal": False,
                }
                store.save_state(payload)

            tasks = store.list_tasks()
            assert len(tasks) == 3

    def test_get_latest_by_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = StateStore(tmpdir)

            for i in range(2):
                payload = {
                    "task_id": f"task-run-{i}",
                    "current_phase": "EXECUTION",
                    "context": {"workspace": ".", "build_round": 1},
                    "is_terminal": False,
                }
                store.save_state(payload, run_id="run-123")

            result = store.get_latest_by_run("run-123")
            assert result is not None
            assert result["task_id"] in ("task-run-0", "task-run-1")

    def test_get_latest_by_run_no_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = StateStore(tmpdir)
            result = store.get_latest_by_run("nonexistent-run")
            assert result is None

    def test_update_lifecycle_creates_new(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = StateStore(tmpdir)
            lifecycle_path = Path(tmpdir) / "lifecycle.json"

            payload = store._update_lifecycle(
                lifecycle_path,
                task_id="new-task",
                run_id="run-1",
                phase="planning",
                persist=False,
            )

            assert payload["task_id"] == "new-task"
            assert payload["run_id"] == "run-1"

    def test_update_lifecycle_appends_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = StateStore(tmpdir)
            lifecycle_path = Path(tmpdir) / "lifecycle.json"

            store._update_lifecycle(lifecycle_path, task_id="task", phase="planning", persist=True)
            store._update_lifecycle(lifecycle_path, task_id="task", phase="execution", status="running", persist=True)

            loaded = json.loads(lifecycle_path.read_text())
            assert len(loaded["events"]) == 2

    def test_update_lifecycle_tracks_phase_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = StateStore(tmpdir)
            lifecycle_path = Path(tmpdir) / "lifecycle.json"

            store._update_lifecycle(lifecycle_path, task_id="task", phase="planning", persist=True)

            loaded = json.loads(lifecycle_path.read_text())
            assert loaded["startup_completed"] is True
            assert loaded["execution_started"] is False

    def test_update_lifecycle_execution_started(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = StateStore(tmpdir)
            lifecycle_path = Path(tmpdir) / "lifecycle.json"

            store._update_lifecycle(lifecycle_path, task_id="task", phase="execution", persist=True)

            loaded = json.loads(lifecycle_path.read_text())
            assert loaded["execution_started"] is True
