from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from polaris.bootstrap.config import Settings
from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import CommandResult
from polaris.cells.resident.autonomy.internal.resident_runtime_service import reset_resident_services
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

    app = create_app(Settings(workspace=str(workspace), ramdisk_root=""))
    with (
        patch(
            "polaris.cells.resident.autonomy.public.service.create_role_adapter",
            side_effect=fake_create_role_adapter,
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
    assert payload["decision"]["verdict"] == "request_evidence"
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
    assert payload["audit_pack"]["hard_rule_gate"]["status"] == "pass"
    assert payload["audit_pack"]["authority_matrix"]["schema_version"] == "resident.agi_authority_matrix.v1"
    assert payload["audit_pack"]["authority_matrix"]["chain_required"] is True
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
    assert "run_ledger.read" in payload["required_evidence_interfaces"]
    assert payload["recorded_decision"]["actual_outcome"]["resident_agi_evidence_gate"]["status"] == "hold"
    assert payload["recorded_decision"]["actual_outcome"]["resident_agi_decision_profile"]["schema_version"] == (
        "resident.agi_decision_profile.v1"
    )
    assert payload["recorded_decision"]["actual_outcome"]["resident_agi_runtime_contract_gate"]["status"] == "pass"
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
    assert captured_input["selected_decision_capability"]["decision_id"] == "quality.gate.response"
    assert "run_ledger.read" in captured_input["required_evidence_interfaces"]
    assert "audit.diagnosis.execute" in captured_input["optional_evidence_interfaces"]
    assert "preserve_pm_chief_engineer_director_qa_chain" in captured_input["constraints"]
    assert "request_evidence" in captured_input["candidate_actions"]
    captured_context = captured["context"]
    assert isinstance(captured_context, dict)
    assert captured_context["resident_agi_audit_pack"]["schema_version"] == "resident.agi_audit_pack.v1"
    captured_metadata = captured_context["metadata"]
    assert isinstance(captured_metadata, dict)
    assert captured_metadata["resident_agi_role_runtime_required"] is True
    assert captured_metadata["resident_agi_audit_pack_injected"] is True
    assert captured_metadata["resident_agi_hard_rule_gate_status"] == "pass"
    assert captured_metadata["resident_agi_authority_matrix_schema"] == "resident.agi_authority_matrix.v1"
    assert captured_metadata["resident_agi_decision_profile_schema"] == "resident.agi_decision_profile.v1"
    assert captured_metadata["resident_agi_role_turn_allowed"] is True
    assert captured_metadata["resident_agi_selected_decision_capability"] == "quality.gate.response"
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

    app = create_app(Settings(workspace=str(workspace), ramdisk_root=""))
    with (
        patch(
            "polaris.cells.resident.autonomy.public.service.create_role_adapter",
            side_effect=fake_create_role_adapter,
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
        assert any(item["capability_id"] == "resident.agi_decision_turn.execute" for item in capabilities["items"])
        assert any(item["capability_id"] == "roles.registry.read" for item in capabilities["items"])
        assert any(item["capability_id"] == "run_ledger.read" for item in capabilities["items"])
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
        assert audit_pack["capability_surface"]["schema_version"] == "resident.agi_capability_surface.v1"
        assert audit_pack["recent_decisions"]
        assert "PM → Chief Engineer → Director" in " ".join(audit_pack["execution_constraints"])

    reset_resident_services()
