"""Tests for Role Kernel Director task-boundary verdict projection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from polaris.cells.roles.kernel.internal.kernel.task_boundary import (
    append_deferred_followup_task_boundary_verdict,
    append_role_turn_task_boundary_verdict,
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


def test_director_task_boundary_verdict_projects_declared_downstream_entrypoints(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"start": "node src/index.js"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    verdict = build_director_task_boundary_verdict(
        role="director",
        workspace=str(tmp_path),
        task_id="TASK-1-foundation",
        run_id="run-1",
        context_override={
            "target_files": ["package.json"],
            "context": {
                "project_declared_target_files": [
                    "package.json",
                    "src/index.js",
                ]
            },
        },
        tool_results=[
            {
                "tool": "write_file",
                "success": True,
                "effect_receipt": {"file": "package.json"},
            }
        ],
    )

    assert verdict is not None
    assert verdict["status"] == "completed_verified"
    assert verdict["failure_class"] == "PASSED"
    assert verdict["downstream_pending_artifacts"] == ["src/index.js"]


def test_director_task_boundary_verdict_allows_nodenext_ts_source_for_js_specifier(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "main.ts").write_text('import { x } from "./util.js";\nconsole.log(x);\n', encoding="utf-8")
    (src_dir / "util.ts").write_text("export const x = 1;\n", encoding="utf-8")

    verdict = build_director_task_boundary_verdict(
        role="director",
        workspace=str(tmp_path),
        task_id="TASK-1-nodenext",
        run_id="run-1",
        context_override={"target_files": ["src/main.ts", "src/util.ts"]},
        tool_results=[
            {"tool": "write_file", "success": True, "effect_receipt": {"file": "src/main.ts"}},
            {"tool": "write_file", "success": True, "effect_receipt": {"file": "src/util.ts"}},
        ],
    )

    assert verdict is not None
    assert verdict["status"] == "completed_verified"
    assert verdict["failure_class"] == "PASSED"
    assert verdict["unresolved_local_imports"] == []


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


def test_director_task_boundary_verdict_projects_evidence_policy(tmp_path: Path) -> None:
    verdict = build_director_task_boundary_verdict(
        role="director",
        workspace=str(tmp_path),
        task_id="TASK-1",
        run_id="run-1",
        context_override={
            "evidence_policy": {
                "required_modalities": ["command", "tool_effect"],
                "present_modalities": ["tool_effect"],
                "missing_required_modalities": ["command"],
                "failed_required_modalities": [],
            }
        },
        tool_results=[],
    )

    assert verdict is not None
    assert verdict["status"] == "execution_evidence_missing"
    assert verdict["failure_class"] == "EXECUTION_EVIDENCE_MISSING"
    assert verdict["missing_required_evidence_modalities"] == ["command"]


def test_director_task_boundary_verdict_projects_blocked_dependencies(tmp_path: Path) -> None:
    verdict = build_director_task_boundary_verdict(
        role="director",
        workspace=str(tmp_path),
        task_id="TASK-2",
        run_id="run-1",
        context_override={"blocked_dependencies": ["TASK-1"], "depends_on": ["TASK-0"]},
        tool_results=[],
    )

    assert verdict is not None
    assert verdict["status"] == "dependency_not_unlocked"
    assert verdict["failure_class"] == "DEPENDENCY_NOT_UNLOCKED"
    assert verdict["blocked_dependencies"] == ["TASK-1"]


def test_director_task_boundary_verdict_projects_verifier_policy(tmp_path: Path) -> None:
    verdict = build_director_task_boundary_verdict(
        role="director",
        workspace=str(tmp_path),
        task_id="TASK-1",
        run_id="run-1",
        context_override={
            "verifier_policy": {
                "required_verifiers": ["npm test"],
                "failed_required_verifiers": ["npm test"],
            }
        },
        tool_results=[],
    )

    assert verdict is not None
    assert verdict["status"] == "required_verifier_failed"
    assert verdict["failure_class"] == "COMPILER_OR_TEST_FAILURE"
    assert verdict["failed_required_verifiers"] == ["npm test"]


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


def test_role_turn_task_boundary_append_uses_director_event_shape(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    target = tmp_path / "src" / "index.py"
    target.parent.mkdir()
    target.write_text("print('planet weather')\n", encoding="utf-8")
    captured: list[Any] = []

    import polaris.cells.control_plane.run_ledger.public as run_ledger_public

    monkeypatch.setattr(run_ledger_public, "append_run_ledger_event", captured.append)

    returned = append_role_turn_task_boundary_verdict(
        role="director",
        workspace=str(tmp_path),
        task_id="TASK-1",
        run_id="run-1",
        context_override={"target_files": ["src/index.py"]},
        tool_results=[
            {
                "tool": "write_file",
                "success": True,
                "effect_receipt": {"file": "src/index.py"},
            }
        ],
        needs_followup_workflow=False,
        evidence_refs=["runtime/contexts/abc"],
    )

    assert len(captured) == 1
    command = captured[0]
    assert command.workspace == str(tmp_path)
    assert command.run_id == "run-1"
    assert command.event["event_type"] == "task_boundary_verdict"
    verdict = command.event["task_boundary_verdict"]
    assert verdict["status"] == "completed_verified"
    assert verdict["failure_class"] == "PASSED"
    assert verdict["target_files"] == ["src/index.py"]
    assert verdict["evidence_refs"] == ["runtime/contexts/abc"]
    assert returned == verdict


def test_role_turn_task_boundary_append_uses_deferred_event_shape(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    captured: list[Any] = []

    import polaris.cells.control_plane.run_ledger.public as run_ledger_public

    monkeypatch.setattr(run_ledger_public, "append_run_ledger_event", captured.append)

    returned = append_role_turn_task_boundary_verdict(
        role="director",
        workspace=str(tmp_path),
        task_id="TASK-1",
        run_id="run-1",
        context_override={"target_files": ["src/index.py"]},
        tool_results=[],
        needs_followup_workflow=True,
        workflow_reason="needs_followup_workflow",
        evidence_refs=["runtime/contexts/abc"],
    )

    assert len(captured) == 1
    verdict = captured[0].event["task_boundary_verdict"]
    assert verdict["status"] == "deferred_followup_required"
    assert verdict["failure_class"] == "DEFERRED_FOLLOWUP_REQUIRED"
    assert verdict["reason"] == "needs_followup_workflow"
    assert verdict["evidence_refs"] == ["runtime/contexts/abc"]
    assert returned == verdict
