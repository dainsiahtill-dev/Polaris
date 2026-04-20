"""T6: apply_patch_with_broadcast file corruption risk tests.

Context: The P0-5 fix added logger.error on failure path, but atomic write
(write-to-temp-then-rename) was NOT implemented. This means a crash mid-write
can corrupt the target file.

This test file serves two purposes:
1. Regression guard: verify that the logged-failure path works (error is observable).
2. Risk documentation: xfail test makes the atomic write gap explicit and auditable.

The xfail test MUST remain until atomic write is implemented and the P1 residual
risk is fully resolved.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

if TYPE_CHECKING:
    from pathlib import Path

# ─── Regression guard: failure path observability ─────────────────────────────


def test_apply_patch_failure_returns_ok_false(tmp_path: Path) -> None:
    """When workspace_write_text raises, apply_patch_with_broadcast must return ok=False."""
    from polaris.kernelone.events.file_event_broadcaster import apply_patch_with_broadcast

    workspace = str(tmp_path)
    target = tmp_path / "target.txt"
    target.write_text("hello world\n", encoding="utf-8")

    with patch(
        "polaris.kernelone.events.file_event_broadcaster.KernelFileSystem.workspace_write_text",
        side_effect=OSError("disk full"),
    ):
        result = apply_patch_with_broadcast(
            workspace=workspace,
            target_file="target.txt",
            patch="+added line\n",
        )

    assert result["ok"] is False
    assert "disk full" in result.get("error", "")


def test_apply_patch_failure_is_logged_at_error_level(tmp_path: Path, caplog) -> None:
    """When workspace_write_text raises, an ERROR log must be emitted."""
    from polaris.kernelone.events.file_event_broadcaster import apply_patch_with_broadcast

    workspace = str(tmp_path)
    target = tmp_path / "target.txt"
    target.write_text("hello world\n", encoding="utf-8")

    with (
        patch(
            "polaris.kernelone.events.file_event_broadcaster.KernelFileSystem.workspace_write_text",
            side_effect=OSError("disk full"),
        ),
        caplog.at_level(logging.ERROR, logger="polaris.kernelone.events.file_event_broadcaster"),
    ):
        apply_patch_with_broadcast(
            workspace=workspace,
            target_file="target.txt",
            patch="+added line\n",
        )

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records, "Expected ERROR log record when apply_patch_with_broadcast fails"
    assert any("apply_patch_with_broadcast" in r.message for r in error_records), (
        "ERROR log must mention 'apply_patch_with_broadcast' for diagnosability"
    )


def test_apply_patch_success_path(tmp_path: Path) -> None:
    """Sanity: successful patch application returns ok=True."""
    from polaris.kernelone.events.file_event_broadcaster import apply_patch_with_broadcast

    workspace = str(tmp_path)
    target = tmp_path / "target.txt"
    target.write_text("hello world\n", encoding="utf-8")

    result = apply_patch_with_broadcast(
        workspace=workspace,
        target_file="target.txt",
        patch="+added line\n",
    )

    assert result["ok"] is True
    assert result.get("applied") is True


# ─── Risk documentation: atomic write gap ─────────────────────────────────────


@pytest.mark.xfail(
    reason=(
        "P1 residual: atomic write NOT implemented in apply_patch_with_broadcast. "
        "Mid-write crash can corrupt the target file. "
        "Fix: write to a .tmp file first, then os.replace() atomically. "
        "This xfail must be removed when atomic write is implemented."
    ),
    strict=False,
)
def test_apply_patch_atomic_write_preserves_original_on_crash(tmp_path: Path) -> None:
    """XFAIL: If a write crashes mid-way, the original file content must be preserved.

    Current implementation: direct workspace_write_text without temp-file+rename.
    This means a crash during write leaves the file in an indeterminate state.

    The test simulates a crash by raising mid-way through the write operation.
    With proper atomic write (write to .tmp, then os.replace()), the original
    MUST be intact after the failure.
    """
    workspace = str(tmp_path)
    original_content = "original line 1\noriginal line 2\n"
    target = tmp_path / "important.txt"
    target.write_text(original_content, encoding="utf-8")

    write_call_count = 0

    def crash_on_second_write(rel_path, content, encoding="utf-8") -> None:
        nonlocal write_call_count
        write_call_count += 1
        if write_call_count == 1:
            # Simulate partial write: write corrupted content then crash
            target.write_text("<<CORRUPTED>>", encoding="utf-8")
            raise OSError("simulated mid-write crash")

    with patch(
        "polaris.kernelone.events.file_event_broadcaster.KernelFileSystem.workspace_write_text",
        side_effect=crash_on_second_write,
    ):
        from polaris.kernelone.events.file_event_broadcaster import apply_patch_with_broadcast

        result = apply_patch_with_broadcast(
            workspace=workspace,
            target_file="important.txt",
            patch="+new line\n",
        )

    assert result["ok"] is False
    # With atomic write: original content must be preserved
    # Without atomic write: file is corrupted with "<<CORRUPTED>>"
    actual_content = target.read_text(encoding="utf-8")
    assert actual_content == original_content, (
        f"File corrupted after mid-write crash: {actual_content!r}\n"
        "This failure confirms the P1 residual risk: atomic write not implemented."
    )
