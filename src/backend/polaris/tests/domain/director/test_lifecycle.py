# ruff: noqa: E402
"""Tests for polaris.domain.director.lifecycle module.

Covers:
- LifecycleEvent and LifecycleState dataclasses
- DirectorLifecycleManager init and path resolution
- get_state() with missing/corrupt/old-format/new-format files
- update() with all parameters, event capping, timestamps
- Module-level update() and read() compatibility functions
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BACKEND_DIR = str(Path(__file__).resolve().parents[4])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from polaris.domain.director.constants import DirectorPhase
from polaris.domain.director.lifecycle import (
    DirectorLifecycleManager,
    LifecycleEvent,
    LifecycleState,
    read,
    update,
)

# =============================================================================
# Dataclasses
# =============================================================================


class TestLifecycleEvent:
    def test_defaults(self) -> None:
        ev = LifecycleEvent(phase="planning", status="running", timestamp="2026-01-01T00:00:00Z")
        assert ev.run_id == ""
        assert ev.task_id == ""
        assert ev.details is None

    def test_full_construction(self) -> None:
        ev = LifecycleEvent(
            phase="executing",
            status="done",
            timestamp="2026-01-01T00:00:00Z",
            run_id="run-1",
            task_id="task-1",
            details={"foo": "bar"},
        )
        assert ev.run_id == "run-1"
        assert ev.details == {"foo": "bar"}

    def test_immutability(self) -> None:
        ev = LifecycleEvent(phase="init", status="ok", timestamp="t")
        with pytest.raises(AttributeError):
            ev.phase = "x"


class TestLifecycleState:
    def test_defaults(self) -> None:
        state = LifecycleState()
        assert state.phase == DirectorPhase.INIT
        assert state.status == "unknown"
        assert state.events == []
        assert state.startup_completed is False

    def test_custom_values(self) -> None:
        state = LifecycleState(phase="planning", status="running", run_id="r1")
        assert state.phase == "planning"
        assert state.run_id == "r1"

    def test_events_list_mutable(self) -> None:
        # LifecycleState is not frozen, so events list can be replaced
        state = LifecycleState()
        state.events.append(LifecycleEvent("a", "b", "c"))
        assert len(state.events) == 1


# =============================================================================
# DirectorLifecycleManager init
# =============================================================================


class TestDirectorLifecycleManagerInit:
    def test_init_none_workspace(self) -> None:
        manager = DirectorLifecycleManager(workspace=None)
        assert manager._workspace == Path.cwd()

    def test_init_str_workspace(self, tmp_path: Path) -> None:
        manager = DirectorLifecycleManager(workspace=str(tmp_path))
        assert manager._workspace == tmp_path

    def test_init_path_workspace(self, tmp_path: Path) -> None:
        manager = DirectorLifecycleManager(workspace=tmp_path)
        assert manager._workspace == tmp_path


# =============================================================================
# Path resolution
# =============================================================================


class TestResolvePath:
    def test_absolute_path_unchanged(self, tmp_path: Path) -> None:
        manager = DirectorLifecycleManager(workspace=tmp_path)
        abs_path = tmp_path / "absolute.json"
        assert manager._resolve_path(str(abs_path)) == abs_path

    def test_relative_path_resolved(self, tmp_path: Path) -> None:
        manager = DirectorLifecycleManager(workspace=tmp_path)
        resolved = manager._resolve_path("relative.json")
        assert resolved == tmp_path / "relative.json"


# =============================================================================
# get_state
# =============================================================================


class TestGetState:
    def test_missing_file_returns_default(self, tmp_path: Path) -> None:
        manager = DirectorLifecycleManager(workspace=tmp_path)
        state = manager.get_state("missing.json")
        assert state.phase == DirectorPhase.INIT
        assert state.status == "unknown"

    def test_corrupt_json_returns_default(self, tmp_path: Path) -> None:
        manager = DirectorLifecycleManager(workspace=tmp_path)
        path = tmp_path / "bad.json"
        path.write_text("not json", encoding="utf-8")
        state = manager.get_state(str(path))
        assert state.phase == DirectorPhase.INIT

    def test_old_format_direct_fields(self, tmp_path: Path) -> None:
        manager = DirectorLifecycleManager(workspace=tmp_path)
        path = tmp_path / "old.json"
        payload = {
            "phase": "planning",
            "status": "running",
            "run_id": "run-1",
            "events": [
                {"phase": "init", "status": "ok", "ts": "2026-01-01T00:00:00Z", "run_id": "r1"},
            ],
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        state = manager.get_state(str(path))
        assert state.phase == "planning"
        assert state.status == "running"
        assert state.run_id == "run-1"
        assert len(state.events) == 1
        assert state.events[0].timestamp == "2026-01-01T00:00:00Z"

    def test_new_format_nested_lifecycle(self, tmp_path: Path) -> None:
        manager = DirectorLifecycleManager(workspace=tmp_path)
        path = tmp_path / "new.json"
        payload = {
            "schema_version": 2,
            "lifecycle": {
                "phase": "executing",
                "status": "busy",
                "run_id": "run-2",
                "task_id": "task-2",
                "workspace": str(tmp_path),
                "startup_completed": True,
                "execution_started": True,
                "terminal": False,
                "details": {"key": "val"},
                "error": None,
                "timestamp": "2026-02-01T00:00:00Z",
            },
            "events": [
                {
                    "phase": "executing",
                    "status": "busy",
                    "timestamp": "2026-02-01T00:00:00Z",
                    "run_id": "run-2",
                    "task_id": "task-2",
                    "details": {"d": 1},
                },
            ],
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        state = manager.get_state(str(path))
        assert state.phase == "executing"
        assert state.status == "busy"
        assert state.startup_completed is True
        assert state.execution_started is True
        assert state.terminal is False
        assert state.details == {"key": "val"}
        assert state.error is None
        assert len(state.events) == 1
        assert state.events[0].details == {"d": 1}

    def test_empty_json_returns_default(self, tmp_path: Path) -> None:
        manager = DirectorLifecycleManager(workspace=tmp_path)
        path = tmp_path / "empty.json"
        path.write_text("{}", encoding="utf-8")
        state = manager.get_state(str(path))
        assert state.phase == DirectorPhase.INIT

    def test_new_format_missing_optional_fields(self, tmp_path: Path) -> None:
        manager = DirectorLifecycleManager(workspace=tmp_path)
        path = tmp_path / "partial.json"
        payload = {
            "lifecycle": {
                "phase": "reviewing",
            },
            "events": [],
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        state = manager.get_state(str(path))
        assert state.phase == "reviewing"
        assert state.status == "unknown"
        assert state.events == []


# =============================================================================
# update
# =============================================================================


class TestUpdate:
    def test_update_creates_file(self, tmp_path: Path) -> None:
        manager = DirectorLifecycleManager(workspace=tmp_path)
        path = tmp_path / "lifecycle.json"
        state = manager.update(phase="planning", status="running", path=str(path))
        assert path.exists()
        assert state.phase == "planning"
        assert state.status == "running"

    def test_update_appends_events(self, tmp_path: Path) -> None:
        manager = DirectorLifecycleManager(workspace=tmp_path)
        path = tmp_path / "lifecycle.json"
        manager.update(phase="planning", path=str(path))
        state = manager.update(phase="executing", path=str(path))
        assert len(state.events) == 2

    def test_update_all_boolean_flags(self, tmp_path: Path) -> None:
        manager = DirectorLifecycleManager(workspace=tmp_path)
        path = tmp_path / "lifecycle.json"
        state = manager.update(
            phase="executing",
            path=str(path),
            startup_completed=True,
            execution_started=True,
            terminal=True,
        )
        assert state.startup_completed is True
        assert state.execution_started is True
        assert state.terminal is True

    def test_update_details_merge(self, tmp_path: Path) -> None:
        manager = DirectorLifecycleManager(workspace=tmp_path)
        path = tmp_path / "lifecycle.json"
        manager.update(phase="planning", path=str(path), details={"a": 1})
        state = manager.update(phase="executing", path=str(path), details={"b": 2})
        assert state.details == {"a": 1, "b": 2}

    def test_update_error(self, tmp_path: Path) -> None:
        manager = DirectorLifecycleManager(workspace=tmp_path)
        path = tmp_path / "lifecycle.json"
        state = manager.update(phase="failed", path=str(path), error="something broke")
        assert state.error == "something broke"

    def test_update_event_capped_at_50(self, tmp_path: Path) -> None:
        manager = DirectorLifecycleManager(workspace=tmp_path)
        path = tmp_path / "lifecycle.json"
        for i in range(55):
            manager.update(phase="planning", path=str(path), status=f"s{i}")
        state = manager.get_state(str(path))
        assert len(state.events) == 50
        # Verify oldest events were dropped
        assert state.events[0].status == "s5"
        assert state.events[-1].status == "s54"

    def test_update_preserves_existing_details_type(self, tmp_path: Path) -> None:
        manager = DirectorLifecycleManager(workspace=tmp_path)
        path = tmp_path / "lifecycle.json"
        # First write with details as None
        manager.update(phase="init", path=str(path))
        # Second write with dict details
        state = manager.update(phase="planning", path=str(path), details={"x": 1})
        assert state.details == {"x": 1}

    def test_update_old_format_migration(self, tmp_path: Path) -> None:
        manager = DirectorLifecycleManager(workspace=tmp_path)
        path = tmp_path / "lifecycle.json"
        old_payload = {
            "phase": "init",
            "status": "idle",
            "run_id": "r1",
            "events": [{"phase": "init", "status": "idle", "ts": "t1", "run_id": "r1"}],
        }
        path.write_text(json.dumps(old_payload), encoding="utf-8")
        manager.update(phase="planning", path=str(path))
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["schema_version"] == 2
        assert "lifecycle" in raw
        assert raw["lifecycle"]["phase"] == "planning"
        assert raw["events"][0]["timestamp"] == "t1"

    def test_update_strips_and_lowercases(self, tmp_path: Path) -> None:
        manager = DirectorLifecycleManager(workspace=tmp_path)
        path = tmp_path / "lifecycle.json"
        state = manager.update(phase="  PLANNING  ", path=str(path), status="  Running ")
        assert state.phase == "planning"
        assert state.status == "running"

    def test_update_sets_timestamps_on_first_completion(self, tmp_path: Path) -> None:
        with patch("polaris.domain.director.lifecycle.utc_now_iso", return_value="fixed-ts"):
            manager = DirectorLifecycleManager(workspace=tmp_path)
            path = tmp_path / "lifecycle.json"
            state = manager.update(phase="executing", path=str(path), startup_completed=True)
            raw = json.loads(path.read_text(encoding="utf-8"))
            assert raw["lifecycle"]["startup_at"] == "fixed-ts"
            assert state.startup_completed is True

    def test_update_does_not_duplicate_timestamp(self, tmp_path: Path) -> None:
        with patch("polaris.domain.director.lifecycle.utc_now_iso", side_effect=["t1", "t2", "t3", "t4"]):
            manager = DirectorLifecycleManager(workspace=tmp_path)
            path = tmp_path / "lifecycle.json"
            manager.update(phase="executing", path=str(path), startup_completed=True)
            manager.update(phase="executing", path=str(path), startup_completed=True)
            raw = json.loads(path.read_text(encoding="utf-8"))
            assert raw["lifecycle"]["startup_at"] == "t1"

    def test_update_run_id_and_task_id(self, tmp_path: Path) -> None:
        manager = DirectorLifecycleManager(workspace=tmp_path)
        path = tmp_path / "lifecycle.json"
        state = manager.update(phase="executing", path=str(path), run_id="r1", task_id="t1")
        assert state.run_id == "r1"
        assert state.task_id == "t1"

    def test_update_event_includes_run_and_task_id(self, tmp_path: Path) -> None:
        manager = DirectorLifecycleManager(workspace=tmp_path)
        path = tmp_path / "lifecycle.json"
        manager.update(phase="executing", path=str(path), run_id="r1", task_id="t1")
        state = manager.get_state(str(path))
        assert state.events[0].run_id == "r1"
        assert state.events[0].task_id == "t1"

    def test_update_event_without_status_omits_status_key(self, tmp_path: Path) -> None:
        manager = DirectorLifecycleManager(workspace=tmp_path)
        path = tmp_path / "lifecycle.json"
        manager.update(phase="executing", path=str(path))
        raw = json.loads(path.read_text(encoding="utf-8"))
        # Event should not have status if not provided
        assert "status" not in raw["events"][0]


# =============================================================================
# Module-level compatibility functions
# =============================================================================


class TestModuleLevelFunctions:
    def test_update_function_returns_dict(self, tmp_path: Path) -> None:
        path = tmp_path / "lc.json"
        result = update(path=str(path), phase="planning", status="running")
        assert isinstance(result, dict)
        assert result["phase"] == "planning"
        assert result["status"] == "running"
        assert "events" in result

    def test_read_function_returns_dict(self, tmp_path: Path) -> None:
        path = tmp_path / "lc.json"
        update(path=str(path), phase="executing", status="busy")
        result = read(path=str(path))
        assert isinstance(result, dict)
        assert result["phase"] == "executing"
        assert "events" in result

    def test_update_function_absolute_path(self, tmp_path: Path) -> None:
        path = tmp_path / "abs.json"
        result = update(path=str(path), phase="failed")
        assert result["phase"] == "failed"

    def test_read_missing_file_returns_default_dict(self, tmp_path: Path) -> None:
        path = tmp_path / "missing.json"
        result = read(path=str(path))
        assert result["phase"] == DirectorPhase.INIT
        assert result["status"] == "unknown"

    def test_update_relative_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = update(path="rel.json", phase="planning")
        assert result["phase"] == "planning"
        assert (tmp_path / "rel.json").exists()

    def test_read_relative_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        update(path="rel.json", phase="planning")
        result = read(path="rel.json")
        assert result["phase"] == "planning"

    def test_update_function_preserves_created_at(self, tmp_path: Path) -> None:
        with patch("polaris.domain.director.lifecycle.utc_now_iso", return_value="fixed-ts"):
            path = tmp_path / "lc.json"
            r1 = update(path=str(path), phase="init")
            created_at = r1["created_at"]
            r2 = update(path=str(path), phase="planning")
            assert r2["created_at"] == created_at

    def test_update_function_event_ts_mapping(self, tmp_path: Path) -> None:
        path = tmp_path / "lc.json"
        result = update(path=str(path), phase="planning", status="running")
        assert len(result["events"]) == 1
        assert "ts" in result["events"][0]
        assert "phase" in result["events"][0]
