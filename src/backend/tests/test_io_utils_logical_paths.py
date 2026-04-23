from __future__ import annotations

import os
import sys
from pathlib import Path

MODULE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "core", "polaris_loop")
)
if MODULE_DIR not in sys.path:
    sys.path.insert(0, MODULE_DIR)

from polaris.infrastructure.compat import io_utils  # noqa: E402
from polaris.kernelone.storage import resolve_storage_roots  # noqa: E402


def test_resolve_artifact_path_workspace_prefix_maps_to_persistent_root(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "docs").mkdir(parents=True, exist_ok=True)

    polaris_home = tmp_path / "polaris_home"
    monkeypatch.setenv("KERNELONE_HOME", str(polaris_home))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    monkeypatch.delenv("KERNELONE_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("KERNELONE_RUNTIME_CACHE_ROOT", raising=False)
    monkeypatch.delenv("KERNELONE_RAMDISK_ROOT", raising=False)

    workspace_str = str(workspace)
    cache_root = io_utils.build_cache_root("", workspace_str)
    resolved = io_utils.resolve_artifact_path(
        workspace_str,
        cache_root,
        "workspace/docs/product/requirements.md",
    )

    roots = resolve_storage_roots(workspace_str)
    expected = (
        Path(roots.workspace_persistent_root)
        / "docs"
        / "product"
        / "requirements.md"
    )
    legacy_wrong = workspace / "workspace" / "docs" / "product" / "requirements.md"

    assert Path(resolved) == expected
    assert Path(resolved) != legacy_wrong


