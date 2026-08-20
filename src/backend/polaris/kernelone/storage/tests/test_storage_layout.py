from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest
from polaris.kernelone._runtime_config import (
    set_workspace_metadata_dir_name,
)
from polaris.kernelone.storage import (
    UNSUPPORTED_PATH_PREFIX,
    StorageLayout,
    clear_storage_roots_cache,
    resolve_global_path,
    resolve_runtime_path,
    resolve_storage_roots,
    resolve_workspace_persistent_path,
    resolve_workspace_runtime_identity,
    workspace_key,
)


def test_workspace_key_stable_and_distinct(tmp_path: Path) -> None:
    ws_a = tmp_path / "a" / "demo"
    ws_b = tmp_path / "b" / "demo"
    ws_a.mkdir(parents=True, exist_ok=True)
    ws_b.mkdir(parents=True, exist_ok=True)

    key_a_1 = workspace_key(str(ws_a))
    key_a_2 = workspace_key(str(ws_a))
    key_b = workspace_key(str(ws_b))

    assert key_a_1 == key_a_2
    assert key_a_1 != key_b
    assert key_a_1.startswith("demo-")


def test_workspace_runtime_identity_is_distinct_under_shared_global_runtime_base(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_base = tmp_path / "global-project-cache"
    workspace_a = tmp_path / "fresh-a" / "l1-01"
    workspace_b = tmp_path / "fresh-b" / "l1-01"
    workspace_a.mkdir(parents=True)
    workspace_b.mkdir(parents=True)
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_base))
    clear_storage_roots_cache()

    identity_a = resolve_workspace_runtime_identity(str(workspace_a))
    identity_b = resolve_workspace_runtime_identity(str(workspace_b))

    assert identity_a.workspace_abs == str(workspace_a.resolve())
    assert identity_b.workspace_abs == str(workspace_b.resolve())
    assert identity_a.runtime_root != identity_b.runtime_root
    assert identity_a.token != identity_b.token


def test_storage_roots_taxonomy(tmp_path: Path) -> None:
    """Default KernelOne deployment uses .kernelone as workspace metadata dir."""
    workspace = tmp_path / "project"
    workspace.mkdir(parents=True, exist_ok=True)

    # In Polaris project, bootstrap sets .polaris as metadata dir
    from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

    current_meta = get_workspace_metadata_dir_name()

    roots = resolve_storage_roots(str(workspace))
    assert roots.storage_layout_mode == "project_local"
    # Check against actual current metadata dir (Polaris uses .polaris)
    assert Path(roots.project_persistent_root).as_posix().endswith(f"/{current_meta}")
    assert Path(roots.runtime_project_root).as_posix().endswith("/runtime")


def test_default_runtime_root_is_inside_target_project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    monkeypatch.delenv("KERNELONE_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("KERNELONE_RUNTIME_CACHE_ROOT", raising=False)
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    clear_storage_roots_cache()

    roots = resolve_storage_roots(str(workspace))

    assert Path(roots.runtime_root).resolve() == (workspace / ".polaris" / "runtime").resolve()
    assert roots.runtime_mode == "project_local"


def test_explicit_project_local_runtime_root_is_not_nested(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    local_runtime = workspace / ".polaris" / "runtime"
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(local_runtime))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    clear_storage_roots_cache()

    roots = resolve_storage_roots(str(workspace))

    assert Path(roots.runtime_root).resolve() == local_runtime.resolve()
    assert roots.runtime_mode == "project_local_explicit"
    assert "/projects/" not in Path(roots.runtime_root).as_posix()


def test_runtime_base_writable_probe_is_concurrency_safe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime-root"
    runtime_root.mkdir()
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("KERNELONE_RUNTIME_CACHE_ROOT", str(runtime_root))
    clear_storage_roots_cache()

    workspaces = []
    for index in range(32):
        workspace = tmp_path / f"workspace-{index}"
        workspace.mkdir()
        workspaces.append(workspace)

    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(resolve_storage_roots, str(workspace)) for workspace in workspaces]
        roots = [future.result() for future in as_completed(futures)]

    assert len(roots) == len(workspaces)
    assert {root.runtime_mode for root in roots} == {"explicit_runtime_root"}
    assert all(Path(root.runtime_base) == runtime_root for root in roots)


def test_explicit_project_runtime_root_is_not_nested_again(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``KERNELONE_RUNTIME_ROOT`` may already be the resolved project runtime.

    ``serve --runtime-root`` writes that path into the env. Re-appending
    ``projects/<key>/runtime`` creates a second empty factory store.
    """
    cache_base = tmp_path / "runtime-cache"
    cache_base.mkdir()
    workspace = tmp_path / "f21e79dac015d4f121370610"
    workspace.mkdir()
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(cache_base))
    clear_storage_roots_cache()

    canonical = resolve_storage_roots(str(workspace))
    project_runtime = Path(canonical.runtime_root)
    project_runtime.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(project_runtime))
    monkeypatch.setenv("KERNELONE_RUNTIME_CACHE_ROOT", str(project_runtime))
    clear_storage_roots_cache()

    rebound = resolve_storage_roots(str(workspace))
    assert Path(rebound.runtime_root).resolve() == project_runtime.resolve()
    nested = project_runtime / "projects" / canonical.workspace_key / "runtime"
    assert Path(rebound.runtime_root).resolve() != nested.resolve()


def test_storage_roots_polaris_compat(tmp_path: Path) -> None:
    """When bootstrap sets .polaris as metadata dir, paths reflect that."""
    from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

    workspace = tmp_path / "project"
    workspace.mkdir(parents=True, exist_ok=True)

    original_meta = get_workspace_metadata_dir_name()
    set_workspace_metadata_dir_name(".polaris")
    try:
        roots = resolve_storage_roots(str(workspace))
        assert Path(roots.project_persistent_root).as_posix().endswith("/.polaris")
        assert Path(roots.runtime_project_root).as_posix().endswith("/runtime")
    finally:
        # Restore to Polaris's actual value
        set_workspace_metadata_dir_name(original_meta)


def test_prefix_guards_and_aliases(tmp_path: Path) -> None:
    """Logical path guards block the metadata dir prefix."""
    from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)

    current_meta = get_workspace_metadata_dir_name()

    docs_path = Path(resolve_workspace_persistent_path(str(workspace), "workspace/docs/plan.md"))
    # Polaris uses .polaris as metadata dir
    assert docs_path.as_posix().endswith(f"/{current_meta}/docs/plan.md")

    # Guard blocks current metadata dir prefix in logical paths
    with pytest.raises(ValueError) as exc_info:
        resolve_runtime_path(str(workspace), f"{current_meta}/runtime/events/runtime.events.jsonl")
    assert UNSUPPORTED_PATH_PREFIX in str(exc_info.value)


def test_prefix_guards_polaris_compat(tmp_path: Path) -> None:
    """Guard blocks .polaris/ prefix when Polaris metadata dir is set."""
    from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)

    original_meta = get_workspace_metadata_dir_name()
    set_workspace_metadata_dir_name(".polaris")
    try:
        with pytest.raises(ValueError) as exc_info:
            # .polaris/ prefix must not be used in runtime paths - it's added by storage layout
            resolve_runtime_path(str(workspace), ".polaris/runtime/events/runtime.events.jsonl")
        assert UNSUPPORTED_PATH_PREFIX in str(exc_info.value)
    finally:
        # Restore to Polaris's actual value
        set_workspace_metadata_dir_name(original_meta)


def test_global_path_under_polaris_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "hp-home"
    monkeypatch.setenv("KERNELONE_HOME", str(home))

    cfg = Path(resolve_global_path("config/settings.json"))
    assert cfg.as_posix().endswith("/hp-home/config/settings.json")


def test_global_path_ignores_appdata_by_default_on_windows_like_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    user_home = tmp_path / "user-home"
    user_home.mkdir()
    appdata = tmp_path / "AppData" / "Roaming"
    appdata.mkdir(parents=True)
    monkeypatch.delenv("KERNELONE_HOME", raising=False)
    monkeypatch.delenv("KERNELONE_ROOT", raising=False)
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("USERPROFILE", str(user_home))
    monkeypatch.setattr(
        os.path, "expanduser", lambda value: str(user_home / ".polaris") if value == "~/.polaris" else value
    )
    clear_storage_roots_cache()

    cfg = Path(resolve_global_path("config/settings.json"))

    assert cfg == user_home / ".polaris" / "config" / "settings.json"
    assert appdata not in cfg.parents


def test_runtime_path_is_outside_workspace_when_external_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir(parents=True, exist_ok=True)

    runtime_root = tmp_path / "runtime-cache"
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")

    runtime_file = Path(resolve_runtime_path(str(workspace), "runtime/events/e.jsonl"))
    assert workspace.resolve() not in runtime_file.parents
    assert os.path.commonpath([str(runtime_root.resolve()), str(runtime_file)]) == str(runtime_root.resolve())


def test_storage_layout_matches_runtime_roots_when_runtime_base_contains_metadata_dir(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir(parents=True, exist_ok=True)
    runtime_base = tmp_path / ".polaris" / "e2e-home" / "runtime-cache"
    runtime_base.mkdir(parents=True, exist_ok=True)

    layout = StorageLayout(workspace, runtime_base)
    key = workspace_key(str(workspace.resolve()))

    assert layout.runtime_root == runtime_base.resolve() / "projects" / key / "runtime"
    assert ".polaris/.polaris" not in layout.runtime_root.as_posix()
