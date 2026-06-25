from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from polaris.cells.audit.evidence.public import QueryEvidenceEventsV1, query_evidence_events
from polaris.cells.control_plane.run_ledger.public import (
    ReadRunLedgerProjectionQueryV1,
    read_run_ledger_projection,
)
from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import CommandResult
from polaris.cells.resident.autonomy.internal.resident_runtime_service import (
    get_resident_service,
    reset_resident_services,
)
from polaris.cells.resident.autonomy.public import service as resident_public_service
from polaris.cells.resident.autonomy.public.service import (
    ApproveResidentGoalCommandV1,
    CreateResidentGoalCommandV1,
    ExtractResidentSkillsCommandV1,
    MaterializeResidentGoalCommandV1,
    QueryResidentAgiAuditPackV1,
    QueryResidentAgiEvidenceInterfacesV1,
    QueryResidentAgiHandoffsV1,
    RecordResidentDecisionCommandV1,
    RejectResidentGoalCommandV1,
    RunResidentAgiDecisionTurnCommandV1,
    RunResidentExperimentsCommandV1,
    RunResidentGoalCommandV1,
    RunResidentImprovementsCommandV1,
    RunResidentTickCommandV1,
    StageResidentGoalCommandV1,
    StartResidentCommandV1,
    StopResidentCommandV1,
    UpdateResidentIdentityCommandV1,
    approve_resident_goal,
    create_resident_goal,
    extract_resident_skills,
    materialize_resident_goal,
    query_resident_agi_audit_pack,
    query_resident_agi_evidence_interfaces,
    query_resident_agi_handoffs,
    record_resident_decision_entry,
    reject_resident_goal,
    run_resident_agi_decision_turn,
    run_resident_experiments,
    run_resident_goal,
    run_resident_improvements,
    run_resident_tick,
    stage_resident_goal,
    start_resident,
    stop_resident,
    update_resident_identity,
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


def test_resident_agi_handoffs_query_derives_role_inbox_from_decision_trace(tmp_path: Path) -> None:
    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    payload = _decision_payload(
        run_id="run-handoff",
        actor="resident_agi",
        stage="quality_gate_response",
        summary="Quality gate can proceed through governed handoff.",
        strategy="resident_agi_turn",
        verdict="success",
        task_id="task-handoff",
        evidence_ref="runtime/gates/quality.json",
    )
    payload["actual_outcome"] = {
        "resident_agi_decision_handoff": {
            "schema_version": "resident.agi_decision_handoff.v1",
            "handoff_status": "ready",
            "target_roles": ["chief_engineer", "director", "qa"],
            "allowed_actions": ["record_decision_trace", "handoff_to_pm_chief_engineer_director_chain"],
            "blocked_actions": ["director_tool_execution_by_agi", "pm_to_director_shortcut"],
            "downstream_allowed": True,
            "reason": "Quality gate can proceed through governed handoff.",
            "required_chain": "PM → Chief Engineer → Director",
            "advisory_only": True,
            "agi_execution_authority": False,
        }
    }
    recorded = record_resident_decision_entry(
        RecordResidentDecisionCommandV1(
            workspace=str(workspace),
            action="resident_agi_decision_recorded",
            payload=payload,
        )
    )

    inbox = query_resident_agi_handoffs(
        QueryResidentAgiHandoffsV1(
            workspace=str(workspace),
            target_role="director",
        )
    )

    assert inbox["schema_version"] == "resident.agi_handoff_inbox.v1"
    assert inbox["source"] == "resident.decision_trace"
    assert inbox["count"] == 1
    assert inbox["summary"]["by_status"] == {"ready": 1}
    assert inbox["summary"]["by_target_role"]["director"] == 1
    assert inbox["summary"]["agi_execution_authority"] is False
    item = inbox["items"][0]
    assert item["decision_id"] == recorded["decision_id"]
    assert item["handoff"]["handoff_status"] == "ready"
    assert item["handoff"]["target_roles"] == ["chief_engineer", "director", "qa"]
    assert "director_tool_execution_by_agi" in item["handoff"]["blocked_actions"]


def test_resident_public_commands_cover_lifecycle_goals_decisions_and_labs(tmp_path: Path) -> None:
    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    started = start_resident(StartResidentCommandV1(workspace=str(workspace), mode="propose"))
    assert started["runtime"]["active"] is True
    assert started["runtime"]["mode"] == "propose"

    identity = update_resident_identity(
        UpdateResidentIdentityCommandV1(
            workspace=str(workspace),
            payload={
                "name": "Resident AGI Supervisor",
                "mission": "Govern platform-level autonomous development decisions.",
                "resident_agi_participation": {
                    "enabled": True,
                    "scopes": ["quality_gate_response", "architecture.option.selection"],
                    "participation": {
                        "quality_gate_response": True,
                        "architecture.option.selection": True,
                    },
                    "custom_scopes_allowed": True,
                },
            },
        )
    )
    assert identity["name"] == "Resident AGI Supervisor"
    assert identity["resident_agi_participation"]["enabled"] is True
    assert identity["resident_agi_participation"]["scopes"] == [
        "quality_gate_response",
        "architecture.option.selection",
    ]

    recorded = record_resident_decision_entry(
        RecordResidentDecisionCommandV1(
            workspace=str(workspace),
            action="resident_agi_decision_recorded",
            detail={"decision_type": "platform_supervision"},
            payload=_decision_payload(
                run_id="run-public-command-1",
                actor="resident_agi",
                stage="platform_supervision",
                summary="Resident AGI selected evidence-first continuation.",
                strategy="evidence_first",
                verdict="success",
                task_id="TASK-PUBLIC-1",
                evidence_ref="runtime/contexts/context-1.json",
            ),
        )
    )
    assert recorded["actor"] == "resident_agi"
    assert recorded["stage"] == "platform_supervision"

    goal = create_resident_goal(
        CreateResidentGoalCommandV1(
            workspace=str(workspace),
            payload={
                "goal_type": "maintenance",
                "title": "Contract-first Resident command coverage",
                "motivation": "Avoid HTTP-only Resident AGI write paths.",
                "source": "test",
                "scope": ["src/backend/polaris/cells/resident/autonomy"],
                "evidence_refs": ["runtime/contexts/context-1.json"],
            },
        )
    )
    approved = approve_resident_goal(
        ApproveResidentGoalCommandV1(workspace=str(workspace), goal_id=goal["goal_id"], note="approved")
    )
    assert approved is not None
    assert approved["status"] == "approved"

    rejected_goal = create_resident_goal(
        CreateResidentGoalCommandV1(
            workspace=str(workspace),
            payload={
                "goal_type": "maintenance",
                "title": "Reject command coverage",
                "motivation": "Exercise the rejection command.",
                "source": "test",
                "scope": ["src/backend/polaris/cells/resident/autonomy"],
            },
        )
    )
    rejected = reject_resident_goal(
        RejectResidentGoalCommandV1(workspace=str(workspace), goal_id=rejected_goal["goal_id"], note="reject")
    )
    assert rejected is not None
    assert rejected["status"] == "rejected"

    ticked = run_resident_tick(RunResidentTickCommandV1(workspace=str(workspace), force=True))
    assert ticked["runtime"]["tick_count"] >= 1
    assert ticked["agi_participation_policy"]["schema_version"] == "resident.agi_participation_policy.v1"
    assert "director_repair_strategy_catalog" in ticked["agi_participation_policy"]["participation_flags"]
    available_scopes = {
        item["scope_id"]: item
        for item in ticked["agi_participation_policy"]["available_scopes"]
        if isinstance(item, dict) and item.get("scope_id")
    }
    assert "director_repair_strategy_catalog" in available_scopes
    assert "director_repair_coverage" in available_scopes
    assert "director_repair_advisory_policy" in available_scopes
    assert available_scopes["director_repair_coverage"]["capability_id"] == "director.repair_coverage.read"
    assert available_scopes["director_repair_advisory_policy"]["category"] == "director_repair_advisory"
    known_scope_keys = resident_public_service._resident_agi_known_participation_scope_keys()
    assert "director_repair_strategy_catalog" in known_scope_keys
    assert "director_repair_coverage" in known_scope_keys
    assert "director_repair_advisory_policy" in known_scope_keys
    assert ticked["identity"]["resident_agi_participation"]["enabled"] is True
    tick_boundary = ticked["runtime"]["last_summary"]["autonomy_boundary"]
    assert tick_boundary["schema_version"] == "resident.tick_autonomy_boundary.v1"
    assert tick_boundary["tick_role"] == "deterministic_evidence_producer"
    assert tick_boundary["goal_proposal_semantics"] == "pending_proposals_only"
    assert tick_boundary["agi_judgement_entrypoint"] == "resident_agi_decision_turn"
    assert tick_boundary["execution_impacting_decision_policy"] == "requires_resident_agi_runtime_contract_gate"
    assert tick_boundary["sidecar_llm_allowed"] is False
    assert isinstance(extract_resident_skills(ExtractResidentSkillsCommandV1(workspace=str(workspace))), list)
    assert isinstance(run_resident_experiments(RunResidentExperimentsCommandV1(workspace=str(workspace))), list)
    assert isinstance(run_resident_improvements(RunResidentImprovementsCommandV1(workspace=str(workspace))), list)

    stopped = stop_resident(StopResidentCommandV1(workspace=str(workspace)))
    assert stopped["runtime"]["active"] is False


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
    assert any(item["capability_id"] == "runtime.status_resident.read" for item in capability_surface["items"])
    assert any(item["capability_id"] == "resident.lifecycle.manage" for item in capability_surface["items"])
    assert any(item["capability_id"] == "resident.goal_governance.write" for item in capability_surface["items"])
    assert any(item["capability_id"] == "resident.autonomy_labs.execute" for item in capability_surface["items"])
    assert any(item["capability_id"] == "roles.registry.read" for item in capability_surface["items"])
    assert any(item["capability_id"] == "contextos.final_request_audit.read" for item in capability_surface["items"])
    capability_ids = {item["capability_id"] for item in capability_surface["items"]}
    assert {
        "audit.diagnosis.read",
        "audit.diagnosis.execute",
        "audit.verdict.read",
        "audit.verdict.execute",
        "context.catalog.search",
        "context.engine.resolve",
        "verifier.policy.read",
        "verifier.execution.execute",
        "director.deterministic_repair_strategy_catalog.read",
        "director.repair_coverage.read",
        "director.repair_advisory_policy.read",
    } <= capability_ids
    repair_catalog = capability_surface["hardcoded_repair_strategy_catalog"]
    assert repair_catalog["schema_version"] == "director.deterministic_repair_strategy_catalog.v1"
    assert repair_catalog["source"] == "director.runtime.repair_kernel.strategy_catalog"
    assert repair_catalog["access"] == "read_only"
    assert repair_catalog["agi_execution_authority"] is False
    assert repair_catalog["director_tool_execution_required"] is True
    assert repair_catalog["owner_cell"] == "director.runtime"
    assert repair_catalog["execution_boundary"] == "director_authorized_tools_only"
    assert repair_catalog["chain"] == "PM → Chief Engineer → Director"
    assert repair_catalog["unknown_source_tool_policy"] == "fail_closed_high_risk"
    assert repair_catalog["summary"]["total"] > 0
    assert repair_catalog["summary"]["returned"] == repair_catalog["summary"]["total"]
    assert repair_catalog["summary"]["by_concern"]
    assert repair_catalog["items"]
    assert {
        "source_tool",
        "language",
        "phase",
        "concern",
        "risk_level",
    } <= set(repair_catalog["items"][0])
    assert "verifier.execution.execute" in authority_matrix["high_risk_capabilities"]
    assert "control_plane.verifier_execution" in authority_matrix["canonical_contracts"]
    assert "director.deterministic_repair_strategy_catalog.read" in authority_matrix["read_only_capabilities"]
    assert "director.repair_coverage.read" in authority_matrix["read_only_capabilities"]
    assert "director.repair_advisory_policy.read" in authority_matrix["read_only_capabilities"]
    assert "director.deterministic_repair_strategy_catalog.v1" in authority_matrix["canonical_contracts"]
    assert "director.repair_coverage_report.v1" in authority_matrix["canonical_contracts"]
    assert "director.repair_advisory_policy.v1" in authority_matrix["canonical_contracts"]
    assert "audit.diagnosis" in authority_matrix["canonical_contracts"]
    assert "audit.verdict" in authority_matrix["canonical_contracts"]
    decision_boundaries = capability_surface["decision_boundaries"]
    assert {item["authority"] for item in decision_boundaries} >= {
        "platform_hard_rule",
        "agi_recommendation",
        "agi_governed_execution",
    }
    assert any(item["boundary_id"] == "role.runtime.foundation" for item in decision_boundaries)
    assert any(item["boundary_id"] == "architecture.options" for item in decision_boundaries)
    assert any(item["boundary_id"] == "audit.interface.selection" for item in decision_boundaries)
    assert any("final_request_context_audit" in item["evidence_required"] for item in decision_boundaries)
    decision_capabilities = capability_surface["decision_capabilities"]
    decision_capability_ids = {item["decision_id"] for item in decision_capabilities}
    assert {
        "platform.invariant.blocker",
        "evidence.interface.selection",
        "architecture.option.selection",
        "goal.promotion.readiness",
        "quality.gate.response",
        "director.repair.advisory",
    } <= decision_capability_ids
    decision_registry = capability_surface["decision_capability_registry"]
    assert decision_registry["schema_version"] == "resident.agi_decision_capability_registry.v1"
    assert "platform.invariant.blocker" in decision_registry["platform_owned_decisions"]
    assert "evidence.interface.selection" in decision_registry["agi_owned_decisions"]
    assert "goal.promotion.readiness" in decision_registry["governed_execution_decisions"]
    assert "verifier.execution.execute" in decision_registry["evidence_interface_ids"]
    assert "director.deterministic_repair_strategy_catalog.read" in decision_registry["evidence_interface_ids"]
    assert "director.repair_coverage.read" in decision_registry["evidence_interface_ids"]
    assert "director.repair_advisory_policy.read" in decision_registry["evidence_interface_ids"]
    assert "request_evidence" in decision_registry["candidate_actions"]
    assert "suggest_repair_rule" in decision_registry["candidate_actions"]
    evidence_interface_contract = capability_surface["evidence_interface_contract"]
    assert capability_surface["evidence_interface_contract_schema"] == "resident.agi_evidence_interface_contract.v1"
    assert evidence_interface_contract["schema_version"] == "resident.agi_evidence_interface_contract.v1"
    assert evidence_interface_contract["coverage_complete"] is True
    assert evidence_interface_contract["missing_interface_ids"] == []
    assert evidence_interface_contract["missing_required_interface_ids"] == []
    assert evidence_interface_contract["missing_optional_interface_ids"] == []
    assert {
        "contextos.final_request_audit.read",
        "run_ledger.read",
        "director.deterministic_repair_strategy_catalog.read",
        "director.repair_coverage.read",
        "director.repair_advisory_policy.read",
    } <= set(evidence_interface_contract["declared_interface_ids"])
    interface_by_id = {item["interface_id"]: item for item in evidence_interface_contract["interfaces"]}
    repair_interface = interface_by_id["director.deterministic_repair_strategy_catalog.read"]
    assert repair_interface["status"] == "available"
    assert repair_interface["access"] == "read_only"
    assert repair_interface["contract_ref"] == "director.deterministic_repair_strategy_catalog.v1"
    assert "quality.gate.response" in repair_interface["required_by_decisions"]
    repair_coverage_interface = interface_by_id["director.repair_coverage.read"]
    assert repair_coverage_interface["status"] == "available"
    assert repair_coverage_interface["access"] == "read_only"
    assert repair_coverage_interface["contract_ref"] == "director.repair_coverage_report.v1"
    assert "quality.gate.response" in repair_coverage_interface["required_by_decisions"]
    advisory_policy_interface = interface_by_id["director.repair_advisory_policy.read"]
    assert advisory_policy_interface["status"] == "available"
    assert advisory_policy_interface["access"] == "read_only"
    assert advisory_policy_interface["contract_ref"] == "director.repair_advisory_policy.v1"
    assert "quality.gate.response" in advisory_policy_interface["required_by_decisions"]
    assert (
        evidence_interface_contract["decision_policy"]["declared_interfaces_must_exist"]
        == "fail_closed_before_agi_decision"
    )
    serialized_capability_surface = json.dumps(capability_surface, ensure_ascii=False)
    assert "PM -> CE -> Director" not in serialized_capability_surface
    assert "PM -> Director" not in serialized_capability_surface
    assert "PM → Chief Engineer → Director" in serialized_capability_surface

    audit_pack = query_resident_agi_audit_pack(QueryResidentAgiAuditPackV1(workspace=str(workspace), decision_limit=2))
    assert audit_pack["schema_version"] == "resident.agi_audit_pack.v1"
    assert audit_pack["workspace"] == str(workspace)
    assert "runtime.v2.status.resident" in audit_pack["truth_sources"]
    assert "runtime.v2.snapshot.resident" in audit_pack["truth_sources"]
    assert "director.runtime.repair_kernel.strategy_catalog" in audit_pack["truth_sources"]
    assert "director.runtime.repair_kernel.registry" in audit_pack["truth_sources"]
    assert "director.runtime.repair_kernel.advisory_policy" in audit_pack["truth_sources"]
    director_repair_contract = audit_pack["director_repair_contract"]
    assert director_repair_contract["schema_version"] == "resident.agi_director_repair_contract.v1"
    assert director_repair_contract["owner_cell"] == "director.runtime"
    assert director_repair_contract["catalog_schema"] == "director.deterministic_repair_strategy_catalog.v1"
    assert director_repair_contract["coverage_schema"] == "director.repair_coverage_report.v1"
    assert director_repair_contract["advisory_policy_schema"] == "director.repair_advisory_policy.v1"
    assert director_repair_contract["profile_summary_schema"] == "director.deterministic_repair_profile_summary.v1"
    assert director_repair_contract["unknown_source_tool_policy"] == "fail_closed_high_risk"
    assert director_repair_contract["execution_boundary"] == "director_authorized_tools_only"
    assert director_repair_contract["chain"] == "PM → Chief Engineer → Director"
    assert director_repair_contract["agi_advisory"]["active"] is True
    assert director_repair_contract["agi_advisory"]["writes_allowed"] is False
    assert director_repair_contract["agi_advisory"]["registration_allowed"] is False
    assert director_repair_contract["agi_advisory"]["suggested_rules_allowed"] is True
    assert "pattern" in director_repair_contract["agi_advisory"]["allowed_suggested_rule_fields"]
    assert "write_file" in director_repair_contract["agi_advisory"]["forbidden_suggested_rule_fields"]
    assert director_repair_contract["agi_execution_authority"] is False
    assert director_repair_contract["director_tool_execution_required"] is True
    assert audit_pack["authority_matrix"]["schema_version"] == "resident.agi_authority_matrix.v1"
    assert audit_pack["hard_rule_gate"]["status"] == "pass"
    assert audit_pack["autonomy_boundary"]["tick_role"] == "deterministic_evidence_producer"
    assert audit_pack["autonomy_boundary"]["sidecar_llm_allowed"] is False
    assert (
        "Resident tick/labs are deterministic evidence producers, not AGI judgement turns."
        in audit_pack["execution_constraints"]
    )
    decision_profile = audit_pack["decision_profile"]
    assert decision_profile["schema_version"] == "resident.agi_decision_profile.v1"
    assert decision_profile["role_id"] == "resident_agi"
    assert decision_profile["runtime_foundation"] == "roles.runtime + ContextOS + TurnEngine"
    assert decision_profile["role_turn_allowed"] is True
    assert decision_profile["downstream_precheck"] in {
        "ready_for_agi_judgement",
        "hold_for_evidence",
        "hold_for_gate_repair",
    }
    assert "request_evidence" in decision_profile["candidate_actions"]
    assert "preserve_pm_chief_engineer_director_qa_chain" in decision_profile["required_constraints"]
    assert "resident_tick_is_deterministic_evidence_only" in decision_profile["required_constraints"]
    assert (
        "execution_impacting_agi_judgement_requires_runtime_contract_gate" in decision_profile["required_constraints"]
    )
    assert "AuditDiagnosisResultV1" in decision_profile["required_evidence"]
    assert "AuditVerdictResultV1" in decision_profile["required_evidence"]
    assert "director.deterministic_repair_strategy_catalog.v1" in decision_profile["required_evidence"]
    assert "director.repair_coverage_report.v1" in decision_profile["required_evidence"]
    assert "director.repair_advisory_policy.v1" in decision_profile["required_evidence"]
    assert "control_plane.verifier_execution" in decision_profile["contract_refs"]
    assert "director.deterministic_repair_strategy_catalog.v1" in decision_profile["contract_refs"]
    assert "director.repair_coverage_report.v1" in decision_profile["contract_refs"]
    assert "director.repair_advisory_policy.v1" in decision_profile["contract_refs"]
    assert (
        decision_profile["decision_capability_registry"]["schema_version"]
        == "resident.agi_decision_capability_registry.v1"
    )
    assert "quality.gate.response" in decision_profile["decision_capability_ids"]
    assert (
        decision_profile["gate_refs"]["decision_capability_registry"] == "resident.agi_decision_capability_registry.v1"
    )
    evidence_recommendations = decision_profile["evidence_interface_recommendations"]
    assert evidence_recommendations[0]["contract_ref"] == "roles.final_request_context_audit"
    recommendation_ids = {item["capability_id"] for item in evidence_recommendations}
    assert {
        "audit.diagnosis.read",
        "audit.diagnosis.execute",
        "audit.verdict.read",
        "run_ledger.read",
        "verifier.policy.read",
        "verifier.execution.execute",
        "director.deterministic_repair_strategy_catalog.read",
        "context.catalog.search",
        "context.engine.resolve",
    } <= recommendation_ids
    repair_recommendation = next(
        item
        for item in evidence_recommendations
        if item["capability_id"] == "director.deterministic_repair_strategy_catalog.read"
    )
    assert repair_recommendation["contract_ref"] == "director.deterministic_repair_strategy_catalog.v1"
    assert repair_recommendation["recommended_now"] is True
    verifier_execution = next(
        item for item in evidence_recommendations if item["capability_id"] == "verifier.execution.execute"
    )
    assert verifier_execution["recommended_now"] is True
    assert verifier_execution["reason"] == "Request missing evidence before continuing."
    assert decision_profile["authority_policy"]["hard_rules"] == "platform_enforced_non_overridable"
    assert decision_profile["authority_policy"]["governed_execution"] == "canonical_role_chain_only"
    assert decision_profile["gate_refs"]["autonomy_boundary"] == "resident.tick_autonomy_boundary.v1"
    assert len(audit_pack["recent_decisions"]) == 2

    reset_resident_services()
    recovered = get_resident_service(str(workspace)).recover()
    assert recovered["counts"]["decisions"] >= 5
    assert recovered["counts"]["goals"] >= 1


def test_resident_agi_evidence_interfaces_query_reports_public_facade_status(tmp_path: Path) -> None:
    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    payload = query_resident_agi_evidence_interfaces(
        QueryResidentAgiEvidenceInterfacesV1(
            workspace=str(workspace),
            decision_type="quality_gate_response",
            interface_ids=(
                "run_ledger.read",
                "verifier.policy.read",
                "audit.verdict.read",
                "director.deterministic_repair_strategy_catalog.read",
                "director.repair_coverage.read",
                "director.repair_advisory_policy.read",
                "audit.diagnosis.execute",
            ),
            evidence_refs=("TypeScript syntax check failed: src/app.ts(1,10): error TS1005: ',' expected.",),
            max_runs=5,
        )
    )

    assert payload["schema_version"] == "resident.agi_evidence_interfaces.v1"
    assert payload["selected_decision_capability"]["decision_id"] == "quality.gate.response"
    by_id = {item["interface_id"]: item for item in payload["interfaces"]}
    assert by_id["run_ledger.read"]["callable"] is True
    assert by_id["run_ledger.read"]["source"] == "control_plane.run_ledger.public.read_run_ledger_projection"
    assert by_id["verifier.policy.read"]["status"] == "available"
    assert by_id["verifier.policy.read"]["callable"] is True
    assert by_id["audit.verdict.read"]["source"] == "audit.verdict.public.query_audit_verdict"
    assert by_id["audit.verdict.read"]["status"] == "empty"
    assert by_id["audit.verdict.read"]["callable"] is True
    repair_catalog = by_id["director.deterministic_repair_strategy_catalog.read"]
    assert repair_catalog["source"] == "director.runtime.public.query_director_repair_strategy_catalog"
    assert repair_catalog["status"] == "available"
    assert repair_catalog["available"] is True
    assert repair_catalog["callable"] is True
    assert repair_catalog["summary"]["schema_version"] == "director.deterministic_repair_strategy_catalog.v1"
    assert repair_catalog["summary"]["owner_cell"] == "director.runtime"
    assert repair_catalog["summary"]["execution_boundary"] == "director_authorized_tools_only"
    assert repair_catalog["summary"]["chain"] == "PM → Chief Engineer → Director"
    assert repair_catalog["summary"]["agi_execution_authority"] is False
    assert repair_catalog["summary"]["director_tool_execution_required"] is True
    assert repair_catalog["summary"]["unknown_source_tool_policy"] == "fail_closed_high_risk"
    repair_coverage = by_id["director.repair_coverage.read"]
    assert repair_coverage["source"] == "director.runtime.public.query_director_repair_coverage"
    assert repair_coverage["status"] == "available"
    assert repair_coverage["available"] is True
    assert repair_coverage["callable"] is True
    assert repair_coverage["summary"]["schema_version"] == "director.repair_coverage_report.v1"
    assert repair_coverage["summary"]["diagnostic_candidate_count"] == 1
    assert repair_coverage["summary"]["covered_diagnostic_count"] == 1
    assert repair_coverage["summary"]["uncovered_diagnostic_count"] == 0
    assert repair_coverage["summary"]["agi_execution_authority"] is False
    advisory_policy = by_id["director.repair_advisory_policy.read"]
    assert advisory_policy["source"] == "director.runtime.public.query_director_repair_advisory_policy"
    assert advisory_policy["status"] == "available"
    assert advisory_policy["available"] is True
    assert advisory_policy["callable"] is True
    assert advisory_policy["summary"]["schema_version"] == "director.repair_advisory_policy.v1"
    assert advisory_policy["summary"]["suggested_rules_allowed"] is True
    assert advisory_policy["summary"]["writes_allowed"] is False
    assert advisory_policy["summary"]["registration_allowed"] is False
    assert by_id["audit.diagnosis.execute"]["status"] == "governed_execute_only"
    assert payload["summary"]["needs_public_facade"] == 0
    assert payload["summary"]["governed_execute_only"] == 1


def test_resident_agi_decision_turn_participation_distinguishes_manual_and_auto_switch(
    tmp_path: Path,
) -> None:
    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    update_resident_identity(
        UpdateResidentIdentityCommandV1(
            workspace=str(workspace),
            payload={
                "resident_agi_participation": {
                    "enabled": True,
                    "scopes": ["quality_gate_response"],
                    "participation": {"quality_gate_response": True},
                    "custom_scopes_allowed": True,
                }
            },
        )
    )

    participation = resident_public_service._resident_agi_decision_turn_participation(
        command=RunResidentAgiDecisionTurnCommandV1(
            workspace=str(workspace),
            decision_type="quality_gate_response",
            objective="check quality gate",
        ),
        selected_decision_capability={"decision_id": "quality.gate.response"},
    )

    assert participation["enabled"] is True
    assert participation["role_turn_enabled"] is True
    assert participation["manual_role_turn_requested"] is True
    assert participation["automatic_participation_enabled"] is True
    assert participation["configured_enabled"] is True
    assert participation["configured_scopes"] == ["quality_gate_response"]
    assert participation["automatic_participation"]["quality_gate_response"] is True
    assert participation["participation"]["quality_gate_response"] is True
    assert participation["participation"]["final_request_audit"] is True
    assert "final_request_audit" in participation["required_role_turn_scopes"]


def test_resident_agi_selects_director_repair_advisory_capability() -> None:
    capability_surface = resident_public_service.resident_agi_capability_surface_payload()
    selected = resident_public_service._resident_agi_select_decision_capability(
        decision_type="repair_rule_suggestion",
        audit_pack={"capability_surface": capability_surface},
    )

    assert selected["decision_id"] == "director.repair.advisory"
    assert selected["owner"] == "resident_agi"
    assert "director.repair_coverage.read" in selected["required_evidence_interfaces"]
    assert "director.repair_advisory_policy.read" in selected["required_evidence_interfaces"]
    assert "suggest_repair_rule" in selected["candidate_actions"]

    handoff = resident_public_service._resident_agi_decision_handoff(
        command=RunResidentAgiDecisionTurnCommandV1(
            workspace="/tmp/polaris-resident-test",
            decision_type="repair_rule_suggestion",
            objective="Suggest a non-authoritative repair rule.",
        ),
        selected_decision_capability=selected,
        decision_preflight={"status": "pass", "passed": True},
        output_contract_gate={"status": "pass", "passed": True},
        runtime_contract_gate={"status": "pass", "passed": True},
        hard_rule_gate={"status": "pass"},
        evidence_gate={"status": "pass"},
        agi_verdict="continue",
        downstream_allowed=True,
        runtime_success=True,
        next_action="suggest_repair_rule",
        rationale="Coverage gap identified.",
        error="",
        effective_candidate_actions=["suggest_repair_rule"],
        evidence_refs=["runtime/repair-coverage.json"],
    )

    assert handoff["decision_capability_id"] == "director.repair.advisory"
    assert handoff["target_roles"] == ["director", "qa"]
    assert "suggest_repair_rule" in handoff["allowed_actions"]
    assert "director_tool_execution_by_agi" in handoff["blocked_actions"]
    assert handoff["downstream_allowed"] is True


def test_resident_agi_evidence_interfaces_treats_metadata_only_required_as_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    def fake_audit_pack(*, workspace: str, status_payload: dict[str, object], decision_limit: int) -> dict[str, object]:
        return {
            "schema_version": "resident.agi_audit_pack.v1",
            "evidence_refs": [],
            "capability_surface": {
                "decision_capabilities": [
                    {
                        "decision_id": "quality.gate.response",
                        "required_evidence_interfaces": ["contextos.final_request_audit.read"],
                        "optional_evidence_interfaces": [],
                        "candidate_actions": ["request_evidence"],
                        "hard_constraints": ["final_provider_request_required"],
                    }
                ],
                "items": [
                    {
                        "capability_id": "contextos.final_request_audit.read",
                        "name": "Final request audit",
                        "access": "read",
                        "contract_ref": "roles.final_request_context_audit",
                        "risk_level": "high",
                    }
                ],
            },
        }

    monkeypatch.setattr(resident_public_service, "build_resident_agi_audit_pack", fake_audit_pack)

    payload = query_resident_agi_evidence_interfaces(
        QueryResidentAgiEvidenceInterfacesV1(
            workspace=str(workspace),
            decision_type="quality_gate_response",
            interface_ids=("contextos.final_request_audit.read",),
        )
    )

    by_id = {item["interface_id"]: item for item in payload["interfaces"]}
    assert by_id["contextos.final_request_audit.read"]["status"] == "metadata_only"
    assert by_id["contextos.final_request_audit.read"]["available"] is False
    assert payload["summary"]["missing_required_interface_ids"] == ["contextos.final_request_audit.read"]


def test_resident_agi_evidence_interfaces_reads_final_provider_request_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.kernelone.llm.engine.executor import AIExecutor

    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    hash_key = AIExecutor._store_context_messages_sync(
        workspace=str(workspace),
        messages=[{"role": "system", "content": "You are Resident AGI."}],
        trace_id="trace-resident-final-request",
        call_id="call-resident-final-request",
        provider_request={
            "schema_version": "llm.provider_request_snapshot.v1",
            "role": "resident_agi",
            "provider_id": "mock-provider",
            "provider_type": "mock",
            "model": "mock-model",
            "tool_schema_count": 1,
            "tools": [{"type": "function", "name": "repo_tree", "argument_keys": [], "required": []}],
            "tool_choice": "auto",
            "response_format": {"type": "json_schema", "name": "ResidentAgiDecisionOutputV1"},
            "final_request_context_audit": {
                "schema_version": "llm.final_request_context_audit.v1",
                "final_request_token_estimate": 256,
                "tool_schema_count": 1,
            },
        },
    )
    context_ref = f"runtime/contexts/{hash_key[:2]}/{hash_key}"

    def fake_audit_pack(*, workspace: str, status_payload: dict[str, object], decision_limit: int) -> dict[str, object]:
        return {
            "schema_version": "resident.agi_audit_pack.v1",
            "evidence_refs": [context_ref],
            "capability_surface": {
                "decision_capabilities": [
                    {
                        "decision_id": "quality.gate.response",
                        "required_evidence_interfaces": ["contextos.final_request_audit.read"],
                        "optional_evidence_interfaces": [],
                        "candidate_actions": ["request_evidence"],
                        "hard_constraints": ["final_provider_request_required"],
                    }
                ],
                "items": [
                    {
                        "capability_id": "contextos.final_request_audit.read",
                        "name": "Final request audit",
                        "access": "read",
                        "contract_ref": "roles.final_request_context_audit",
                        "risk_level": "high",
                    }
                ],
            },
        }

    monkeypatch.setattr(resident_public_service, "build_resident_agi_audit_pack", fake_audit_pack)

    payload = query_resident_agi_evidence_interfaces(
        QueryResidentAgiEvidenceInterfacesV1(
            workspace=str(workspace),
            decision_type="quality_gate_response",
            interface_ids=("contextos.final_request_audit.read",),
        )
    )

    by_id = {item["interface_id"]: item for item in payload["interfaces"]}
    item = by_id["contextos.final_request_audit.read"]
    assert item["status"] == "available"
    assert item["available"] is True
    assert item["callable"] is True
    assert item["source"] == "context.engine.public.query_final_provider_request_audit"
    assert item["payload"]["provider_request"]["schema_version"] == "llm.provider_request_snapshot.v1"
    assert item["payload"]["final_request_context_audit"]["final_request_token_estimate"] == 256
    assert payload["summary"]["missing_required_interface_ids"] == []


def test_resident_agi_decision_turn_rejects_disabled_audit_pack(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    with pytest.raises(ValueError, match="include_audit_pack"):
        RunResidentAgiDecisionTurnCommandV1(
            workspace=str(workspace),
            objective="Attempt to run Resident AGI without the platform audit pack.",
            include_audit_pack=False,
        )


@pytest.mark.asyncio
async def test_resident_agi_decision_turn_public_command_uses_role_runtime_contract(tmp_path: Path) -> None:
    from polaris.kernelone.llm.engine.executor import AIExecutor

    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    captured: dict[str, object] = {}
    hash_key = AIExecutor._store_context_messages_sync(
        workspace=str(workspace),
        messages=[{"role": "system", "content": "You are Resident AGI."}],
        trace_id="trace-resident-runtime-contract",
        call_id="call-resident-runtime-contract",
        provider_request={
            "schema_version": "llm.provider_request_snapshot.v1",
            "role": "resident_agi",
            "provider_id": "mock-provider",
            "provider_type": "mock",
            "model": "mock-model",
            "tool_schema_count": 1,
            "tools": [{"type": "function", "name": "repo_tree", "argument_keys": [], "required": []}],
            "tool_choice": "auto",
            "response_format": {"type": "json_schema", "name": "ResidentAgiDecisionOutputV1"},
            "final_request_context_audit": {
                "schema_version": "llm.final_request_context_audit.v1",
                "final_request_token_estimate": 256,
                "tool_schema_count": 1,
            },
        },
    )
    context_ref = f"runtime/contexts/{hash_key[:2]}/{hash_key}"

    def fake_audit_pack(*, workspace: str, status_payload: dict[str, object], decision_limit: int) -> dict[str, object]:
        return {
            "schema_version": "resident.agi_audit_pack.v1",
            "truth_sources": ["resident.status"],
            "evidence_refs": [],
            "role_registry": {"resident_agi_available": True},
            "hard_rule_gate": {"schema_version": "resident.agi_hard_rule_gate.v1", "status": "pass"},
            "evidence_gate": {
                "schema_version": "resident.agi_evidence_gate.v1",
                "status": "pass",
                "recommended_verdict": "continue",
            },
            "authority_matrix": {
                "schema_version": "resident.agi_authority_matrix.v1",
                "chain_required": True,
            },
            "decision_profile": {
                "schema_version": "resident.agi_decision_profile.v1",
                "role_turn_allowed": True,
                "candidate_actions": ["continue", "request_evidence"],
                "required_constraints": ["preserve_pm_chief_engineer_director_qa_chain"],
            },
            "capability_surface": {
                "decision_capabilities": [
                    {
                        "decision_id": "evidence.interface.selection",
                        "required_evidence_interfaces": ["contextos.final_request_audit.read"],
                        "optional_evidence_interfaces": ["verifier.execution.execute"],
                        "candidate_actions": ["request_evidence"],
                        "hard_constraints": ["final_provider_request_required"],
                    }
                ],
                "items": [
                    {
                        "capability_id": "contextos.final_request_audit.read",
                        "name": "Final request audit",
                        "access": "read",
                        "contract_ref": "roles.final_request_context_audit",
                        "risk_level": "high",
                    },
                    {
                        "capability_id": "verifier.execution.execute",
                        "name": "Verifier execution request",
                        "access": "execute_through_control_plane_contract",
                        "contract_ref": "control_plane.verifier_execution",
                        "risk_level": "high",
                    },
                ],
            },
        }

    class FakeResidentAgiAdapter:
        async def execute(
            self,
            task_id: str,
            input_data: dict[str, object],
            context: dict[str, object],
        ) -> dict[str, object]:
            captured["task_id"] = task_id
            captured["input_data"] = input_data
            captured["context"] = context
            return {
                "success": True,
                "stage": "resident_agi",
                "decision_type": "platform_supervision",
                "decision": {
                    "verdict": "request_evidence",
                    "rationale": "Final request and run ledger evidence must be gathered before downstream work.",
                    "evidence_refs": ["runtime/contexts/context-public.json"],
                    "risks": [],
                    "next_action": "request final provider request and run ledger evidence",
                    "downstream_allowed": False,
                    "decision_capability_id": "evidence.interface.selection",
                },
                "metadata": {
                    "role_runtime_entrypoint": "roles.runtime.execute_role_session",
                    "context_os_expected": True,
                    "runtime_fallback_used": False,
                    "fallback_policy": "fail_closed",
                },
            }

    def fake_create_role_adapter(role_id: str, workspace_arg: str) -> FakeResidentAgiAdapter:
        captured["role_id"] = role_id
        captured["workspace"] = workspace_arg
        return FakeResidentAgiAdapter()

    with (
        patch(
            "polaris.cells.resident.autonomy.public.service.create_role_adapter",
            side_effect=fake_create_role_adapter,
        ),
        patch(
            "polaris.cells.resident.autonomy.public.service.build_resident_agi_audit_pack",
            side_effect=fake_audit_pack,
        ),
    ):
        result = await run_resident_agi_decision_turn(
            RunResidentAgiDecisionTurnCommandV1(
                workspace=str(workspace),
                decision_type="platform_supervision",
                objective="Decide whether the Resident AGI can continue.",
                task_id="resident-agi-public-command",
                evidence={"source": "service-test"},
                constraints=("preserve_pm_chief_engineer_director_qa_chain",),
                candidate_actions=("continue", "request_evidence"),
                context_refs=(context_ref,),
                evidence_refs=("runtime/gates/qa.json",),
            )
        )

    assert result["ok"] is True
    assert result["runtime_contract_gate"]["status"] == "pass"
    assert result["output_contract_gate"]["status"] == "pass"
    assert result["decision_preflight"]["status"] == "pass"
    control_plane_gate = result["control_plane_gate"]
    assert control_plane_gate["schema_version"] == "resident.agi_control_gate_receipt.v1"
    assert control_plane_gate["policy_decision"] == "request_evidence"
    assert control_plane_gate["gate_ok"] is False
    assert control_plane_gate["evidence_receipt_path"] == "runtime/evidence/resident_agi.decision_gate.jsonl"
    ledger_projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(
            workspace=str(workspace),
            run_id=control_plane_gate["run_id"],
        )
    ).projection
    assert ledger_projection["available"] is True
    assert ledger_projection["projected"] == 1
    resident_project = ledger_projection["projects"][0]
    assert resident_project["gate_count"] == 1
    assert resident_project["failed_gate_count"] == 1
    assert resident_project["ok"] is False
    evidence_query = query_evidence_events(QueryEvidenceEventsV1(limit=10), workspace=str(workspace))
    assert evidence_query.total == 1
    assert evidence_query.events[0]["kind"] == "resident_agi.decision_gate"
    assert result["selected_decision_capability"]["decision_id"] == "evidence.interface.selection"
    assert "contextos.final_request_audit.read" in result["required_evidence_interfaces"]
    assert "verifier.execution.execute" in result["optional_evidence_interfaces"]
    assert result["recorded_decision"]["actor"] == "resident_agi"
    assert result["recorded_decision"]["expected_outcome"]["decision_capability"]["decision_id"] == (
        "evidence.interface.selection"
    )
    assert (
        "contextos.final_request_audit.read"
        in (result["recorded_decision"]["expected_outcome"]["required_evidence_interfaces"])
    )
    assert result["recorded_decision"]["actual_outcome"]["decision_source"] == "resident_agi_role_runtime"
    assert result["recorded_decision"]["actual_outcome"]["resident_agi_decision_capability"]["decision_id"] == (
        "evidence.interface.selection"
    )
    assert result["recorded_decision"]["actual_outcome"]["resident_agi_runtime_contract_gate"]["passed"] is True
    assert result["recorded_decision"]["actual_outcome"]["resident_agi_decision_preflight"]["passed"] is True
    decision_handoff = result["decision_handoff"]
    assert decision_handoff["schema_version"] == "resident.agi_decision_handoff.v1"
    assert decision_handoff["handoff_status"] == "hold"
    assert decision_handoff["target_roles"] == ["resident_agi", "qa"]
    assert decision_handoff["downstream_allowed"] is False
    assert decision_handoff["advisory_only"] is True
    assert decision_handoff["agi_execution_authority"] is False
    assert "request_evidence_via_public_cell_contract" in decision_handoff["allowed_actions"]
    assert "director_tool_execution_by_agi" in decision_handoff["blocked_actions"]
    assert decision_handoff["required_chain"] == "PM → Chief Engineer → Director"
    assert (
        result["recorded_decision"]["actual_outcome"]["resident_agi_decision_handoff"]["schema_version"]
        == "resident.agi_decision_handoff.v1"
    )
    assert "runtime/contexts/context-public.json" in result["recorded_decision"]["evidence_refs"]
    assert captured["role_id"] == "resident_agi"
    assert captured["workspace"] == str(workspace)
    captured_context = captured["context"]
    assert isinstance(captured_context, dict)
    assert captured_context["metadata"]["source"] == "resident.autonomy.public.run_resident_agi_decision_turn"
    assert captured_context["metadata"]["context_os_expected"] is True
    assert captured_context["metadata"]["resident_agi_selected_decision_capability"] == "evidence.interface.selection"
    assert (
        "contextos.final_request_audit.read"
        in (captured_context["metadata"]["resident_agi_required_evidence_interfaces"])
    )
    assert captured_context["metadata"]["resident_agi_decision_preflight_passed"] is True
    captured_input = captured["input_data"]
    assert isinstance(captured_input, dict)
    assert captured_input["selected_decision_capability"]["decision_id"] == "evidence.interface.selection"
    assert captured_input["resident_agi_decision_preflight"]["status"] == "pass"
    assert "verifier.execution.execute" in captured_input["optional_evidence_interfaces"]


@pytest.mark.asyncio
async def test_resident_agi_decision_turn_blocks_before_adapter_when_required_evidence_missing(
    tmp_path: Path,
) -> None:
    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    def fail_create_role_adapter(role_id: str, workspace_arg: str) -> object:
        raise AssertionError(f"adapter should not be created: {role_id} {workspace_arg}")

    with patch(
        "polaris.cells.resident.autonomy.public.service.create_role_adapter",
        side_effect=fail_create_role_adapter,
    ):
        result = await run_resident_agi_decision_turn(
            RunResidentAgiDecisionTurnCommandV1(
                workspace=str(workspace),
                decision_type="quality_gate_response",
                objective="Decide whether the current run can proceed without required evidence.",
                task_id="resident-agi-preflight-block",
            )
        )

    assert result["ok"] is False
    assert result["decision"]["verdict"] == "request_evidence"
    assert result["decision_preflight"]["schema_version"] == "resident.agi_decision_preflight.v1"
    assert result["decision_preflight"]["status"] == "block"
    assert result["decision_preflight"]["passed"] is False
    assert "run_ledger.read" in result["decision_preflight"]["missing_required_interface_ids"]
    assert result["runtime_contract_gate"]["status"] == "preflight_blocked"
    assert result["output_contract_gate"]["status"] == "preflight_blocked"
    assert result["recorded_decision"]["verdict"] == "blocked"
    actual_outcome = result["recorded_decision"]["actual_outcome"]
    assert actual_outcome["resident_agi_decision_preflight"] == result["decision_preflight"]
    assert actual_outcome["runtime_success"] is False


@pytest.mark.asyncio
async def test_resident_agi_decision_turn_rejects_continue_when_evidence_gate_holds(tmp_path: Path) -> None:
    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    def fake_audit_pack(*, workspace: str, status_payload: dict[str, object], decision_limit: int) -> dict[str, object]:
        return {
            "schema_version": "resident.agi_audit_pack.v1",
            "truth_sources": ["resident.status"],
            "evidence_refs": [],
            "role_registry": {"resident_agi_available": True},
            "hard_rule_gate": {"schema_version": "resident.agi_hard_rule_gate.v1", "status": "pass"},
            "evidence_gate": {
                "schema_version": "resident.agi_evidence_gate.v1",
                "status": "hold",
                "recommended_verdict": "request_evidence",
            },
            "authority_matrix": {
                "schema_version": "resident.agi_authority_matrix.v1",
                "chain_required": True,
            },
            "decision_profile": {
                "schema_version": "resident.agi_decision_profile.v1",
                "role_turn_allowed": True,
                "candidate_actions": ["continue", "request_evidence"],
                "required_constraints": ["preserve_pm_chief_engineer_director_qa_chain"],
            },
            "capability_surface": {
                "decision_capabilities": [
                    {
                        "decision_id": "quality.gate.response",
                        "required_evidence_interfaces": [],
                        "optional_evidence_interfaces": [],
                        "candidate_actions": ["continue", "request_evidence"],
                        "hard_constraints": ["failed_quality_gate_cannot_be_marked_passed_by_agi"],
                    }
                ],
                "items": [],
            },
        }

    class FakeResidentAgiAdapter:
        async def execute(
            self,
            task_id: str,
            input_data: dict[str, object],
            context: dict[str, object],
        ) -> dict[str, object]:
            return {
                "success": True,
                "stage": "resident_agi",
                "decision_type": "quality_gate_response",
                "decision": {
                    "verdict": "continue",
                    "rationale": "Attempt to continue without required final request evidence.",
                    "evidence_refs": ["runtime/contexts/context-public.json"],
                    "risks": [],
                    "next_action": "continue governed run",
                    "downstream_allowed": True,
                    "decision_capability_id": "quality.gate.response",
                },
                "metadata": {
                    "role_runtime_entrypoint": "roles.runtime.execute_role_session",
                    "context_os_expected": True,
                    "runtime_fallback_used": False,
                    "fallback_policy": "fail_closed",
                },
            }

    with (
        patch(
            "polaris.cells.resident.autonomy.public.service.create_role_adapter",
            return_value=FakeResidentAgiAdapter(),
        ),
        patch(
            "polaris.cells.resident.autonomy.public.service.build_resident_agi_audit_pack",
            side_effect=fake_audit_pack,
        ),
    ):
        result = await run_resident_agi_decision_turn(
            RunResidentAgiDecisionTurnCommandV1(
                workspace=str(workspace),
                decision_type="quality_gate_response",
                objective="Decide whether the current run can proceed.",
                task_id="resident-agi-output-contract",
                evidence_refs=("runtime/gates/qa.json",),
            )
        )

    assert result["ok"] is False
    assert result["runtime_contract_gate"]["status"] == "pass"
    assert result["decision_preflight"]["status"] == "pass"
    assert result["output_contract_gate"]["status"] == "fail"
    assert "evidence_gate.continue_guard" in result["output_contract_gate"]["failed_check_ids"]
    assert "evidence_gate.downstream_guard" in result["output_contract_gate"]["failed_check_ids"]
    actual_outcome = result["recorded_decision"]["actual_outcome"]
    assert actual_outcome["runtime_success"] is False
    assert actual_outcome["resident_agi_output_contract_gate"] == result["output_contract_gate"]


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


def test_public_goal_command_publishes_resident_status_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = get_resident_service(str(workspace))
    goal = service.create_goal_proposal(
        {
            "goal_type": "maintenance",
            "title": "Publish resident status update",
            "motivation": "Keep Resident AGI runtime.v2 projection live.",
            "source": "test",
            "scope": ["src/backend/polaris/cells/resident/autonomy"],
            "evidence_refs": ["runtime/contexts/context-1.json"],
        }
    )
    assert service.approve_goal(goal.goal_id, note="approved") is not None

    published: list[dict[str, object]] = []

    class FakePublisher:
        def publish(self, *, subject: str, payload: dict[str, object]) -> bool:
            published.append({"subject": subject, "payload": payload})
            return True

    monkeypatch.setenv("KERNELONE_JETSTREAM_PUBLISH", "1")
    monkeypatch.setattr(
        "polaris.cells.resident.autonomy.public.service.resolve_storage_roots",
        lambda workspace_token: SimpleNamespace(workspace_key="workspace-key"),
    )
    monkeypatch.setattr(
        "polaris.cells.resident.autonomy.public.service.get_log_jetstream_publisher",
        lambda: FakePublisher(),
    )

    contract = materialize_resident_goal(
        MaterializeResidentGoalCommandV1(workspace=str(workspace), goal_id=goal.goal_id)
    )

    assert contract is not None
    assert len(published) == 1
    event = published[0]
    assert event["subject"] == "hp.runtime.workspace-key.status.resident"
    payload = event["payload"]
    assert isinstance(payload, dict)
    assert payload["schema_version"] == "runtime.v2"
    assert payload["channel"] == "status.resident"
    assert payload["kind"] == "resident_status_update"
    meta = payload["meta"]
    assert isinstance(meta, dict)
    assert meta == {
        "source": "resident.autonomy",
        "role_id": "resident_agi",
        "runtime_foundation": "roles.runtime + ContextOS + TurnEngine",
        "channel": "runtime.v2.status.resident",
    }
    event_payload = payload["payload"]
    assert isinstance(event_payload, dict)
    assert event_payload["action"] == "goal_materialized"
    assert event_payload["workspace"] == str(workspace)
    assert event_payload["role_id"] == "resident_agi"
    resident = event_payload["resident"]
    assert isinstance(resident, dict)
    assert resident["workspace"] == str(workspace)
    assert resident["agi_capability_surface"]["role_id"] == "resident_agi"
    assert "runtime.v2" in str(meta["channel"])
