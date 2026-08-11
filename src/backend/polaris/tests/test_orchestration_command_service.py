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
    stub = _SubmitCaptureService()

    async def _get_service() -> _SubmitCaptureService:
        return stub

    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service.get_orchestration_service",
        _get_service,
    )
    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service.register_all_adapters",
        lambda _: None,
    )

    service = OrchestrationCommandService(settings={})
    result = await service.execute_pm_run(
        workspace=str(tmp_path),
        run_type="pm",
        options={
            "directive": "生成受控投影计划",
            "metadata": {
                "execution_backend": "projection_generate",
                "projection": {
                    "scenario_id": "scenario_alpha",
                    "project_slug": "projection_lab",
                },
            },
        },
    )

    assert result.status == "pending", result.message
    assert stub.request is not None
    assert stub.request.role_entries[0].metadata["execution_backend"] == "projection_generate"
    assert stub.request.metadata["execution_backend"] == "projection_generate"
    assert stub.request.metadata["projection"]["scenario_id"] == "scenario_alpha"


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


def test_select_pm_task_payloads_adds_explicit_task_file_tokens_to_delivery_scope(tmp_path: Path) -> None:
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
    assert payloads[0]["target_files"] == ["src/app.py", "requirements.txt", "README.md", "main.py"]
    assert payloads[0]["scope_paths"] == ["src", "src/app.py", "requirements.txt", "README.md", "main.py"]


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
    _allow_strict_handoff_for_orchestration_boundary(monkeypatch)
    stub = _SubmitCaptureService()

    async def _get_service() -> _SubmitCaptureService:
        return stub

    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service.get_orchestration_service",
        _get_service,
    )
    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service.register_all_adapters",
        lambda _: None,
    )
    blueprint_id = _generate_valid_ce_blueprint(
        tmp_path,
        task_id="task-1",
        objective="Execute projection reproject task",
        target_files=["src/projection.py"],
    )

    service = OrchestrationCommandService(settings={})
    result = await service.execute_director_run(
        workspace=str(tmp_path),
        tasks=["task-1"],
        options={
            "execution_mode": "parallel",
            "metadata": {
                "blueprint_id": blueprint_id,
                "execution_backend": "projection_reproject",
                "projection": {
                    "scenario_id": "scenario_alpha",
                    "experiment_id": "exp-001",
                },
            },
        },
    )

    assert result.status == "pending", result.message
    assert result.metadata is not None
    assert result.metadata["tasks_queued"] == 1
    assert result.metadata["requested_task_ids"] == ["task-1"]
    assert stub.request is not None
    assert stub.request.role_entries[0].metadata["execution_backend"] == "projection_reproject"
    assert stub.request.role_entries[0].metadata["blueprint_id"] == blueprint_id
    assert stub.request.role_entries[0].input == "Execute tasks: task-1"
    assert stub.request.metadata["tasks"] == ["task-1"]
    assert stub.request.metadata["execution_backend"] == "projection_reproject"
    assert stub.request.metadata["projection"]["experiment_id"] == "exp-001"


@pytest.mark.asyncio
async def test_execute_director_run_requires_chief_engineer_handoff(tmp_path: Path) -> None:
    service = OrchestrationCommandService(settings={})

    result = await service.execute_director_run(
        workspace=str(tmp_path),
        tasks=["task-1"],
        options={"execution_mode": "parallel"},
    )

    assert result.status == "failed"
    assert result.reason_code == "CHIEF_ENGINEER_HANDOFF_REQUIRED"
    assert "valid Chief Engineer blueprint/handoff evidence" in result.message


@pytest.mark.asyncio
async def test_execute_director_run_materializes_pm_task_payloads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stub = _SubmitCaptureService()

    async def _get_service() -> _SubmitCaptureService:
        return stub

    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service.get_orchestration_service",
        _get_service,
    )
    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service.register_all_adapters",
        lambda _: None,
    )
    blueprint_id = _generate_valid_ce_blueprint(
        tmp_path,
        task_id="T01-001",
        objective="Bootstrap TypeScript foundation",
        target_files=["package.json", "src/index.ts"],
    )
    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service._select_pm_task_payloads",
        lambda _workspace, _task_ids: [
            {
                "id": "T01-001",
                "title": "Bootstrap project",
                "goal": "Create the TypeScript foundation",
                "target_files": ["package.json", "src/index.ts"],
                "scope_paths": ["src/config"],
                "metadata": {"blueprint_id": blueprint_id},
            }
        ],
    )
    task_completion_projection = {
        "schema_version": "polaris.task_completion_projection.v1",
        "task_id": "T01-001",
        "project_contract_hash": "contract-hash",
        "projection_hash": "projection-hash",
        "owned_artifacts": [],
        "owned_entrypoints": [],
        "owned_verification": [],
    }
    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service."
        "validate_director_handoff_from_payload",
        lambda _workspace, _payload, require_strict: {
            "allowed": True,
            "reason": "handoff_ready",
            "decision_payload": {"allowed": True},
            "task_completion_projection": task_completion_projection,
            "require_strict": require_strict,
        },
    )

    service = OrchestrationCommandService(settings={})
    result = await service.execute_director_run(
        workspace=str(tmp_path),
        tasks=["T01-001"],
        options={"execution_mode": "parallel"},
    )

    assert result.status == "pending", result.message
    assert result.metadata is not None
    assert result.metadata["tasks_queued"] == 1
    assert stub.request is not None
    role_entry = stub.request.role_entries[0]
    assert "Bootstrap project" in role_entry.input
    assert role_entry.metadata["task_id"] == "T01-001"
    assert role_entry.metadata["pm_task_id"] == "T01-001"
    assert role_entry.metadata["blueprint_id"] == blueprint_id
    assert role_entry.metadata["target_files"] == ["package.json", "src/index.ts"]
    assert role_entry.metadata["scope_paths"] == ["src/config"]
    projected_completion = role_entry.metadata["task_completion_projection"]
    assert projected_completion == task_completion_projection
    assert stub.request.metadata["pm_task_payloads"][0]["id"] == "T01-001"
