from __future__ import annotations

import json
import os
from pathlib import Path

from polaris.bootstrap.config import Settings
from fastapi.testclient import TestClient
from polaris.cells.policy.workspace_guard.service import SELF_UPGRADE_MODE_ENV, get_meta_project_root
from polaris.cells.storage.layout.internal.settings_utils import get_settings_path, load_persisted_settings
from polaris.delivery.http.app_factory import create_app


def test_settings_route_updates_workspace_env_and_persists_workspace(
    tmp_path: Path, monkeypatch
) -> None:
    test_token = "test-settings-token"
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir(parents=True, exist_ok=True)
    workspace_b.mkdir(parents=True, exist_ok=True)

    config_root = tmp_path / "config-root"
    monkeypatch.setenv("POLARIS_ROOT", str(config_root))
    monkeypatch.setenv("POLARIS_TOKEN", test_token)
    monkeypatch.delenv(SELF_UPGRADE_MODE_ENV, raising=False)

    app = create_app(Settings(workspace=str(workspace_a), ramdisk_root=""))
    with TestClient(app, headers={"Authorization": f"Bearer {test_token}"}) as client:
        response = client.post("/settings", json={"workspace": str(workspace_b)})

    assert response.status_code == 200
    payload = response.json()
    assert Path(payload["workspace"]).resolve() == workspace_b.resolve()
    assert Path(os.environ["POLARIS_WORKSPACE"]).resolve() == workspace_b.resolve()
    assert SELF_UPGRADE_MODE_ENV not in os.environ

    settings_path = Path(get_settings_path())
    persisted = json.loads(settings_path.read_text(encoding="utf-8"))
    assert Path(str(persisted["workspace"])).resolve() == workspace_b.resolve()
    os.environ.pop("POLARIS_WORKSPACE", None)


def test_load_persisted_settings_recovers_workspace_local_settings(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    workspace_settings_path = workspace / ".polaris" / "settings.json"
    workspace_settings_path.parent.mkdir(parents=True, exist_ok=True)
    workspace_settings_path.write_text(
        json.dumps({"timeout": 12}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    config_root = tmp_path / "config-root"
    monkeypatch.setenv("POLARIS_ROOT", str(config_root))

    payload = load_persisted_settings(str(workspace))

    assert payload["timeout"] == 12
    assert Path(str(payload["workspace"])).resolve() == workspace.resolve()

    global_settings_path = Path(get_settings_path())
    persisted = json.loads(global_settings_path.read_text(encoding="utf-8"))
    assert Path(str(persisted["workspace"])).resolve() == workspace.resolve()


def test_settings_route_rejects_meta_project_workspace_without_self_upgrade(
    tmp_path: Path, monkeypatch
) -> None:
    test_token = "test-settings-token"
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    config_root = tmp_path / "config-root"
    monkeypatch.setenv("POLARIS_ROOT", str(config_root))
    monkeypatch.setenv("POLARIS_TOKEN", test_token)
    monkeypatch.delenv(SELF_UPGRADE_MODE_ENV, raising=False)

    app = create_app(Settings(workspace=str(workspace), ramdisk_root=""))
    with TestClient(app, headers={"Authorization": f"Bearer {test_token}"}) as client:
        response = client.post(
            "/settings",
            json={"workspace": str(get_meta_project_root())},
        )

    assert response.status_code == 400
    assert "self_upgrade_mode" in str(response.json().get("detail") or "")
    os.environ.pop("POLARIS_WORKSPACE", None)
    os.environ.pop(SELF_UPGRADE_MODE_ENV, None)


def test_settings_route_allows_meta_project_workspace_with_self_upgrade(
    tmp_path: Path, monkeypatch
) -> None:
    test_token = "test-settings-token"
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    project_root = get_meta_project_root()

    config_root = tmp_path / "config-root"
    monkeypatch.setenv("POLARIS_ROOT", str(config_root))
    monkeypatch.setenv("POLARIS_TOKEN", test_token)
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
    os.environ.pop("POLARIS_WORKSPACE", None)
    os.environ.pop(SELF_UPGRADE_MODE_ENV, None)

