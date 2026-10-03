"""Real append handles retain safe leaf identity and descriptor ownership."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter


def test_guarded_log_appends_utf8_and_returns_owned_handle(tmp_path: Path) -> None:
    fs = KernelFileSystem(str(tmp_path), get_default_adapter())
    with fs.workspace_open_log_append("service.log") as handle:
        descriptor = handle.fileno()
        assert not os.get_inheritable(descriptor)
        handle.write("原始\n")
    with pytest.raises(OSError):
        os.fstat(descriptor)
    with fs.workspace_open_log_append("service.log") as handle:
        handle.write("appended\n")
    assert (tmp_path / "service.log").read_text(encoding="utf-8") == "原始\nappended\n"


@pytest.mark.parametrize("attack", ["symlink", "hardlink", "uid", "directory"])
def test_guarded_log_rejects_unsafe_leaf_and_closes_descriptor(
    tmp_path: Path, attack: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.kernelone.exceptions import PathSecurityError

    fs = KernelFileSystem(str(tmp_path), get_default_adapter())
    target = tmp_path / "target"
    target.write_text("untouched", encoding="utf-8")
    leaf = tmp_path / "service.log"
    if attack == "symlink":
        leaf.symlink_to(target)
    elif attack == "hardlink":
        os.link(target, leaf)
    elif attack == "uid":
        leaf.write_text("untouched", encoding="utf-8")
        uid = os.getuid()
        monkeypatch.setattr(os, "getuid", lambda: uid + 1)
    else:
        leaf.mkdir()
    descriptors = set(os.listdir("/proc/self/fd"))
    with pytest.raises((OSError, RuntimeError, ValueError, PathSecurityError)):
        fs.workspace_open_log_append("service.log")
    assert set(os.listdir("/proc/self/fd")) == descriptors
    assert target.read_text(encoding="utf-8") == "untouched"


def test_guarded_log_root_alias_rejected_before_create(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    fs = KernelFileSystem(str(alias), get_default_adapter())
    with pytest.raises(RuntimeError, match="symbolic links"):
        fs.workspace_open_log_append("service.log")
    assert list(target.iterdir()) == []


def test_guarded_log_closes_leaf_on_handle_conversion_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from polaris.kernelone.fs import text_ops

    def reject(*args: Any, **kwargs: Any) -> Any:
        raise OSError("conversion failed")

    fs = KernelFileSystem(str(tmp_path), get_default_adapter())
    before = set(os.listdir("/proc/self/fd"))
    monkeypatch.setattr(text_ops.os, "fdopen", reject)
    with pytest.raises(OSError, match="conversion failed"):
        fs.workspace_open_log_append("service.log")
    assert set(os.listdir("/proc/self/fd")) == before


def test_guarded_log_root_rebind_before_open_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from polaris.kernelone.fs import text_ops

    root = tmp_path / "root"
    root.mkdir()
    moved = tmp_path / "moved"
    fs = KernelFileSystem(str(root), get_default_adapter())
    original = text_ops._retain_directory

    def rebound(*args: Any, **kwargs: Any) -> Any:
        witness = original(*args, **kwargs)
        if kwargs.get("display_name") == str(root):
            root.rename(moved)
            root.mkdir()
        return witness

    monkeypatch.setattr(text_ops, "_retain_directory", rebound)
    with pytest.raises(RuntimeError, match="identity changed"):
        fs.workspace_open_log_append("service.log")
    assert list(root.iterdir()) == list(moved.iterdir()) == []
