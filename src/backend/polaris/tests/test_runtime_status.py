from __future__ import annotations

from polaris.bootstrap.config import Settings
from polaris.cells.runtime.projection.internal import director_runtime_status, director_status_owner
from polaris.cells.runtime.projection.public import DirectorStatusObservationV1
from polaris.cells.runtime.state_owner.internal.state import AppState


def test_build_director_runtime_status_uses_service_when_running(monkeypatch):
    state = AppState(settings=Settings(workspace="X:/workspace"))
    monkeypatch.setattr(
        director_runtime_status,
        "_read_director_service_status_sync",
        lambda _workspace: {"state": "RUNNING", "started_at": 123.0},
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
        lambda _workspace: {"state": "IDLE", "started_at": 456.0},
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
        lambda _workspace: None,
    )
    payload = director_runtime_status.build_director_runtime_status(state, state.settings.workspace, "")
    assert payload["running"] is False
    assert payload["pid"] is None
    assert payload["source"] == "none"
    assert payload["mode"] == ""
    assert payload["status"] is None
    assert payload["projection_error"].startswith("invalid_director_status_owner_payload:")


def test_read_director_service_status_uses_projection_owned_port(monkeypatch):
    workspace = "X:/workspace"
    expected = {"state": "IDLE", "workspace": workspace}

    def _observe(requested_workspace: str) -> DirectorStatusObservationV1:
        assert requested_workspace == workspace
        return DirectorStatusObservationV1(
            workspace=workspace,
            available=True,
            status=expected,
        )

    monkeypatch.setattr(director_status_owner, "observe_director_status_owner_sync", _observe)

    assert director_runtime_status._read_director_service_status_sync(workspace) == expected
