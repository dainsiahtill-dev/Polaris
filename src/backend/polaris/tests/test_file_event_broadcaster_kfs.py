from __future__ import annotations

from pathlib import Path

from polaris.infrastructure.storage import LocalFileSystemAdapter
from polaris.kernelone.events.file_event_broadcaster import (
    append_file_with_broadcast,
    apply_patch_with_broadcast,
    replace_in_file_with_broadcast,
    write_file_with_broadcast,
)
from polaris.kernelone.fs import set_default_adapter


def _configure_default_fs_adapter() -> None:
    set_default_adapter(LocalFileSystemAdapter())


def test_write_and_append_file_with_broadcast_use_kfs(tmp_path: Path) -> None:
    _configure_default_fs_adapter()
    result = write_file_with_broadcast(
        workspace=str(tmp_path),
        file_path="notes/log.txt",
        content="hello",
    )
    assert result["ok"] is True
    assert result["operation"] == "create"
    assert (tmp_path / "notes" / "log.txt").read_text(encoding="utf-8") == "hello"

    appended = append_file_with_broadcast(
        workspace=str(tmp_path),
        file_path="notes/log.txt",
        content="\nworld",
    )
    assert appended["ok"] is True
    assert (tmp_path / "notes" / "log.txt").read_text(encoding="utf-8") == "hello\nworld"


def test_replace_and_patch_with_broadcast_use_kfs(tmp_path: Path) -> None:
    _configure_default_fs_adapter()
    target = tmp_path / "src" / "fastapi_entrypoint.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("print('alpha')\nprint('beta')\n", encoding="utf-8")

    replaced = replace_in_file_with_broadcast(
        workspace=str(tmp_path),
        file_path="src/fastapi_entrypoint.py",
        old_text="beta",
        new_text="gamma",
    )
    assert replaced["ok"] is True
    assert "gamma" in target.read_text(encoding="utf-8")

    patched = apply_patch_with_broadcast(
        workspace=str(tmp_path),
        target_file="src/fastapi_entrypoint.py",
        patch="+print('delta')\n-print('alpha')",
    )
    assert patched["ok"] is True
    content = target.read_text(encoding="utf-8")
    assert "delta" in content
    assert "alpha" not in content


def test_file_broadcaster_rejects_workspace_escape(tmp_path: Path) -> None:
    _configure_default_fs_adapter()
    outside = tmp_path.parent / "escape.txt"
    result = write_file_with_broadcast(
        workspace=str(tmp_path),
        file_path=str(outside),
        content="bad",
    )
    assert result["ok"] is False
