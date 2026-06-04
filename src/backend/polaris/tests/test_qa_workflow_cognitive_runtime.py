from __future__ import annotations

import inspect
from typing import Any

import pytest
from polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.activities.base import get_registered_activity
from polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.activities.qa_activities import (
    record_qa_cognitive_receipt,
)
from polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.workflows.pm_workflow import PMWorkflow
from polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.workflows.qa_workflow import QAWorkflow


@pytest.mark.asyncio
async def test_record_qa_cognitive_receipt_records_required_runtime_evidence(monkeypatch, tmp_path) -> None:
    captured: dict[str, Any] = {}

    class _FakeCognitiveRuntimeService:
        def resolve_context(self, command: Any) -> Any:
            captured["resolve_command"] = command
            snapshot = type(
                "Snapshot",
                (),
                {
                    "workspace": str(tmp_path),
                    "role": "qa",
                    "run_id": "pm-00007",
                    "session_id": "qa-session-1",
                    "mode": "workflow_runtime_qa_verification",
                    "token_usage_estimate": 31,
                    "source_refs": (
                        "runtime/results/unit_qa.result.json",
                        "runtime/results/integration_qa.result.json",
                    ),
                    "context_os_summary": {"qa_gate": "final"},
                },
            )()
            return type("Result", (), {"ok": True, "snapshot": snapshot})()

        def record_runtime_receipt(self, command: Any) -> Any:
            captured["receipt_command"] = command
            return type("Result", (), {"ok": True, "receipt": type("Receipt", (), {"receipt_id": "receipt-qa-1"})()})()

        def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(
        "polaris.cells.factory.cognitive_runtime.public.get_cognitive_runtime_public_service",
        lambda: _FakeCognitiveRuntimeService(),
    )

    result = await record_qa_cognitive_receipt(
        {
            "workspace": str(tmp_path),
            "run_id": "pm-00007",
            "status": "completed",
            "reason": "qa_passed",
            "summary": "QA workflow completed",
            "unit": {"result_path": "runtime/results/unit_qa.result.json", "command": "pytest -q"},
            "integration": {"result_path": "runtime/results/integration_qa.result.json"},
            "evidence_refs": [
                "runtime/results/unit_qa.result.json",
                "runtime/results/integration_qa.result.json",
            ],
            "metadata": {
                "qa_session_id": "qa-session-1",
                "cognitive_runtime_required": True,
                "context_os_expected": True,
            },
        }
    )

    assert result["success"] is True
    assert get_registered_activity("record_qa_cognitive_receipt") is record_qa_cognitive_receipt
    receipt = result["payload"]["cognitive_runtime_receipt"]
    assert receipt["ok"] is True
    assert receipt["receipt_id"] == "receipt-qa-1"
    receipt_command = captured["receipt_command"]
    resolve_command = captured["resolve_command"]
    assert resolve_command.role == "qa"
    assert resolve_command.session_id == "qa-session-1"
    assert resolve_command.mode == "workflow_runtime_qa_verification"
    assert resolve_command.sources_enabled == ("runtime", "events", "contracts")
    assert receipt_command.receipt_type == "qa_verification"
    assert receipt_command.session_id == "qa-session-1"
    assert receipt_command.run_id == "pm-00007"
    assert receipt_command.trace_refs == (
        "runtime/results/unit_qa.result.json",
        "runtime/results/integration_qa.result.json",
    )
    assert receipt_command.payload["role"] == "qa"
    assert receipt_command.payload["context_os_expected"] is True
    assert receipt_command.payload["context_os"]["ok"] is True
    assert receipt_command.payload["context_os"]["snapshot"]["mode"] == "workflow_runtime_qa_verification"
    assert receipt_command.payload["context_os"]["snapshot"]["context_os_summary"] == {"qa_gate": "final"}
    assert receipt_command.payload["unit"]["command"] == "pytest -q"
    assert receipt_command.turn_envelope["task_id"] == "qa::verification"
    assert captured["closed"] is True


@pytest.mark.asyncio
async def test_record_qa_cognitive_receipt_fails_closed_when_context_os_fails(monkeypatch, tmp_path) -> None:
    captured: dict[str, Any] = {"record_called": False}

    class _FakeCognitiveRuntimeService:
        def resolve_context(self, command: Any) -> Any:
            captured["resolve_command"] = command
            return type(
                "Result",
                (),
                {
                    "ok": False,
                    "error_code": "context_unavailable",
                    "error_message": "Context OS offline",
                },
            )()

        def record_runtime_receipt(self, command: Any) -> Any:
            captured["record_called"] = True
            return type("Result", (), {"ok": True, "receipt": type("Receipt", (), {"receipt_id": "bad"})()})()

        def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(
        "polaris.cells.factory.cognitive_runtime.public.get_cognitive_runtime_public_service",
        lambda: _FakeCognitiveRuntimeService(),
    )

    result = await record_qa_cognitive_receipt(
        {
            "workspace": str(tmp_path),
            "run_id": "pm-qa-context-fail",
            "status": "failed",
            "reason": "qa_failed",
            "summary": "QA workflow failed",
            "metadata": {
                "qa_session_id": "qa-session-fail",
                "cognitive_runtime_required": True,
                "context_os_expected": True,
            },
        }
    )

    assert result["success"] is False
    assert result["error_code"] == "qa_context_os_resolve_failed"
    assert result["payload"]["cognitive_runtime_receipt"]["context_os"]["error_message"] == "Context OS offline"
    assert captured["record_called"] is False
    assert captured["closed"] is True


def test_pm_and_qa_workflows_require_cognitive_runtime_for_final_qa_gate() -> None:
    qa_source = inspect.getsource(QAWorkflow.run)
    assert '"record_qa_cognitive_receipt"' in qa_source
    assert "receipt_success" in qa_source
    assert '"cognitive_runtime"' in qa_source

    pm_source = inspect.getsource(PMWorkflow.run)
    qa_call_index = pm_source.index("QAWorkflow.run")
    assert '"qa_session_id": f"qa-{workflow_input.run_id}"' in pm_source[qa_call_index:]
    assert '"cognitive_runtime_required": True' in pm_source[qa_call_index:]
    assert '"context_os_expected": True' in pm_source[qa_call_index:]
