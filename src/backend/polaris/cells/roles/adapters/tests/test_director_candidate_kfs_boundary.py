"""Rollback authority must not bypass registered workspace filesystem policy."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.roles.adapters.internal.director.quality_gate._candidate_guard import (
    DirectorQualityRepairCandidateGuard,
)
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.fs import registry


class _DeniedRollbackAdapter(LocalFileSystemAdapter):
    def write_text(self, path: str, content: str, *, encoding: str = "utf-8", atomic: bool = False) -> int:
        raise PermissionError("rollback denied by registered adapter")

    def write_bytes(self, path: str, content: bytes, *, atomic: bool = False) -> int:
        raise PermissionError("rollback denied by registered adapter")

    def remove(self, path: str, *, missing_ok: bool = True) -> bool:
        raise PermissionError("rollback denied by registered adapter")


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", ["// 已验证\n", None])
async def test_rollback_denial_retains_candidate_and_reports_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, initial: str | None
) -> None:
    """Both restoration and removal must fail honestly when filesystem policy denies them."""

    monkeypatch.setattr(registry, "_default_adapter", _DeniedRollbackAdapter())
    target = tmp_path / "main.rs"
    if initial is not None:
        target.write_text(initial, encoding="utf-8")
    guard = await DirectorQualityRepairCandidateGuard.capture(
        workspace=tmp_path, candidate_id="registered-policy", target_files=["main.rs"]
    )
    target.write_text("// 候选\n", encoding="utf-8")
    await guard.seal_effect()

    receipt = await guard.rollback(reason="candidate_rejected")

    assert receipt["status"] == "partial"
    assert receipt["restored_files"] == []
    assert receipt["failed_files"] == ["main.rs"]
    assert target.read_text(encoding="utf-8") == "// 候选\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("relative_path", ["runtime/main.rs", "workspace/main.rs"])
async def test_rollback_preserves_physical_workspace_path_not_logical_storage_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative_path: str
) -> None:
    """Using logical KFS write APIs would restore a different storage-root file."""

    monkeypatch.setattr(registry, "_default_adapter", LocalFileSystemAdapter())
    target = tmp_path / relative_path
    target.parent.mkdir()
    target.write_text("// 已验证\n", encoding="utf-8")
    guard = await DirectorQualityRepairCandidateGuard.capture(
        workspace=tmp_path, candidate_id="physical-workspace", target_files=[relative_path]
    )
    target.write_text("// 候选\n", encoding="utf-8")
    await guard.seal_effect()

    receipt = await guard.rollback(reason="candidate_rejected")

    assert receipt["status"] == "restored"
    assert receipt["restored_files"] == [relative_path]
    assert target.read_text(encoding="utf-8") == "// 已验证\n"
    assert not (tmp_path / ".polaris").exists()
