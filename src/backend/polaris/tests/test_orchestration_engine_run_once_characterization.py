"""Characterization tests for ``run_once`` clusters lacking direct coverage.

These tests freeze the *current* behavior of ``run_once`` for the clusters
flagged in the decomposition blueprint as coverage gaps:

- the ramdisk ``RuntimeError`` guard,
- the dispatch-result unpack + traceability registration + final
  engine-status fan-out (``run_director`` enabled, workflow success),
- the post-dispatch blocked-policy stop block, and
- the duplicate ``_merge_engine_config`` delegating to ``EngineRuntimeConfig``.

They are intentionally driven through the public ``run_once`` entry point with
monkeypatch-through-namespace stubs (the established pattern in
``test_pm_runtime_plan_sync``), so they keep passing across the extraction of
the heavy bodies into sibling modules — that is exactly what they guard.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest
from polaris.delivery.cli.pm import orchestration_engine
from polaris.kernelone.storage.io_paths import resolve_artifact_path


def _build_args(workspace: Path, *, run_director: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        pm_backend="fake",
        workspace=str(workspace),
        model="fake-model",
        timeout=0,
        plan_path="runtime/contracts/plan.md",
        gap_report_path="runtime/contracts/gap_report.md",
        qa_path="runtime/results/qa.review.md",
        requirements_path="workspace/docs/product/requirements.md",
        pm_out="runtime/contracts/pm_tasks.contract.json",
        pm_report="runtime/results/pm.report.md",
        state_path="runtime/state/pm.state.json",
        task_history_path="runtime/events/pm.task_history.events.jsonl",
        director_result_path="runtime/results/director.result.json",
        director_events_path="runtime/events/runtime.events.jsonl",
        pm_task_path="runtime/contracts/pm_tasks.contract.json",
        loop=False,
        interval=1,
        max_iterations=0,
        max_failures=5,
        max_blocked=5,
        max_same_task=3,
        stop_on_failure=True,
        heartbeat=False,
        json_log="runtime/events/pm.events.jsonl",
        run_director=run_director,
        director_path="",
        events_path="runtime/events/runtime.events.jsonl",
        director_model="",
        director_timeout=0,
        director_show_output=False,
        director_result_timeout=60,
        director_iterations=1,
        director_workflow_execution_mode="parallel",
        director_max_parallel_tasks=1,
        director_ready_timeout_seconds=1,
        director_claim_timeout_seconds=1,
        director_phase_timeout_seconds=1,
        director_complete_timeout_seconds=1,
        director_task_timeout_seconds=1,
        director_match_mode="run_id",
        dialogue_path="runtime/events/dialogue.transcript.jsonl",
        pm_last_message_path="runtime/results/pm_last.output.md",
        ramdisk_root="",
        codex_profile="",
        codex_full_auto=True,
        codex_dangerous=False,
        clear_spin_guard=False,
        directive="",
        directive_file="",
        directive_stdin=False,
        directive_max_chars=200000,
        start_from="pm",
        prompt_profile="generic",
        pm_show_output=False,
        agents_approval_mode="auto_accept",
        agents_approval_timeout=0,
        orchestration_runtime="workflow",
        blocked_strategy="auto",
        blocked_degrade_max_retries=1,
        max_director_retries=5,
    )


def _seed_requirements(workspace: Path, text: str = "Build a local file server with tests.\n") -> None:
    requirements_path = Path(resolve_artifact_path(str(workspace), "", "workspace/docs/product/requirements.md"))
    requirements_path.parent.mkdir(parents=True, exist_ok=True)
    requirements_path.write_text(f"# Requirements\n\n{text}", encoding="utf-8")


def _single_task_payload() -> dict[str, Any]:
    return {
        "overall_goal": "File server",
        "focus": "Create executable task",
        "tasks": [
            {
                "id": "PM-CHAR-1",
                "task_id": "PM-CHAR-1",
                "title": "Implement file server scaffold",
                "goal": "Create the server entry point and tests.",
                "target_files": ["src/server.ts"],
                "execution_checklist": ["Create server module"],
                "acceptance": ["npm test passes"],
            }
        ],
    }


@pytest.fixture
def _planning_env(tmp_path: Path, monkeypatch: Any) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime_root = workspace / ".polaris" / "runtime"
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("KERNELONE_RUNTIME_CACHE_ROOT", str(runtime_root))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    monkeypatch.setattr(orchestration_engine, "resolve_pm_backend_kind", lambda *_a, **_k: ("fake", None))
    monkeypatch.setattr(orchestration_engine, "ensure_pm_backend_available", lambda *_a, **_k: None)
    monkeypatch.setattr(orchestration_engine, "wait_for_agents_confirmation", lambda *_a, **_k: True)
    monkeypatch.setattr(orchestration_engine, "check_stop_conditions", lambda *_a, **_k: None)
    _seed_requirements(workspace)
    return workspace


def test_run_once_ramdisk_guard_raises_runtime_error(_planning_env: Path, monkeypatch: Any) -> None:
    """When state-to-ramdisk is enabled but no cache root resolves, run_once raises."""
    workspace = _planning_env
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "1")
    monkeypatch.setattr(orchestration_engine, "state_to_ramdisk_enabled", lambda: True)
    monkeypatch.setattr(orchestration_engine, "build_cache_root", lambda *_a, **_k: "")

    with pytest.raises(RuntimeError) as exc:
        orchestration_engine.run_once(_build_args(workspace), iteration=1)
    assert "KERNELONE_STATE_TO_RAMDISK is enabled" in str(exc.value)


def test_run_once_dispatch_unpack_traceability_and_final_status(_planning_env: Path, monkeypatch: Any) -> None:
    """run_director success: unpack workflow result, register traceability, exit 0."""
    workspace = _planning_env
    monkeypatch.setattr(orchestration_engine, "run_pm_planning_iteration", lambda **_k: (0, _single_task_payload()))

    director_result = {
        "status": "success",
        "task_id": "PM-CHAR-1",
        "tasks": [{"task_id": "PM-CHAR-1", "changed_files": ["src/server.ts"]}],
    }
    integration_qa_result = {"ran": True, "passed": True, "reason": "integration_qa_passed"}
    chief_engineer_result = {"blueprint_id": "BP-CHAR-1", "mode": "workflow"}
    engine_dispatch = {"summary": {"mode": "workflow", "total": 1, "successes": 1, "failures": 0}}

    captured: dict[str, Any] = {}

    def _fake_pipeline(**kwargs: Any) -> dict[str, Any]:
        captured["kwargs"] = kwargs
        return {
            "used": True,
            "exit_code": 0,
            "chief_engineer_result": chief_engineer_result,
            "engine_dispatch": engine_dispatch,
            "integration_qa_result": integration_qa_result,
            "director_result": director_result,
            "error": "",
        }

    monkeypatch.setattr(orchestration_engine, "_run_dispatch_pipeline_with_workflow", _fake_pipeline)

    result = orchestration_engine.run_once(_build_args(workspace, run_director=True), iteration=1)

    assert result == 0
    # The dispatch pipeline was invoked with the iteration's normalized contract.
    assert captured["kwargs"]["normalized"]["tasks"][0]["id"] == "PM-CHAR-1"
    # Traceability matrix written for the iteration (blueprint + commit + verdict).
    matrix_path = workspace / ".polaris" / "runtime" / "traceability" / "pm-00001.1.matrix.json"
    assert matrix_path.is_file()
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    node_kinds = {node.get("kind") for node in matrix.get("nodes", [])}
    assert "blueprint" in node_kinds
    assert "qa_verdict" in node_kinds


def test_run_once_blocked_policy_stop_overrides_exit_code(_planning_env: Path, monkeypatch: Any) -> None:
    """A blocked Director result with a stop decision drives the run_once exit code."""
    workspace = _planning_env
    monkeypatch.setattr(orchestration_engine, "run_pm_planning_iteration", lambda **_k: (0, _single_task_payload()))

    director_result = {
        "status": "blocked",
        "task_id": "PM-CHAR-1",
        "qa_retry_count": 9,
        "successes": 0,
        "total": 1,
    }

    def _fake_pipeline(**_kwargs: Any) -> dict[str, Any]:
        return {
            "used": True,
            "exit_code": 4,
            "chief_engineer_result": {"mode": "workflow"},
            "engine_dispatch": {"summary": {"mode": "workflow"}},
            "integration_qa_result": {"ran": False, "passed": None, "reason": "blocked"},
            "director_result": director_result,
            "error": "",
        }

    monkeypatch.setattr(orchestration_engine, "_run_dispatch_pipeline_with_workflow", _fake_pipeline)

    result = orchestration_engine.run_once(_build_args(workspace, run_director=True), iteration=1)

    # Blocked policy / graded dispatch keeps the run fail-closed (nonzero) and
    # finalization completes without raising.
    assert result != 0


def test_merge_engine_config_delegates_to_engine_runtime_config(
    _planning_env: Path,
) -> None:
    """_merge_engine_config maps desktop workflow args onto canonical engine config."""
    workspace = _planning_env
    args = _build_args(workspace)
    args.director_scheduling_policy = "dag"
    args.director_workflow_execution_mode = "parallel"
    args.director_max_parallel_tasks = 3

    merged = orchestration_engine._merge_engine_config(None, args)

    assert merged == {
        "director_execution_mode": "multi",
        "max_directors": 3,
        "scheduling_policy": "dag",
    }
