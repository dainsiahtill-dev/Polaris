"""Tests for Role Kernel Director task-boundary verdict projection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from polaris.cells.roles.kernel.internal.kernel.task_boundary import (
    append_deferred_followup_task_boundary_verdict,
    build_director_task_boundary_verdict,
)


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


def test_deferred_followup_task_boundary_append_uses_owner_event_shape(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    captured: list[Any] = []

    import polaris.cells.control_plane.run_ledger.public as run_ledger_public

    monkeypatch.setattr(run_ledger_public, "append_run_ledger_event", captured.append)

    append_deferred_followup_task_boundary_verdict(
        workspace=str(tmp_path),
        task_id="TASK-1",
        run_id="run-1",
        reason="needs_followup_workflow",
        evidence_refs=["runtime/contexts/abc"],
    )

    assert len(captured) == 1
    command = captured[0]
    assert command.workspace == str(tmp_path)
    assert command.run_id == "run-1"
    assert command.event["event_type"] == "task_boundary_verdict"
    assert command.event["stage"] == "task_boundary"
    assert command.event["task_id"] == "TASK-1"
    assert command.event["run_id"] == "run-1"
    assert command.event["job_token"]["project_id"] == "TASK-1"
    verdict = command.event["task_boundary_verdict"]
    assert verdict["status"] == "deferred_followup_required"
    assert verdict["failure_class"] == "DEFERRED_FOLLOWUP_REQUIRED"
    assert verdict["responsible_layer"] == "execution_control_plane"
    assert verdict["evidence_refs"] == ["runtime/contexts/abc"]
