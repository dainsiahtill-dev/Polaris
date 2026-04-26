"""Tests for dispatch_pipeline run_engine_dispatch."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_ROOT = os.path.join(BACKEND_ROOT, "scripts")
CORE_ROOT = os.path.join(BACKEND_ROOT, "core", "polaris_loop")
for candidate in (BACKEND_ROOT, SCRIPTS_ROOT, CORE_ROOT):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from polaris.cells.orchestration.pm_dispatch.internal import dispatch_pipeline  # noqa: E402
from polaris.cells.orchestration.workflow_runtime.internal.models import PMWorkflowInput  # noqa: E402
from polaris.cells.orchestration.workflow_runtime.internal.workflow_client import (  # noqa: E402
    WorkflowSubmissionResult,
)
from polaris.cells.runtime.artifact_store.public.service import resolve_artifact_path  # noqa: E402


class _DummyEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool, str]] = []

    def update_role_status(self, role: str, status: str, running: bool, detail: str, meta=None) -> None:
        self.calls.append((role, status, running, detail))


def _make_paths(tmp_path: Path) -> tuple[str, str]:
    run_events = resolve_artifact_path(str(tmp_path), "", "runtime/events/runtime.events.jsonl")
    dialogue_full = resolve_artifact_path(str(tmp_path), "", "runtime/events/dialogue.transcript.jsonl")
    return run_events, dialogue_full


def test_run_engine_dispatch_uses_workflow_client_contract(tmp_path: Path) -> None:
    engine = _DummyEngine()
    captured: dict[str, object] = {}
    run_events, dialogue_full = _make_paths(tmp_path)

    def _fake_submit(workflow_input: PMWorkflowInput, config=None) -> WorkflowSubmissionResult:
        captured["workflow_input"] = workflow_input
        return WorkflowSubmissionResult(
            submitted=True,
            status="started",
            workflow_id="wf-1",
            workflow_run_id="wf-run-1",
            details={"accepted": True},
        )

    result = dispatch_pipeline.run_engine_dispatch(
        args=SimpleNamespace(),
        engine=engine,
        workspace_full=str(tmp_path),
        run_id="pm-001",
        iteration=3,
        tasks=[{"id": "TASK-A", "assigned_to": "Director", "status": "todo"}],
        run_events=run_events,
        dialogue_full=dialogue_full,
        _submit_fn=_fake_submit,
    )

    workflow_input = captured["workflow_input"]
    assert isinstance(workflow_input, PMWorkflowInput)
    assert workflow_input.workspace == str(tmp_path)
    assert workflow_input.run_id == "pm-001"
    assert workflow_input.precomputed_payload["tasks"][0]["id"] == "TASK-A"
    assert result["exit_code"] == 0
    assert result["director_result"]["status"] == "queued"
    assert result["director_result"]["workflow_id"] == "wf-1"
    assert result["director_result"]["workflow_run_id"] == "wf-run-1"


def test_run_engine_dispatch_surfaces_failed_submission(tmp_path: Path) -> None:
    engine = _DummyEngine()
    run_events, dialogue_full = _make_paths(tmp_path)

    def _fake_fail(workflow_input: PMWorkflowInput, config=None) -> WorkflowSubmissionResult:
        return WorkflowSubmissionResult(
            submitted=False,
            status="invalid_request",
            error="workspace and run_id are required",
        )

    result = dispatch_pipeline.run_engine_dispatch(
        args=SimpleNamespace(),
        engine=engine,
        workspace_full=str(tmp_path),
        run_id="pm-002",
        iteration=1,
        tasks=[{"id": "TASK-B", "assigned_to": "Director", "status": "todo"}],
        run_events=run_events,
        dialogue_full=dialogue_full,
        _submit_fn=_fake_fail,
    )

    assert result["exit_code"] == 1
    assert result["error"] == "workspace and run_id are required"
    assert result["director_result"]["status"] == "invalid_request"
