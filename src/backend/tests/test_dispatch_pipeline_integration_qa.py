from __future__ import annotations

import os
import sys
from types import SimpleNamespace

BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_ROOT = os.path.join(BACKEND_ROOT, "scripts")
CORE_ROOT = os.path.join(BACKEND_ROOT, "core", "polaris_loop")
for candidate in (BACKEND_ROOT, SCRIPTS_ROOT, CORE_ROOT):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline import (
    run_post_dispatch_integration_qa,
)


def test_run_post_dispatch_integration_qa_accepts_args_keyword(tmp_path) -> None:
    # Use real filesystem paths, NOT KernelOne virtual paths (X:\...) which are
    # returned by resolve_artifact_path.  The function writes via os.makedirs +
    # write_json_atomic which needs real paths.
    run_dir = tmp_path / "runtime" / "runs" / "qa-accepts-args"
    run_dir.mkdir(parents=True, exist_ok=True)
    run_events = tmp_path / "runtime" / "events" / "runtime.events.jsonl"
    run_events.parent.mkdir(parents=True, exist_ok=True)
    dialogue_full = tmp_path / "runtime" / "events" / "dialogue.transcript.jsonl"
    dialogue_full.parent.mkdir(parents=True, exist_ok=True)

    payload = run_post_dispatch_integration_qa(
        args=SimpleNamespace(integration_qa=True),
        workspace_full=str(tmp_path),
        cache_root_full="",
        run_dir=str(run_dir),
        run_id="pm-00001",
        iteration=1,
        tasks=[
            {
                "id": "TASK-A",
                "assigned_to": "Director",
                "status": "needs_continue",
            }
        ],
        run_events=str(run_events),
        dialogue_full=str(dialogue_full),
    )

    assert payload["ran"] is False
    assert payload["passed"] is None
    assert payload["reason"] == "pending_director_tasks"
    result_path = run_dir / "qa" / "integration_qa.result.json"
    assert result_path.is_file(), f"result file not found at {result_path}"


def test_run_post_dispatch_integration_qa_supports_calls_without_args(tmp_path) -> None:
    # Use real filesystem paths, NOT KernelOne virtual paths (X:\...) which are
    # returned by resolve_artifact_path.  The function writes via os.makedirs +
    # write_json_atomic which needs real paths.
    run_dir = tmp_path / "runtime" / "runs" / "qa-no-args"
    run_dir.mkdir(parents=True, exist_ok=True)
    run_events = tmp_path / "runtime" / "events" / "runtime.events.jsonl"
    run_events.parent.mkdir(parents=True, exist_ok=True)
    dialogue_full = tmp_path / "runtime" / "events" / "dialogue.transcript.jsonl"
    dialogue_full.parent.mkdir(parents=True, exist_ok=True)

    payload = run_post_dispatch_integration_qa(
        workspace_full=str(tmp_path),
        cache_root_full="",
        run_dir=str(run_dir),
        run_id="pm-00002",
        iteration=2,
        tasks=[
            {
                "id": "TASK-A",
                "assigned_to": "Director",
                "status": "needs_continue",
            }
        ],
        run_events=str(run_events),
        dialogue_full=str(dialogue_full),
    )

    assert payload["ran"] is False
    assert payload["passed"] is None
    assert payload["reason"] == "pending_director_tasks"
    result_path = run_dir / "qa" / "integration_qa.result.json"
    assert result_path.is_file(), f"result file not found at {result_path}"
