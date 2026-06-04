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

from polaris.cells.orchestration.pm_dispatch.internal import dispatch_pipeline  # noqa: E402
from polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline import (  # noqa: E402
    run_post_dispatch_integration_qa,
)


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
