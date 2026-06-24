from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_ROOT = os.path.join(BACKEND_ROOT, "scripts")
CORE_ROOT = os.path.join(BACKEND_ROOT, "core", "polaris_loop")
for candidate in (BACKEND_ROOT, SCRIPTS_ROOT, CORE_ROOT):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from polaris.cells.control_plane.run_ledger.public import (  # noqa: E402
    ReadRunLedgerProjectionQueryV1,
    read_run_ledger_projection,
)
from polaris.cells.orchestration.pm_dispatch.internal import dispatch_pipeline  # noqa: E402
from polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline import (  # noqa: E402
    run_post_dispatch_integration_qa,
)
from polaris.kernelone.storage import resolve_logical_path  # noqa: E402


class _SuccessfulCognitiveRuntimeService:
    def __init__(self, captured: dict[str, Any]) -> None:
        self._captured = captured

    def resolve_context(self, command: Any) -> Any:
        self._captured["context_command"] = command
        snapshot = type(
            "Snapshot",
            (),
            {
                "workspace": command.workspace,
                "role": command.role,
                "run_id": command.run_id,
                "session_id": command.session_id,
                "mode": command.mode,
                "token_usage_estimate": 128,
                "source_refs": ("runtime/results/integration_qa.result.json",),
                "context_os_summary": {"state_first_context_os": True, "projection": "qa"},
            },
        )()
        return type("ContextResult", (), {"ok": True, "snapshot": snapshot})()

    def record_runtime_receipt(self, command: Any) -> Any:
        self._captured["receipt_command"] = command
        return type(
            "Result", (), {"ok": True, "receipt": type("Receipt", (), {"receipt_id": "receipt-dispatch-qa"})()}
        )()

    def close(self) -> None:
        self._captured["closed"] = True


class _FailingCognitiveRuntimeService:
    def __init__(self, captured: dict[str, Any]) -> None:
        self._captured = captured

    def resolve_context(self, command: Any) -> Any:
        self._captured["context_command"] = command
        snapshot = type(
            "Snapshot",
            (),
            {
                "workspace": command.workspace,
                "role": command.role,
                "run_id": command.run_id,
                "session_id": command.session_id,
                "mode": command.mode,
                "token_usage_estimate": 64,
                "source_refs": ("runtime/results/integration_qa.result.json",),
                "context_os_summary": {"state_first_context_os": True, "projection": "qa"},
            },
        )()
        return type("ContextResult", (), {"ok": True, "snapshot": snapshot})()

    def record_runtime_receipt(self, command: Any) -> Any:
        self._captured["receipt_command"] = command
        return type(
            "Result",
            (),
            {
                "ok": False,
                "error_code": "record_runtime_receipt_failed",
                "error_message": "receipt backend unavailable",
            },
        )()

    def close(self) -> None:
        self._captured["closed"] = True


class _ContextFailingCognitiveRuntimeService:
    def __init__(self, captured: dict[str, Any]) -> None:
        self._captured = captured

    def resolve_context(self, command: Any) -> Any:
        self._captured["context_command"] = command
        return type(
            "ContextResult",
            (),
            {
                "ok": False,
                "error_code": "context_os_unavailable",
                "error_message": "Context OS projection unavailable",
                "snapshot": None,
            },
        )()

    def record_runtime_receipt(self, command: Any) -> Any:
        self._captured["receipt_command"] = command
        return type("Result", (), {"ok": True, "receipt": type("Receipt", (), {"receipt_id": "should-not-record"})()})()

    def close(self) -> None:
        self._captured["closed"] = True


def test_run_post_dispatch_integration_qa_accepts_args_keyword(tmp_path) -> None:
    # Use real filesystem paths, NOT KernelOne virtual paths (X:\...) which are
    # returned by resolve_artifact_path.  The function writes via os.makedirs +
    # write_json_atomic which needs real paths.
    run_dir = tmp_path / "runtime" / "runs" / "qa-accepts-args"
    run_dir.mkdir(parents=True, exist_ok=True)
    run_events = tmp_path / "runtime" / "events" / "runtime.events.jsonl"
    run_events.parent.mkdir(parents=True, exist_ok=True)
    dialogue_full = tmp_path / "runtime" / "events" / "dialogue.transcript.jsonl"
    dialogue_full.parent.mkdir(parents=True, exist_ok=True)

    payload = run_post_dispatch_integration_qa(
        args=SimpleNamespace(integration_qa=True),
        workspace_full=str(tmp_path),
        cache_root_full="",
        run_dir=str(run_dir),
        run_id="pm-00001",
        iteration=1,
        tasks=[
            {
                "id": "TASK-A",
                "assigned_to": "Director",
                "status": "needs_continue",
            }
        ],
        run_events=str(run_events),
        dialogue_full=str(dialogue_full),
    )

    assert payload["ran"] is False
    assert payload["passed"] is None
    assert payload["reason"] == "pending_director_tasks"
    assert payload["evidence_grade"] == "not_run"
    result_path = run_dir / "qa" / "integration_qa.result.json"
    assert result_path.is_file(), f"result file not found at {result_path}"


def test_run_post_dispatch_integration_qa_supports_calls_without_args(tmp_path) -> None:
    # Use real filesystem paths, NOT KernelOne virtual paths (X:\...) which are
    # returned by resolve_artifact_path.  The function writes via os.makedirs +
    # write_json_atomic which needs real paths.
    run_dir = tmp_path / "runtime" / "runs" / "qa-no-args"
    run_dir.mkdir(parents=True, exist_ok=True)
    run_events = tmp_path / "runtime" / "events" / "runtime.events.jsonl"
    run_events.parent.mkdir(parents=True, exist_ok=True)
    dialogue_full = tmp_path / "runtime" / "events" / "dialogue.transcript.jsonl"
    dialogue_full.parent.mkdir(parents=True, exist_ok=True)

    payload = run_post_dispatch_integration_qa(
        workspace_full=str(tmp_path),
        cache_root_full="",
        run_dir=str(run_dir),
        run_id="pm-00002",
        iteration=2,
        tasks=[
            {
                "id": "TASK-A",
                "assigned_to": "Director",
                "status": "needs_continue",
            }
        ],
        run_events=str(run_events),
        dialogue_full=str(dialogue_full),
    )

    assert payload["ran"] is False
    assert payload["passed"] is None
    assert payload["reason"] == "pending_director_tasks"
    assert payload["evidence_grade"] == "not_run"
    result_path = run_dir / "qa" / "integration_qa.result.json"
    assert result_path.is_file(), f"result file not found at {result_path}"


def test_run_post_dispatch_integration_qa_records_cognitive_runtime_receipt(monkeypatch, tmp_path) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "polaris.cells.factory.cognitive_runtime.public.get_cognitive_runtime_public_service",
        lambda: _SuccessfulCognitiveRuntimeService(captured),
    )
    run_dir = tmp_path / "runtime" / "runs" / "qa-receipt"
    run_dir.mkdir(parents=True, exist_ok=True)
    run_events = tmp_path / "runtime" / "events" / "runtime.events.jsonl"
    run_events.parent.mkdir(parents=True, exist_ok=True)
    dialogue_full = tmp_path / "runtime" / "events" / "dialogue.transcript.jsonl"
    dialogue_full.parent.mkdir(parents=True, exist_ok=True)

    payload = run_post_dispatch_integration_qa(
        args=SimpleNamespace(integration_qa=True),
        workspace_full=str(tmp_path),
        cache_root_full="",
        run_dir=str(run_dir),
        run_id="pm-00003",
        iteration=3,
        tasks=[
            {
                "id": "TASK-A",
                "assigned_to": "Director",
                "status": "done",
            }
        ],
        run_events=str(run_events),
        dialogue_full=str(dialogue_full),
        verify_runner=lambda workspace: (True, "Integration verification passed: pytest -q", []),
    )

    assert payload["passed"] is True
    receipt = payload["cognitive_runtime_receipt"]
    assert receipt["ok"] is True
    assert receipt["receipt_id"] == "receipt-dispatch-qa"
    result_path = run_dir / "qa" / "integration_qa.result.json"
    stored = json.loads(result_path.read_text(encoding="utf-8"))
    assert stored["cognitive_runtime_receipt"] == receipt
    receipt_command = captured["receipt_command"]
    assert receipt_command.receipt_type == "qa_verification"
    assert receipt_command.session_id == "qa-pm-00003-3"
    assert receipt_command.run_id == "pm-00003"
    assert str(result_path) in receipt_command.trace_refs
    assert receipt_command.payload["source"] == "pm_dispatch.integration_qa"
    assert receipt_command.payload["context_os_expected"] is True
    assert receipt_command.payload["context_os"]["ok"] is True
    assert receipt_command.payload["context_os"]["snapshot"]["context_os_summary"]["state_first_context_os"] is True
    context_command = captured["context_command"]
    assert context_command.role == "qa"
    assert context_command.session_id == "qa-pm-00003-3"
    assert context_command.mode == "pm_dispatch_integration_qa"
    assert context_command.policy["context_os_required"] is True
    assert receipt_command.turn_envelope["task_id"] == "qa::post_dispatch_integration"
    assert captured["closed"] is True


def test_run_post_dispatch_integration_qa_appends_fail_closed_run_ledger_without_job_token(tmp_path) -> None:
    run_dir = tmp_path / "runtime" / "runs" / "qa-ledger"
    run_dir.mkdir(parents=True, exist_ok=True)
    run_events = tmp_path / "runtime" / "events" / "runtime.events.jsonl"
    run_events.parent.mkdir(parents=True, exist_ok=True)
    dialogue_full = tmp_path / "runtime" / "events" / "dialogue.transcript.jsonl"
    dialogue_full.parent.mkdir(parents=True, exist_ok=True)

    payload = run_post_dispatch_integration_qa(
        args=SimpleNamespace(integration_qa=True),
        workspace_full=str(tmp_path),
        cache_root_full="",
        run_dir=str(run_dir),
        run_id="pm-ledger-missing-token",
        iteration=7,
        tasks=[
            {
                "id": "TASK-A",
                "assigned_to": "Director",
                "status": "done",
            }
        ],
        run_events=str(run_events),
        dialogue_full=str(dialogue_full),
        verify_runner=lambda workspace: (True, "Integration verification passed: pytest -q", []),
    )

    assert payload["passed"] is True
    assert payload["run_ledger_receipt"]["event"]["run_id"] == "pm-ledger-missing-token"

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(
            workspace=str(tmp_path),
            run_id="pm-ledger-missing-token",
        )
    ).projection
    assert projection["available"] is True
    assert projection["failed"] == 1
    assert projection["projects"][0]["latest_token_id"] == "qa-pm-ledger-missing-token"
    assert "missing_contract_hash" not in projection["projects"][0]["missing"]
    assert "missing_blueprint_hash" in projection["projects"][0]["missing"]


def test_run_post_dispatch_integration_qa_job_token_accepts_pm_contract_and_ce_blueprint_lineage(tmp_path) -> None:
    run_dir = tmp_path / "runtime" / "runs" / "qa-ledger-lineage"
    run_dir.mkdir(parents=True, exist_ok=True)
    run_events = tmp_path / "runtime" / "events" / "runtime.events.jsonl"
    run_events.parent.mkdir(parents=True, exist_ok=True)
    dialogue_full = tmp_path / "runtime" / "events" / "dialogue.transcript.jsonl"
    dialogue_full.parent.mkdir(parents=True, exist_ok=True)

    payload = run_post_dispatch_integration_qa(
        args=SimpleNamespace(integration_qa=True),
        workspace_full=str(tmp_path),
        cache_root_full="",
        run_dir=str(run_dir),
        run_id="pm-ledger-lineage",
        iteration=8,
        tasks=[
            {
                "id": "TASK-A",
                "assigned_to": "Director",
                "status": "done",
                "title": "Implement ledger-backed QA",
                "target_files": ["src/index.ts"],
                "metadata": {
                    "lineage": {
                        "blueprint_hash": "ce-blueprint-hash",
                        "job_token_id": "jt-qa-lineage",
                        "project_id": "ledger-project",
                    }
                },
            }
        ],
        run_events=str(run_events),
        dialogue_full=str(dialogue_full),
        verify_runner=lambda workspace: (True, "Integration verification passed: pytest -q", []),
    )

    assert payload["passed"] is True
    assert payload["contract_hash"]
    assert payload["blueprint_hash"] == "ce-blueprint-hash"

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(
            workspace=str(tmp_path),
            run_id="pm-ledger-lineage",
        )
    ).projection
    assert projection["available"] is True
    assert projection["ok"] is True
    assert projection["failed"] == 0
    assert projection["projects"][0]["latest_token_id"] == "jt-qa-lineage"
    assert projection["projects"][0]["missing"] == []


def test_run_post_dispatch_integration_qa_failure_requeues_director_with_critique(
    monkeypatch,
    tmp_path,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "polaris.cells.factory.cognitive_runtime.public.get_cognitive_runtime_public_service",
        lambda: _SuccessfulCognitiveRuntimeService(captured),
    )
    from polaris.cells.runtime.task_market.public.contracts import (
        AcknowledgeTaskStageCommandV1,
        ClaimTaskWorkItemCommandV1,
        PublishTaskWorkItemCommandV1,
        QueryTaskMarketStatusV1,
    )
    from polaris.cells.runtime.task_market.public.service import get_task_market_service

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = get_task_market_service()
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-integration-qa",
            run_id="pm-00003b",
            task_id="TASK-A",
            stage="pending_exec",
            source_role="PM",
            payload={
                "title": "Implement feature",
                "target_files": ["src/app.py"],
                "acceptance_criteria": ["pytest passes"],
            },
        )
    )
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-integration-qa-pm",
            run_id="pm-00003b",
            task_id="TASK-PM",
            stage="pending_exec",
            source_role="PM",
            payload={
                "title": "Plan feature",
                "target_files": ["docs/plan.md"],
                "acceptance_criteria": ["plan approved"],
            },
        )
    )
    claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director",
            worker_role="director",
            task_id="TASK-A",
        )
    )
    service.acknowledge_task_stage(
        AcknowledgeTaskStageCommandV1(
            workspace=str(workspace),
            task_id="TASK-A",
            lease_token=claim.lease_token,
            terminal_status="resolved",
            summary="Director completed",
        )
    )
    pm_claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="pm",
            worker_role="pm",
            task_id="TASK-PM",
        )
    )
    service.acknowledge_task_stage(
        AcknowledgeTaskStageCommandV1(
            workspace=str(workspace),
            task_id="TASK-PM",
            lease_token=pm_claim.lease_token,
            terminal_status="resolved",
            summary="PM completed",
        )
    )

    run_dir = tmp_path / "runtime" / "runs" / "qa-critique"
    run_dir.mkdir(parents=True, exist_ok=True)
    run_events = tmp_path / "runtime" / "events" / "runtime.events.jsonl"
    run_events.parent.mkdir(parents=True, exist_ok=True)
    dialogue_full = tmp_path / "runtime" / "events" / "dialogue.transcript.jsonl"
    dialogue_full.parent.mkdir(parents=True, exist_ok=True)

    payload = run_post_dispatch_integration_qa(
        args=SimpleNamespace(integration_qa=True),
        workspace_full=str(workspace),
        cache_root_full="",
        run_dir=str(run_dir),
        run_id="pm-00003b",
        iteration=3,
        tasks=[
            {
                "id": "TASK-A",
                "title": "Implement feature",
                "assigned_to": "Director",
                "status": "done",
                "target_files": ["src/app.py"],
                "acceptance_criteria": ["pytest passes"],
            },
            {
                "id": "TASK-PM",
                "title": "Plan feature",
                "assigned_to": "PM",
                "status": "done",
                "target_files": ["docs/plan.md"],
                "acceptance_criteria": ["plan approved"],
            },
        ],
        run_events=str(run_events),
        dialogue_full=str(dialogue_full),
        verify_runner=lambda workspace_arg: (
            False,
            "Integration verification failed: pytest -q",
            ["tests/test_app.py::test_feature failed"],
        ),
    )

    assert payload["passed"] is False
    assert payload["reason"] == "integration_qa_failed"
    feedback = payload["director_critique_feedback"]
    assert feedback["attempted_task_ids"] == ["TASK-A"]
    assert feedback["requeued_task_ids"] == ["TASK-A"]
    status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace), include_payload=True))
    rows = {item["task_id"]: item for item in status.items}
    row = rows["TASK-A"]
    assert row["status"] == "pending_design"
    assert rows["TASK-PM"]["status"] == "resolved"
    last_failure = row["payload"]["last_failure"]
    assert last_failure["error_code"] == "INTEGRATION_QA_FAILED"
    assert last_failure["source"] == "pm_dispatch.integration_qa"
    assert "pytest -q" in last_failure["error_message"]
    assert last_failure["target_files"] == ["src/app.py"]
    assert last_failure["ce_rework_required"] is True
    assert last_failure["ce_rework_blueprint_path"].startswith("runtime/blueprints/")
    assert last_failure["chief_engineer_handoff"]["chain"] == "PM->ChiefEngineer->Director"
    metadata = row["metadata"]["requeue_metadata"]
    assert metadata["source"] == "pm_dispatch.integration_qa.rework"
    assert metadata["ce_rework_required"] is True
    assert metadata["ce_rework_blueprint_id"]
    assert metadata["ce_rework_blueprint_path"].startswith("runtime/blueprints/")
    assert Path(resolve_logical_path(str(workspace), metadata["ce_rework_blueprint_path"])).is_file()
    assert metadata["chief_engineer_handoff"]["chain"] == "PM->ChiefEngineer->Director"
    assert metadata["chief_engineer_handoff"]["director_task_id"] == "TASK-A"
    assert metadata["verification_failure_report"]["failure_classification"] == "Director Execution"
    assert row["metadata"]["reopen_count"] == 1


def test_integration_qa_failure_requeues_fission_leaf_for_parent_pm_task(
    monkeypatch,
    tmp_path,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "polaris.cells.factory.cognitive_runtime.public.get_cognitive_runtime_public_service",
        lambda: _SuccessfulCognitiveRuntimeService(captured),
    )
    from polaris.cells.runtime.task_market.public.contracts import (
        PublishTaskWorkItemCommandV1,
        QueryTaskMarketStatusV1,
    )
    from polaris.cells.runtime.task_market.public.service import get_task_market_service

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = get_task_market_service()
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-fission-parent",
            run_id="pm-fission",
            task_id="TASK-PARENT",
            stage="pending_exec",
            source_role="PM",
            payload={
                "title": "Build fissioned feature",
                "target_files": ["src/app.py"],
                "acceptance_criteria": ["pytest passes"],
            },
            is_leaf=False,
        )
    )
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-fission-leaf",
            run_id="pm-fission",
            task_id="TASK-PARENT::step-1",
            stage="pending_exec",
            source_role="chief_engineer",
            payload={
                "title": "Implement leaf step",
                "target_files": ["src/app.py"],
                "acceptance_criteria": ["pytest passes"],
            },
            root_task_id="TASK-PARENT",
            parent_task_id="TASK-PARENT",
            is_leaf=True,
        )
    )

    run_dir = tmp_path / "runtime" / "runs" / "qa-fission"
    run_dir.mkdir(parents=True, exist_ok=True)
    run_events = tmp_path / "runtime" / "events" / "runtime.events.jsonl"
    run_events.parent.mkdir(parents=True, exist_ok=True)
    dialogue_full = tmp_path / "runtime" / "events" / "dialogue.transcript.jsonl"
    dialogue_full.parent.mkdir(parents=True, exist_ok=True)

    payload = run_post_dispatch_integration_qa(
        args=SimpleNamespace(integration_qa=True),
        workspace_full=str(workspace),
        cache_root_full="",
        run_dir=str(run_dir),
        run_id="pm-fission",
        iteration=4,
        tasks=[
            {
                "id": "TASK-PARENT",
                "title": "Build fissioned feature",
                "assigned_to": "Director",
                "status": "done",
                "target_files": ["src/app.py"],
                "acceptance_criteria": ["pytest passes"],
            }
        ],
        run_events=str(run_events),
        dialogue_full=str(dialogue_full),
        verify_runner=lambda workspace_arg: (
            False,
            "Integration verification failed: pytest -q",
            ["tests/test_app.py::test_feature failed"],
        ),
    )

    feedback = payload["director_critique_feedback"]
    assert feedback["attempted_task_ids"] == ["TASK-PARENT"]
    assert feedback["requeued_task_ids"] == ["TASK-PARENT::step-1"]
    assert feedback["skipped_task_ids"] == []
    status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace), include_payload=True))
    rows = {item["task_id"]: item for item in status.items}
    assert rows["TASK-PARENT"]["is_leaf"] is False
    assert rows["TASK-PARENT"]["status"] == "pending_exec"
    leaf = rows["TASK-PARENT::step-1"]
    assert leaf["status"] == "pending_design"
    last_failure = leaf["payload"]["last_failure"]
    assert last_failure["error_code"] == "INTEGRATION_QA_FAILED"
    assert last_failure["source"] == "pm_dispatch.integration_qa"
    assert "pytest -q" in last_failure["error_message"]
    assert last_failure["ce_rework_required"] is True
    assert last_failure["chief_engineer_handoff"]["director_task_id"] == "TASK-PARENT::step-1"
    metadata = leaf["metadata"]["requeue_metadata"]
    assert metadata["source"] == "pm_dispatch.integration_qa.rework"
    assert metadata["ce_rework_required"] is True
    assert metadata["chief_engineer_handoff"]["chain"] == "PM->ChiefEngineer->Director"
    assert metadata["chief_engineer_handoff"]["pm_task_id"] == "TASK-PARENT"
    assert metadata["chief_engineer_handoff"]["director_task_id"] == "TASK-PARENT::step-1"
    assert metadata["verification_failure_report"]["target_task_id"] == "TASK-PARENT::step-1"


def test_integration_qa_last_failure_preserves_string_errors() -> None:
    last_failure = dispatch_pipeline._build_integration_qa_last_failure(
        result={
            "reason": "integration_qa_failed",
            "summary": "Integration verification failed",
            "errors": "tests/test_app.py::test_feature failed",
        },
        task={
            "id": "TASK-A",
            "target_files": ["src/app.py"],
            "acceptance_criteria": ["pytest passes"],
        },
    )

    assert "tests/test_app.py::test_feature failed" in last_failure["error_message"]
    assert "t | e | s | t | s" not in last_failure["error_message"]


def test_run_post_dispatch_integration_qa_fails_closed_when_required_receipt_fails(monkeypatch, tmp_path) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "polaris.cells.factory.cognitive_runtime.public.get_cognitive_runtime_public_service",
        lambda: _FailingCognitiveRuntimeService(captured),
    )
    run_dir = tmp_path / "runtime" / "runs" / "qa-receipt-failed"
    run_dir.mkdir(parents=True, exist_ok=True)
    run_events = tmp_path / "runtime" / "events" / "runtime.events.jsonl"
    run_events.parent.mkdir(parents=True, exist_ok=True)
    dialogue_full = tmp_path / "runtime" / "events" / "dialogue.transcript.jsonl"
    dialogue_full.parent.mkdir(parents=True, exist_ok=True)

    payload = run_post_dispatch_integration_qa(
        args=SimpleNamespace(integration_qa=True),
        workspace_full=str(tmp_path),
        cache_root_full="",
        run_dir=str(run_dir),
        run_id="pm-00004",
        iteration=4,
        tasks=[
            {
                "id": "TASK-A",
                "assigned_to": "Director",
                "status": "done",
            }
        ],
        run_events=str(run_events),
        dialogue_full=str(dialogue_full),
        verify_runner=lambda workspace: (True, "Integration verification passed: pytest -q", []),
    )

    assert payload["passed"] is False
    assert payload["reason"] == "qa_cognitive_runtime_receipt_failed"
    assert payload["evidence_grade"] == "qa_error"
    assert payload["cognitive_runtime_receipt"]["ok"] is False
    assert payload["cognitive_runtime_receipt"]["error_message"] == "receipt backend unavailable"
    stored = json.loads(Path(payload["result_path"]).read_text(encoding="utf-8"))
    assert stored["reason"] == "qa_cognitive_runtime_receipt_failed"
    assert captured["closed"] is True


def test_run_post_dispatch_integration_qa_fails_closed_when_context_os_resolve_fails(monkeypatch, tmp_path) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "polaris.cells.factory.cognitive_runtime.public.get_cognitive_runtime_public_service",
        lambda: _ContextFailingCognitiveRuntimeService(captured),
    )
    run_dir = tmp_path / "runtime" / "runs" / "qa-context-failed"
    run_dir.mkdir(parents=True, exist_ok=True)
    run_events = tmp_path / "runtime" / "events" / "runtime.events.jsonl"
    run_events.parent.mkdir(parents=True, exist_ok=True)
    dialogue_full = tmp_path / "runtime" / "events" / "dialogue.transcript.jsonl"
    dialogue_full.parent.mkdir(parents=True, exist_ok=True)

    payload = run_post_dispatch_integration_qa(
        args=SimpleNamespace(integration_qa=True),
        workspace_full=str(tmp_path),
        cache_root_full="",
        run_dir=str(run_dir),
        run_id="pm-00005",
        iteration=5,
        tasks=[
            {
                "id": "TASK-A",
                "assigned_to": "Director",
                "status": "done",
            }
        ],
        run_events=str(run_events),
        dialogue_full=str(dialogue_full),
        verify_runner=lambda workspace: (True, "Integration verification passed: pytest -q", []),
    )

    assert payload["passed"] is False
    assert payload["reason"] == "qa_context_os_resolve_failed"
    assert payload["cognitive_runtime_receipt"]["context_os"]["ok"] is False
    assert payload["cognitive_runtime_receipt"]["context_os"]["error_message"] == "Context OS projection unavailable"
    assert "receipt_command" not in captured
    stored = json.loads(Path(payload["result_path"]).read_text(encoding="utf-8"))
    assert stored["reason"] == "qa_context_os_resolve_failed"
    assert captured["closed"] is True


def test_legacy_run_integration_qa_records_context_os_and_cognitive_receipt(monkeypatch, tmp_path) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "polaris.cells.factory.cognitive_runtime.public.get_cognitive_runtime_public_service",
        lambda: _SuccessfulCognitiveRuntimeService(captured),
    )
    monkeypatch.setattr(
        dispatch_pipeline,
        "_get_shared_quality",
        lambda: (
            lambda workspace: "pytest -q",
            lambda workspace: (True, "Integration verification passed: pytest -q", []),
        ),
    )

    payload = dispatch_pipeline.run_integration_qa(
        workspace_full=str(tmp_path),
        cache_root_full="",
        run_dir=str(tmp_path / "run"),
        run_id="pm-00006",
        iteration=6,
        tasks=[
            {
                "id": "TASK-A",
                "assigned_to": "Director",
                "status": "done",
            }
        ],
        run_events=str(tmp_path / "events.jsonl"),
        dialogue_full=str(tmp_path / "dialogue.jsonl"),
        docs_stage=None,
    )

    assert payload["passed"] is True
    assert payload["cognitive_runtime_receipt"]["ok"] is True
    assert payload["cognitive_runtime_receipt"]["context_os"]["ok"] is True
    receipt_command = captured["receipt_command"]
    assert receipt_command.receipt_type == "qa_verification"
    assert receipt_command.session_id == "qa-pm-00006-6"
    assert receipt_command.payload["context_os"]["snapshot"]["role"] == "qa"
    assert captured["context_command"].mode == "pm_dispatch_integration_qa"
