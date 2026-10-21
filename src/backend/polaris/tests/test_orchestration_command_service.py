"""Tests for OrchestrationCommandService status-query diagnostics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from polaris.cells.chief_engineer.blueprint.public import GenerateTaskBlueprintCommandV1, generate_task_blueprint
from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import (
    OrchestrationCommandService,
    _canonical_factory_stage_sequence,
    _select_pm_task_payloads,
    _validated_chief_engineer_handoff,
)
from polaris.cells.orchestration.workflow_runtime.internal.runtime_contracts import (
    OrchestrationSnapshot,
    RunStatus,
    TaskPhase,
    TaskSnapshot,
)
from polaris.kernelone.storage import resolve_runtime_path


def _generate_valid_ce_blueprint(
    workspace: Path,
    *,
    task_id: str,
    objective: str,
    target_files: list[str] | None = None,
) -> str:
    result = generate_task_blueprint(
        GenerateTaskBlueprintCommandV1(
            task_id=task_id,
            workspace=str(workspace),
            objective=objective,
            context={
                "task_title": objective,
                "target_files": target_files or ["src/app.py"],
                "acceptance_criteria": ["source behavior is implemented", "tests pass"],
                "execution_checklist": ["Implement source behavior", "Run tests"],
            },
        )
    )
    assert result.ok is True
    assert result.blueprint_id is not None
    return result.blueprint_id


def _allow_strict_handoff_for_orchestration_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep orchestration discovery tests focused on projection transport.

    Strict Chief Engineer completion-contract validation is covered by the
    blueprint Cell.  These tests exercise discovery/dispatch, so provide the
    already-validated public handoff result that boundary consumes.
    """

    def _validate(_workspace: str, payload: dict[str, object], *, require_strict: bool) -> dict[str, object]:
        task_id = str(payload.get("id") or payload.get("task_id") or payload.get("pm_task_id") or "").strip()
        metadata = payload.get("metadata")
        metadata_row = metadata if isinstance(metadata, dict) else {}
        blueprint_id = str(
            payload.get("blueprint_id")
            or payload.get("chief_engineer_blueprint_id")
            or metadata_row.get("blueprint_id")
            or metadata_row.get("chief_engineer_blueprint_id")
            or ""
        ).strip()
        allowed = bool(blueprint_id)
        job_token = {
            "schema_version": 1,
            "token_id": f"job-{task_id or 'unknown'}",
            "run_id": "run-test",
            "factory_run_id": "run-test",
            "project_id": "project-test",
            "stage": "pending_exec",
            "contract_hash": "a" * 64,
            "blueprint_hash": "b" * 64,
            "capability_audit": {"ok": True, "issues": []},
        }
        return {
            "allowed": allowed,
            "reason": "handoff_ready" if allowed else "chief_engineer_blueprint_missing",
            "decision_payload": {"allowed": allowed},
            "task_completion_projection": {
                "schema_version": "polaris.task_completion_projection.v1",
                "project_id": "project-test",
                "run_id": "run-test",
                "project_contract_hash": "c" * 64,
                "projection_hash": "p" * 64,
                "task_id": task_id,
                "owned_artifacts": [],
                "owned_entrypoints": [],
                "owned_verification": [],
            },
            "job_token": job_token,
            "capability_token": job_token,
            "require_strict": require_strict,
        }

    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service."
        "validate_director_handoff_from_payload",
        _validate,
    )


class _StubOrchestrationService:
    def __init__(self, snapshot: OrchestrationSnapshot | None) -> None:
        self._snapshot = snapshot

    async def query_run(self, run_id: str) -> OrchestrationSnapshot | None:
        if self._snapshot is None:
            return None
        return self._snapshot if self._snapshot.run_id == run_id else None


class _SubmitCaptureService:
    def __init__(self) -> None:
        self.request = None

    async def submit_run(self, request):
        self.request = request
        return OrchestrationSnapshot(
            run_id=request.run_id,
            workspace=str(request.workspace),
            mode=request.mode.value,
            status=RunStatus.PENDING,
            current_phase=TaskPhase.INIT,
        )


@pytest.mark.asyncio
async def test_query_run_status_includes_failed_task_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    snapshot = OrchestrationSnapshot(
        run_id="pm-run-001",
        workspace="C:/Temp/demo",
        mode="workflow",
        status=RunStatus.FAILED,
        current_phase=TaskPhase.EXECUTING,
        overall_progress=50.0,
    )

    pm_task = TaskSnapshot(
        task_id="task-0-pm",
        status=RunStatus.FAILED,
        phase=TaskPhase.EXECUTING,
        role_id="pm",
    )
    pm_task.error_category = "runtime"
    pm_task.error_message = "PM contract normalization failed: missing acceptance criteria"
    pm_task.updated_at = now

    qa_task = TaskSnapshot(
        task_id="task-1-qa",
        status=RunStatus.BLOCKED,
        phase=TaskPhase.EXECUTING,
        role_id="qa",
    )
    qa_task.error_category = "runtime"
    qa_task.error_message = "Upstream task failed"
    qa_task.updated_at = now - timedelta(seconds=1)

    snapshot.tasks = {
        pm_task.task_id: pm_task,
        qa_task.task_id: qa_task,
    }

    stub = _StubOrchestrationService(snapshot)

    async def _get_service() -> _StubOrchestrationService:
        return stub

    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service.get_orchestration_service",
        _get_service,
    )

    service = OrchestrationCommandService(settings={})
    result = await service.query_run_status("pm-run-001")

    assert result.status == "failed"
    assert "failed_task=task-0-pm (pm)" in str(result.message)
    assert "missing acceptance criteria" in str(result.message)
    assert isinstance(result.metadata, dict)
    assert result.metadata["failed_task_count"] == 2
    assert result.metadata["task_status_counts"]["failed"] == 1
    assert result.metadata["task_status_counts"]["blocked"] == 1
    failed_tasks = result.metadata["failed_tasks"]
    assert failed_tasks[0]["task_id"] == "task-0-pm"
    assert failed_tasks[0]["role_id"] == "pm"
    assert "missing acceptance criteria" in str(failed_tasks[0]["error_message"])


@pytest.mark.asyncio
async def test_query_run_status_prioritizes_failed_cause_over_later_blocked_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    snapshot = OrchestrationSnapshot(
        run_id="director-run-001",
        workspace="/tmp/demo",
        mode="workflow",
        status=RunStatus.FAILED,
        current_phase=TaskPhase.EXECUTING,
        overall_progress=50.0,
    )

    failed_director_task = TaskSnapshot(
        task_id="task-1-director",
        status=RunStatus.FAILED,
        phase=TaskPhase.EXECUTING,
        role_id="director",
    )
    failed_director_task.error_category = "runtime"
    failed_director_task.error_message = "director_materialization_quality_failed"
    failed_director_task.updated_at = now - timedelta(seconds=10)

    blocked_director_task = TaskSnapshot(
        task_id="task-2-director",
        status=RunStatus.BLOCKED,
        phase=TaskPhase.EXECUTING,
        role_id="director",
    )
    blocked_director_task.error_category = "runtime"
    blocked_director_task.error_message = "Director must claim TaskBoard task before execution"
    blocked_director_task.updated_at = now

    snapshot.tasks = {
        failed_director_task.task_id: failed_director_task,
        blocked_director_task.task_id: blocked_director_task,
    }

    stub = _StubOrchestrationService(snapshot)

    async def _get_service() -> _StubOrchestrationService:
        return stub

    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service.get_orchestration_service",
        _get_service,
    )

    service = OrchestrationCommandService(settings={})
    result = await service.query_run_status("director-run-001")

    assert result.status == "failed"
    assert "failed_task=task-1-director (director)" in str(result.message)
    assert "director_materialization_quality_failed" in str(result.message)
    failed_tasks = result.metadata["failed_tasks"]
    assert failed_tasks[0]["task_id"] == "task-1-director"
    assert failed_tasks[1]["task_id"] == "task-2-director"


@pytest.mark.asyncio
async def test_query_run_status_returns_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubOrchestrationService(None)

    async def _get_service() -> _StubOrchestrationService:
        return stub

    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service.get_orchestration_service",
        _get_service,
    )

    service = OrchestrationCommandService(settings={})
    result = await service.query_run_status("missing-run")

    assert result.status == "failed"
    assert result.reason_code == "RUN_NOT_FOUND"
    assert result.message == "Run missing-run not found"


@pytest.mark.asyncio
async def test_execute_pm_run_propagates_metadata_to_role_entry_and_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Retired orchestration rejects all prior metadata, budgets and handoff variations."""
    service = OrchestrationCommandService(settings={})
    result = await service.execute_pm_run(workspace=str(tmp_path))
    assert result.status == "failed"
    assert result.reason_code == "legacy_engine_retired"
    assert result.run_id == ""
    assert service.list_active_runs() == []


@pytest.mark.parametrize(
    "options,want_stage,want_call,want_output",
    [
        ({}, 1530, 750, 60000),
        ({"max_tokens": 20000}, 530, 250, 20000),
        ({"metadata": {"task_execution_contract": {"context_budget": {"max_tokens": 20000}}}}, 530, 250, 20000),
        ({"max_output_tokens": 64000}, 1630, 800, 64000),
        ({"metadata": {"execution_strategy": {"output_budget_tokens": 64000}}}, 1630, 800, 64000),
        ({"llm_max_tokens": 20000, "metadata": {"llm_max_tokens": 64000}}, 530, 250, 20000),
        ({"timeout_seconds": 1800, "llm_call_timeout_seconds": 800}, 1800, 800, 60000),
        ({"timeout_seconds": 600}, None, None, None),
        ({"metadata": {"pm_planning_deadline_epoch_seconds": float("nan")}}, None, None, None),
        ({"metadata": {"pm_planning_deadline_epoch_seconds": float("inf")}}, None, None, None),
        ({"metadata": {"pm_planning_deadline_epoch_seconds": 11505.0}}, None, None, None),
    ],
)
async def test_pm_command_admits_timeout_before_workflow_submission(
    tmp_path, monkeypatch, options, want_stage, want_call, want_output
):
    """Retired orchestration rejects all prior metadata, budgets and handoff variations."""
    service = OrchestrationCommandService(settings={})
    result = await service.execute_pm_run(workspace=str(tmp_path), options=options)
    assert result.status == "failed"
    assert result.reason_code == "legacy_engine_retired"
    assert result.run_id == ""
    assert service.list_active_runs() == []


def test_factory_stage_sequence_forces_unique_full_chain() -> None:
    assert _canonical_factory_stage_sequence(["pm_planning"]) == [
        "pm_planning",
        "chief_engineer_review",
        "director_dispatch",
        "quality_gate",
    ]
    assert _canonical_factory_stage_sequence(["docs", "pm", "director"]) == [
        "docs_generation",
        "pm_planning",
        "chief_engineer_review",
        "director_dispatch",
        "quality_gate",
    ]


def test_strict_handoff_rejects_completion_projection_without_job_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service."
        "validate_director_handoff_from_payload",
        lambda _workspace, _payload, require_strict: {
            "allowed": True,
            "reason": "handoff_ready",
            "decision_payload": {"allowed": True},
            "task_completion_projection": {
                "schema_version": "polaris.task_completion_projection.v1",
                "task_id": "TASK-1",
            },
            "require_strict": require_strict,
        },
    )

    allowed, reason, _decision, _completion, token = _validated_chief_engineer_handoff(
        "/workspace",
        {"task_id": "TASK-1", "blueprint_id": "ce_TASK-1"},
    )

    assert allowed is False
    assert reason == "strict Chief Engineer handoff lacks one exact validated JobToken"
    assert token == {}


def test_select_pm_task_payloads_discovers_chief_engineer_blueprint_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _allow_strict_handoff_for_orchestration_boundary(monkeypatch)
    contract_path = Path(resolve_runtime_path(str(tmp_path), "runtime/contracts/pm_tasks.contract.json"))
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(
        ('{"tasks": [{"id": "TASK-1", "title": "Create app", "goal": "Write source", "metadata": {}}]}\n'),
        encoding="utf-8",
    )
    blueprint_id = _generate_valid_ce_blueprint(
        tmp_path,
        task_id="TASK-1",
        objective="Write source for TASK-1",
        target_files=["src/app.py"],
    )
    blueprint_path = Path(resolve_runtime_path(str(tmp_path), f"runtime/blueprints/{blueprint_id}.json")).resolve()

    payloads = _select_pm_task_payloads(str(tmp_path), ["TASK-1"])

    assert len(payloads) == 1
    metadata = payloads[0]["metadata"]
    assert metadata["handoff_ready"] is True
    assert metadata["handoff_source"] == "chief_engineer_blueprint_file"
    assert metadata["chief_engineer_blueprint_id"] == blueprint_id
    assert metadata["runtime_blueprint_path"] == str(blueprint_path)
    assert metadata["handoff_decision"]["allowed"] is True


def test_select_pm_task_payloads_ignores_stale_or_malformed_blueprint_file(tmp_path: Path) -> None:
    contract_path = Path(resolve_runtime_path(str(tmp_path), "runtime/contracts/pm_tasks.contract.json"))
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(
        ('{"tasks": [{"id": "TASK-1", "title": "Create app", "goal": "Write source", "metadata": {}}]}\n'),
        encoding="utf-8",
    )
    blueprint_path = Path(resolve_runtime_path(str(tmp_path), "runtime/blueprints/ce_TASK-1_20260621.json"))
    blueprint_path.parent.mkdir(parents=True, exist_ok=True)
    blueprint_path.write_text(
        '{"task_id": "OTHER", "target_files": [], "acceptance_criteria": [], "construction_plan": {}}\n',
        encoding="utf-8",
    )

    payloads = _select_pm_task_payloads(str(tmp_path), ["TASK-1"])

    assert len(payloads) == 1
    assert payloads[0]["metadata"] == {}


def test_select_pm_task_payloads_preserves_structured_scope_despite_prose_file_tokens(tmp_path: Path) -> None:
    plan_path = tmp_path / ".polaris" / "plans" / "latest.plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        (
            '{"tasks": [{'
            '"id": "TASK-1",'
            '"title": "Bootstrap Python project",'
            '"goal": "Create src/app.py and a runnable Python entrypoint.",'
            '"target_files": ["src/app.py"],'
            '"scope_paths": ["src"],'
            '"steps": ["Create requirements.txt before running python -m pip install -r requirements.txt."],'
            '"acceptance": ["README.md documents execution through main.py."],'
            '"metadata": {}'
            "}]}\n"
        ),
        encoding="utf-8",
    )
    blueprint_path = tmp_path / ".polaris" / "blueprints" / "ce_TASK-1_20260621.json"
    blueprint_path.parent.mkdir(parents=True, exist_ok=True)
    blueprint_path.write_text('{"task_id": "TASK-1", "construction_plan": {}}\n', encoding="utf-8")

    payloads = _select_pm_task_payloads(str(tmp_path), ["TASK-1"])

    assert len(payloads) == 1
    assert payloads[0]["target_files"] == ["src/app.py"]
    assert payloads[0]["scope_paths"] == ["src"]
    assert "requirements.txt" in payloads[0]["steps"][0]
    assert "README.md" in payloads[0]["acceptance"][0]


def test_select_pm_task_payloads_discovers_factory_pm_plan_mirror_and_workspace_blueprint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _allow_strict_handoff_for_orchestration_boundary(monkeypatch)
    plan_path = tmp_path / ".polaris" / "plans" / "latest.plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        (
            '{"tasks": ['
            '{"id": "TASK-1", "title": "Create app", "goal": "Write source", "metadata": {}},'
            '{"id": "TASK-2", "title": "Add tests", "goal": "Run gate", "metadata": {}}'
            "]}\n"
        ),
        encoding="utf-8",
    )
    blueprint_id = _generate_valid_ce_blueprint(
        tmp_path,
        task_id="TASK-2",
        objective="Run gate for TASK-2",
        target_files=["tests/test_app.py"],
    )
    blueprint_path = Path(resolve_runtime_path(str(tmp_path), f"runtime/blueprints/{blueprint_id}.json")).resolve()

    payloads = _select_pm_task_payloads(str(tmp_path), ["TASK-2"])

    assert len(payloads) == 1
    assert payloads[0]["id"] == "TASK-2"
    metadata = payloads[0]["metadata"]
    assert metadata["handoff_ready"] is True
    assert metadata["handoff_source"] == "chief_engineer_blueprint_file"
    assert metadata["chief_engineer_blueprint_id"] == blueprint_id
    assert metadata["runtime_blueprint_path"] == str(blueprint_path.resolve())
    assert metadata["handoff_decision"]["allowed"] is True


@pytest.mark.asyncio
async def test_execute_director_run_propagates_metadata_to_role_entry_and_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Retired orchestration rejects all prior metadata, budgets and handoff variations."""
    service = OrchestrationCommandService(settings={})
    result = await service.execute_director_run(workspace=str(tmp_path))
    assert result.status == "failed"
    assert result.reason_code == "legacy_engine_retired"
    assert result.run_id == ""
    assert service.list_active_runs() == []


@pytest.mark.asyncio
async def test_execute_director_run_requires_chief_engineer_handoff(tmp_path: Path) -> None:
    """Retired orchestration rejects all prior metadata, budgets and handoff variations."""
    service = OrchestrationCommandService(settings={})
    result = await service.execute_director_run(workspace=str(tmp_path))
    assert result.status == "failed"
    assert result.reason_code == "legacy_engine_retired"
    assert result.run_id == ""
    assert service.list_active_runs() == []


@pytest.mark.asyncio
async def test_execute_director_run_materializes_pm_task_payloads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Retired orchestration rejects all prior metadata, budgets and handoff variations."""
    service = OrchestrationCommandService(settings={})
    result = await service.execute_pm_run(workspace=str(tmp_path))
    assert result.status == "failed"
    assert result.reason_code == "legacy_engine_retired"
    assert result.run_id == ""
    assert service.list_active_runs() == []
