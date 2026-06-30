from __future__ import annotations

from pathlib import Path

from polaris.cells.roles.adapters.internal.director.post_execution_repair_bridge import (
    _direct_runtime_writer,
)


def test_direct_runtime_writer_rejects_workspace_escape(tmp_path: Path) -> None:
    workspace = tmp_path.resolve()

    result = _direct_runtime_writer(workspace, "../outside.py", "print('bad')\n")

    assert result["ok"] is False
    assert result["error"] == "repair target path escaped workspace"
    assert not (workspace.parent / "outside.py").exists()


def test_direct_runtime_writer_writes_workspace_file(tmp_path: Path) -> None:
    workspace = tmp_path.resolve()

    result = _direct_runtime_writer(workspace, "src/app.py", "print('ok')\n")

    assert result == {
        "ok": True,
        "file": "src/app.py",
        "bytes_written": len(b"print('ok')\n"),
        "operation": "modify",
    }
    assert (workspace / "src" / "app.py").read_text(encoding="utf-8") == "print('ok')\n"
