from __future__ import annotations

import inspect
from typing import Any

import pytest
from polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.activities.base import get_registered_activity
from polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.activities.pm_activities import (
    run_chief_engineer_blueprint,
)
from polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.workflows.pm_workflow import PMWorkflow


@pytest.mark.asyncio
async def test_run_chief_engineer_blueprint_enriches_director_tasks(monkeypatch, tmp_path) -> None:
    captured: dict[str, Any] = {}

    class _FakeCognitiveRuntimeService:
        def resolve_context(self, command: Any) -> Any:
            captured["resolve_command"] = command
            snapshot = type(
                "Snapshot",
                (),
                {
                    "workspace": str(tmp_path),
                    "role": "chief_engineer",
                    "run_id": "pm-00001",
                    "session_id": "ce-session-1",
                    "mode": "workflow_runtime_chief_engineer_blueprint",
                    "token_usage_estimate": 42,
                    "source_refs": ("runtime/contracts/plan.md",),
                    "context_os_summary": {"task_count": 1},
                },
            )()
            return type("Result", (), {"ok": True, "snapshot": snapshot})()

        def record_runtime_receipt(self, command: Any) -> Any:
            captured["receipt_command"] = command
            return type("Result", (), {"ok": True, "receipt": type("Receipt", (), {"receipt_id": "receipt-ce-1"})()})()

        def close(self) -> None:
            captured["closed"] = True

    def fake_analysis(**kwargs: Any) -> dict[str, Any]:
        return {
            "ran": True,
            "hard_failure": False,
            "summary": "ChiefEngineer generated construction blueprint.",
            "task_update_count": 1,
            "blueprint_path": kwargs["run_blueprint_path"],
            "runtime_blueprint_path": kwargs["runtime_blueprint_path"],
            "task_update_map": {
                "TASK-1": {
                    "task_id": "TASK-1",
                    "scope_for_apply": ["src/app.ts", "src/routes.ts", "src/schema.ts"],
                    "missing_targets": ["src/routes.ts"],
                    "blueprint_scope": {"module": "app"},
                    "construction_plan": {
                        "file_plans": [{"path": "src/app.ts", "method_names": ["registerRoutes"]}],
                        "method_catalog": ["registerRoutes"],
                    },
                    "constraints": ["Keep API routes idempotent."],
                }
            },
        }

    monkeypatch.setattr(
        "polaris.delivery.cli.pm.chief_engineer.run_chief_engineer_analysis",
        fake_analysis,
    )
    monkeypatch.setattr(
        "polaris.cells.factory.cognitive_runtime.public.get_cognitive_runtime_public_service",
        lambda: _FakeCognitiveRuntimeService(),
    )

    result = await run_chief_engineer_blueprint(
        {
            "workspace": str(tmp_path),
            "run_id": "pm-00001",
            "tasks": [
                {
                    "id": "TASK-1",
                    "title": "Wire API",
                    "target_files": ["src/app.ts"],
                    "scope_paths": ["src/app.ts"],
                    "constraints": ["Use existing style."],
                }
            ],
            "metadata": {
                "run_dir": str(tmp_path / "run"),
                "cache_root_full": str(tmp_path / ".polaris"),
                "pm_iteration": 1,
                "chief_engineer_session_id": "ce-session-1",
                "cognitive_runtime_required": True,
                "context_os_expected": True,
            },
        }
    )

    assert result["success"] is True
    assert get_registered_activity("run_chief_engineer_blueprint") is run_chief_engineer_blueprint
    payload = result["payload"]
    [task] = payload["tasks"]
    assert task["construction_plan"]["method_catalog"] == ["registerRoutes"]
    assert task["chief_engineer"]["scope_for_apply"] == ["src/app.ts", "src/routes.ts", "src/schema.ts"]
    assert task["target_files"] == ["src/app.ts", "src/routes.ts", "src/schema.ts"]
    assert task["constraints"] == ["Use existing style.", "Keep API routes idempotent."]
    receipt = payload["cognitive_runtime_receipt"]
    assert receipt["ok"] is True
    assert receipt["receipt_id"] == "receipt-ce-1"
    assert task["chief_engineer"]["cognitive_runtime_receipt"] == receipt
    receipt_command = captured["receipt_command"]
    resolve_command = captured["resolve_command"]
    assert resolve_command.role == "chief_engineer"
    assert resolve_command.session_id == "ce-session-1"
    assert resolve_command.mode == "workflow_runtime_chief_engineer_blueprint"
    assert resolve_command.sources_enabled == ("runtime", "events", "contracts")
    assert receipt_command.receipt_type == "chief_engineer_blueprint"
    assert receipt_command.session_id == "ce-session-1"
    assert receipt_command.run_id == "pm-00001"
    assert receipt_command.payload["role"] == "chief_engineer"
    assert receipt_command.payload["context_os_expected"] is True
    assert receipt_command.payload["context_os"]["ok"] is True
    assert receipt_command.payload["context_os"]["snapshot"]["mode"] == "workflow_runtime_chief_engineer_blueprint"
    assert receipt_command.payload["context_os"]["snapshot"]["context_os_summary"] == {"task_count": 1}
    assert receipt_command.payload["task_ids"] == ["TASK-1"]
    assert captured["closed"] is True


@pytest.mark.asyncio
async def test_run_chief_engineer_blueprint_fails_closed_when_context_os_fails(
    monkeypatch,
    tmp_path,
) -> None:
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

    def fake_analysis(**kwargs: Any) -> dict[str, Any]:
        return {
            "ran": True,
            "hard_failure": False,
            "summary": "ChiefEngineer generated construction blueprint.",
            "task_update_count": 0,
            "blueprint_path": kwargs["run_blueprint_path"],
            "runtime_blueprint_path": kwargs["runtime_blueprint_path"],
            "task_update_map": {},
        }

    monkeypatch.setattr(
        "polaris.delivery.cli.pm.chief_engineer.run_chief_engineer_analysis",
        fake_analysis,
    )
    monkeypatch.setattr(
        "polaris.cells.factory.cognitive_runtime.public.get_cognitive_runtime_public_service",
        lambda: _FakeCognitiveRuntimeService(),
    )

    result = await run_chief_engineer_blueprint(
        {
            "workspace": str(tmp_path),
            "run_id": "pm-context-fail",
            "tasks": [
                {
                    "id": "TASK-CONTEXT-FAIL",
                    "title": "Require Context OS",
                    "scope_paths": ["src/app.ts"],
                    "acceptance_criteria": ["Context OS must be resolved"],
                }
            ],
            "metadata": {
                "chief_engineer_session_id": "ce-session-fail",
                "cognitive_runtime_required": True,
                "context_os_expected": True,
            },
        }
    )

    assert result["success"] is False
    assert result["error_code"] == "chief_engineer_cognitive_runtime_receipt_failed"
    assert "chief_engineer_context_os_resolve_failed" in result["summary"]
    assert captured["record_called"] is False
    assert captured["closed"] is True


def test_pm_workflow_runs_chief_engineer_before_director_dispatch() -> None:
    source = inspect.getsource(PMWorkflow.run)
    ce_index = source.index('"run_chief_engineer_blueprint"')
    director_index = source.index("DirectorWorkflow.run")
    assert ce_index < director_index
    assert "chief_engineer" in source
    assert '"cognitive_runtime_required": True' in source
    assert '"context_os_expected": True' in source
