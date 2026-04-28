from __future__ import annotations

from pathlib import Path

from polaris.delivery.http.routers.docs import _sync_plan_to_runtime
from polaris.kernelone.storage.io_paths import build_cache_root, resolve_artifact_path


def test_plan_sync_no_plan_source_is_noop(tmp_path):
    """When no plan source exists, _sync_plan_to_runtime should be a no-op."""
    workspace = str(tmp_path)
    cache_root = build_cache_root("", workspace)
    _sync_plan_to_runtime(workspace, cache_root)


def test_sync_plan_to_runtime_skips_when_source_missing(tmp_path):
    """When plan source is missing, runtime plan should not be created."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    _sync_plan_to_runtime(str(workspace), "")

    runtime_plan = Path(
        resolve_artifact_path(
            str(workspace),
            "",
            "runtime/contracts/plan.md",
        )
    )
    assert not runtime_plan.exists()


def test_sync_plan_to_runtime_copies_and_is_idempotent(tmp_path):
    """Plan should be copied to runtime and idempotent across multiple calls."""
    workspace = tmp_path / "workspace"
    plan_src = workspace / "docs" / "product" / "plan.md"
    plan_src.parent.mkdir(parents=True, exist_ok=True)
    plan_src.write_text("# Plan\n- item A\n", encoding="utf-8")

    _sync_plan_to_runtime(str(workspace), "")
    _sync_plan_to_runtime(str(workspace), "")

    runtime_plan = Path(
        resolve_artifact_path(
            str(workspace),
            "",
            "runtime/contracts/plan.md",
        )
    )
    assert runtime_plan.exists()
    assert runtime_plan.read_text(encoding="utf-8") == "# Plan\n- item A\n"
    assert not runtime_plan.with_suffix(".md.tmp").exists()
