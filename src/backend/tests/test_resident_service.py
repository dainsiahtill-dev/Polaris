from __future__ import annotations

from pathlib import Path

from polaris.cells.resident.autonomy.internal.resident_runtime_service import (
    get_resident_service,
    reset_resident_services,
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

    contract = service.materialize_goal(goals[0].goal_id)
    assert contract is not None
    assert contract["focus"] == "resident_goal_materialization"
    assert len(contract["tasks"]) == 2

    assert Path(service.storage.paths.identity_path).is_file()
    assert Path(service.storage.paths.decision_trace_path).is_file()
    assert Path(service.storage.paths.capability_graph_path).is_file()

    reset_resident_services()
    recovered = get_resident_service(str(workspace)).recover()
    assert recovered["counts"]["decisions"] == 4
    assert recovered["counts"]["goals"] >= 1
