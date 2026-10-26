"""Rejected namespace configuration must precede engineering file effects."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService


@pytest.mark.parametrize("kind", ["root", "cache", "ramdisk"])
def test_rejected_runtime_namespace_does_not_create_engineering_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "external-runtime"
    for key in ("KERNELONE_RUNTIME_ROOT", "KERNELONE_RUNTIME_CACHE_ROOT", "KERNELONE_RAMDISK_ROOT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    if kind == "root":
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(external))
    elif kind == "cache":
        monkeypatch.setenv("KERNELONE_RUNTIME_CACHE_ROOT", str(external))
    else:
        external.mkdir()
        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "1")
        monkeypatch.setenv("KERNELONE_RAMDISK_ROOT", str(external))
    before = tuple(workspace.iterdir())
    with pytest.raises(RuntimeError, match="canonical project-local root"):
        EngineeringRunService(workspace)
    assert tuple(workspace.iterdir()) == before
    assert not (workspace / ".polaris" / "runtime" / "engineering" / "sessions.sqlite3").exists()
    assert not external.exists() or not list(external.iterdir())


def test_missing_workspace_is_not_provisioned_by_namespace_preflight(tmp_path: Path) -> None:
    missing = tmp_path / "missing-workspace"
    with pytest.raises(FileNotFoundError):
        EngineeringRunService(missing)
    assert not missing.exists()


def test_file_workspace_retains_original_rejection_without_new_directories(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace-file"
    workspace.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ValueError, match=r"^workspace must be a directory$"):
        EngineeringRunService(workspace)
    assert workspace.read_text(encoding="utf-8") == "not a directory"
    assert not (tmp_path / ".polaris").exists()
