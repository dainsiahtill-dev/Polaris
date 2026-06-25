from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import CommandResult
from polaris.cells.resident.autonomy.internal.resident_runtime_service import (
    get_resident_service,
    reset_resident_services,
)
from polaris.cells.resident.autonomy.public.service import (
    MaterializeResidentGoalCommandV1,
    QueryResidentAgiAuditPackV1,
    RunResidentGoalCommandV1,
    StageResidentGoalCommandV1,
    materialize_resident_goal,
    query_resident_agi_audit_pack,
    run_resident_goal,
    stage_resident_goal,
)


def _decision_payload(
    *,
    run_id: str,
    actor: str,
    stage: str,
    summary: str,
    strategy: str,
    verdict: str,
    task_id: str,
    evidence_ref: str,
    option_tag: str = "",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "run_id": run_id,
        "actor": actor,
        "stage": stage,
        "summary": summary,
        "task_id": task_id,
        "strategy_tags": [strategy],
        "expected_outcome": {"status": "completed", "success": True},
        "actual_outcome": {
            "status": "completed" if verdict == "success" else "failed",
            "success": verdict == "success",
        },
        "verdict": verdict,
        "evidence_refs": [evidence_ref],
        "confidence": 0.8,
    }
    if option_tag:
        payload["options"] = [
            {
                "label": "counterfactual_candidate",
                "rationale": "Replay a safer alternative strategy.",
                "strategy_tags": [option_tag],
                "estimated_score": 0.7,
            }
        ]
    return payload


def test_resident_service_builds_skills_goals_and_contracts(tmp_path: Path) -> None:
    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    service = get_resident_service(str(workspace))
    service.start("propose")

    service.record_decision(
        _decision_payload(
            run_id="run-1",
            actor="director",
            stage="task_execution",
            summary="Use surgical patch for scoped task",
            strategy="surgical_patch",
            verdict="success",
            task_id="TASK-1",
            evidence_ref="runtime/results/director.result.json",
        )
    )
    service.record_decision(
        _decision_payload(
            run_id="run-1",
            actor="director",
            stage="task_execution",
            summary="Use surgical patch for repeated scoped task",
            strategy="surgical_patch",
            verdict="success",
            task_id="TASK-2",
            evidence_ref="runtime/results/director.result.json",
        )
    )
    service.record_decision(
        _decision_payload(
            run_id="run-2",
            actor="director",
            stage="task_execution",
            summary="Broad refactor regressed the task",
            strategy="broad_refactor",
            verdict="failure",
            task_id="TASK-3",
            evidence_ref="runtime/results/director.result.json",
            option_tag="surgical_patch",
        )
    )
    service.record_decision(
        _decision_payload(
            run_id="run-3",
            actor="director",
            stage="task_execution",
            summary="Second broad refactor regression",
            strategy="broad_refactor",
            verdict="failure",
            task_id="TASK-4",
            evidence_ref="runtime/results/director.result.json",
            option_tag="surgical_patch",
        )
    )

    status = service.tick(force=True)

    assert status["runtime"]["tick_count"] >= 1
    assert status["counts"]["decisions"] == 4
    assert status["counts"]["skills"] >= 1
    assert status["counts"]["experiments"] >= 1
    assert status["counts"]["improvements"] >= 1
    assert status["counts"]["goals"] >= 1

    goals = service.list_goals()
    approved = service.approve_goal(goals[0].goal_id, note="approved in test")
    assert approved is not None
    assert approved.status.value == "approved"

    contract = materialize_resident_goal(
        MaterializeResidentGoalCommandV1(workspace=str(workspace), goal_id=goals[0].goal_id)
    )
    assert contract is not None
    assert contract["focus"] == "resident_goal_materialization"
    assert len(contract["tasks"]) == 2

    staged = stage_resident_goal(
        StageResidentGoalCommandV1(
            workspace=str(workspace),
            goal_id=goals[0].goal_id,
            promote_to_pm_runtime=True,
        )
    )
    assert staged is not None
    assert staged["promoted_to_pm_runtime"] is True
    assert staged["artifacts"]["pm_contract_path"]

    assert Path(service.storage.paths.identity_path).is_file()
    assert Path(service.storage.paths.decision_trace_path).is_file()
    assert Path(service.storage.paths.decision_events_path).is_file()
    assert Path(service.storage.paths.capability_graph_path).is_file()

    event_rows = [
        json.loads(line)
        for line in Path(service.storage.paths.decision_events_path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    decision_events = [row for row in event_rows if row.get("name") == "resident_decision_recorded"]
    assert len(decision_events) >= 4
    latest_event = decision_events[-1]
    assert latest_event["actor"] == "ResidentAGI"
    assert latest_event["meta"]["schema_version"] == "resident.decision_event.v1"
    assert latest_event["meta"]["source_of_truth"] == service.storage.paths.decision_trace_path
    assert latest_event["output"]["decision_id"]
    assert latest_event["output"]["verdict"] in {"success", "failure"}

    summary = service.get_status(include_details=False)
    assert summary["agi_capability_surface"]["schema_version"] == "resident.agi_capability_surface.v1"

    detailed = service.get_status(include_details=True)
    capability_surface = detailed["agi_capability_surface"]
    assert capability_surface["schema_version"] == "resident.agi_capability_surface.v1"
    assert capability_surface["decision_boundary_schema"] == "resident.agi_decision_boundary.v1"
    assert capability_surface["authority_matrix_schema"] == "resident.agi_authority_matrix.v1"
    assert capability_surface["role_id"] == "resident_agi"
    assert capability_surface["runtime_foundation"] == "roles.runtime + ContextOS + TurnEngine"
    assert capability_surface["count"] >= 1
    authority_matrix = capability_surface["authority_matrix"]
    assert authority_matrix["schema_version"] == "resident.agi_authority_matrix.v1"
    assert authority_matrix["runtime_foundation"] == "roles.runtime + ContextOS + TurnEngine"
    assert authority_matrix["chain"] == "PM → Chief Engineer → Director"
    assert authority_matrix["chain_required"] is True
    assert authority_matrix["platform_enforced"] is True
    assert authority_matrix["llm_decision_required"] is True
    assert "role.runtime.foundation" in authority_matrix["platform_hard_rules"]
    assert "resident.goal_bridge.execute" in authority_matrix["high_risk_capabilities"]
    assert authority_matrix["decision_policy"]["governed_execution"] == "canonical_role_chain_only"
    assert any(
        item["capability_id"] == "resident.agi_decision_turn.execute"
        and item["access"] == "execute_through_role_runtime"
        for item in capability_surface["items"]
    )
    assert any(item["capability_id"] == "roles.registry.read" for item in capability_surface["items"])
    assert any(item["capability_id"] == "contextos.final_request_audit.read" for item in capability_surface["items"])
    decision_boundaries = capability_surface["decision_boundaries"]
    assert {item["authority"] for item in decision_boundaries} >= {
        "platform_hard_rule",
        "agi_recommendation",
        "agi_governed_execution",
    }
    assert any(item["boundary_id"] == "role.runtime.foundation" for item in decision_boundaries)
    assert any(item["boundary_id"] == "architecture.options" for item in decision_boundaries)
    assert any("final_request_context_audit" in item["evidence_required"] for item in decision_boundaries)
    serialized_capability_surface = json.dumps(capability_surface, ensure_ascii=False)
    assert "PM -> CE -> Director" not in serialized_capability_surface
    assert "PM -> Director" not in serialized_capability_surface
    assert "PM → Chief Engineer → Director" in serialized_capability_surface

    audit_pack = query_resident_agi_audit_pack(QueryResidentAgiAuditPackV1(workspace=str(workspace), decision_limit=2))
    assert audit_pack["schema_version"] == "resident.agi_audit_pack.v1"
    assert audit_pack["workspace"] == str(workspace)
    assert "runtime.v2.snapshot.resident" in audit_pack["truth_sources"]
    assert audit_pack["authority_matrix"]["schema_version"] == "resident.agi_authority_matrix.v1"
    assert audit_pack["hard_rule_gate"]["status"] == "pass"
    assert len(audit_pack["recent_decisions"]) == 2

    reset_resident_services()
    recovered = get_resident_service(str(workspace)).recover()
    assert recovered["counts"]["decisions"] >= 5
    assert recovered["counts"]["goals"] >= 1


@pytest.mark.asyncio
async def test_run_resident_goal_public_command_uses_governed_pm_bridge(tmp_path: Path) -> None:
    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = get_resident_service(str(workspace))
    goal = service.create_goal_proposal(
        {
            "goal_type": "maintenance",
            "title": "Run through public goal bridge",
            "motivation": "Prove contract-first Resident goal execution.",
            "source": "test",
            "scope": ["src/backend/polaris/cells/resident/autonomy"],
            "evidence_refs": ["runtime/contexts/context-1.json"],
        }
    )
    approved = service.approve_goal(goal.goal_id, note="approved")
    assert approved is not None

    with patch(
        "polaris.cells.resident.autonomy.internal.resident_runtime_service.OrchestrationCommandService.execute_pm_run",
        new=AsyncMock(
            return_value=CommandResult(
                run_id="pm-resident-public-001",
                status="pending",
                message="Resident PM run started",
                started_at="2026-03-07T00:00:00+00:00",
            )
        ),
    ):
        result = await run_resident_goal(
            RunResidentGoalCommandV1(
                workspace=str(workspace),
                goal_id=goal.goal_id,
                run_type="pm",
                run_director=True,
                director_iterations=2,
            )
        )

    assert result is not None
    assert result["pm_run"]["run_id"] == "pm-resident-public-001"
    assert result["staging"]["promoted_to_pm_runtime"] is True
    assert result["goal"]["materialization_artifacts"]["pm_run"]["run_id"] == "pm-resident-public-001"
