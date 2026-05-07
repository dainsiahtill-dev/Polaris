"""Regression tests for PM runtime plan contract synchronization."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from polaris.delivery.cli.pm import orchestration_engine
from polaris.kernelone.storage.io_paths import resolve_artifact_path


def _build_args(workspace: Path) -> SimpleNamespace:
    return SimpleNamespace(
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
        run_director=False,
        director_path="src/backend/polaris/delivery/cli/loop-director.py",
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
    )


def test_pm_canonical_director_script_path_resolves_repo_relative_default() -> None:
    """Workflow metadata must not carry cwd-sensitive Director paths."""
    resolved = Path(
        orchestration_engine._canonical_director_script_path("src/backend/polaris/delivery/cli/loop-director.py")
    )

    assert resolved.is_absolute()
    assert resolved.name == "loop-director.py"
    assert resolved.is_file()


def test_pm_run_once_syncs_persistent_docs_plan_to_runtime_contract(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """PM run_once must copy existing product plan into runtime contracts.

    Real Electron flows often use the persistent workspace docs location
    ``workspace/docs/product/plan.md``. PM dispatch later validates
    ``runtime/contracts/plan.md``; without this bridge the process exits
    before producing tasks.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    kernelone_home = tmp_path / "kernelone-home"
    runtime_root = workspace / ".polaris" / "runtime"
    monkeypatch.setenv("KERNELONE_HOME", str(kernelone_home))
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("KERNELONE_RUNTIME_CACHE_ROOT", str(runtime_root))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")

    plan_text = "# Plan\n\n## Backlog\n- Build a file server\n"
    requirements_text = "# Requirements\n\nBuild a local file server with tests.\n"
    plan_path = Path(resolve_artifact_path(str(workspace), "", "workspace/docs/product/plan.md"))
    requirements_path = Path(resolve_artifact_path(str(workspace), "", "workspace/docs/product/requirements.md"))
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(plan_text, encoding="utf-8")
    requirements_path.write_text(requirements_text, encoding="utf-8")

    monkeypatch.setattr(orchestration_engine, "resolve_pm_backend_kind", lambda *_args, **_kwargs: ("fake", None))
    monkeypatch.setattr(orchestration_engine, "ensure_pm_backend_available", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(orchestration_engine, "wait_for_agents_confirmation", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(orchestration_engine, "check_stop_conditions", lambda *_args, **_kwargs: None)

    def fake_planning_iteration(**_kwargs: Any) -> tuple[int, dict[str, Any]]:
        return (
            0,
            {
                "overall_goal": "File server",
                "focus": "Create executable task",
                "tasks": [
                    {
                        "id": "PM-SYNC-1",
                        "title": "Implement file server scaffold",
                        "goal": "Create the server entry point and tests.",
                        "target_files": ["src/server.ts"],
                        "execution_checklist": ["Create server module"],
                        "acceptance": ["npm test passes"],
                    }
                ],
            },
        )

    monkeypatch.setattr(orchestration_engine, "run_pm_planning_iteration", fake_planning_iteration)

    result = orchestration_engine.run_once(_build_args(workspace), iteration=1)

    assert result == 0
    runtime_plan_path = Path(resolve_artifact_path(str(workspace), "", "runtime/contracts/plan.md"))
    assert runtime_plan_path.is_file()
    assert runtime_plan_path.read_text(encoding="utf-8") == plan_text
