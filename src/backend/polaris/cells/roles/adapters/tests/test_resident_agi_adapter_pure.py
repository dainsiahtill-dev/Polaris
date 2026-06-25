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
            "turn_engine_expected",
            "failed_quality_gate_cannot_be_marked_passed_by_agi",
        ],
        "output_contract": "resident.agi_decision_turn",
        "contract_refs": ["control_plane.run_ledger", "audit.verdict"],
        "llm_decision_required": True,
        "platform_enforced": False,
    }
    adapter = ResidentAgiAdapter(workspace=str(tmp_path))
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
                        "runtime_foundation": "roles.runtime + ContextOS + TurnEngine",
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
    assert "resident.agi_audit_pack.v1" in captured["message"]
    assert "architecture.option.selection" not in captured["message"]
    assert '"resident_agi_audit_pack"' not in captured["message"]
    runtime_context = captured["context"]
    assert runtime_context["run_id"] == "run-1"
    assert runtime_context["decision_type"] == "quality_gate_response"
    assert runtime_context["resident_agi_audit_pack"]["schema_version"] == "resident.agi_audit_pack.v1"
    assert runtime_context["resident_agi_decision_contract"]["schema_version"] == "resident.agi_decision_contract.v1"
    assert runtime_context["resident_agi_decision_contract"]["decision_capability_id"] == "quality.gate.response"
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
async def test_execute_requires_objective(tmp_path: Any) -> None:
    adapter = ResidentAgiAdapter(workspace=str(tmp_path))

    result = await adapter.execute("task-1", {"decision_type": "platform_supervision"}, {})

    assert result["success"] is False
    assert result["stage"] == "resident_agi"
    assert "objective" in result["error"]
