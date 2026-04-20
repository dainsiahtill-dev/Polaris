import os
import sys

import pytest

MODULE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "core", "polaris_loop")
)
if MODULE_DIR not in sys.path:
    sys.path.insert(0, MODULE_DIR)

from polaris.cells.docs.court_workflow.internal import plan_template
from polaris.infrastructure.compat import io_utils  # noqa: E402


def _build_plan_path(workspace: str) -> str:
    cache_root = io_utils.build_cache_root("", workspace)
    return io_utils.resolve_artifact_path(
        workspace,
        cache_root,
        "runtime/contracts/plan.md",
    )


def test_ensure_plan_file_raises_when_missing(tmp_path):
    workspace = str(tmp_path)
    os.makedirs(os.path.join(workspace, "docs"), exist_ok=True)
    plan_path = _build_plan_path(workspace)

    with pytest.raises(FileNotFoundError, match="Plan contract missing"):
        plan_template.ensure_plan_file(plan_path, auto_continue=True)


def test_ensure_plan_file_raises_for_legacy_template(tmp_path):
    workspace = str(tmp_path)
    os.makedirs(os.path.join(workspace, "docs"), exist_ok=True)
    plan_path = _build_plan_path(workspace)
    os.makedirs(os.path.dirname(plan_path), exist_ok=True)
    with open(plan_path, "w", encoding="utf-8") as handle:
        handle.write(
            "# Legacy\n"
            "# - MMO_CORE_SPEC.md\n"
            "# - apps/game-client/src/main.ts\n"
            "# - apps/physics-lab/src/main.ts\n"
        )

    with pytest.raises(RuntimeError, match="legacy template"):
        plan_template.ensure_plan_file(plan_path, auto_continue=True)


def test_ensure_plan_file_passes_for_explicit_valid_plan(tmp_path):
    workspace = str(tmp_path)
    os.makedirs(os.path.join(workspace, "docs"), exist_ok=True)
    plan_path = _build_plan_path(workspace)
    os.makedirs(os.path.dirname(plan_path), exist_ok=True)
    with open(plan_path, "w", encoding="utf-8") as handle:
        handle.write(
            "# 项目计划\n\n"
            "## 目标\n"
            "- 实现核心功能\n\n"
            "## 验收\n"
            "- `pytest -q`\n"
        )

    assert plan_template.ensure_plan_file(plan_path, auto_continue=False) is True
