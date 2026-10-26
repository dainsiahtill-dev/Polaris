"""Settings cannot implicitly opt a workspace into external runtime storage."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterator

import pytest
from polaris.bootstrap.config import RuntimeConfig, Settings, SettingsUpdate
from polaris.cells.storage.layout.public.service import (
    PolarisStorageLayout,
    get_settings_path,
    load_persisted_settings,
    save_persisted_settings,
    sync_process_settings_environment,
)
from polaris.kernelone.storage.layout import clear_storage_roots_cache, resolve_storage_roots


@pytest.fixture(autouse=True)
def isolated_settings_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Use real environment operations with every affected variable restored."""
    keys = (
        "KERNELONE_WORKSPACE",
        "KERNELONE_SELF_UPGRADE_MODE",
        "KERNELONE_RUNTIME_ROOT",
        "KERNELONE_STATE_TO_RAMDISK",
        "KERNELONE_RUNTIME_CACHE_ROOT",
        "KERNELONE_RAMDISK_ROOT",
        "KERNELONE_NATS_ENABLED",
        "KERNELONE_NATS_REQUIRED",
        "KERNELONE_NATS_URL",
        "KERNELONE_NATS_USER",
        "KERNELONE_NATS_PASSWORD",
        "KERNELONE_NATS_CONNECT_TIMEOUT",
        "KERNELONE_NATS_RECONNECT_WAIT",
        "KERNELONE_NATS_MAX_RECONNECT",
        "KERNELONE_NATS_STREAM_NAME",
        "KERNELONE_AUDIT_LLM_ENABLED",
        "KERNELONE_AUDIT_LLM_ROLE",
        "KERNELONE_AUDIT_LLM_TIMEOUT",
        "KERNELONE_AUDIT_LLM_PREFER_LOCAL_OLLAMA",
        "KERNELONE_AUDIT_LLM_ALLOW_REMOTE_FALLBACK",
    )
    original_values = {key: os.environ.get(key) for key in keys}
    for key in ("KERNELONE_HOME", "KERNELONE_RUNTIME_ROOT", "KERNELONE_RUNTIME_CACHE_ROOT", "KERNELONE_RAMDISK_ROOT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("KERNELONE_ROOT", str(tmp_path / "settings-host"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "system-cache"))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    monkeypatch.delenv("KERNELONE_E2E_PROTECT_GLOBAL_SETTINGS", raising=False)
    clear_storage_roots_cache()
    try:
        yield
    finally:
        # Undo caller/fixture patches first; sync writes directly to os.environ,
        # so restore its exact key set after monkeypatch has finished as well.
        monkeypatch.undo()
        for key, original in original_values.items():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original
        clear_storage_roots_cache()


def test_runtime_config_defaults_do_not_opt_in_to_ramdisk() -> None:
    assert RuntimeConfig().use_ramdisk is False


def test_explicitly_local_settings_sync_clears_stale_external_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KERNELONE_RUNTIME_CACHE_ROOT", str(tmp_path / "previous-workspace-cache"))
    settings = Settings(
        workspace=str(tmp_path),
        runtime=RuntimeConfig(root=None, cache_root=None, use_ramdisk=False),
    )
    sync_process_settings_environment(settings)
    cache_claim = os.environ.get("KERNELONE_RUNTIME_CACHE_ROOT")
    root_claim = os.environ.get("KERNELONE_RUNTIME_ROOT")
    assert cache_claim is None
    assert root_claim is None
    assert os.environ["KERNELONE_STATE_TO_RAMDISK"] == "0"


def test_settings_property_and_real_layout_bind_default_to_project_runtime(tmp_path: Path) -> None:
    settings = Settings(workspace=tmp_path, runtime=RuntimeConfig(use_ramdisk=False))
    canonical = tmp_path / ".polaris" / "runtime"
    layout = PolarisStorageLayout(settings.workspace, settings.runtime_base)
    assert settings.runtime_base == canonical
    assert layout.runtime_root == canonical


def test_settings_sync_and_kernel_writer_use_same_canonical_root(tmp_path: Path) -> None:
    settings = Settings(workspace=tmp_path)
    sync_process_settings_environment(settings)
    roots = resolve_storage_roots(str(tmp_path))
    assert Path(roots.runtime_root) == tmp_path / ".polaris" / "runtime"
    assert Path(roots.runtime_project_root) == settings.runtime_base
    assert roots.runtime_mode == "project_local"


def test_workspace_switch_and_empty_runtime_update_do_not_inherit_previous_cache(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    settings = Settings(workspace=first, runtime=RuntimeConfig(cache_root=tmp_path / "old-cache", use_ramdisk=False))
    sync_process_settings_environment(settings)
    settings.apply_update(SettingsUpdate(workspace=str(second), runtime={}))
    sync_process_settings_environment(settings)
    claim = os.environ.get("KERNELONE_RUNTIME_CACHE_ROOT")
    assert claim is None
    assert settings.runtime.use_ramdisk is False
    assert settings.runtime_base == second / ".polaris" / "runtime"
    assert Path(resolve_storage_roots(str(second)).runtime_root) == settings.runtime_base


@pytest.mark.parametrize("kind", ["cache", "root", "ramdisk"])
def test_explicit_external_claim_is_preserved_and_writer_still_rejects_it(tmp_path: Path, kind: str) -> None:
    external = tmp_path / "explicit-external"
    runtime = RuntimeConfig(use_ramdisk=False)
    if kind == "cache":
        runtime.cache_root = external
        expected_key = "KERNELONE_RUNTIME_CACHE_ROOT"
    elif kind == "root":
        runtime.root = external
        expected_key = "KERNELONE_RUNTIME_ROOT"
    else:
        external.mkdir()
        runtime.use_ramdisk = True
        runtime.ramdisk_root = external
        expected_key = "KERNELONE_RAMDISK_ROOT"
    settings = Settings(workspace=tmp_path, runtime=runtime)
    sync_process_settings_environment(settings)
    assert os.environ[expected_key] == str(external)
    assert settings.runtime_base == external
    with pytest.raises(RuntimeError, match="canonical project-local root"):
        resolve_storage_roots(str(tmp_path))
    assert not (tmp_path / ".polaris" / "runtime").exists()
    assert not external.exists() or not list(external.iterdir())


def test_explicit_canonical_root_is_retained_without_invented_cache_claim(tmp_path: Path) -> None:
    canonical = tmp_path / ".polaris" / "runtime"
    settings = Settings(workspace=tmp_path, runtime=RuntimeConfig(root=canonical, use_ramdisk=False))
    sync_process_settings_environment(settings)
    cache_claim = os.environ.get("KERNELONE_RUNTIME_CACHE_ROOT")
    assert cache_claim is None
    assert os.environ["KERNELONE_RUNTIME_ROOT"] == str(canonical)
    assert Path(resolve_storage_roots(str(tmp_path)).runtime_root) == canonical


def test_from_env_and_empty_partial_runtime_keep_missing_opt_in_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KERNELONE_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("KERNELONE_STATE_TO_RAMDISK", raising=False)
    settings = Settings.from_env()
    assert settings.runtime.use_ramdisk is False
    settings.apply_update(SettingsUpdate(runtime={}))
    assert settings.runtime.use_ramdisk is False


@pytest.mark.parametrize("origin", ["model", "environment", "update"])
def test_empty_runtime_paths_clear_overrides_instead_of_selecting_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, origin: str
) -> None:
    empty_paths = {"root": "", "cache_root": " ", "ramdisk_root": ""}
    if origin == "model":
        runtime = RuntimeConfig(**empty_paths)
    elif origin == "environment":
        monkeypatch.setenv("KERNELONE_WORKSPACE", str(tmp_path))
        for key, value in (
            ("KERNELONE_RUNTIME_ROOT", ""),
            ("KERNELONE_RUNTIME_CACHE_ROOT", " "),
            ("KERNELONE_RAMDISK_ROOT", ""),
        ):
            monkeypatch.setenv(key, value)
        runtime = Settings.from_env().runtime
    else:
        settings = Settings(workspace=tmp_path)
        settings.apply_update(SettingsUpdate(runtime=empty_paths))
        runtime = settings.runtime
    assert runtime.root is None
    assert runtime.cache_root is None
    assert runtime.ramdisk_root is None


def test_persisted_local_snapshot_cold_load_clears_stale_external_claim(tmp_path: Path) -> None:
    settings = Settings(workspace=tmp_path, runtime=RuntimeConfig(use_ramdisk=False))
    save_persisted_settings(settings)
    payload = load_persisted_settings()
    assert payload["runtime"]["use_ramdisk"] is False
    assert Path(get_settings_path()).is_relative_to(tmp_path / "settings-host")
    program = """
import json
import os
from polaris.bootstrap.config import Settings
from polaris.cells.storage.layout.public.service import load_persisted_settings,sync_process_settings_environment
from polaris.kernelone.storage.layout import resolve_storage_roots
settings = Settings.model_validate(load_persisted_settings())
sync_process_settings_environment(settings)
roots = resolve_storage_roots(str(settings.workspace))
print(json.dumps({'workspace':str(settings.workspace),'runtime':roots.runtime_root,
    'base':str(settings.runtime_base),'cache_claim':os.environ.get('KERNELONE_RUNTIME_CACHE_ROOT'),
    'ramdisk':settings.runtime.use_ramdisk}))
"""
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": os.pathsep.join(sys.path)}
    environment["KERNELONE_RUNTIME_CACHE_ROOT"] = str(tmp_path / "stale-other-workspace-cache")
    result = subprocess.run(
        ["rtk", "proxy", sys.executable, "-c", program],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout.splitlines()[-1])
    assert observed == {
        "workspace": str(tmp_path),
        "runtime": str(tmp_path / ".polaris" / "runtime"),
        "base": str(tmp_path / ".polaris" / "runtime"),
        "cache_claim": None,
        "ramdisk": False,
    }


@pytest.mark.asyncio
async def test_actual_settings_http_and_engineering_owner_share_project_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from httpx import ASGITransport, AsyncClient
    from polaris.config.nats_config import NATSConfig
    from polaris.delivery.http.app_factory import create_app

    token = "settings-runtime-fixture-token"
    monkeypatch.setenv("KERNELONE_TOKEN", token)
    settings = Settings(
        workspace=tmp_path,
        runtime=RuntimeConfig(use_ramdisk=False),
        nats=NATSConfig(enabled=False, required=False, url=""),
    )
    # ASGI transport does not run application lifespan or start network services.
    app = create_app(settings=settings)
    canonical = tmp_path / ".polaris" / "runtime"
    assert app.state.engineering_run_service.workspace == tmp_path
    assert app.state.engineering_run_service.store.database_path == canonical / "engineering" / "sessions.sqlite3"
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://127.0.0.1",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        response = await client.get("/v2/settings")
        assert response.status_code == 200, response.text
        assert response.json()["runtime"]["use_ramdisk"] is False
        updated = await client.post(
            "/v2/settings",
            json={"runtime": {"root": None, "cache_root": None, "use_ramdisk": False}},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["runtime"]["cache_root"] is None
    assert settings.runtime_base == canonical
    assert Path(resolve_storage_roots(str(tmp_path)).runtime_root) == canonical
