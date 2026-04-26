from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.runtime.projection.internal.file_io import (
    write_text_atomic as projection_write_text_atomic,
)
from polaris.infrastructure.compat.io_utils import (
    write_text_atomic as compat_write_text_atomic,
)
from polaris.kernelone.fs.text_ops import (
    append_text_atomic as kernel_append_text_atomic,
    write_text_atomic as kernel_write_text_atomic,
)
from polaris.kernelone.storage import resolve_storage_roots


def test_kernel_write_text_atomic_accepts_explicit_utf8(tmp_path: Path) -> None:
    target = tmp_path / "kernel.txt"
    kernel_write_text_atomic(str(target), "kernel", encoding="utf-8", lock_timeout_sec=None)
    assert target.read_text(encoding="utf-8") == "kernel"


def test_kernel_write_text_atomic_rejects_non_utf8(tmp_path: Path) -> None:
    target = tmp_path / "kernel.txt"
    with pytest.raises(ValueError, match="UTF-8"):
        kernel_write_text_atomic(str(target), "kernel", encoding="utf-16", lock_timeout_sec=None)


def test_kernel_append_text_atomic_accepts_explicit_utf8(tmp_path: Path) -> None:
    target = tmp_path / "append.txt"
    kernel_append_text_atomic(str(target), "a", encoding="utf-8", lock_timeout_sec=None)
    kernel_append_text_atomic(str(target), "b", encoding="utf-8", lock_timeout_sec=None)
    assert target.read_text(encoding="utf-8") == "ab"


def test_kernel_append_text_atomic_rejects_non_utf8(tmp_path: Path) -> None:
    target = tmp_path / "append.txt"
    with pytest.raises(ValueError, match="UTF-8"):
        kernel_append_text_atomic(str(target), "a", encoding="utf-16", lock_timeout_sec=None)


def test_compat_write_text_atomic_accepts_explicit_utf8(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_WORKSPACE", str(tmp_path))
    roots = resolve_storage_roots(str(tmp_path))
    target = Path(roots.workspace_persistent_root) / "compat.txt"
    compat_write_text_atomic("workspace/compat.txt", "compat", encoding="utf-8")
    assert target.read_text(encoding="utf-8") == "compat"


def test_compat_write_text_atomic_rejects_non_utf8(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_WORKSPACE", str(tmp_path))
    with pytest.raises(ValueError, match="UTF-8"):
        compat_write_text_atomic("workspace/compat.txt", "compat", encoding="utf-16")


def test_projection_write_text_atomic_accepts_explicit_utf8(tmp_path: Path) -> None:
    target = tmp_path / "projection.txt"
    projection_write_text_atomic(str(target), "projection", encoding="utf-8")
    assert target.read_text(encoding="utf-8") == "projection"


def test_projection_write_text_atomic_rejects_non_utf8(tmp_path: Path) -> None:
    target = tmp_path / "projection.txt"
    with pytest.raises(ValueError, match="UTF-8"):
        projection_write_text_atomic(str(target), "projection", encoding="utf-16")
