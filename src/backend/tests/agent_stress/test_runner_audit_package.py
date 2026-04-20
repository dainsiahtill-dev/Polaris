from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from tests.agent_stress.engine import RoundResult, StageExecution, StageResult
from tests.agent_stress.project_pool import PROJECT_POOL


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_round_result(
    *,
    workspace: Path,
    with_stage_artifact: bool = True,
) -> RoundResult:
    project = PROJECT_POOL[0]
    workspace.mkdir(parents=True, exist_ok=True)
    run_id = "factory_test_run_001"
    run_root = workspace / ".polaris" / "factory" / run_id
    (run_root / "events").mkdir(parents=True, exist_ok=True)
    (run_root / "events" / "events.jsonl").write_text("", encoding="utf-8")
    (run_root / "run.json").write_text(
        (
            "{\n"
            '  "id": "factory_test_run_001",\n'
            '  "status": "completed",\n'
            '  "updated_at": "2026-03-20T12:00:00Z",\n'
            '  "completed_at": "2026-03-20T12:01:00Z",\n'
            '  "metadata": {"current_stage": "quality_gate", "last_successful_stage": "quality_gate"},\n'
            '  "stages_completed": ["docs_generation", "pm_planning", "director_dispatch", "quality_gate"]\n'
            "}\n"
        ),
        encoding="utf-8",
    )

    artifact_path = workspace / ".polaris" / "docs" / "plan.md"
    if with_stage_artifact:
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text("# plan\n", encoding="utf-8")

    pm_stage = StageExecution(
        stage_name="pm",
        result=StageResult.SUCCESS,
        start_time=_utc_now_iso(),
        end_time=_utc_now_iso(),
        duration_ms=10,
        artifacts=["docs/plan.md"],
    )
    result = RoundResult(
        round_number=1,
        project=project,
        start_time=_utc_now_iso(),
        end_time=_utc_now_iso(),
        overall_result="PASS",
        factory_run_id=run_id,
        pm_stage=pm_stage,
        workspace_artifacts={"workspace": str(workspace)},
    )
    return result


def test_generate_json_report_includes_forensics_sections(tmp_path: Path) -> None:
    from tests.agent_stress.runner import AgentStressRunner

    workspace = tmp_path / "stress-workspace"
    result_workspace = workspace / "projects" / PROJECT_POOL[0].id
    result = _build_round_result(workspace=result_workspace, with_stage_artifact=True)

    runner = AgentStressRunner(workspace=workspace, rounds=1)
    runner.start_time = _utc_now_iso()
    runner.results = [result]
    runner.backend_preflight_report = {"status": "healthy"}
    runner.probe_report = {"overall_status": "healthy"}

    report = runner._generate_json_report(run_state="running")
    assert report["run_state"] == "running"
    assert len(report["project_results"]) == 1
    assert report["project_results"][0]["project_id"] == PROJECT_POOL[0].id
    assert report["runtime_forensics"]["summary"]["total_factory_runs"] >= 1
    assert "artifact_integrity" in report
    assert "audit_package_health" in report


def test_collect_artifact_integrity_detects_missing_stage_artifact(tmp_path: Path) -> None:
    from tests.agent_stress.runner import AgentStressRunner

    workspace = tmp_path / "stress-workspace"
    result_workspace = workspace / "projects" / PROJECT_POOL[0].id
    result = _build_round_result(workspace=result_workspace, with_stage_artifact=False)

    runner = AgentStressRunner(workspace=workspace, rounds=1)
    runner.results = [result]

    integrity = runner._collect_artifact_integrity(run_state="completed")
    stage_artifacts = integrity.get("stage_artifacts", {})
    assert int(stage_artifacts.get("missing") or 0) >= 1
    missing_items = stage_artifacts.get("missing_items") or []
    assert any(item.get("artifact") == "docs/plan.md" for item in missing_items)


@pytest.mark.asyncio
async def test_save_intermediate_results_writes_audit_checkpoint(tmp_path: Path) -> None:
    from tests.agent_stress.runner import AgentStressRunner

    workspace = tmp_path / "stress-workspace"
    result_workspace = workspace / "projects" / PROJECT_POOL[0].id
    result = _build_round_result(workspace=result_workspace, with_stage_artifact=True)

    runner = AgentStressRunner(workspace=workspace, rounds=1)
    runner.start_time = _utc_now_iso()
    runner.results = [result]
    runner.backend_preflight_report = {"status": "healthy"}
    runner.probe_report = {"overall_status": "healthy"}

    await runner._save_intermediate_results()

    output_dir = workspace / "stress_reports"
    stress_results_path = output_dir / "stress_results.json"
    package_path = output_dir / "stress_audit_package.json"
    assert stress_results_path.exists()
    assert package_path.exists()

    payload = package_path.read_text(encoding="utf-8")
    assert "audit_checkpoint" in payload
    assert "intermediate_results_saved" in payload
