from __future__ import annotations

import builtins

from polaris.bootstrap.config import Settings
from polaris.cells.runtime.projection.internal import director_runtime_status
from polaris.cells.runtime.state_owner.internal.state import AppState


def test_build_director_runtime_status_uses_service_when_running(monkeypatch):
    state = AppState(settings=Settings(workspace="X:/workspace"))
    monkeypatch.setattr(
        director_runtime_status,
        "_read_director_service_status_sync",
        lambda: {"state": "RUNNING", "started_at": 123.0},
    )
    payload = director_runtime_status.build_director_runtime_status(state, state.settings.workspace, "")
    assert payload["running"] is True
    assert payload["pid"] is None
    assert payload["source"] == "v2_service"
    assert payload["mode"] == "v2_service"
    assert payload["started_at"] == 123.0
    assert payload["status"]["state"] == "RUNNING"


def test_build_director_runtime_status_marks_idle_when_service_not_running(monkeypatch):
    state = AppState(settings=Settings(workspace="X:/workspace"))
    monkeypatch.setattr(
        director_runtime_status,
        "_read_director_service_status_sync",
        lambda: {"state": "IDLE", "started_at": 456.0},
    )
    payload = director_runtime_status.build_director_runtime_status(state, state.settings.workspace, "")
    assert payload["running"] is False
    assert payload["pid"] is None
    assert payload["source"] == "v2_service"
    assert payload["mode"] == "v2_service"
    assert payload["started_at"] == 456.0
    assert payload["status"]["state"] == "IDLE"


def test_build_director_runtime_status_returns_none_source_when_service_unavailable(monkeypatch):
    state = AppState(settings=Settings(workspace="X:/workspace"))
    monkeypatch.setattr(
        director_runtime_status,
        "_read_director_service_status_sync",
        lambda: None,
    )
    payload = director_runtime_status.build_director_runtime_status(state, state.settings.workspace, "")
    assert payload["running"] is False
    assert payload["pid"] is None
    assert payload["source"] == "none"
    assert payload["mode"] == ""
    assert payload["status"] is None


def test_read_director_service_status_imports_execution_service_not_legacy(monkeypatch):
    seen: list[str] = []
    original_import = builtins.__import__

    def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "polaris.cells.director.execution.service":
            seen.append(name)
            raise ImportError("simulated import failure")
        if name == "polaris.cells.director.execution.public.service":
            raise AssertionError("legacy import path must not be used")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _guarded_import)

    status = director_runtime_status._read_director_service_status_sync()

    assert status is None
    assert seen == ["polaris.cells.director.execution.service"]
