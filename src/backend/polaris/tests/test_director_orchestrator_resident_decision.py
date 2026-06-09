"""Tests for G3: DirectorOrchestrator.execute_task records a Resident decision.

The application-layer Director execution path previously recorded nothing into
the ``resident.autonomy`` decision trace (only the workflow engine did), which
starved the meta-cognition / skill / counterfactual loops in the standard
``director`` console path.  These tests pin the new best-effort recording and
its double-write guard.
"""

from __future__ import annotations

from typing import Any

import pytest
from polaris.application.orchestration.director_orchestrator import (
    DirectorExecutionConfig,
    DirectorOrchestrator,
    _record_director_decision_safe,
)


class _RecordingTaskBoard:
    def __init__(self) -> None:
        self.updates: list[tuple[int, dict[str, Any]]] = []

    def update(self, task_id: int, **kwargs: Any) -> None:
        self.updates.append((task_id, dict(kwargs)))


class _FakeDirectorAdapter:
    def __init__(self, *, success: bool = True) -> None:
        self._success = success

    async def execute(
        self,
        task_id: str,
        input_data: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "success": self._success,
            "task_id": task_id,
            "changed_files": ["src/app/main.py"] if self._success else [],
            "qa_required_for_final_verdict": True,
            "error": "" if self._success else "adapter failed",
        }


def _install_adapter(monkeypatch: pytest.MonkeyPatch, *, success: bool = True) -> None:
    monkeypatch.setattr(
        "polaris.cells.roles.adapters.public.service.create_role_adapter",
        lambda role_id, workspace: _FakeDirectorAdapter(success=success),
    )


def _capture_decisions(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    captured: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        "polaris.cells.resident.autonomy.public.service.record_resident_decision",
        lambda ws, payload: captured.append((ws, dict(payload))),
    )
    return captured


# -- end-to-end through execute_task ----------------------------------------


@pytest.mark.asyncio
async def test_execute_task_records_success_decision(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_adapter(monkeypatch, success=True)
    captured = _capture_decisions(monkeypatch)
    monkeypatch.delenv("KERNELONE_WORKFLOW_ID", raising=False)

    orchestrator = DirectorOrchestrator(DirectorExecutionConfig(workspace=str(tmp_path), execution_mode="serial"))
    orchestrator._task_board = _RecordingTaskBoard()

    await orchestrator.execute_task({"id": "1", "subject": "Implement feature"})

    assert len(captured) == 1
    ws, payload = captured[0]
    assert ws == str(tmp_path)
    assert payload["actor"] == "director"
    assert payload["stage"] == "task_execution"
    assert payload["verdict"] == "success"
    assert payload["context_refs"] == ["1"]
    assert payload["evidence_refs"] == ["src/app/main.py"]
    assert "serial_dispatch" in payload["strategy_tags"]


@pytest.mark.asyncio
async def test_execute_task_records_failure_verdict(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_adapter(monkeypatch, success=False)
    captured = _capture_decisions(monkeypatch)
    monkeypatch.delenv("KERNELONE_WORKFLOW_ID", raising=False)

    orchestrator = DirectorOrchestrator(DirectorExecutionConfig(workspace=str(tmp_path), execution_mode="serial"))
    orchestrator._task_board = _RecordingTaskBoard()

    await orchestrator.execute_task({"id": "7", "subject": "Broken task"})

    assert len(captured) == 1
    _, payload = captured[0]
    assert payload["verdict"] == "failure"
    assert payload["actual_outcome"]["success"] is False


@pytest.mark.asyncio
async def test_execute_task_skips_recording_under_workflow(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_adapter(monkeypatch, success=True)
    captured = _capture_decisions(monkeypatch)
    # Workflow context owns recording — orchestrator must not double-count.
    monkeypatch.setenv("KERNELONE_WORKFLOW_ID", "wf-abc123")

    orchestrator = DirectorOrchestrator(DirectorExecutionConfig(workspace=str(tmp_path), execution_mode="serial"))
    orchestrator._task_board = _RecordingTaskBoard()

    result = await orchestrator.execute_task({"id": "2", "subject": "Under workflow"})

    assert result.success is True  # task execution unaffected
    assert captured == []  # but no resident decision recorded


# -- helper-level guards ----------------------------------------------------


def test_helper_skips_under_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_decisions(monkeypatch)
    monkeypatch.setenv("KERNELONE_WORKFLOW_ID", "wf-1")
    _record_director_decision_safe("/ws", {"actor": "director"})
    assert captured == []


def test_helper_swallows_recording_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_ws: str, _payload: Any) -> None:
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(
        "polaris.cells.resident.autonomy.public.service.record_resident_decision",
        _boom,
    )
    monkeypatch.delenv("KERNELONE_WORKFLOW_ID", raising=False)
    # Must not raise — decision capture is observability, not a dependency.
    _record_director_decision_safe("/ws", {"actor": "director"})
