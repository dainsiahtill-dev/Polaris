import json

from fastapi.testclient import TestClient
from polaris.bootstrap.config import Settings
from polaris.cells.runtime.artifact_store.internal import artifacts as artifacts_service
from polaris.cells.runtime.projection.internal import status_snapshot_builder as runtime_ws_status_service
from polaris.delivery.http.app_factory import create_app


def test_settings_normalize_legacy_json_log_path(tmp_path):
    settings = Settings(
        workspace=str(tmp_path),
        json_log_path="runtime/events/pm.events.jsonl",
    )
    assert settings.json_log_path == "runtime/events/pm.events.jsonl"


def test_websocket_status_payload_is_serializable(tmp_path, monkeypatch):
    token = "ws-test-token"
    monkeypatch.setenv("KERNELONE_TOKEN", token)

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    ramdisk_root = tmp_path / "runtime"
    ramdisk_root.mkdir(parents=True, exist_ok=True)

    settings = Settings(
        workspace=str(workspace),
        ramdisk_root=str(ramdisk_root),
        json_log_path="runtime/events/pm.events.jsonl",
    )
    app = create_app(settings)

    with TestClient(app) as client, client.websocket_connect(f"/v2/ws/runtime?token={token}") as ws:
        payload = json.loads(ws.receive_text())

    assert payload.get("type") == "status"
    snapshot = payload.get("snapshot")
    assert isinstance(snapshot, dict)
    git_payload = snapshot.get("git")
    assert isinstance(git_payload, dict)
    assert isinstance(git_payload.get("root"), str)


def test_websocket_status_payload_tolerates_workflow_sync_helpers(tmp_path, monkeypatch):
    token = "ws-workflow-token"
    monkeypatch.setenv("KERNELONE_TOKEN", token)

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    ramdisk_root = tmp_path / "runtime"
    ramdisk_root.mkdir(parents=True, exist_ok=True)

    settings = Settings(
        workspace=str(workspace),
        ramdisk_root=str(ramdisk_root),
        json_log_path="runtime/events/pm.events.jsonl",
    )

    def _fake_workflow_status(*_args, **_kwargs):
        return {
            "source": "workflow",
            "running": False,
            "workflow_id": "demo",
            "workflow_status": "completed",
            "stage": "qa_completed",
        }

    monkeypatch.setattr(runtime_ws_status_service, "get_workflow_runtime_status", _fake_workflow_status)
    monkeypatch.setattr(artifacts_service, "get_workflow_runtime_status", _fake_workflow_status)

    app = create_app(settings)

    with TestClient(app) as client, client.websocket_connect(f"/v2/ws/runtime?token={token}") as ws:
        payload = json.loads(ws.receive_text())

    assert payload.get("type") == "status"
    pm_status = payload.get("pm_status")
    assert isinstance(pm_status, dict)
    workflow = pm_status.get("workflow")
    assert isinstance(workflow, dict)
    assert workflow.get("stage") == "qa_completed"
