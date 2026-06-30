"""Pure tests for ResidentAgiAdapter."""

from __future__ import annotations

from typing import Any

import pytest
from polaris.cells.roles.adapters.internal.resident_agi_adapter import ResidentAgiAdapter


@pytest.mark.asyncio
async def test_execute_enters_shared_role_runtime(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_invoke_role_runtime_first(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "success": True,
            "response": (
                '{"verdict":"continue","rationale":"evidence is sufficient",'
                '"evidence_refs":["context/audit.json"],"risks":[],"next_action":"run qa",'
                '"downstream_allowed":true}'
            ),
            "metadata": {"role_runtime_entrypoint": "roles.runtime.execute_role_session"},
            "tool_calls": [],
            "execution_stats": {"tokens": 128},
        }

    monkeypatch.setattr(
        "polaris.cells.roles.adapters.internal.resident_agi_adapter.invoke_role_runtime_first",
        fake_invoke_role_runtime_first,
    )

    selected_capability = {
        "decision_id": "quality.gate.response",
        "name": "Quality gate response",
        "owner": "resident_agi",
        "decision_scope": "Choose whether to continue after quality evidence.",
        "risk_level": "high",
        "required_evidence_interfaces": [
            "run_ledger.read",
            "contextos.final_request_audit.read",
            "audit.verdict.read",
        ],
        "optional_evidence_interfaces": [
            "audit.diagnosis.execute",
            "verifier.execution.execute",
        ],
        "candidate_actions": ["block", "request_evidence", "escalate", "continue"],
        "hard_constraints": [
            "contextos_expected",
            "transaction_kernel_expected",
            "failed_quality_gate_cannot_be_marked_passed_by_agi",
        ],
        "output_contract": "resident.agi_decision_turn",
        "contract_refs": ["control_plane.run_ledger", "audit.verdict"],
        "llm_decision_required": True,
        "platform_enforced": False,
    }
    adapter = ResidentAgiAdapter(workspace=str(tmp_path))
    evidence_capability_matrix = {
        "schema_version": "resident.agi_evidence_capability_matrix.v1",
        "decision_type": "quality_gate_response",
        "selected_decision_id": "quality.gate.response",
        "summary": {
            "total": 3,
            "available": 3,
            "required": 3,
            "required_available": 3,
            "missing_required": 0,
            "missing_required_interface_ids": [],
            "recommended_now": 3,
            "callable": 3,
            "high_risk": 1,
            "governed_execute": 0,
            "advisory_only": True,
            "authoritative": False,
            "agi_execution_authority": False,
        },
        "groups": [
            {
                "group_id": "run_ledger",
                "name": "Run ledger",
                "total": 1,
                "available": 1,
                "required": 1,
                "missing_required": 0,
                "recommended_now": 1,
                "governed_execute": 0,
            },
            {
                "group_id": "llm_context",
                "name": "LLM context",
                "total": 1,
                "available": 1,
                "required": 1,
                "missing_required": 0,
                "recommended_now": 1,
                "governed_execute": 0,
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
            }
        ],
    }
    decision_boundary_policy = {
        "schema_version": "resident.agi_decision_boundary_policy.v1",
        "role_id": "resident_agi",
        "runtime_foundation": "roles.runtime + ContextOS + TransactionKernel",
        "chain": "PM → Chief Engineer → Director",
        "decision_modes": {
            "platform_hard_rule": {
                "owner": "platform_code",
                "llm_decision_allowed": False,
                "llm_may_explain_or_request_evidence": True,
                "override_allowed": False,
                "execution_authority": "none",
                "write_authority": False,
                "default_action": "block_or_request_governed_remediation",
            },
            "agi_recommendation": {
                "owner": "resident_agi",
                "llm_decision_allowed": True,
                "llm_may_explain_or_request_evidence": True,
                "override_allowed": False,
                "execution_authority": "advisory_only",
                "write_authority": False,
                "default_action": "recommend_request_evidence_or_escalate",
            },
        },
        "boundary_policies": [
            {
                "boundary_id": "prompt_leakage",
                "authority": "platform_hard_rule",
                "decision_owner": "platform_code",
                "llm_decision_allowed": False,
                "override_allowed": False,
                "execution_authority": "none",
                "write_authority": False,
                "requires_pm_chief_engineer_director_chain": False,
                "advisory_only": False,
                "platform_enforced": True,
                "default_action": "block_or_request_governed_remediation",
                "hard_rule": "never allow prompt leakage",
                "agi_scope": "explain why the hard rule blocked progress",
            },
            {
                "boundary_id": "quality_gate_response",
                "authority": "agi_recommendation",
                "decision_owner": "resident_agi",
                "llm_decision_allowed": True,
                "override_allowed": False,
                "execution_authority": "advisory_only",
                "write_authority": False,
                "requires_pm_chief_engineer_director_chain": False,
                "advisory_only": True,
                "platform_enforced": False,
                "default_action": "recommend_request_evidence_or_escalate",
                "hard_rule": "",
                "agi_scope": "judge whether quality evidence is enough to continue",
            },
        ],
        "capability_execution_policy": {
            "agi_direct_writes_allowed": False,
            "agi_direct_tool_execution_allowed": False,
            "director_runtime_remains_authoritative": True,
            "pm_chief_engineer_director_chain_required": True,
            "governed_request_capabilities": ["audit.diagnosis.execute"],
            "write_contract_capabilities": [],
            "high_risk_capabilities": ["contextos.final_request_audit.read"],
            "advisory_evidence_capabilities": ["director.repair.strategy_catalog.read"],
        },
        "non_overridable_rules": ["prompt_leakage"],
        "agi_judgement_boundaries": ["quality_gate_response"],
        "governed_execution_boundaries": [],
        "counts": {
            "boundary_policies": 2,
            "platform_hard_rules": 1,
            "agi_judgement": 1,
            "governed_execution": 0,
            "read_only_capabilities": 3,
            "governed_request_capabilities": 1,
            "write_contract_capabilities": 0,
            "high_risk_capabilities": 1,
        },
    }
    result = await adapter.execute(
        "task-1",
        {
            "decision_type": "quality_gate_response",
            "objective": "Decide whether the current run can continue.",
            "evidence": {"context_snapshot_ref": "runtime/contexts/abc.json"},
            "resident_agi_audit_pack": {
                "schema_version": "resident.agi_audit_pack.v1",
                "role_id": "resident_agi",
                "truth_sources": ["resident.status", "roles.registry"],
                "role_registry": {"resident_agi_available": True},
                "hard_rule_gate": {"schema_version": "gate.v1", "status": "pass", "passed": True},
                "evidence_gate": {
                    "schema_version": "evidence_gate.v1",
                    "status": "pass",
                    "recommended_verdict": "continue",
                },
                "decision_profile": {
                    "schema_version": "decision_profile.v1",
                    "recommended_verdict": "continue",
                    "recommended_next_action": "run qa",
                    "role_turn_allowed": True,
                    "downstream_precheck": "ready",
                },
                "capability_surface": {
                    "decision_capability_registry": {
                        "schema_version": "resident.agi_decision_capability_registry.v1",
                        "role_id": "resident_agi",
                        "runtime_foundation": "roles.runtime + ContextOS + TransactionKernel",
                        "counts": {"decisions": 2},
                    },
                    "decision_capabilities": [
                        selected_capability,
                        {
                            "decision_id": "architecture.option.selection",
                            "name": "Architecture option selection",
                        },
                    ],
                },
            },
            "selected_decision_capability": selected_capability,
            "resident_agi_evidence_capability_matrix": evidence_capability_matrix,
            "resident_agi_decision_boundary_policy": decision_boundary_policy,
            "required_evidence_interfaces": [
                "run_ledger.read",
                "contextos.final_request_audit.read",
                "audit.verdict.read",
            ],
            "optional_evidence_interfaces": ["audit.diagnosis.execute"],
            "constraints": ["fail closed on missing evidence"],
            "candidate_actions": ["continue", "request_evidence"],
        },
        {"run_id": "run-1", "metadata": {"source": "test"}},
    )

    assert result["success"] is True
    assert result["stage"] == "resident_agi"
    assert result["decision"]["verdict"] == "continue"
    assert captured["workspace"] == str(tmp_path)
    assert captured["role"] == "resident_agi"
    assert captured["domain"] == "resident_agi_decision"
    assert captured["validate_output"] is False
    assert captured["max_retries"] == 1
    assert "Do not bypass PM -> Chief Engineer -> Director -> QA" in captured["message"]
    assert "resident_agi_decision_contract" in captured["message"]
    assert "quality.gate.response" in captured["message"]
    assert "run_ledger.read" in captured["message"]
    assert "resident.agi_evidence_capability_matrix.v1" in captured["message"]
    assert "required_available" in captured["message"]
    assert "agi_execution_authority" in captured["message"]
    assert "resident.agi_decision_boundary_policy.v1" in captured["message"]
    assert "platform_hard_rule" in captured["message"]
    assert "advisory_only" in captured["message"]
    assert '"agi_direct_tool_execution_allowed": false' in captured["message"]
    assert "resident.agi_audit_pack.v1" in captured["message"]
    assert "architecture.option.selection" not in captured["message"]
    assert '"resident_agi_audit_pack"' not in captured["message"]
    runtime_context = captured["context"]
    assert runtime_context["run_id"] == "run-1"
    assert runtime_context["decision_type"] == "quality_gate_response"
    assert runtime_context["resident_agi_audit_pack"]["schema_version"] == "resident.agi_audit_pack.v1"
    assert runtime_context["resident_agi_evidence_capability_matrix"]["schema_version"] == (
        "resident.agi_evidence_capability_matrix.v1"
    )
    assert runtime_context["resident_agi_decision_boundary_policy"]["schema_version"] == (
        "resident.agi_decision_boundary_policy.v1"
    )
    assert runtime_context["resident_agi_decision_contract"]["schema_version"] == "resident.agi_decision_contract.v1"
    assert runtime_context["resident_agi_decision_contract"]["decision_capability_id"] == "quality.gate.response"
    assert (
        runtime_context["resident_agi_decision_contract"]["evidence_capability_matrix"]["summary"]["required_available"]
        == 3
    )
    assert (
        runtime_context["resident_agi_decision_contract"]["evidence_capability_matrix"]["summary"][
            "agi_execution_authority"
        ]
        is False
    )
    assert (
        runtime_context["resident_agi_decision_contract"]["evidence_capability_matrix"]["groups"][0]["group_id"]
        == "run_ledger"
    )
    assert (
        runtime_context["resident_agi_decision_contract"]["decision_boundary_policy"]["decision_modes"][
            "platform_hard_rule"
        ]["llm_decision_allowed"]
        is False
    )
    assert (
        runtime_context["resident_agi_decision_contract"]["decision_boundary_policy"]["capability_execution_policy"][
            "agi_direct_writes_allowed"
        ]
        is False
    )
    assert (
        runtime_context["resident_agi_decision_contract"]["decision_boundary_policy"]["boundary_policies"][0][
            "boundary_id"
        ]
        == "prompt_leakage"
    )
    assert runtime_context["selected_decision_capability"]["decision_id"] == "quality.gate.response"
    assert runtime_context["required_evidence_interfaces"] == [
        "run_ledger.read",
        "contextos.final_request_audit.read",
        "audit.verdict.read",
    ]
    assert runtime_context["metadata"]["resident_agi_role_runtime_required"] is True
    assert runtime_context["metadata"]["resident_agi_contextos_required"] is True
    assert runtime_context["metadata"]["resident_agi_turn_engine_required"] is True
    assert runtime_context["metadata"]["resident_agi_decision_contract_schema"] == "resident.agi_decision_contract.v1"


@pytest.mark.asyncio
async def test_repair_advisory_contract_allows_non_authoritative_suggested_rules(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_invoke_role_runtime_first(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "success": True,
            "response": (
                '{"verdict":"continue","rationale":"compile evidence supports a reusable advisory",'
                '"evidence_refs":["runtime/compile.log"],"risks":["advisory must stay non-authoritative"],'
                '"next_action":"suggest_repair_rule","downstream_allowed":true,'
                '"decision_capability_id":"director.repair.advisory",'
                '"suggested_rules":[{"name":"rust_borrow_marker_self","language":"rust",'
                '"pattern":"expected &self in method receiver","fix_template":"replace (&) with (&self)",'
                '"confidence":0.82,"evidence":["error: expected self parameter"]}]}'
            ),
            "metadata": {},
            "tool_calls": [],
            "execution_stats": {},
        }

    monkeypatch.setattr(
        "polaris.cells.roles.adapters.internal.resident_agi_adapter.invoke_role_runtime_first",
        fake_invoke_role_runtime_first,
    )

    selected_capability = {
        "decision_id": "director.repair.advisory",
        "name": "Director repair advisory",
        "owner": "resident_agi",
        "decision_scope": "Suggest non-authoritative repair rules from diagnostics.",
        "risk_level": "high",
        "required_evidence_interfaces": [
            "director.repair.strategy_catalog.read",
            "director.repair.advisory_policy.read",
            "runtime.repair_receipts.read",
        ],
        "optional_evidence_interfaces": ["director.repair.coverage.read"],
        "candidate_actions": ["continue", "request_evidence", "suggest_repair_rule"],
        "hard_constraints": [
            "advisory_only",
            "director_runtime_remains_authoritative",
            "no_write_file",
            "no_policy_override",
        ],
        "output_contract": "resident.agi_repair_advisory_overlay.v1",
        "llm_decision_required": True,
        "platform_enforced": False,
    }
    adapter = ResidentAgiAdapter(workspace=str(tmp_path))

    result = await adapter.execute(
        "task-repair",
        {
            "decision_type": "repair_rule_suggestion",
            "objective": "Inspect compile errors and suggest reusable non-authoritative repair rules.",
            "evidence": {
                "diagnostics": [
                    {
                        "source": "cargo",
                        "message": "error: expected self parameter",
                        "raw": "expected `self`, found `&`",
                    }
                ]
            },
            "selected_decision_capability": selected_capability,
        },
        {"run_id": "run-repair"},
    )

    assert result["success"] is True
    assert result["decision"]["suggested_rules"][0]["pattern"] == "expected &self in method receiver"

    runtime_contract = captured["context"]["resident_agi_decision_contract"]
    output_protocol = runtime_contract["decision_output_protocol"]
    assert output_protocol["decision_mode"] == "director_repair_advisory"
    assert output_protocol["suggested_rules_allowed"] is True
    assert output_protocol["authoritative_fields_allowed"] is False
    assert output_protocol["director_runtime_policy"]["schema_version"] == "director.repair_advisory_policy.v1"
    assert "pattern" in output_protocol["allowed_suggested_rule_fields"]
    assert "fix_template" in output_protocol["allowed_suggested_rule_fields"]
    assert "write_file" in output_protocol["forbidden_suggested_rule_fields"]
    assert "policy_override" in output_protocol["forbidden_suggested_rule_fields"]
    assert "repair_plan" in output_protocol["forbidden_metadata_fields"]

    message = captured["message"]
    assert '"suggested_rules_allowed": true' in message
    assert '"suggested_rules"' in message
    assert '"pattern": "diagnostic pattern that motivated the proposed rule"' in message
    assert "Director Runtime remains the only owner of repair execution and receipts" in message
    assert "Do not include repair_plan, policy_override, success_verdict, patches, write_file" in message


@pytest.mark.asyncio
async def test_execute_requires_objective(tmp_path: Any) -> None:
    adapter = ResidentAgiAdapter(workspace=str(tmp_path))

    result = await adapter.execute("task-1", {"decision_type": "platform_supervision"}, {})

    assert result["success"] is False
    assert result["stage"] == "resident_agi"
    assert "objective" in result["error"]
