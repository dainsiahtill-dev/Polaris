from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import HTTPException
from polaris.bootstrap.config import Settings, SettingsUpdate, default_system_cache_base
from polaris.cells.policy.workspace_guard.service import (
    SELF_UPGRADE_MODE_ENV,
    get_meta_project_root,
    is_meta_project_target,
)
from polaris.cells.storage.layout.internal.settings_utils import sync_process_settings_environment
from polaris.cells.workspace.integrity.public.service import validate_workspace
from polaris.domain.exceptions import ValidationError


def test_validate_workspace_rejects_meta_project_without_self_upgrade() -> None:
    project_root = get_meta_project_root()

    with pytest.raises((HTTPException, ValidationError)) as exc_info:
        validate_workspace(str(project_root))

    exc = exc_info.value
    detail = str(getattr(exc, "detail", None) or getattr(exc, "message", ""))
    assert "self_upgrade_mode" in detail


def test_validate_workspace_rejects_meta_project_child_without_self_upgrade() -> None:
    protected_child = get_meta_project_root() / "docs"
    assert protected_child.is_dir()
    assert is_meta_project_target(protected_child) is True

    with pytest.raises((HTTPException, ValidationError)):
        validate_workspace(str(protected_child))


def test_validate_workspace_allows_meta_project_when_self_upgrade_enabled() -> None:
    project_root = get_meta_project_root()

    resolved = validate_workspace(str(project_root), self_upgrade_mode=True)

    assert Path(resolved).resolve() == project_root.resolve()


def test_settings_from_env_rejects_meta_project_without_self_upgrade(monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_WORKSPACE", str(get_meta_project_root()))
    monkeypatch.delenv(SELF_UPGRADE_MODE_ENV, raising=False)

    with pytest.raises(ValueError):
        Settings.from_env()


def test_settings_apply_update_requires_self_upgrade_for_meta_project(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    project_root = get_meta_project_root()

    settings = Settings(workspace=str(workspace))

    with pytest.raises(ValueError):
        settings.apply_update(SettingsUpdate(workspace=str(project_root)))

    settings.apply_update(
        SettingsUpdate(
            self_upgrade_mode=True,
            workspace=str(project_root),
        )
    )

    assert settings.self_upgrade_mode is True
    assert Path(settings.workspace).resolve() == project_root.resolve()


def test_settings_apply_update_rejects_disabling_self_upgrade_on_meta_project(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    project_root = get_meta_project_root()

    settings = Settings(workspace=str(workspace))
    settings.apply_update(
        SettingsUpdate(
            self_upgrade_mode=True,
            workspace=str(project_root),
        )
    )

    with pytest.raises(ValueError):
        settings.apply_update(SettingsUpdate(self_upgrade_mode=False))


def test_sync_process_settings_environment_tracks_self_upgrade_mode(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    settings = Settings(workspace=str(workspace), self_upgrade_mode=True)
    sync_process_settings_environment(settings)
    assert os.environ.get(SELF_UPGRADE_MODE_ENV) == "1"

    settings.self_upgrade_mode = False
    sync_process_settings_environment(settings)
    assert SELF_UPGRADE_MODE_ENV not in os.environ
    os.environ.pop("KERNELONE_WORKSPACE", None)


def test_sync_process_settings_environment_overrides_stale_runtime_env(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    runtime_cache = tmp_path / "runtime-cache"
    stale_runtime = tmp_path / "stale-runtime"
    workspace.mkdir(parents=True, exist_ok=True)
    runtime_cache.mkdir(parents=True, exist_ok=True)
    stale_runtime.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(stale_runtime))
    monkeypatch.setenv("KERNELONE_RUNTIME_CACHE_ROOT", str(stale_runtime))

    settings = Settings(
        workspace=str(workspace),
        runtime={"cache_root": str(runtime_cache), "root": None, "use_ramdisk": False},
    )
    sync_process_settings_environment(settings)

    assert "KERNELONE_RUNTIME_ROOT" not in os.environ
    assert os.environ.get("KERNELONE_RUNTIME_CACHE_ROOT") == str(runtime_cache.resolve())
    assert os.environ.get("KERNELONE_STATE_TO_RAMDISK") == "0"
    assert os.environ.get("KERNELONE_WORKSPACE") == str(workspace.resolve())


def test_sync_process_settings_environment_uses_default_cache_not_stale_runtime_env(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    stale_runtime_root = tmp_path / "cache" / ".polaris" / "projects" / "stale" / "runtime"
    workspace.mkdir(parents=True, exist_ok=True)
    stale_runtime_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("KERNELONE_RUNTIME_CACHE_ROOT", str(stale_runtime_root))

    settings = Settings(
        workspace=str(workspace),
        runtime={"cache_root": None, "root": None, "use_ramdisk": False},
    )
    sync_process_settings_environment(settings)

    assert os.environ.get("KERNELONE_RUNTIME_CACHE_ROOT") == str(default_system_cache_base())
    assert os.environ.get("KERNELONE_RUNTIME_CACHE_ROOT") != str(stale_runtime_root)
    assert os.environ.get("KERNELONE_STATE_TO_RAMDISK") == "0"


def test_sync_process_settings_environment_preserves_explicit_runtime_root(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    runtime_root = tmp_path / "runtime-root"
    workspace.mkdir(parents=True, exist_ok=True)
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(tmp_path / "stale-runtime"))

    settings = Settings(
        workspace=str(workspace),
        runtime={"root": str(runtime_root), "cache_root": None, "use_ramdisk": False},
    )
    sync_process_settings_environment(settings)

    assert os.environ.get("KERNELONE_RUNTIME_ROOT") == str(runtime_root.resolve())
    assert os.environ.get("KERNELONE_STATE_TO_RAMDISK") == "0"


def test_sync_process_settings_environment_tracks_nats_settings(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    settings = Settings(
        workspace=str(workspace),
        nats={
            "enabled": False,
            "required": False,
            "url": "nats://127.0.0.1:4555",
            "user": "demo",
            "password": "secret",
            "connect_timeout_sec": 4.5,
            "reconnect_wait_sec": 2.0,
            "max_reconnect_attempts": 7,
            "stream_name": "HP_RUNTIME",
        },
    )
    sync_process_settings_environment(settings)

    assert os.environ.get("KERNELONE_NATS_ENABLED") == "0"
    assert os.environ.get("KERNELONE_NATS_REQUIRED") == "0"
    assert os.environ.get("KERNELONE_NATS_URL") == "nats://127.0.0.1:4555"
    assert os.environ.get("KERNELONE_NATS_USER") == "demo"
    assert os.environ.get("KERNELONE_NATS_PASSWORD") == "secret"
    assert os.environ.get("KERNELONE_NATS_CONNECT_TIMEOUT") == "4.5"
    assert os.environ.get("KERNELONE_NATS_RECONNECT_WAIT") == "2.0"
    assert os.environ.get("KERNELONE_NATS_MAX_RECONNECT") == "7"
    assert os.environ.get("KERNELONE_NATS_STREAM_NAME") == "HP_RUNTIME"

    for name in (
        "KERNELONE_WORKSPACE",
        "KERNELONE_NATS_ENABLED",
        "KERNELONE_NATS_REQUIRED",
        "KERNELONE_NATS_URL",
        "KERNELONE_NATS_USER",
        "KERNELONE_NATS_PASSWORD",
        "KERNELONE_NATS_CONNECT_TIMEOUT",
        "KERNELONE_NATS_RECONNECT_WAIT",
        "KERNELONE_NATS_MAX_RECONNECT",
        "KERNELONE_NATS_STREAM_NAME",
        "KERNELONE_STATE_TO_RAMDISK",
    ):
        os.environ.pop(name, None)
