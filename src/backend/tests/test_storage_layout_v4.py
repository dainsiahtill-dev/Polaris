from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.storage.layout import resolve_polaris_roots
from polaris.kernelone._runtime_config import (
    get_workspace_metadata_dir_default,
    set_workspace_metadata_dir_name,
)
from polaris.kernelone.storage import (
    UNSUPPORTED_PATH_PREFIX,
    clear_business_roots_resolver,
    clear_storage_roots_cache,
    register_business_roots_resolver,
    resolve_global_path,
    resolve_runtime_path,
    resolve_storage_roots,
    resolve_workspace_persistent_path,
    workspace_key,
)


@pytest.fixture(autouse=True)
def _polaris_metadata_dir():
    """Set .polaris as the workspace metadata dir for all tests in this module.

    Tests in this module verify Polaris-specific path conventions (e.g. .polaris
    in resolved paths). The generic StorageLayout uses the injected metadata dir name;
    this fixture ensures the Polaris name is active during the test session.
    """
    original = get_workspace_metadata_dir_default()
    clear_storage_roots_cache()
    set_workspace_metadata_dir_name(".polaris")
    register_business_roots_resolver(resolve_polaris_roots)
    yield
    clear_storage_roots_cache()
    clear_business_roots_resolver()
    set_workspace_metadata_dir_name(original)


def test_workspace_key_stable_and_not_collision_for_same_basename(tmp_path: Path) -> None:
    workspace_a = tmp_path / "team-a" / "demo"
    workspace_b = tmp_path / "team-b" / "demo"
    workspace_a.mkdir(parents=True, exist_ok=True)
    workspace_b.mkdir(parents=True, exist_ok=True)

    key_a_first = workspace_key(str(workspace_a))
    key_a_second = workspace_key(str(workspace_a))
    key_b = workspace_key(str(workspace_b))

    assert key_a_first == key_a_second
    assert key_a_first != key_b
    assert key_a_first.startswith("demo-")
    assert key_b.startswith("demo-")


def test_runtime_base_priority_explicit_runtime_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    explicit_runtime = tmp_path / "runtime-explicit"
    monkeypatch.setenv("POLARIS_RUNTIME_ROOT", str(explicit_runtime))
    monkeypatch.delenv("POLARIS_RUNTIME_CACHE_ROOT", raising=False)
    monkeypatch.delenv("POLARIS_RAMDISK_ROOT", raising=False)
    monkeypatch.setenv("POLARIS_STATE_TO_RAMDISK", "1")

    roots = resolve_storage_roots(str(workspace))
    assert Path(roots.runtime_base) == explicit_runtime.resolve()
    assert roots.runtime_mode == "explicit_runtime_root"
    runtime_root_posix = Path(roots.runtime_root).as_posix()
    assert "/.polaris/projects/" in runtime_root_posix
    assert runtime_root_posix.endswith("/runtime")


def test_runtime_base_rejects_workspace_scoped_explicit_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("POLARIS_RUNTIME_ROOT", str(workspace))
    monkeypatch.delenv("POLARIS_RUNTIME_CACHE_ROOT", raising=False)
    monkeypatch.setenv("POLARIS_STATE_TO_RAMDISK", "0")

    roots = resolve_storage_roots(str(workspace))
    assert roots.runtime_mode != "explicit_runtime_root"
    assert workspace.resolve() not in Path(roots.runtime_root).resolve().parents


def test_runtime_base_priority_ramdisk_then_cache_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    ramdisk_root = tmp_path / "ramdisk"
    cache_root = tmp_path / "cache-root"
    ramdisk_root.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.delenv("POLARIS_RUNTIME_ROOT", raising=False)
    monkeypatch.setenv("POLARIS_STATE_TO_RAMDISK", "1")
    monkeypatch.setenv("POLARIS_RAMDISK_ROOT", str(ramdisk_root))
    monkeypatch.setenv("POLARIS_RUNTIME_CACHE_ROOT", str(cache_root))

    # Use Cell layer API directly — this tests HP ramdisk behavior, not kernelone conditional delegation.
    roots = resolve_polaris_roots(str(workspace), ramdisk_root=str(ramdisk_root))
    assert Path(roots.runtime_base) == ramdisk_root.resolve()
    assert roots.runtime_mode == "ramdisk"
    runtime_root_posix = Path(roots.runtime_root).as_posix()
    assert "/.polaris/projects/" in runtime_root_posix
    assert runtime_root_posix.endswith("/runtime")

    # Disable ramdisk: should fall back to explicit runtime cache root.
    monkeypatch.setenv("POLARIS_STATE_TO_RAMDISK", "0")
    roots_cache = resolve_polaris_roots(str(workspace), ramdisk_root=None)
    assert Path(roots_cache.runtime_base) == cache_root.resolve()
    assert roots_cache.runtime_mode == "explicit_runtime_cache"
    cache_root_posix = Path(roots_cache.runtime_root).as_posix()
    assert "/.polaris/projects/" in cache_root_posix
    assert cache_root_posix.endswith("/runtime")


def test_runtime_base_rejects_workspace_scoped_cache_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    cache_root = workspace / "cache-local"
    cache_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.delenv("POLARIS_RUNTIME_ROOT", raising=False)
    monkeypatch.setenv("POLARIS_STATE_TO_RAMDISK", "0")
    monkeypatch.setenv("POLARIS_RUNTIME_CACHE_ROOT", str(cache_root))

    roots = resolve_storage_roots(str(workspace))
    assert roots.runtime_mode != "explicit_runtime_cache"
    assert workspace.resolve() not in Path(roots.runtime_root).resolve().parents


def test_runtime_resolver_rejects_dot_polaris_prefix(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)

    with pytest.raises(ValueError) as exc_info:
        resolve_runtime_path(str(workspace), "runtime/events/runtime.events.jsonl")
    assert UNSUPPORTED_PATH_PREFIX in str(exc_info.value)


def test_runtime_resolver_rejects_old_legacy_prefixes(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)

    for rel in (
        "polaris/runtime/events/runtime.events.jsonl",
        "state/ollama/DIALOGUE.jsonl",
    ):
        with pytest.raises(ValueError) as exc_info:
            resolve_runtime_path(str(workspace), rel)
        assert UNSUPPORTED_PATH_PREFIX in str(exc_info.value)


def test_runtime_resolver_rejects_unknown_dot_polaris_prefix(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)

    with pytest.raises(ValueError) as exc_info:
        resolve_runtime_path(str(workspace), ".polaris/unknown/path.txt")
    assert UNSUPPORTED_PATH_PREFIX in str(exc_info.value)


def test_legacy_artifact_aliases_resolve_to_storage_layout_roots(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)

    docs_plan = Path(resolve_workspace_persistent_path(str(workspace), "docs/plan.md"))
    tasks_plan = Path(resolve_runtime_path(str(workspace), "tasks/plan.json"))
    dispatch_log = Path(resolve_runtime_path(str(workspace), "dispatch/log.json"))

    assert docs_plan.as_posix().endswith("/.polaris/docs/plan.md")
    assert tasks_plan.as_posix().endswith("/runtime/tasks/plan.json")
    assert dispatch_log.as_posix().endswith("/runtime/dispatch/log.json")


def test_workspace_persistent_and_runtime_paths_are_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir(parents=True, exist_ok=True)

    runtime_file = Path(resolve_runtime_path(str(workspace), "runtime/events/runtime.events.jsonl"))
    persistent_file = Path(resolve_workspace_persistent_path(str(workspace), "workspace/brain/MEMORY.jsonl"))

    workspace_abs = workspace.resolve()
    assert workspace_abs not in runtime_file.parents
    assert workspace_abs in persistent_file.parents
    assert persistent_file.as_posix().endswith("/.polaris/brain/MEMORY.jsonl")


def test_storage_layout_exposes_global_project_runtime_taxonomy(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir(parents=True, exist_ok=True)

    roots = resolve_storage_roots(str(workspace))
    assert roots.storage_layout_mode == "project_local"
    assert Path(roots.config_root).as_posix().endswith("/.polaris/config")
    assert Path(roots.project_persistent_root).as_posix().endswith("/.polaris")
    assert Path(roots.runtime_project_root).as_posix().endswith("/runtime")

    global_cfg = resolve_global_path("config/settings.json")
    assert Path(global_cfg).as_posix().endswith("/.polaris/config/settings.json")


def test_project_and_global_logical_prefix_aliases_are_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir(parents=True, exist_ok=True)

    with pytest.raises(ValueError) as project_exc:
        resolve_workspace_persistent_path(str(workspace), "project/meta/workspace_status.json")
    assert UNSUPPORTED_PATH_PREFIX in str(project_exc.value)

    with pytest.raises(ValueError) as global_exc:
        resolve_global_path("global/config/settings.json")
    assert UNSUPPORTED_PATH_PREFIX in str(global_exc.value)
