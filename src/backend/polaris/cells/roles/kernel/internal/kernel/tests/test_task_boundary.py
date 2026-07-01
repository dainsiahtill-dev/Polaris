"""Tests for Role Kernel Director task-boundary verdict projection."""

from __future__ import annotations

from pathlib import Path

from polaris.cells.roles.kernel.internal.kernel.task_boundary import build_director_task_boundary_verdict


def test_director_task_boundary_verdict_reports_missing_target(tmp_path: Path) -> None:
    verdict = build_director_task_boundary_verdict(
        role="director",
        workspace=str(tmp_path),
        task_id="TASK-1",
        run_id="run-1",
        context_override={"target_files": ["src/index.js"]},
        tool_results=[],
    )

    assert verdict is not None
    assert verdict["status"] == "incomplete_materialization"
    assert verdict["failure_class"] == "INCOMPLETE_MATERIALIZATION"
    assert verdict["missing_target_files"] == ["src/index.js"]


def test_director_task_boundary_verdict_reports_dropped_dispatch(tmp_path: Path) -> None:
    verdict = build_director_task_boundary_verdict(
        role="director",
        workspace=str(tmp_path),
        task_id="TASK-1",
        run_id="run-1",
        context_override={},
        tool_results=[],
        tool_dispatch={"status": "dropped", "dropped": True},
    )

    assert verdict is not None
    assert verdict["status"] == "tool_dispatch_dropped"
    assert verdict["failure_class"] == "TOOL_DISPATCH_DROPPED"
    assert verdict["responsible_layer"] == "execution_control_plane"


def test_director_task_boundary_verdict_skips_non_director(tmp_path: Path) -> None:
    assert (
        build_director_task_boundary_verdict(
            role="pm",
            workspace=str(tmp_path),
            task_id="TASK-1",
            run_id="run-1",
            context_override={"target_files": ["src/index.js"]},
            tool_results=[],
        )
        is None
    )
