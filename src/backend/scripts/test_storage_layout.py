"""Smoke tests for KernelOne storage layout APIs."""

from __future__ import annotations

import os
import tempfile

from polaris.kernelone.storage import (
    resolve_global_path,
    resolve_runtime_path,
    resolve_storage_roots,
    resolve_workspace_persistent_path,
    workspace_key,
)
from polaris.kernelone.storage.layout import kernelone_home as polaris_home


def test_storage_layout_smoke() -> None:
    """Verify storage helpers resolve sane, absolute paths."""
    with tempfile.TemporaryDirectory(prefix="polaris_test_") as workspace:
        roots = resolve_storage_roots(workspace)
        assert roots.storage_layout_mode
        assert workspace_key(workspace)
        assert polaris_home()

        assert os.path.isabs(resolve_global_path("config/settings.json"))
        assert os.path.isabs(
            resolve_workspace_persistent_path(
                workspace,
                "workspace/brain/MEMORY.jsonl",
            )
        )
        assert os.path.isabs(resolve_runtime_path(workspace, "runtime/logs/test.log"))
