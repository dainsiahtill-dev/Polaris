from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from polaris.bootstrap.config import Settings, SettingsUpdate
from polaris.cells.policy.workspace_guard.service import SELF_UPGRADE_MODE_ENV, get_meta_project_root
from polaris.cells.runtime.state_owner.public.service import AppState
from polaris.cells.storage.layout.internal.settings_utils import get_settings_path, load_persisted_settings
from polaris.delivery.http.app_factory import create_app
from polaris.delivery.http.routers.system import _update_settings_internal


class _RawJsonRequest:
    def __init__(self, state: AppState, payload: dict[str, object]) -> None:
        self.app = SimpleNamespace(state=SimpleNamespace(app_state=state))
        self._payload = payload

    async def json(self) -> dict[str, object]:
        return self._payload


def test_settings_route_updates_workspace_env_and_persists_workspace(tmp_path: Path, monkeypatch) -> None:
    test_token = "test-settings-token"
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir(parents=True, exist_ok=True)
    workspace_b.mkdir(parents=True, exist_ok=True)

    config_root = tmp_path / "config-root"
    monkeypatch.setenv("KERNELONE_ROOT", str(config_root))
    monkeypatch.setenv("KERNELONE_TOKEN", test_token)
    monkeypatch.delenv(SELF_UPGRADE_MODE_ENV, raising=False)

    app = create_app(Settings(workspace=str(workspace_a), ramdisk_root=""))
    with TestClient(app, headers={"Authorization": f"Bearer {test_token}"}) as client:
        response = client.post("/settings", json={"workspace": str(workspace_b)})

    assert response.status_code == 200
    payload = response.json()
    assert Path(payload["workspace"]).resolve() == workspace_b.resolve()
    assert Path(os.environ["KERNELONE_WORKSPACE"]).resolve() == workspace_b.resolve()
    assert SELF_UPGRADE_MODE_ENV not in os.environ

    settings_path = Path(get_settings_path())
    persisted = json.loads(settings_path.read_text(encoding="utf-8"))
    assert Path(str(persisted["workspace"])).resolve() == workspace_b.resolve()
    os.environ.pop("KERNELONE_WORKSPACE", None)


def test_load_persisted_settings_recovers_workspace_local_settings(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    workspace_settings_path = workspace / ".polaris" / "settings.json"
    workspace_settings_path.parent.mkdir(parents=True, exist_ok=True)
    workspace_settings_path.write_text(
        json.dumps({"timeout": 12}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    config_root = tmp_path / "config-root"
    monkeypatch.setenv("KERNELONE_ROOT", str(config_root))

    payload = load_persisted_settings(str(workspace))

    assert payload["timeout"] == 12
    assert Path(str(payload["workspace"])).resolve() == workspace.resolve()

    global_settings_path = Path(get_settings_path())
    persisted = json.loads(global_settings_path.read_text(encoding="utf-8"))
    assert Path(str(persisted["workspace"])).resolve() == workspace.resolve()


def test_load_persisted_settings_drops_missing_global_workspace(tmp_path: Path, monkeypatch) -> None:
    config_root = tmp_path / "config-root"
    missing_workspace = tmp_path / "deleted-workspace"
    monkeypatch.setenv("KERNELONE_ROOT", str(config_root))

    settings_path = Path(get_settings_path())
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps({"workspace": str(missing_workspace), "timeout": 42}, ensure_ascii=False),
        encoding="utf-8",
    )

    payload = load_persisted_settings()

    assert "workspace" not in payload
    assert payload["timeout"] == 42
    persisted = json.loads(settings_path.read_text(encoding="utf-8"))
    assert "workspace" not in persisted
    assert persisted["timeout"] == 42


def test_settings_route_rejects_meta_project_workspace_without_self_upgrade(tmp_path: Path, monkeypatch) -> None:
    test_token = "test-settings-token"
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    config_root = tmp_path / "config-root"
    monkeypatch.setenv("KERNELONE_ROOT", str(config_root))
    monkeypatch.setenv("KERNELONE_TOKEN", test_token)
    monkeypatch.delenv(SELF_UPGRADE_MODE_ENV, raising=False)

    app = create_app(Settings(workspace=str(workspace), ramdisk_root=""))
    with TestClient(app, headers={"Authorization": f"Bearer {test_token}"}) as client:
        response = client.post(
            "/settings",
            json={"workspace": str(get_meta_project_root())},
        )

    assert response.status_code == 400
    assert "self_upgrade_mode" in json.dumps(response.json(), ensure_ascii=False)
    os.environ.pop("KERNELONE_WORKSPACE", None)
    os.environ.pop(SELF_UPGRADE_MODE_ENV, None)


def test_settings_route_allows_meta_project_workspace_with_self_upgrade(tmp_path: Path, monkeypatch) -> None:
    test_token = "test-settings-token"
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    project_root = get_meta_project_root()

    config_root = tmp_path / "config-root"
    monkeypatch.setenv("KERNELONE_ROOT", str(config_root))
    monkeypatch.setenv("KERNELONE_TOKEN", test_token)
    monkeypatch.delenv(SELF_UPGRADE_MODE_ENV, raising=False)

    app = create_app(Settings(workspace=str(workspace), ramdisk_root=""))
    with TestClient(app, headers={"Authorization": f"Bearer {test_token}"}) as client:
        response = client.post(
            "/settings",
            json={
                "self_upgrade_mode": True,
                "workspace": str(project_root),
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["self_upgrade_mode"] is True
    assert Path(payload["workspace"]).resolve() == project_root.resolve()
    assert os.environ.get(SELF_UPGRADE_MODE_ENV) == "1"
    os.environ.pop("KERNELONE_WORKSPACE", None)
    os.environ.pop(SELF_UPGRADE_MODE_ENV, None)


def test_settings_internal_honors_raw_workspace_when_projection_misses_field(tmp_path: Path, monkeypatch) -> None:
    test_token = "test-settings-token"
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir(parents=True, exist_ok=True)
    workspace_b.mkdir(parents=True, exist_ok=True)

    config_root = tmp_path / "config-root"
    monkeypatch.setenv("KERNELONE_ROOT", str(config_root))
    monkeypatch.setenv("KERNELONE_TOKEN", test_token)
    monkeypatch.delenv(SELF_UPGRADE_MODE_ENV, raising=False)

    state = AppState(settings=Settings(workspace=str(workspace_a), ramdisk_root=""))
    request = _RawJsonRequest(state, {"workspace": str(workspace_b)})

    payload = asyncio.run(_update_settings_internal(request, SettingsUpdate()))

    assert Path(payload["workspace"]).resolve() == workspace_b.resolve()
    assert Path(str(state.settings.workspace)).resolve() == workspace_b.resolve()
