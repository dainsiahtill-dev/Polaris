from __future__ import annotations

import json
from pathlib import Path

from polaris.cells.control_plane.run_ledger.public.task_boundary import (
    evaluate_task_boundary_verdict,
)


def test_task_boundary_reports_incomplete_materialization(tmp_path: Path) -> None:
    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-1",
        run_id="run-1",
        target_files=["src/index.js"],
    ).to_dict()

    assert verdict["ok"] is False
    assert verdict["status"] == "incomplete_materialization"
    assert verdict["failure_class"] == "INCOMPLETE_MATERIALIZATION"
    assert verdict["responsible_layer"] == "director"
    assert verdict["missing_target_files"] == ["src/index.js"]


def test_task_boundary_reports_missing_package_entrypoint_when_not_declared_downstream(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"start": "node src/index.js"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-1",
        run_id="run-1",
        target_files=["package.json"],
    ).to_dict()

    assert verdict["ok"] is False
    assert verdict["status"] == "missing_entrypoint_target"
    assert verdict["failure_class"] == "MISSING_ENTRYPOINT_TARGET"
    assert verdict["missing_entrypoint_targets"] == ["src/index.js"]


def test_task_boundary_allows_package_entrypoint_declared_downstream(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"start": "node src/index.js"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-1",
        run_id="run-1",
        target_files=["package.json"],
        downstream_pending_artifacts=["src/index.js"],
    ).to_dict()

    assert verdict["ok"] is True
    assert verdict["status"] == "completed_verified"


def test_task_boundary_reports_tool_dispatch_dropped(tmp_path: Path) -> None:
    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-1",
        run_id="run-1",
        tool_dispatch={"status": "dropped", "native_tool_calls_count": 1},
    ).to_dict()

    assert verdict["ok"] is False
    assert verdict["status"] == "tool_dispatch_dropped"
    assert verdict["failure_class"] == "TOOL_DISPATCH_DROPPED"
    assert verdict["responsible_layer"] == "execution_control_plane"
