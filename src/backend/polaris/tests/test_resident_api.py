from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from polaris.bootstrap.config import Settings
from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import CommandResult
from polaris.cells.resident.autonomy.internal.resident_runtime_service import reset_resident_services
from polaris.cells.resident.autonomy.public import record_resident_decision
from polaris.cells.resident.autonomy.public.contracts import RunResidentAgiDecisionTurnCommandV1
from polaris.delivery.http.app_factory import create_app


def test_resident_agi_decide_runs_role_adapter_and_records_decision(tmp_path: Path, monkeypatch) -> None:
    test_token = "test-resident-token"
    monkeypatch.setenv("KERNELONE_TOKEN", test_token)
    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    captured: dict[str, object] = {}

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
                "decision_type": "quality_gate_response",
                "decision": {
                    "verdict": "request_evidence",
                    "rationale": "ContextOS and quality gate evidence are sufficient.",
                    "evidence_refs": ["runtime/contexts/context-1.json"],
                    "risks": [],
                    "next_action": "request final provider request and run ledger evidence",
                    "downstream_allowed": False,
                    "decision_capability_id": "quality.gate.response",
                },
                "metadata": {
                    "role_runtime_entrypoint": "roles.runtime.execute_role_session",
                    "context_os_expected": True,
                    "runtime_fallback_used": False,
                    "fallback_policy": "fail_closed",
                },
                "tool_calls": [],
                "execution_stats": {"total_tokens": 128},
            }

    def fake_create_role_adapter(role_id: str, workspace_arg: str) -> FakeResidentAgiAdapter:
        captured["role_id"] = role_id
        captured["workspace"] = workspace_arg
        return FakeResidentAgiAdapter()

    def fake_evidence_interfaces(query: object) -> dict[str, object]:
        return {
            "schema_version": "resident.agi_evidence_interfaces.v1",
            "workspace": str(workspace),
            "decision_type": "quality_gate_response",
            "run_id": "run-agi-1",
            "task_id": "task-agi-1",
            "selected_decision_capability": {"decision_id": "quality.gate.response"},
            "required_evidence_interfaces": ["run_ledger.read"],
            "optional_evidence_interfaces": ["audit.diagnosis.execute"],
            "requested_interface_ids": ["run_ledger.read", "audit.diagnosis.execute"],
            "interfaces": [
                {
                    "interface_id": "run_ledger.read",
                    "status": "available",
                    "available": True,
                    "source": "control_plane.run_ledger.public.read_run_ledger_projection",
                    "gaps": [],
                    "recommended_next_action": "use_run_ledger_projection",
                },
                {
                    "interface_id": "audit.diagnosis.execute",
                    "status": "governed_execute_only",
                    "available": False,
                    "source": "resident.agi_capability_surface",
                    "gaps": [],
                    "recommended_next_action": "request_governed_execution_if_read_evidence_is_insufficient",
                },
            ],
            "capability_matrix": {
                "schema_version": "resident.agi_evidence_capability_matrix.v1",
                "decision_type": "quality_gate_response",
                "selected_decision_id": "quality.gate.response",
                "summary": {
                    "total": 2,
                    "available": 1,
                    "required": 1,
                    "required_available": 1,
                    "missing_required": 0,
                    "missing_required_interface_ids": [],
                    "recommended_now": 2,
                    "callable": 1,
                    "high_risk": 0,
                    "governed_execute": 1,
                    "advisory_only": True,
                    "authoritative": False,
                    "agi_execution_authority": False,
                },
                "groups": [
                    {
                        "group_id": "run_ledger",
                        "name": "Run ledger",
                        "interface_ids": ["run_ledger.read"],
                        "total": 1,
                        "available": 1,
                        "required": 1,
                        "missing_required": 0,
                        "recommended_now": 1,
                        "high_risk": 0,
                        "governed_execute": 0,
                    },
                    {
                        "group_id": "audit",
                        "name": "Audit",
                        "interface_ids": ["audit.diagnosis.execute"],
                        "total": 1,
                        "available": 0,
                        "required": 0,
                        "missing_required": 0,
                        "recommended_now": 1,
                        "high_risk": 0,
                        "governed_execute": 1,
                    },
                ],
                "rows": [
                    {
                        "interface_id": "run_ledger.read",
                        "group_id": "run_ledger",
                        "required": True,
                        "recommended_now": True,
                        "available": True,
                        "status": "available",
                        "source": "control_plane.run_ledger.public.read_run_ledger_projection",
                        "recommended_next_action": "use_run_ledger_projection",
                        "gap_count": 0,
                    },
                    {
                        "interface_id": "audit.diagnosis.execute",
                        "group_id": "audit",
                        "required": False,
                        "recommended_now": True,
                        "available": False,
                        "status": "governed_execute_only",
                        "source": "resident.agi_capability_surface",
                        "recommended_next_action": "request_governed_execution_if_read_evidence_is_insufficient",
                        "gap_count": 0,
                    },
                ],
            },
            "summary": {
                "total": 2,
                "available": 1,
                "missing_required_interface_ids": [],
            },
            "audit_pack_ref": {},
        }

    app = create_app(Settings(workspace=str(workspace), ramdisk_root=""))
    with (
        patch(
            "polaris.cells.resident.autonomy.public.service.create_role_adapter",
            side_effect=fake_create_role_adapter,
        ),
        patch(
            "polaris.cells.resident.autonomy.public.service.query_resident_agi_evidence_interfaces",
            side_effect=fake_evidence_interfaces,
        ),
        TestClient(app, headers={"Authorization": f"Bearer {test_token}"}) as client,
    ):
        response = client.post(
            "/v2/resident/agi/decide",
            json={
                "workspace": str(workspace),
                "decision_type": "quality_gate_response",
                "objective": "Decide whether the current run can proceed to QA.",
                "run_id": "run-agi-1",
                "task_id": "task-agi-1",
                "evidence": {"context_snapshot_ref": "runtime/contexts/context-1.json"},
                "constraints": ["fail closed when evidence is missing"],
                "candidate_actions": ["continue", "request_evidence"],
                "context_refs": ["runtime/contexts/context-1.json"],
                "evidence_refs": ["runtime/gates/qa.json"],
                "confidence": 0.82,
                "audit_pack_decision_limit": 7,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    runtime_context = captured["context"]
    assert isinstance(runtime_context, dict)
    assert runtime_context["resident_agi_enabled"] is True
    assert runtime_context["resident_agi_role_turn_enabled"] is True
    assert runtime_context["resident_agi_manual_role_turn_requested"] is True
    assert runtime_context["resident_agi_automatic_participation_enabled"] is False
    assert runtime_context["resident_agi_participation"]["enabled"] is True
    assert runtime_context["resident_agi_participation"]["role_turn_enabled"] is True
    assert runtime_context["resident_agi_participation"]["manual_role_turn_requested"] is True
    assert runtime_context["resident_agi_participation"]["automatic_participation_enabled"] is False
    assert runtime_context["resident_agi_participation"]["configured_enabled"] is False
    assert runtime_context["resident_agi_participation"]["automatic_participation"]["final_request_audit"] is False
    assert runtime_context["resident_agi_participation"]["participation"]["final_request_audit"] is True
    assert "final_request_audit" in runtime_context["resident_agi_participation_scopes"]
    assert payload["resident_agi_participation"]["enabled"] is True
    assert payload["resident_agi_participation"]["automatic_participation_enabled"] is False
    assert payload["resident_agi_participation"]["manual_role_turn_requested"] is True
    assert payload["decision"]["verdict"] == "request_evidence"
    assert payload["control_plane_gate"]["schema_version"] == "resident.agi_control_gate_receipt.v1"
    assert payload["control_plane_gate"]["policy_decision"] == "request_evidence"
    assert payload["control_plane_gate"]["gate_ok"] is False
    assert payload["recorded_decision"]["actor"] == "resident_agi"
    assert payload["recorded_decision"]["verdict"] == "blocked"
    assert payload["recorded_decision"]["actual_outcome"]["agi_verdict"] == "request_evidence"
    assert payload["recorded_decision"]["actual_outcome"]["role_runtime_entrypoint"] == (
        "roles.runtime.execute_role_session"
    )
    assert payload["recorded_decision"]["actual_outcome"]["resident_agi_audit_pack_injected"] is True
    assert payload["recorded_decision"]["actual_outcome"]["resident_agi_audit_pack_schema"] == (
        "resident.agi_audit_pack.v1"
    )
    assert payload["recorded_decision"]["expected_outcome"]["resident_agi_audit_pack_required"] is True
    assert payload["audit_pack"]["schema_version"] == "resident.agi_audit_pack.v1"
    assert payload["audit_pack"]["role_registry"]["resident_agi_available"] is True
    assert payload["audit_pack"]["director_repair_contract"]["owner_cell"] == "director.runtime"
    assert (
        payload["audit_pack"]["director_repair_contract"]["catalog_schema"]
        == "director.deterministic_repair_strategy_catalog.v1"
    )
    assert payload["audit_pack"]["director_repair_contract"]["coverage_schema"] == "director.repair_coverage_report.v1"
    assert (
        payload["audit_pack"]["director_repair_contract"]["advisory_policy_schema"]
        == "director.repair_advisory_policy.v1"
    )
    assert payload["audit_pack"]["director_repair_contract"]["agi_execution_authority"] is False
    assert payload["audit_pack"]["director_repair_contract"]["agi_advisory"]["suggested_rules_allowed"] is True
    assert payload["audit_pack"]["director_repair_contract"]["agi_advisory"]["writes_allowed"] is False
    assert payload["audit_pack"]["director_repair_contract"]["execution_boundary"] == "director_authorized_tools_only"
    assert payload["audit_pack"]["hard_rule_gate"]["status"] == "pass"
    assert payload["audit_pack"]["authority_matrix"]["schema_version"] == "resident.agi_authority_matrix.v1"
    assert payload["audit_pack"]["authority_matrix"]["chain_required"] is True
    assert (
        payload["audit_pack"]["capability_surface"]["decision_boundary_policy"]["schema_version"]
        == "resident.agi_decision_boundary_policy.v1"
    )
    assert (
        payload["audit_pack"]["capability_surface"]["decision_boundary_policy"]["capability_execution_policy"][
            "agi_direct_tool_execution_allowed"
        ]
        is False
    )
    assert payload["audit_pack"]["run_ledger_summary"]["source"] == "run_ledger_projection"
    assert payload["audit_pack"]["evidence_gate"]["status"] == "hold"
    assert payload["audit_pack"]["decision_profile"]["schema_version"] == "resident.agi_decision_profile.v1"
    assert payload["audit_pack"]["decision_profile"]["role_turn_allowed"] is True
    assert payload["audit_pack"]["decision_profile"]["recommended_verdict"] == "request_evidence"
    assert "request_evidence" in payload["audit_pack"]["decision_profile"]["candidate_actions"]
    assert (
        payload["audit_pack"]["decision_profile"]["decision_capability_registry"]["schema_version"]
        == "resident.agi_decision_capability_registry.v1"
    )
    assert "quality.gate.response" in payload["audit_pack"]["decision_profile"]["decision_capability_ids"]
    assert payload["selected_decision_capability"]["decision_id"] == "quality.gate.response"
    assert payload["decision_preflight"]["status"] == "pass"
    assert "run_ledger.read" in payload["required_evidence_interfaces"]
    assert payload["recorded_decision"]["actual_outcome"]["resident_agi_evidence_gate"]["status"] == "hold"
    assert payload["recorded_decision"]["actual_outcome"]["resident_agi_decision_profile"]["schema_version"] == (
        "resident.agi_decision_profile.v1"
    )
    assert payload["recorded_decision"]["actual_outcome"]["resident_agi_runtime_contract_gate"]["status"] == "pass"
    assert payload["recorded_decision"]["actual_outcome"]["resident_agi_decision_preflight"]["status"] == "pass"
    assert (
        payload["recorded_decision"]["actual_outcome"]["resident_agi_evidence_capability_matrix"]["schema_version"]
        == "resident.agi_evidence_capability_matrix.v1"
    )
    assert payload["evidence_capability_matrix"]["summary"]["required_available"] == 1
    assert payload["evidence_capability_matrix"]["summary"]["authoritative"] is False
    assert payload["decision_boundary_policy"]["schema_version"] == "resident.agi_decision_boundary_policy.v1"
    assert (
        payload["decision_boundary_policy"]["capability_execution_policy"]["director_runtime_remains_authoritative"]
        is True
    )
    assert payload["recorded_decision"]["actual_outcome"]["resident_agi_runtime_contract_gate"]["passed"] is True
    assert payload["recorded_decision"]["actual_outcome"]["resident_agi_output_contract_gate"]["status"] == "pass"
    assert payload["output_contract_gate"]["passed"] is True
    assert payload["runtime_contract_gate"]["schema_version"] == "resident.agi_runtime_contract_gate.v1"
    assert payload["runtime_contract_gate"]["status"] == "pass"
    assert payload["runtime_contract_gate"]["passed"] is True
    assert (
        payload["recorded_decision"]["actual_outcome"]["resident_agi_authority_matrix"]["decision_policy"][
            "governed_execution"
        ]
        == "canonical_role_chain_only"
    )
    assert (
        payload["recorded_decision"]["actual_outcome"]["resident_agi_decision_boundary_policy"]["decision_modes"][
            "platform_hard_rule"
        ]["llm_decision_allowed"]
        is False
    )
    assert (
        "preserve_pm_chief_engineer_director_qa_chain"
        in payload["recorded_decision"]["expected_outcome"]["constraints"]
    )
    assert payload["recorded_decision"]["expected_outcome"]["decision_capability"]["decision_id"] == (
        "quality.gate.response"
    )
    assert "run_ledger.read" in payload["recorded_decision"]["expected_outcome"]["required_evidence_interfaces"]
    assert payload["recorded_decision"]["actual_outcome"]["resident_agi_decision_capability"]["decision_id"] == (
        "quality.gate.response"
    )
    assert "runtime/gates/qa.json" in payload["recorded_decision"]["evidence_refs"]
    assert captured["role_id"] == "resident_agi"
    assert captured["workspace"] == str(workspace)
    assert captured["task_id"] == "task-agi-1"
    captured_input = captured["input_data"]
    assert isinstance(captured_input, dict)
    assert captured_input["resident_agi_audit_pack"]["schema_version"] == "resident.agi_audit_pack.v1"
    assert captured_input["evidence"]["resident_agi_audit_pack_schema"] == "resident.agi_audit_pack.v1"
    assert captured_input["evidence"]["resident_agi_hard_rule_gate_status"] == "pass"
    assert captured_input["evidence"]["resident_agi_evidence_gate_status"] == "hold"
    assert captured_input["evidence"]["resident_agi_authority_matrix_schema"] == "resident.agi_authority_matrix.v1"
    assert captured_input["evidence"]["resident_agi_chain_required"] is True
    assert captured_input["evidence"]["resident_agi_decision_profile_schema"] == "resident.agi_decision_profile.v1"
    assert captured_input["evidence"]["resident_agi_decision_profile_recommended_verdict"] == "request_evidence"
    assert captured_input["evidence"]["resident_agi_role_turn_allowed"] is True
    assert captured_input["evidence"]["resident_agi_manual_role_turn_requested"] is True
    assert captured_input["evidence"]["resident_agi_automatic_participation_enabled"] is False
    assert captured_input["evidence"]["resident_agi_evidence_capability_matrix_schema"] == (
        "resident.agi_evidence_capability_matrix.v1"
    )
    assert captured_input["evidence"]["resident_agi_evidence_matrix_required_available"] == 1
    assert captured_input["evidence"]["resident_agi_decision_boundary_policy_schema"] == (
        "resident.agi_decision_boundary_policy.v1"
    )
    assert captured_input["evidence"]["resident_agi_policy_direct_tools_allowed"] is False
    assert captured_input["resident_agi_evidence_capability_matrix"]["summary"]["recommended_now"] == 2
    assert captured_input["resident_agi_evidence_capability_matrix"]["summary"]["agi_execution_authority"] is False
    assert captured_input["resident_agi_decision_boundary_policy"]["schema_version"] == (
        "resident.agi_decision_boundary_policy.v1"
    )
    assert captured_input["resident_agi_tactical_action_catalog"]["schema_version"] == (
        "resident.agi_tactical_action_catalog.v1"
    )
    captured_tactical_items = {
        item["action_id"]: item for item in captured_input["resident_agi_tactical_action_catalog"]["items"]
    }
    assert captured_tactical_items["request_resident_agi_judgement"]["contract_ref"] == (
        "resident.autonomy.public.run_resident_agi_decision_turn"
    )
    assert captured_input["evidence"]["resident_agi_tactical_action_catalog_schema"] == (
        "resident.agi_tactical_action_catalog.v1"
    )
    assert captured_input["evidence"]["resident_agi_tactical_direct_execution_allowed"] is False
    assert "request_resident_agi_judgement" in captured_input["evidence"]["resident_agi_tactical_action_ids"]
    assert captured_input["selected_decision_capability"]["decision_id"] == "quality.gate.response"
    assert "run_ledger.read" in captured_input["required_evidence_interfaces"]
    assert "audit.diagnosis.execute" in captured_input["optional_evidence_interfaces"]
    assert "preserve_pm_chief_engineer_director_qa_chain" in captured_input["constraints"]
    assert "request_evidence" in captured_input["candidate_actions"]
    captured_context = captured["context"]
    assert isinstance(captured_context, dict)
    assert captured_context["resident_agi_audit_pack"]["schema_version"] == "resident.agi_audit_pack.v1"
    assert captured_context["resident_agi_evidence_capability_matrix"]["schema_version"] == (
        "resident.agi_evidence_capability_matrix.v1"
    )
    assert captured_context["resident_agi_evidence_capability_matrix"]["summary"]["governed_execute"] == 1
    assert captured_context["resident_agi_decision_boundary_policy"]["schema_version"] == (
        "resident.agi_decision_boundary_policy.v1"
    )
    assert captured_context["resident_agi_tactical_action_catalog"]["schema_version"] == (
        "resident.agi_tactical_action_catalog.v1"
    )
    assert (
        captured_context["resident_agi_decision_boundary_policy"]["capability_execution_policy"][
            "agi_direct_writes_allowed"
        ]
        is False
    )
    captured_metadata = captured_context["metadata"]
    assert isinstance(captured_metadata, dict)
    assert captured_metadata["resident_agi_role_runtime_required"] is True
    assert captured_metadata["resident_agi_audit_pack_injected"] is True
    assert captured_metadata["resident_agi_hard_rule_gate_status"] == "pass"
    assert captured_metadata["resident_agi_authority_matrix_schema"] == "resident.agi_authority_matrix.v1"
    assert captured_metadata["resident_agi_decision_profile_schema"] == "resident.agi_decision_profile.v1"
    assert captured_metadata["resident_agi_role_turn_allowed"] is True
    assert captured_metadata["resident_agi_selected_decision_capability"] == "quality.gate.response"
    assert captured_metadata["resident_agi_evidence_capability_matrix_schema"] == (
        "resident.agi_evidence_capability_matrix.v1"
    )
    assert captured_metadata["resident_agi_evidence_matrix_recommended_now"] == 2
    assert captured_metadata["resident_agi_decision_boundary_policy_schema"] == (
        "resident.agi_decision_boundary_policy.v1"
    )
    assert captured_metadata["resident_agi_policy_direct_writes_allowed"] is False
    assert captured_metadata["resident_agi_tactical_action_catalog_schema"] == (
        "resident.agi_tactical_action_catalog.v1"
    )
    assert captured_metadata["resident_agi_tactical_direct_execution_allowed"] is False
    assert "run_ledger.read" in captured_metadata["resident_agi_required_evidence_interfaces"]


def test_resident_agi_evidence_interfaces_endpoint_reports_readiness(tmp_path: Path, monkeypatch) -> None:
    test_token = "test-resident-token"
    monkeypatch.setenv("KERNELONE_TOKEN", test_token)
    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    app = create_app(Settings(workspace=str(workspace), ramdisk_root=""))
    with TestClient(app, headers={"Authorization": f"Bearer {test_token}"}) as client:
        response = client.get(
            "/v2/resident/agi/evidence-interfaces",
            params={
                "workspace": str(workspace),
                "decision_type": "quality_gate_response",
                "interface_ids": "run_ledger.read,verifier.policy.read,audit.verdict.read",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "resident.agi_evidence_interfaces.v1"
    assert payload["selected_decision_capability"]["decision_id"] == "quality.gate.response"
    by_id = {item["interface_id"]: item for item in payload["interfaces"]}
    assert by_id["run_ledger.read"]["callable"] is True
    assert by_id["verifier.policy.read"]["status"] == "available"
    assert by_id["audit.verdict.read"]["source"] == "audit.verdict.public.query_audit_verdict"
    assert by_id["audit.verdict.read"]["status"] == "empty"
    assert by_id["audit.verdict.read"]["callable"] is True
    matrix = payload["capability_matrix"]
    assert matrix["schema_version"] == "resident.agi_evidence_capability_matrix.v1"
    assert matrix["decision_type"] == "quality_gate_response"
    assert matrix["summary"]["advisory_only"] is True
    assert matrix["summary"]["authoritative"] is False
    assert matrix["summary"]["agi_execution_authority"] is False
    matrix_groups = {item["group_id"]: item for item in matrix["groups"]}
    assert "run_ledger" in matrix_groups
    assert "verifier" in matrix_groups
    assert "audit" in matrix_groups


def test_resident_agi_tactical_chat_endpoint_returns_evidence_backed_response(
    tmp_path: Path,
    monkeypatch,
) -> None:
    test_token = "test-resident-token"
    monkeypatch.setenv("KERNELONE_TOKEN", test_token)
    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    app = create_app(Settings(workspace=str(workspace), ramdisk_root=""))
    with TestClient(app, headers={"Authorization": f"Bearer {test_token}"}) as client:
        response = client.post(
            "/v2/resident/agi/chat",
            json={
                "workspace": str(workspace),
                "message": "帮我看下当前项目进度",
                "decision_type": "quality_gate_response",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "resident.agi_tactical_chat.v1"
    assert payload["source"] == "resident.autonomy.public.query_resident_agi_tactical_chat"
    assert payload["intent"] == "status_summary"
    assert "Resident runtime" in payload["message"]
    assert any("resident.agi_audit_pack.v1" in item for item in payload["flow"])
    assert payload["receipt"]["status"] == "READ"
    assert payload["policy"]["advisory_only"] is True
    assert payload["policy"]["agi_direct_writes_allowed"] is False
    assert payload["policy"]["required_chain"] == "PM → Chief Engineer → Director → QA"
    assert payload["mission_brief"]["schema_version"] == "resident.agi_tactical_mission_brief.v1"
    assert payload["mission_brief"]["title"] == "项目态势"
    assert payload["mission_brief"]["policy"]["ui_must_not_recompute_verdict"] is True
    assert any(item["label"] == "证据" for item in payload["mission_brief"]["metrics"])
    assert payload["tool_trace"]["schema_version"] == "resident.agi_tactical_tool_trace.v1"
    assert payload["tool_trace"]["summary"]["direct_execution_allowed"] is False
    assert payload["participation_gate"]["schema_version"] == "resident.agi_tactical_participation_gate.v1"
    assert payload["participation_gate"]["status"] == "disabled"
    assert payload["participation_gate"]["settings_action_available"] is False
    assert payload["participation_gate"]["agi_direct_permission_change_allowed"] is False
    assert payload["decision_route"]["schema_version"] == "resident.agi_tactical_decision_route.v1"
    assert payload["decision_route"]["source"] == "resident.autonomy.internal.agi_tactical_chat"
    assert payload["decision_route"]["hard_rules"]["llm_override_allowed"] is False
    assert payload["decision_route"]["governed_execution"]["agi_direct_execution_allowed"] is False
    assert "open_evidence_black_box" in payload["decision_route"]["recommended_action_ids"]
    assert payload["facts"]["decision_route_schema"] == "resident.agi_tactical_decision_route.v1"
    trace_ids = {item["step_id"] for item in payload["tool_trace"]["items"]}
    assert "resident.status.read" in trace_ids
    assert "resident.agi_audit_pack.read" in trace_ids
    assert "resident.agi_controlled_actions.boundary" in trace_ids
    action_ids = {item["action_id"] for item in payload["suggested_actions"]}
    assert "open_evidence_black_box" in action_ids
    assert "refresh_evidence_interfaces" in action_ids
    assert payload["action_catalog"]["schema_version"] == "resident.agi_tactical_action_catalog.v1"
    catalog_items = {item["action_id"]: item for item in payload["action_catalog"]["items"]}
    assert catalog_items["open_evidence_black_box"]["ui_handler"] == "open_advanced_audit"
    assert catalog_items["open_evidence_black_box"]["requires_participation"] is False
    assert catalog_items["refresh_evidence_interfaces"]["requires_participation"] is False
    assert catalog_items["open_operator_settings"]["ui_handler"] == "open_operator_settings"
    assert catalog_items["open_operator_settings"]["requires_participation"] is False
    assert catalog_items["request_resident_agi_judgement"]["capability_id"] == "resident.agi_decision_turn.execute"
    assert catalog_items["request_resident_agi_judgement"]["requires_participation"] is True

    with TestClient(app, headers={"Authorization": f"Bearer {test_token}"}) as client:
        catalog_response = client.get("/v2/resident/agi/actions/catalog")
    assert catalog_response.status_code == 200
    catalog_payload = catalog_response.json()
    assert catalog_payload["schema_version"] == "resident.agi_tactical_action_catalog.v1"
    assert catalog_payload["summary"]["agi_direct_execution_allowed"] is False
    assert catalog_payload["summary"]["requires_participation"] == 2


def test_resident_agi_tactical_chat_respects_participation_scopes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    test_token = "test-resident-token"
    monkeypatch.setenv("KERNELONE_TOKEN", test_token)
    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    app = create_app(Settings(workspace=str(workspace), ramdisk_root=""))
    with TestClient(app, headers={"Authorization": f"Bearer {test_token}"}) as client:
        disabled_response = client.post(
            "/v2/resident/agi/chat",
            json={
                "workspace": str(workspace),
                "message": "交给 Director 修复这个阻塞",
                "decision_type": "quality_gate_response",
            },
        )
        assert disabled_response.status_code == 200
        disabled_payload = disabled_response.json()
        assert disabled_payload["policy"]["participation_enabled"] is False
        assert disabled_payload["policy"]["participation_allowed_for_intent"] is False
        assert disabled_payload["participation_gate"]["status"] == "disabled"
        assert disabled_payload["participation_gate"]["settings_action_available"] is True
        assert "director_repair_advisory_policy" in disabled_payload["participation_gate"]["missing_scope_ids"]
        assert disabled_payload["participation_gate"]["governed_actions_available"] is False
        disabled_action_ids = {item["action_id"] for item in disabled_payload["suggested_actions"]}
        assert "open_evidence_black_box" in disabled_action_ids
        assert "refresh_evidence_interfaces" in disabled_action_ids
        assert "open_operator_settings" in disabled_action_ids
        assert "request_resident_agi_judgement" not in disabled_action_ids
        assert "request_director_controlled_repair" not in disabled_action_ids

        participation_response = client.patch(
            "/v2/resident/agi/participation",
            json={
                "workspace": str(workspace),
                "enabled": True,
                "scopes": ["quality_gate_response", "director_repair_advisory_policy"],
                "participation": {"director_repair_advisory_policy": True},
            },
        )
        assert participation_response.status_code == 200

        enabled_response = client.post(
            "/v2/resident/agi/chat",
            json={
                "workspace": str(workspace),
                "message": "交给 Director 修复这个阻塞",
                "decision_type": "quality_gate_response",
            },
        )
        assert enabled_response.status_code == 200
        enabled_payload = enabled_response.json()
        assert enabled_payload["policy"]["participation_enabled"] is True
        assert enabled_payload["policy"]["participation_allowed_for_intent"] is True
        assert enabled_payload["participation_gate"]["status"] == "allowed"
        assert enabled_payload["participation_gate"]["settings_action_available"] is False
        assert enabled_payload["participation_gate"]["governed_actions_available"] is True
        assert enabled_payload["participation_gate"]["missing_scope_ids"] == []
        assert "director_repair_advisory_policy" in enabled_payload["facts"]["participation"]["configured_scope_ids"]
        enabled_actions = {item["action_id"]: item for item in enabled_payload["suggested_actions"]}
        enabled_action_ids = set(enabled_actions)
        assert "open_operator_settings" not in enabled_action_ids
        assert "request_resident_agi_judgement" in enabled_action_ids
        assert "request_director_controlled_repair" in enabled_action_ids
        repair_action = enabled_actions["request_director_controlled_repair"]
        assert repair_action["endpoint"] == "/v2/resident/goals"
        assert repair_action["ui_handler"] == "execute_governed_action"
        assert repair_action["risk_level"] == "high"
        assert repair_action["requires_participation"] is True
        assert repair_action["agi_direct_execution_allowed"] is False
        assert repair_action["goal_draft"]["source"] == "resident_agi_tactical_console"
        assert repair_action["goal_draft"]["budget"]["handoff_chain"] == "PM → Chief Engineer → Director → QA"
        assert repair_action["goal_draft"]["budget"]["agi_direct_repair_allowed"] is False


def test_resident_agi_tactical_action_executes_through_goal_and_decision_contracts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    test_token = "test-resident-token"
    monkeypatch.setenv("KERNELONE_TOKEN", test_token)
    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    app = create_app(Settings(workspace=str(workspace), ramdisk_root=""))
    with TestClient(app, headers={"Authorization": f"Bearer {test_token}"}) as client:
        blocked_response = client.post(
            "/v2/resident/agi/actions/execute",
            json={
                "workspace": str(workspace),
                "message": "交给 Director 修复这个阻塞",
                "action_id": "request_director_controlled_repair",
                "decision_type": "quality_gate_response",
                "context_refs": ["runtime/contexts/context-1"],
                "evidence_refs": ["run_ledger.read"],
            },
        )
        assert blocked_response.status_code == 200
        blocked_payload = blocked_response.json()
        assert blocked_payload["status"] == "blocked"
        assert blocked_payload["goal"] is None
        assert blocked_payload["decision"] is None

        participation_response = client.patch(
            "/v2/resident/agi/participation",
            json={
                "workspace": str(workspace),
                "enabled": True,
                "scopes": ["quality_gate_response", "director_repair_advisory_policy"],
                "participation": {"director_repair_advisory_policy": True},
            },
        )
        assert participation_response.status_code == 200

        executed_response = client.post(
            "/v2/resident/agi/actions/execute",
            json={
                "workspace": str(workspace),
                "message": "交给 Director 修复这个阻塞",
                "action_id": "request_director_controlled_repair",
                "decision_type": "quality_gate_response",
                "context_refs": ["runtime/contexts/context-1"],
                "evidence_refs": ["run_ledger.read"],
            },
        )
        assert executed_response.status_code == 200
        payload = executed_response.json()
        assert payload["schema_version"] == "resident.agi_tactical_action_result.v1"
        assert payload["status"] == "executed"
        assert payload["action_spec"]["ui_handler"] == "execute_governed_action"
        assert payload["action_spec"]["capability_id"] == "resident.goal_governance.commands"
        assert payload["goal"]["source"] == "resident_agi_tactical_console"
        assert payload["goal"]["status"] == "pending"
        assert payload["decision"]["actor"] == "resident_agi"
        assert payload["decision"]["stage"] == "tactical_console_action"
        assert payload["decision"]["goal_id"] == payload["goal"]["goal_id"]
        assert payload["decision"]["actual_outcome"]["action_id"] == "request_director_controlled_repair"
        assert payload["receipt"]["status"] == "EXECUTED"
        assert payload["policy"]["agi_direct_repair_allowed"] is False
        assert payload["tool_trace"]["schema_version"] == "resident.agi_tactical_action_tool_trace.v1"
        assert payload["tool_trace"]["summary"]["direct_execution_allowed"] is False
        repair_trace_ids = {item["step_id"] for item in payload["tool_trace"]["items"]}
        assert "resident.goal_governance.commands" in repair_trace_ids
        assert "resident.decision_trace.write" in repair_trace_ids
        follow_up_ids = {item["action_id"] for item in payload["follow_up_actions"]}
        assert "open_goals_tab" in follow_up_ids
        assert "request_resident_agi_judgement" in follow_up_ids


def test_resident_agi_tactical_action_runs_resident_agi_judgement_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    test_token = "test-resident-token"
    monkeypatch.setenv("KERNELONE_TOKEN", test_token)
    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    captured: dict[str, object] = {}

    async def fake_run_resident_agi_decision_turn(command: object) -> dict[str, object]:
        captured["command"] = command
        return {
            "ok": True,
            "decision": {
                "verdict": "request_evidence",
                "rationale": "Need fresh Run Ledger and final request audit before continuing.",
                "evidence_refs": ["run_ledger.read"],
                "risks": [],
                "next_action": "request missing evidence",
                "downstream_allowed": False,
            },
            "recorded_decision": {
                "decision_id": "decision-agi-judgement",
                "actor": "resident_agi",
                "stage": "quality_gate_response",
                "verdict": "request_evidence",
            },
            "role_result": {"success": True, "stage": "resident_agi"},
        }

    monkeypatch.setattr(
        "polaris.cells.resident.autonomy.public.service.run_resident_agi_decision_turn",
        fake_run_resident_agi_decision_turn,
    )

    app = create_app(Settings(workspace=str(workspace), ramdisk_root=""))
    with TestClient(app, headers={"Authorization": f"Bearer {test_token}"}) as client:
        participation_response = client.patch(
            "/v2/resident/agi/participation",
            json={
                "workspace": str(workspace),
                "enabled": True,
                "scopes": ["quality_gate_response"],
                "participation": {"quality_gate_response": True},
            },
        )
        assert participation_response.status_code == 200

        response = client.post(
            "/v2/resident/agi/actions/execute",
            json={
                "workspace": str(workspace),
                "message": "请让 AGI 判断当前质量门禁下一步怎么办",
                "action_id": "request_resident_agi_judgement",
                "decision_type": "quality_gate_response",
                "context_refs": ["runtime/contexts/context-1"],
                "evidence_refs": ["run_ledger.read"],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "resident.agi_tactical_action_result.v1"
    assert payload["status"] == "executed"
    assert payload["goal"] is None
    assert payload["decision"]["decision_id"] == "decision-agi-judgement"
    assert payload["role_result"]["ok"] is True
    assert payload["receipt"]["status"] == "JUDGED"
    assert payload["action_spec"]["contract_ref"] == "resident.autonomy.public.run_resident_agi_decision_turn"
    assert payload["policy"]["role_runtime_required"] is True
    assert payload["policy"]["agi_direct_repair_allowed"] is False
    assert payload["tool_trace"]["schema_version"] == "resident.agi_tactical_action_tool_trace.v1"
    assert payload["tool_trace"]["summary"]["direct_execution_allowed"] is False
    judgement_trace_ids = {item["step_id"] for item in payload["tool_trace"]["items"]}
    assert "resident.agi_decision_turn.execute" in judgement_trace_ids
    assert "resident.decision_trace.write" in judgement_trace_ids
    follow_up_ids = {item["action_id"] for item in payload["follow_up_actions"]}
    assert "refresh_evidence_interfaces" in follow_up_ids
    command = cast(RunResidentAgiDecisionTurnCommandV1, captured["command"])
    assert command.workspace == str(workspace)
    assert command.decision_type == "quality_gate_response"
    assert command.candidate_actions == ("continue", "block", "request_evidence", "escalate")
    assert "preserve_pm_chief_engineer_director_qa_chain" in command.constraints
    assert "run_ledger.read" in command.evidence_refs
    assert command.evidence["selected_action_spec"]["action_id"] == "request_resident_agi_judgement"
    assert command.evidence["selected_action_spec"]["contract_ref"] == (
        "resident.autonomy.public.run_resident_agi_decision_turn"
    )
    assert command.evidence["selected_action_spec"]["agi_direct_execution_allowed"] is False
    assert command.evidence["tactical_action_catalog"]["schema_version"] == ("resident.agi_tactical_action_catalog.v1")
    command_catalog = {item["action_id"]: item for item in command.evidence["tactical_action_catalog"]["items"]}
    assert command_catalog["request_director_controlled_repair"]["execution_boundary"] == (
        "write_through_resident_goal_governance_only"
    )
    command_available_actions = {
        item["action_id"] for item in command.evidence["available_tactical_actions"] if isinstance(item, dict)
    }
    assert "request_resident_agi_judgement" in command_available_actions


def test_resident_agi_repair_advisory_overlay_endpoint_reports_missing(tmp_path: Path, monkeypatch) -> None:
    test_token = "test-resident-token"
    monkeypatch.setenv("KERNELONE_TOKEN", test_token)
    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    app = create_app(Settings(workspace=str(workspace), ramdisk_root=""))
    with TestClient(app, headers={"Authorization": f"Bearer {test_token}"}) as client:
        response = client.get(
            "/v2/resident/agi/repair-advisory-overlay",
            params={
                "workspace": str(workspace),
                "require_ready": "true",
                "require_eligible": "true",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "resident.agi_repair_advisory_overlay_query.v1"
    assert payload["status"] == "missing"
    assert payload["found"] is False
    assert payload["filters"]["require_ready"] is True
    assert payload["filters"]["require_eligible"] is True
    assert payload["advisory_only"] is True
    assert payload["authoritative"] is False


def test_resident_agi_repair_advisory_overlay_endpoint_reads_non_authoritative_trace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    test_token = "test-resident-token"
    monkeypatch.setenv("KERNELONE_TOKEN", test_token)
    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    overlay = {
        "schema_version": "resident.agi_repair_advisory_overlay.v1",
        "source": "resident.autonomy.public.build_resident_agi_repair_advisory_overlay",
        "workspace": str(workspace),
        "status": "ready",
        "active": True,
        "eligible_for_director_injection": True,
        "advisory_only": True,
        "authoritative": False,
        "agi_execution_authority": False,
        "director_runtime_contract": "director.repair_advisory_policy.v1",
        "decision_capability_id": "director.repair_advisory",
        "participation_enabled": True,
        "advisor_notes": [
            {
                "advisor_source": "resident_agi",
                "message": "Suggest a non-authoritative rule family for future review.",
                "confidence": 0.72,
                "suggested_rules": [
                    {
                        "pattern": "error E0432 unresolved import",
                        "fix_template": "map import path through crate module index",
                        "source_tool": "deterministic_rust_import_path_repair",
                    }
                ],
                "metadata": {"source_role": "resident_agi"},
            }
        ],
        "reason": "Resident AGI repair advisory is valid and non-authoritative.",
    }
    record_resident_decision(
        str(workspace),
        {
            "workspace": str(workspace),
            "actor": "resident_agi",
            "stage": "resident_agi_decision",
            "run_id": "run-agi-1",
            "task_id": "task-agi-1",
            "summary": "Resident AGI produced a repair advisory overlay.",
            "actual_outcome": {"resident_agi_repair_advisory_overlay": overlay},
            "verdict": "blocked",
            "confidence": 0.72,
        },
    )

    app = create_app(Settings(workspace=str(workspace), ramdisk_root=""))
    with TestClient(app, headers={"Authorization": f"Bearer {test_token}"}) as client:
        response = client.get(
            "/v2/resident/agi/repair-advisory-overlay",
            params={
                "workspace": str(workspace),
                "require_ready": "true",
                "require_eligible": "true",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "resident.agi_repair_advisory_overlay_query.v1"
    assert payload["source"] == "resident.autonomy.public.query_resident_agi_repair_advisory_overlay"
    assert payload["status"] == "found"
    assert payload["found"] is True
    assert payload["advisory_only"] is True
    assert payload["authoritative"] is False
    assert payload["agi_execution_authority"] is False
    assert payload["director_runtime_contract"] == "director.repair_advisory_policy.v1"
    assert payload["filters"]["require_ready"] is True
    assert payload["filters"]["require_eligible"] is True
    assert payload["matched_overlay_count"] == 1
    assert payload["rejected_by_filter_count"] == 0
    assert payload["decision_ref"]["run_id"] == "run-agi-1"
    assert payload["decision_ref"]["task_id"] == "task-agi-1"
    returned_overlay = payload["overlay"]
    assert returned_overlay["status"] == "ready"
    assert returned_overlay["eligible_for_director_injection"] is True
    assert returned_overlay["advisory_only"] is True
    assert returned_overlay["authoritative"] is False
    assert returned_overlay["agi_execution_authority"] is False
    assert returned_overlay["advisor_notes"][0]["advisor_source"] == "resident_agi"


def test_resident_agi_decide_rejects_disabled_audit_pack_before_adapter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    test_token = "test-resident-token"
    monkeypatch.setenv("KERNELONE_TOKEN", test_token)
    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    def fail_create_role_adapter(role_id: str, workspace_arg: str) -> object:
        raise AssertionError(f"adapter should not be created: {role_id} {workspace_arg}")

    app = create_app(Settings(workspace=str(workspace), ramdisk_root=""))
    with (
        patch(
            "polaris.cells.resident.autonomy.public.service.create_role_adapter",
            side_effect=fail_create_role_adapter,
        ),
        TestClient(app, headers={"Authorization": f"Bearer {test_token}"}) as client,
    ):
        response = client.post(
            "/v2/resident/agi/decide",
            json={
                "workspace": str(workspace),
                "decision_type": "quality_gate_response",
                "objective": "Attempt to bypass Resident AGI audit pack injection.",
                "include_audit_pack": False,
            },
        )

    assert response.status_code == 400
    payload = response.json()
    assert payload["detail"]["code"] == "resident_agi_contract_rejected"
    assert "include_audit_pack" in payload["detail"]["message"]


def test_resident_agi_decide_blocks_before_adapter_when_required_evidence_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    test_token = "test-resident-token"
    monkeypatch.setenv("KERNELONE_TOKEN", test_token)
    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    def fail_create_role_adapter(role_id: str, workspace_arg: str) -> object:
        raise AssertionError(f"adapter should not be created: {role_id} {workspace_arg}")

    app = create_app(Settings(workspace=str(workspace), ramdisk_root=""))
    with (
        patch(
            "polaris.cells.resident.autonomy.public.service.create_role_adapter",
            side_effect=fail_create_role_adapter,
        ),
        TestClient(app, headers={"Authorization": f"Bearer {test_token}"}) as client,
    ):
        response = client.post(
            "/v2/resident/agi/decide",
            json={
                "workspace": str(workspace),
                "decision_type": "quality_gate_response",
                "objective": "Decide whether the current run can proceed without required evidence.",
                "task_id": "task-agi-preflight-missing",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["decision"]["verdict"] == "request_evidence"
    assert payload["decision_preflight"]["schema_version"] == "resident.agi_decision_preflight.v1"
    assert payload["decision_preflight"]["status"] == "block"
    assert payload["decision_preflight"]["passed"] is False
    assert "run_ledger.read" in payload["decision_preflight"]["missing_required_interface_ids"]
    assert payload["runtime_contract_gate"]["status"] == "preflight_blocked"
    assert payload["output_contract_gate"]["status"] == "preflight_blocked"
    assert payload["recorded_decision"]["verdict"] == "blocked"
    assert (
        payload["recorded_decision"]["actual_outcome"]["resident_agi_decision_preflight"]
        == (payload["decision_preflight"])
    )


def test_resident_agi_decide_fails_closed_when_runtime_receipt_is_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    test_token = "test-resident-token"
    monkeypatch.setenv("KERNELONE_TOKEN", test_token)
    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

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
                    "rationale": "This should not be accepted without runtime receipt evidence.",
                    "evidence_refs": [],
                    "risks": [],
                    "next_action": "allow QA",
                    "downstream_allowed": True,
                    "decision_capability_id": "quality.gate.response",
                },
                "metadata": {
                    "runtime_fallback_used": True,
                },
                "tool_calls": [],
                "execution_stats": {"total_tokens": 128},
            }

    def fake_create_role_adapter(role_id: str, workspace_arg: str) -> FakeResidentAgiAdapter:
        return FakeResidentAgiAdapter()

    def fake_evidence_interfaces(query: object) -> dict[str, object]:
        return {
            "schema_version": "resident.agi_evidence_interfaces.v1",
            "workspace": str(workspace),
            "decision_type": "quality_gate_response",
            "run_id": "",
            "task_id": "",
            "selected_decision_capability": {"decision_id": "quality.gate.response"},
            "required_evidence_interfaces": ["run_ledger.read"],
            "optional_evidence_interfaces": [],
            "requested_interface_ids": ["run_ledger.read"],
            "interfaces": [
                {
                    "interface_id": "run_ledger.read",
                    "status": "available",
                    "available": True,
                    "source": "control_plane.run_ledger.public.read_run_ledger_projection",
                    "gaps": [],
                    "recommended_next_action": "use_run_ledger_projection",
                }
            ],
            "summary": {
                "total": 1,
                "available": 1,
                "missing_required_interface_ids": [],
            },
            "audit_pack_ref": {},
        }

    app = create_app(Settings(workspace=str(workspace), ramdisk_root=""))
    with (
        patch(
            "polaris.cells.resident.autonomy.public.service.create_role_adapter",
            side_effect=fake_create_role_adapter,
        ),
        patch(
            "polaris.cells.resident.autonomy.public.service.query_resident_agi_evidence_interfaces",
            side_effect=fake_evidence_interfaces,
        ),
        TestClient(app, headers={"Authorization": f"Bearer {test_token}"}) as client,
    ):
        response = client.post(
            "/v2/resident/agi/decide",
            json={
                "workspace": str(workspace),
                "decision_type": "quality_gate_response",
                "objective": "Decide whether a fake adapter response can bypass RoleRuntime receipts.",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["recorded_decision"]["actor"] == "resident_agi"
    assert payload["recorded_decision"]["verdict"] == "failure"
    gate = payload["recorded_decision"]["actual_outcome"]["resident_agi_runtime_contract_gate"]
    assert gate["schema_version"] == "resident.agi_runtime_contract_gate.v1"
    assert gate["status"] == "fail"
    assert gate["passed"] is False
    assert payload["runtime_contract_gate"] == gate
    assert "metadata.role_runtime_entrypoint" in gate["failed_check_ids"]
    assert "metadata.context_os_expected" in gate["failed_check_ids"]
    assert "metadata.runtime_fallback_used" in gate["failed_check_ids"]
    assert payload["recorded_decision"]["actual_outcome"]["runtime_success"] is False


def test_resident_agi_decide_blocks_before_llm_when_hard_gate_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    test_token = "test-resident-token"
    monkeypatch.setenv("KERNELONE_TOKEN", test_token)
    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    def fail_create_role_adapter(role_id: str, workspace_arg: str) -> object:
        raise AssertionError(f"adapter should not be created: {role_id} {workspace_arg}")

    app = create_app(Settings(workspace=str(workspace), ramdisk_root=""))
    with (
        patch(
            "polaris.cells.resident.autonomy.internal.agi_audit_pack.get_supported_roles",
            return_value=["pm", "director"],
        ),
        patch(
            "polaris.cells.resident.autonomy.public.service.create_role_adapter",
            side_effect=fail_create_role_adapter,
        ),
        TestClient(app, headers={"Authorization": f"Bearer {test_token}"}) as client,
    ):
        response = client.post(
            "/v2/resident/agi/decide",
            json={
                "workspace": str(workspace),
                "decision_type": "platform_supervision",
                "objective": "Decide whether AGI can proceed without the resident adapter.",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["decision"]["verdict"] == "block"
    assert payload["recorded_decision"]["verdict"] == "blocked"
    assert payload["recorded_decision"]["actual_outcome"]["resident_agi_hard_rule_gate"]["status"] == "block"
    assert payload["recorded_decision"]["actual_outcome"]["resident_agi_decision_profile"]["role_turn_allowed"] is False
    assert payload["recorded_decision"]["actual_outcome"]["resident_agi_decision_profile"]["recommended_verdict"] == (
        "block"
    )
    assert payload["recorded_decision"]["actual_outcome"]["resident_agi_runtime_contract_gate"]["status"] == (
        "preflight_blocked"
    )
    assert payload["runtime_contract_gate"]["status"] == "preflight_blocked"
    assert payload["runtime_contract_gate"]["required"] is False
    assert "role_registry.resident_agi_available" in payload["audit_pack"]["hard_rule_gate"]["failed_check_ids"]


def test_resident_api_supports_identity_goals_and_decisions(tmp_path: Path, monkeypatch) -> None:
    test_token = "test-resident-token"
    monkeypatch.setenv("KERNELONE_TOKEN", test_token)
    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    app = create_app(Settings(workspace=str(workspace), ramdisk_root=""))
    with TestClient(app, headers={"Authorization": f"Bearer {test_token}"}) as client:
        identity_response = client.patch(
            "/v2/resident/identity",
            json={
                "workspace": str(workspace),
                "name": "Resident AGI Supervisor",
                "mission": "Govern platform-level autonomous development decisions.",
            },
        )
        assert identity_response.status_code == 200
        assert identity_response.json()["name"] == "Resident AGI Supervisor"

        participation_response = client.patch(
            "/v2/resident/agi/participation",
            json={
                "workspace": str(workspace),
                "enabled": True,
                "scopes": ["final_request_audit", "director.repair.advisory"],
                "participation": {
                    "final_request_audit": True,
                    "director_repair_advisory": True,
                },
                "custom_scopes_allowed": False,
            },
        )
        assert participation_response.status_code == 200
        participation_payload = participation_response.json()
        assert participation_payload["enabled"] is True
        assert participation_payload["scopes"] == [
            "final_request_audit",
            "director.repair.advisory",
        ]
        assert participation_payload["participation"]["final_request_audit"] is True
        assert participation_payload["custom_scopes_allowed"] is False

        participation_patch_response = client.patch(
            "/v2/resident/agi/participation",
            json={
                "workspace": str(workspace),
                "participation": {
                    "final_request_audit": False,
                    "director_repair_advisory": True,
                },
            },
        )
        assert participation_patch_response.status_code == 200
        participation_patch_payload = participation_patch_response.json()
        assert participation_patch_payload["enabled"] is True
        assert participation_patch_payload["scopes"] == [
            "final_request_audit",
            "director.repair.advisory",
        ]
        assert participation_patch_payload["participation"]["final_request_audit"] is False
        assert participation_patch_payload["custom_scopes_allowed"] is False

        participation_get_response = client.get(
            "/v2/resident/agi/participation",
            params={"workspace": str(workspace)},
        )
        assert participation_get_response.status_code == 200
        assert participation_get_response.json() == participation_patch_payload

        goal_response = client.post(
            "/v2/resident/goals",
            json={
                "workspace": str(workspace),
                "goal_type": "maintenance",
                "title": "Audit Resident AGI contract",
                "motivation": "Keep AGI decisions on the shared RoleRuntime foundation.",
                "source": "api-test",
                "scope": ["src/backend/polaris/cells/resident/autonomy"],
                "evidence_refs": ["runtime/contexts/context-1.json"],
            },
        )
        assert goal_response.status_code == 200
        goal_payload = goal_response.json()
        assert goal_payload["status"] == "pending"
        assert goal_payload["title"] == "Audit Resident AGI contract"

        decision_response = client.post(
            "/v2/resident/decisions",
            json={
                "workspace": str(workspace),
                "actor": "resident_agi",
                "stage": "platform_supervision",
                "summary": "Resident AGI requested evidence before execution.",
                "context_refs": ["runtime/contexts/context-1.json"],
                "options": [
                    {
                        "option_id": "request_evidence",
                        "label": "Request evidence",
                        "rationale": "Run Ledger projection is not available.",
                        "estimated_score": 0.8,
                    }
                ],
                "selected_option_id": "request_evidence",
                "expected_outcome": {"objective": "preserve shared role runtime"},
                "actual_outcome": {"decision_source": "resident_agi_role_runtime"},
                "verdict": "success",
                "evidence_refs": ["runtime/contexts/context-1.json"],
                "confidence": 0.8,
            },
        )
        assert decision_response.status_code == 200
        assert decision_response.json()["actor"] == "resident_agi"

        status_response = client.get("/v2/resident/status", params={"workspace": str(workspace), "details": True})
        assert status_response.status_code == 200
        status_payload = status_response.json()
        assert status_payload["identity"]["name"] == "Resident AGI Supervisor"
        assert status_payload["identity"]["resident_agi_participation"]["enabled"] is True
        assert status_payload["identity"]["resident_agi_participation"]["custom_scopes_allowed"] is False
        assert status_payload["counts"]["goals"] == 1
        assert status_payload["counts"]["decisions"] == 1

        decisions_response = client.get(
            "/v2/resident/decisions", params={"workspace": str(workspace), "actor": "resident_agi"}
        )
        assert decisions_response.status_code == 200
        assert decisions_response.json()["count"] == 1


def test_resident_api_tick_reports_evidence_only_boundary(tmp_path: Path, monkeypatch) -> None:
    test_token = "test-resident-token"
    monkeypatch.setenv("KERNELONE_TOKEN", test_token)
    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    app = create_app(Settings(workspace=str(workspace), ramdisk_root=""))
    with TestClient(app, headers={"Authorization": f"Bearer {test_token}"}) as client:
        response = client.post(
            "/v2/resident/tick",
            params={"force": True},
            json={"workspace": str(workspace)},
        )
        assert response.status_code == 200
        boundary = response.json()["runtime"]["last_summary"]["autonomy_boundary"]
        assert boundary["schema_version"] == "resident.tick_autonomy_boundary.v1"
        assert boundary["tick_role"] == "deterministic_evidence_producer"
        assert boundary["goal_proposal_semantics"] == "pending_proposals_only"
        assert boundary["agi_judgement_entrypoint"] == "resident_agi_decision_turn"
        assert boundary["execution_impacting_decision_policy"] == "requires_resident_agi_runtime_contract_gate"
        assert boundary["sidecar_llm_allowed"] is False


def test_resident_api_stages_and_runs_goals_through_pm_bridge(tmp_path: Path, monkeypatch) -> None:
    test_token = "test-resident-token"
    monkeypatch.setenv("KERNELONE_TOKEN", test_token)
    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    app = create_app(Settings(workspace=str(workspace), ramdisk_root=""))
    with TestClient(app, headers={"Authorization": f"Bearer {test_token}"}) as client:
        goal_response = client.post(
            "/v2/resident/goals",
            json={
                "workspace": str(workspace),
                "goal_type": "maintenance",
                "title": "Promote governed resident goal",
                "motivation": "Bridge approved goals into PM runtime.",
                "source": "manual",
                "scope": ["src/backend/app/resident"],
                "evidence_refs": ["docs/resident/resident-api.md"],
            },
        )
        assert goal_response.status_code == 200
        goal_id = goal_response.json()["goal_id"]

        materialize_before_approval = client.post(
            f"/v2/resident/goals/{goal_id}/materialize",
            json={"workspace": str(workspace)},
        )
        assert materialize_before_approval.status_code == 409

        approve_response = client.post(
            f"/v2/resident/goals/{goal_id}/approve",
            json={"workspace": str(workspace), "note": "approved for PM bridge"},
        )
        assert approve_response.status_code == 200

        stage_response = client.post(
            f"/v2/resident/goals/{goal_id}/stage",
            json={"workspace": str(workspace), "promote_to_pm_runtime": True},
        )
        assert stage_response.status_code == 200
        staged_payload = stage_response.json()
        assert staged_payload["promoted_to_pm_runtime"] is True
        assert staged_payload["artifacts"]["pm_contract_path"]

        with patch(
            "polaris.cells.resident.autonomy.internal.resident_runtime_service.OrchestrationCommandService.execute_pm_run",
            new=AsyncMock(
                return_value=CommandResult(
                    run_id="pm-resident-001",
                    status="pending",
                    message="Resident PM run started",
                    started_at="2026-03-07T00:00:00+00:00",
                )
            ),
        ):
            run_response = client.post(
                f"/v2/resident/goals/{goal_id}/run",
                json={
                    "workspace": str(workspace),
                    "run_type": "pm",
                    "run_director": True,
                    "director_iterations": 2,
                },
            )
        assert run_response.status_code == 200
        run_payload = run_response.json()
        assert run_payload["pm_run"]["run_id"] == "pm-resident-001"
        assert run_payload["goal"]["materialization_artifacts"]["pm_run"]["run_id"] == "pm-resident-001"

    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    app = create_app(Settings(workspace=str(workspace), ramdisk_root=""))
    with TestClient(app, headers={"Authorization": f"Bearer {test_token}"}) as client:
        response = client.post("/v2/resident/start", json={"workspace": str(workspace), "mode": "propose"})
        assert response.status_code == 200
        assert response.json()["runtime"]["active"] is True

        identity_response = client.patch(
            "/v2/resident/identity",
            json={
                "workspace": str(workspace),
                "name": "Polaris Resident",
                "mission": "Keep Polaris stable and evidence-driven.",
            },
        )
        assert identity_response.status_code == 200
        assert identity_response.json()["name"] == "Polaris Resident"

        decision_response = client.post(
            "/v2/resident/decisions",
            json={
                "workspace": str(workspace),
                "run_id": "run-api-1",
                "actor": "pm",
                "stage": "contract_validation",
                "summary": "Validated PM contract",
                "strategy_tags": ["contract_validation"],
                "expected_outcome": {"status": "validated", "success": True},
                "actual_outcome": {"status": "validated", "success": True},
                "verdict": "success",
                "evidence_refs": ["runtime/contracts/plan.md"],
                "confidence": 0.85,
            },
        )
        assert decision_response.status_code == 200
        assert decision_response.json()["actor"] == "pm"

        goal_response = client.post(
            "/v2/resident/goals",
            json={
                "workspace": str(workspace),
                "goal_type": "maintenance",
                "title": "Refresh resident docs",
                "motivation": "Keep resident rollout documented.",
                "source": "manual",
                "scope": ["docs/resident"],
                "evidence_refs": ["docs/resident/resident-engineering-rfc.md"],
            },
        )
        assert goal_response.status_code == 200
        goal_id = goal_response.json()["goal_id"]

        approve_response = client.post(
            f"/v2/resident/goals/{goal_id}/approve",
            json={"workspace": str(workspace), "note": "ship it"},
        )
        assert approve_response.status_code == 200
        assert approve_response.json()["status"] == "approved"

        materialize_response = client.post(
            f"/v2/resident/goals/{goal_id}/materialize",
            json={"workspace": str(workspace)},
        )
        assert materialize_response.status_code == 200
        assert materialize_response.json()["focus"] == "resident_goal_materialization"

        summary_response = client.get("/v2/resident/status", params={"workspace": str(workspace)})
        assert summary_response.status_code == 200
        assert summary_response.json()["agi_capability_surface"]["schema_version"] == (
            "resident.agi_capability_surface.v1"
        )

        status_response = client.get("/v2/resident/status", params={"workspace": str(workspace), "details": True})
        assert status_response.status_code == 200
        payload = status_response.json()
        assert payload["identity"]["name"] == "Polaris Resident"
        assert payload["counts"]["decisions"] >= 1
        assert payload["counts"]["goals"] >= 1
        assert payload["agi_capability_surface"]["role_id"] == "resident_agi"

        capabilities_response = client.get("/v2/resident/capabilities", params={"workspace": str(workspace)})
        assert capabilities_response.status_code == 200
        capabilities = capabilities_response.json()
        assert capabilities["schema_version"] == "resident.agi_capability_surface.v1"
        assert capabilities["decision_boundary_schema"] == "resident.agi_decision_boundary.v1"
        assert capabilities["authority_matrix_schema"] == "resident.agi_authority_matrix.v1"
        assert capabilities["runtime_foundation"] == "roles.runtime + ContextOS + TurnEngine"
        assert capabilities["authority_matrix"]["chain"] == "PM → Chief Engineer → Director"
        assert capabilities["authority_matrix"]["decision_policy"]["code_changes"] == "director_authorized_tools_only"
        assert capabilities["decision_boundary_policy"]["schema_version"] == "resident.agi_decision_boundary_policy.v1"
        assert (
            capabilities["decision_boundary_policy"]["decision_modes"]["platform_hard_rule"]["llm_decision_allowed"]
            is False
        )
        assert (
            capabilities["decision_boundary_policy"]["decision_modes"]["agi_recommendation"]["execution_authority"]
            == "advisory_only"
        )
        assert (
            capabilities["decision_boundary_policy"]["capability_execution_policy"]["agi_direct_tool_execution_allowed"]
            is False
        )
        assert (
            capabilities["decision_boundary_policy"]["capability_execution_policy"][
                "director_runtime_remains_authoritative"
            ]
            is True
        )
        assert any(item["capability_id"] == "resident.agi_decision_turn.execute" for item in capabilities["items"])
        assert any(item["capability_id"] == "roles.registry.read" for item in capabilities["items"])
        assert any(item["capability_id"] == "run_ledger.read" for item in capabilities["items"])
        assert any(item["capability_id"] == "director.repair_coverage.read" for item in capabilities["items"])
        assert any(item["capability_id"] == "director.repair_advisory_policy.read" for item in capabilities["items"])
        assert capabilities["director_repair_advisory_policy"]["schema_version"] == (
            "director.repair_advisory_policy.v1"
        )
        assert capabilities["director_repair_advisory_policy"]["writes_allowed"] is False
        assert any(item["boundary_id"] == "role.runtime.foundation" for item in capabilities["decision_boundaries"])
        assert any(item["authority"] == "platform_hard_rule" for item in capabilities["decision_boundaries"])

        audit_pack_response = client.get(
            "/v2/resident/agi/audit-pack",
            params={"workspace": str(workspace), "decision_limit": 5},
        )
        assert audit_pack_response.status_code == 200
        audit_pack = audit_pack_response.json()
        assert audit_pack["schema_version"] == "resident.agi_audit_pack.v1"
        assert audit_pack["role_id"] == "resident_agi"
        assert audit_pack["runtime_foundation"] == "roles.runtime + ContextOS + TurnEngine"
        assert audit_pack["role_registry"]["resident_agi_available"] is True
        assert audit_pack["hard_rule_gate"]["status"] == "pass"
        assert audit_pack["authority_matrix"]["schema_version"] == "resident.agi_authority_matrix.v1"
        assert audit_pack["authority_matrix"]["decision_policy"]["hard_rules"] == "platform_enforced_non_overridable"
        assert audit_pack["run_ledger_summary"]["source"] == "run_ledger_projection"
        assert audit_pack["evidence_gate"]["recommended_verdict"] in {"continue", "request_evidence", "block"}
        assert "resident_agi" in audit_pack["role_registry"]["dialogue_roles"]
        assert "resident_agi" in audit_pack["role_registry"]["adapter_roles"]
        assert "role.runtime.foundation" in audit_pack["boundary_summary"]["boundary_ids"]
        assert "resident.agi_repair_advisory_overlay_query" in audit_pack["truth_sources"]
        assert audit_pack["repair_advisory_overlay_query"]["schema_version"] == (
            "resident.agi_repair_advisory_overlay_query.v1"
        )
        assert audit_pack["repair_advisory_overlay_query"]["status"] == "missing"
        assert audit_pack["repair_advisory_overlay_query"]["advisory_only"] is True
        assert audit_pack["repair_advisory_overlay_query"]["authoritative"] is False
        assert audit_pack["latest_repair_advisory_overlay"] is None
        assert audit_pack["capability_surface"]["schema_version"] == "resident.agi_capability_surface.v1"
        assert audit_pack["director_repair_contract"]["coverage_schema"] == "director.repair_coverage_report.v1"
        assert audit_pack["director_repair_contract"]["advisory_policy_schema"] == "director.repair_advisory_policy.v1"
        assert audit_pack["recent_decisions"]
        assert "PM → Chief Engineer → Director" in " ".join(audit_pack["execution_constraints"])

    reset_resident_services()
