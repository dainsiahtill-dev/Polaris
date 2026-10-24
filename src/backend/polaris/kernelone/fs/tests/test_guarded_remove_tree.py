"""Owned directory cleanup must preserve foreign or rebound physical objects."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter


@pytest.fixture
def fs(tmp_path: Path) -> KernelFileSystem:
    return KernelFileSystem(str(tmp_path), get_default_adapter(), read_only_paths=True)


def _remove(fs: KernelFileSystem, name: str, expected):
    remove = getattr(fs, "workspace_remove_tree", None)
    assert remove is not None, "KFS has no identity-bound directory cleanup capability"
    return remove(name, expected=expected)


def test_owned_directory_cleanup_allows_real_child_changes_and_keeps_link_targets(fs: KernelFileSystem, tmp_path: Path):
    fs.workspace_mkdir("owned")
    expected = fs.workspace_lstat("owned")
    fs.workspace_mkdir("owned/nested")
    fs.workspace_write_text("owned/nested/actual", "output", encoding="utf-8")
    fs.workspace_mkdir("outside")
    fs.workspace_write_text("outside/keep", "unchanged", encoding="utf-8")
    (tmp_path / "owned/alias").symlink_to(tmp_path / "outside", target_is_directory=True)
    _remove(fs, "owned", expected)
    assert not (tmp_path / "owned").exists()
    assert (tmp_path / "outside/keep").read_text(encoding="utf-8") == "unchanged"


@pytest.mark.parametrize("rebind", ["leaf", "parent", "root", "symlink"])
def test_cleanup_refuses_rebound_identity_without_deleting_replacement(
    fs: KernelFileSystem, tmp_path: Path, rebind: str
):
    fs.workspace_mkdir("parent/owned", parents=True)
    expected = fs.workspace_lstat("parent/owned")
    if rebind == "root":
        renamed = tmp_path.with_name(tmp_path.name + "-retained")
        tmp_path.rename(renamed)
        tmp_path.mkdir()
        replacement = KernelFileSystem(str(tmp_path), get_default_adapter(), read_only_paths=True)
        replacement.workspace_mkdir("parent/owned", parents=True)
    elif rebind == "parent":
        (tmp_path / "parent").rename(tmp_path / "retained-parent")
        fs.workspace_mkdir("parent/owned", parents=True)
    else:
        (tmp_path / "parent/owned").rename(tmp_path / "parent/retained")
        if rebind == "leaf":
            fs.workspace_mkdir("parent/owned")
        else:
            (tmp_path / "parent/owned").symlink_to(tmp_path / "parent/retained", target_is_directory=True)
    with pytest.raises((RuntimeError, ValueError)):
        _remove(fs, "parent/owned", expected)
    assert (tmp_path / "parent/owned").is_dir()


@pytest.mark.parametrize("name", [".", "..", "", "/", "owned/..", "owned/../other"])
def test_cleanup_never_accepts_root_ancestor_or_traversal(fs: KernelFileSystem, tmp_path: Path, name: str):
    fs.workspace_mkdir("owned")
    with pytest.raises(ValueError):
        _remove(fs, name, fs.workspace_lstat("owned"))
    assert (tmp_path / "owned").is_dir()


@pytest.mark.parametrize("field", ["root_inode", "root_device", "inode", "device", "mode", "relative_path"])
def test_cleanup_requires_exact_original_binding(fs: KernelFileSystem, tmp_path: Path, field: str):
    fs.workspace_mkdir("owned")
    expected = fs.workspace_lstat("owned")
    value = "other" if field == "relative_path" else 0 if field == "mode" else getattr(expected, field) + 1
    with pytest.raises((RuntimeError, ValueError)):
        _remove(fs, "owned", replace(expected, **{field: value}))
    assert (tmp_path / "owned").is_dir()


def test_cleanup_refuses_unsafe_platform_without_removing_owned_bytes(
    fs: KernelFileSystem, tmp_path: Path, monkeypatch
):
    import importlib

    module = importlib.import_module("polaris.kernelone.fs.guarded_remove_tree")
    fs.workspace_mkdir("owned")
    fs.workspace_write_text("owned/keep", "actual", encoding="utf-8")
    monkeypatch.setattr(module.shutil.rmtree, "avoids_symlink_attacks", False)
    with pytest.raises(RuntimeError, match="capability_unavailable"):
        _remove(fs, "owned", fs.workspace_lstat("owned"))
    assert (tmp_path / "owned/keep").read_text(encoding="utf-8") == "actual"
