from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from polaris.kernelone.llm.toolkit.protocol_kernel import apply_protocol_output


def _make_workspace() -> Path:
    backend_root = Path(__file__).resolve().parents[1]
    workspace = backend_root / f".tmp_rich_edit_{uuid.uuid4().hex[:8]}"
    workspace.mkdir(parents=True, exist_ok=False)
    return workspace


def test_apply_protocol_output_routes_apply_patch_add_file() -> None:
    workspace = _make_workspace()
    try:
        text = "*** Begin Patch\n*** Add File: src/new.py\n+def new_fn():\n+    return 42\n*** End Patch\n"
        report = apply_protocol_output(text, str(workspace), strict=True, allow_fuzzy_match=True)
        assert report.success is True
        assert "src/new.py" in report.changed_files
        new_file = workspace / "src" / "new.py"
        assert new_file.exists()
        assert "def new_fn()" in new_file.read_text(encoding="utf-8")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_apply_protocol_output_routes_unified_diff_block() -> None:
    workspace = _make_workspace()
    try:
        target = workspace / "src" / "app.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("def old():\n    return 1\n", encoding="utf-8")

        text = (
            "```diff\n"
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ @@\n"
            "-def old():\n"
            "-    return 1\n"
            "+def new():\n"
            "+    return 2\n"
            "```\n"
        )
        report = apply_protocol_output(text, str(workspace), strict=True, allow_fuzzy_match=True)
        assert report.success is True
        updated = target.read_text(encoding="utf-8")
        assert "def new():" in updated
        assert "return 2" in updated
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_apply_protocol_output_apply_patch_move_to() -> None:
    workspace = _make_workspace()
    try:
        source = workspace / "src" / "app.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("def old():\n    return 1\n", encoding="utf-8")

        text = (
            "*** Begin Patch\n"
            "*** Update File: src/app.py\n"
            "*** Move to: src/app_renamed.py\n"
            "@@\n"
            "-def old():\n"
            "-    return 1\n"
            "+def new():\n"
            "+    return 2\n"
            "*** End Patch\n"
        )
        report = apply_protocol_output(text, str(workspace), strict=True, allow_fuzzy_match=True)
        assert report.success is True
        moved = workspace / "src" / "app_renamed.py"
        assert moved.exists()
        assert not source.exists()
        content = moved.read_text(encoding="utf-8")
        assert "def new():" in content
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_apply_protocol_output_move_to_outside_workspace_blocked() -> None:
    workspace = _make_workspace()
    try:
        source = workspace / "src" / "app.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("def old():\n    return 1\n", encoding="utf-8")

        text = (
            "*** Begin Patch\n"
            "*** Update File: src/app.py\n"
            "*** Move to: ../outside.py\n"
            "@@\n"
            "-def old():\n"
            "+def new():\n"
            "*** End Patch\n"
        )
        report = apply_protocol_output(text, str(workspace), strict=True, allow_fuzzy_match=True)
        assert report.success is False
        assert (workspace / "outside.py").exists() is False
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
