"""Native KFS directory effects reject aliasing and root rebinding."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.exceptions import PathSecurityError
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter


def test_workspace_mkdir_creates_only_directories_with_physical_receipt(tmp_path: Path) -> None:
    fs = KernelFileSystem(str(tmp_path), get_default_adapter())
    receipt = fs.workspace_mkdir("cache/store", parents=True)
    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")) == ["cache", "cache/store"]
    assert receipt.created_paths == ("cache", "cache/store")
    assert receipt.root_identity == (tmp_path.stat().st_dev, tmp_path.stat().st_ino)
    target = tmp_path / "cache/store"
    assert receipt.target_identity == (target.stat().st_dev, target.stat().st_ino)
    assert receipt.relative_path == "cache/store"
    with pytest.raises(FileExistsError):
        fs.workspace_mkdir("cache/store")
    assert fs.workspace_mkdir("cache/store", exist_ok=True).created_paths == ()


@pytest.mark.parametrize("attack", ["symlink", "ancestor_symlink", "outside", "file", "missing_parent"])
def test_workspace_mkdir_rejects_unsafe_target_before_effect(tmp_path: Path, attack: str) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    fs = KernelFileSystem(str(root), get_default_adapter())
    path = "store"
    if attack in {"symlink", "ancestor_symlink"}:
        (root / "store").symlink_to(outside, target_is_directory=True)
        if attack == "ancestor_symlink":
            path = "store/child"
    elif attack == "outside":
        path = str(outside / "child")
    elif attack == "file":
        (root / "store").write_text("not a directory", encoding="utf-8")
    else:
        path = "missing/child"
    before = set(root.rglob("*"))
    with pytest.raises((RuntimeError, OSError, ValueError, PathSecurityError)):
        fs.workspace_mkdir(path, exist_ok=True)
    assert set(root.rglob("*")) == before
    assert list(outside.iterdir()) == []


def test_workspace_mkdir_rejects_root_rebinding_before_effect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from polaris.kernelone.fs import guarded_directory as module

    root = tmp_path / "root"
    root.mkdir()
    moved = tmp_path / "moved"
    fs = KernelFileSystem(str(root), get_default_adapter())
    original = module._retain_directory

    def rebound(*args: Any, **kwargs: Any) -> Any:
        witness = original(*args, **kwargs)
        if kwargs.get("display_name") == str(root):
            root.rename(moved)
            root.mkdir()
        return witness

    monkeypatch.setattr(module, "_retain_directory", rebound)
    with pytest.raises(RuntimeError, match="identity changed"):
        fs.workspace_mkdir("store")
    assert list(root.iterdir()) == list(moved.iterdir()) == []


def test_workspace_mkdir_does_not_canonicalize_away_root_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    fs = KernelFileSystem(str(alias), get_default_adapter())
    with pytest.raises(RuntimeError, match="symbolic links"):
        fs.workspace_mkdir("store")
    assert list(target.iterdir()) == []


@pytest.mark.parametrize("operation", ["mkdir", "append"])
@pytest.mark.parametrize("absolute", [False, True])
def test_descriptor_facades_never_resolve_requested_path_to_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, absolute: bool
) -> None:
    root = tmp_path / "root"
    requested = root / "requested"
    sibling = root / "sibling"
    requested.mkdir(parents=True)
    sibling.mkdir()
    target = requested / "effect"
    fs = KernelFileSystem(str(root), get_default_adapter())
    original = Path.resolve

    def swap_before_resolution(path: Path, *args: Any, **kwargs: Any) -> Path:
        if path == target and not requested.is_symlink():
            requested.rename(root / "original")
            requested.symlink_to(sibling, target_is_directory=True)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", swap_before_resolution)
    argument = str(target) if absolute else "requested/effect"
    if operation == "mkdir":
        receipt = fs.workspace_mkdir(argument)
        assert receipt.relative_path == "requested/effect"
        assert target.is_dir()
    else:
        with fs.workspace_open_log_append(argument) as handle:
            handle.write("exact lexical destination\n")
        assert target.read_text(encoding="utf-8") == "exact lexical destination\n"
    assert list(sibling.iterdir()) == []
    assert not requested.is_symlink()


@pytest.mark.parametrize("operation", ["mkdir", "append"])
@pytest.mark.parametrize("path_kind", ["relative_parent", "absolute_parent", "absolute_sibling"])
def test_descriptor_facade_lexical_boundary_rejects_escape_before_effect(
    tmp_path: Path, operation: str, path_kind: str
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "inside").mkdir()
    sibling = tmp_path / "root-other"
    sibling.mkdir()
    fs = KernelFileSystem(str(root), get_default_adapter())
    argument = {
        "relative_parent": "inside/../effect",
        "absolute_parent": str(root) + "/inside/../effect",
        "absolute_sibling": str(sibling / "effect"),
    }[path_kind]
    with pytest.raises((RuntimeError, ValueError)):
        if operation == "mkdir":
            fs.workspace_mkdir(argument)
        else:
            with fs.workspace_open_log_append(argument) as handle:
                handle.write("must not be written")
    assert sorted(path.name for path in root.iterdir()) == ["inside"]
    assert list(sibling.iterdir()) == []
