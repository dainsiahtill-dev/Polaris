from __future__ import annotations

import os
from pathlib import Path

import pytest
from polaris.kernelone._runtime_config import (
    set_workspace_metadata_dir_name,
)
from polaris.kernelone.storage import (
    UNSUPPORTED_PATH_PREFIX,
    resolve_global_path,
    resolve_runtime_path,
    resolve_storage_roots,
    resolve_workspace_persistent_path,
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
