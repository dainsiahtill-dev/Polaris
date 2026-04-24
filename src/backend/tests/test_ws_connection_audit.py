import json
import time
from pathlib import Path

from fastapi.testclient import TestClient
from polaris.bootstrap.config import Settings
from polaris.delivery.http.app_factory import create_app
from polaris.kernelone.storage.io_paths import build_cache_root


def _audit_log_path(workspace: Path, ramdisk_root: Path) -> Path:
    cache_root = build_cache_root(str(ramdisk_root), str(workspace))
    return Path(cache_root) / "events" / "ws.connection.events.jsonl"


def _read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    events: list[dict] = []
    for line in lines:
        text = str(line or "").strip()
        if not text:
            continue
        payload = json.loads(text)
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _wait_for_events(path: Path, *, timeout_sec: float = 2.0) -> list[dict]:
    deadline = time.time() + max(0.1, timeout_sec)
    latest: list[dict] = []
    while time.time() < deadline:
        latest = _read_events(path)
        if latest:
            has_open = any(item.get("event") == "open" for item in latest)
            has_terminal = any(item.get("event") in {"disconnect", "closed"} for item in latest)
            if has_open and has_terminal:
                return latest
        time.sleep(0.05)
    return latest


def test_websocket_close_events_are_persisted(tmp_path, monkeypatch):
    token = "ws-audit-token"
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

    with (
        TestClient(app) as client,
        client.websocket_connect(f"/v2/ws/runtime?token={token}&roles=pm,director,qa") as ws,
    ):
        payload = json.loads(ws.receive_text())
        assert payload.get("type") == "status"

    events = _wait_for_events(_audit_log_path(workspace, ramdisk_root))
    assert events, "ws connection audit log should not be empty"

    runtime_events = [event for event in events if event.get("endpoint") == "/v2/ws/runtime"]
    accepted_events = [event for event in runtime_events if event.get("event") == "accepted"]
    assert accepted_events, "expected at least one accepted event"
    accepted_details = accepted_events[-1].get("details") or {}

    assert any(event.get("event") == "open" for event in runtime_events)
    assert any(event.get("event") in {"disconnect", "closed"} for event in runtime_events)
    assert str(accepted_details.get("workspace") or "").strip() == str(workspace.resolve())
    assert str(accepted_details.get("workspace_key") or "").strip()
    assert str(accepted_details.get("runtime_root") or "").strip()
    assert str(accepted_details.get("workspace_source") or "").strip() == "settings"
