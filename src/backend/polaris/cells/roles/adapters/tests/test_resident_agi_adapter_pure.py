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
            },
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
    assert "resident.agi_audit_pack.v1" in captured["message"]
    runtime_context = captured["context"]
    assert runtime_context["run_id"] == "run-1"
    assert runtime_context["decision_type"] == "quality_gate_response"
    assert runtime_context["resident_agi_audit_pack"]["schema_version"] == "resident.agi_audit_pack.v1"
    assert runtime_context["metadata"]["resident_agi_role_runtime_required"] is True
    assert runtime_context["metadata"]["resident_agi_contextos_required"] is True
    assert runtime_context["metadata"]["resident_agi_turn_engine_required"] is True


@pytest.mark.asyncio
async def test_execute_requires_objective(tmp_path: Any) -> None:
    adapter = ResidentAgiAdapter(workspace=str(tmp_path))

    result = await adapter.execute("task-1", {"decision_type": "platform_supervision"}, {})

    assert result["success"] is False
    assert result["stage"] == "resident_agi"
    assert "objective" in result["error"]
